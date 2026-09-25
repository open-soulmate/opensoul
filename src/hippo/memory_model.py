"""kilocode supplement3 #8 记忆模型独立解析链（MemoryModel.port 移植）。

调研来源：kilocode-source-supplement3.md #8「记忆模型独立解析链（MemoryModel.port）：
配置模型无效→warn回退session模型；OpenAI走streamText手工收集（规避store字段问题）；
timeout+AbortSignal.any双取消；temperature/topP/topK按模型解析 | OpenSoul LLM_MAX_TOKENS
全局 | 部分有 | "记忆蒸馏用小模型、失败回退会话模型"省成本范式」。
源码级精读：~/agent-research-src/kilocode/packages/opencode/src/kilocode/memory/ports.ts
（MemoryModel.port resolve/run 两方法 + memoryText 的 AbortSignal.any([ctl.signal,
input.signal]) 双取消 + Promise.race 超时 + consolidationOptions 按模型解析采样）。

此前状态：dream_distiller._call_gland_llm / memory_pipeline._call_llm 两处 ~40行
near-duplicate 各自手搓 ModelRouter，模型恒为 settings.llm_model（=会话/全局聊天模型），
**无独立记忆模型配置**（"记忆蒸馏用小模型、失败回退会话模型"省成本范式缺失）、
**无端到端超时**（router底层httpx仅在无注入client时有180s上限，fallback链会放大）、
**无调用方取消**、采样参数硬编码（0.3/0.2）不可按模型解析。

本模块 = 记忆蒸馏专用模型解析链：
1. resolve_memory_model()（kilocode MemoryModel.port.resolve）：
   - 配置了记忆模型但spec非法 → warn + 回退会话模型，fallback.reason="invalid model"
   - 配置了记忆模型但不可用（可用性钩子model_exists判定）→ warn + 回退，
     fallback.reason="model unavailable"
   - 未配置 → 会话模型（source="session"，无fallback）
   - 有效 → 记忆模型（source="memory_config"），base_url/api_key 可独立于会话模型
   （kilocode模型来自models.dev权威目录；本侧是env声明值，"可能失真"风险与
   context_budget同款已知偏差——可用性分支经model_exists钩子可注入权威探测）
2. call_memory_llm()（kilocode MemoryModel.port.run + memoryText）：
   - **timeout + 调用方cancel_event双取消**（AbortSignal.any同构：任一先到即中止
     in-flight调用；work先完成则结果赢——与Promise.race同语义）
   - temperature/top_p/top_k按模型解析（ResolvedMemoryModel.sampling覆盖调用方默认）
   - 记忆模型运行时故障（非超时/非取消）→ warn + **一次性回退会话模型重试**
     （"失败回退会话模型"的调用时形态——resolve期无法探测的不可用在这里兜住），
     回退计数进describe()可观测
   - extract_chat_text权威解包（kilocode"OpenAI手工收集规避store字段"同族：
     不猜响应结构）
3. describe()：解析链快照（configured/resolved/source/fallback/timeout/sampling）
   → /api/hippo/health（"我都不知道他们在干嘛"的记忆模型维度答案）。

fail-safe纪律（mem0 §1.1 + turn.ts订阅器同款）：describe()绝不抛出（health不能炸）；
超时/取消是**显式契约**（TimeoutError/CancelledError原样上抛，不伪装成router故障），
其余异常才包RuntimeError——"失败必须可见，禁止静默降级"。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("opensoul.hippo.memory_model")

# kilocode memoryText: "const ms = Math.max(1, input.timeoutMs)" —— 超时必须为正
DEFAULT_MEMORY_TIMEOUT_S = 120.0

# spec合法性（kilocode MemoryConfig.parse的"invalid model"分支同义）：
# 非空、无内部空白/控制字符、长度≤200。空串=「未配置」走会话模型（非invalid）。
_SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_SPEC_MAX = 200

# 运行时回退计数（describe可观测；进程内轻量状态，不做持久化）
_runtime_fallbacks: list[dict] = []


@dataclass
class ResolvedMemoryModel:
    """记忆蒸馏模型解析结果（kilocode ModelHandle同构：source+language+options+采样）。"""

    model_id: str
    base_url: str = ""
    api_key: str = ""
    source: str = "session"  # session | memory_config
    # 回退发生时 {"reason": "invalid model"|"model unavailable", "configured": spec}
    fallback: dict | None = None
    # 会话模型落点（运行时回退"失败回退会话模型"的重试目标——与解析期同一真源，
    # 不在回退时刻重新读settings，防两次解析不一致）
    session: dict = field(default_factory=dict)
    # temperature/top_p/top_k按模型解析（settings.memory_*声明值；None=调用方默认）
    sampling: dict = field(default_factory=dict)

    def describe(self) -> dict:
        return {
            "resolved_model": self.model_id,
            "base_url": self.base_url,
            "source": self.source,
            "fallback": self.fallback,
            "sampling": {k: v for k, v in self.sampling.items() if v is not None},
        }


def parse_memory_model_spec(spec: str) -> str | None:
    """解析记忆模型spec。合法→规范化model_id；非法→None（调用方warn+回退会话模型）。

    kilocode MemoryConfig.parse(configured)的`configured && !parsed`分支同语义：
    「配置了但解析不出来」是独立的失败形态（fallback.reason="invalid model"），
    与「没配置」（走会话模型、无fallback）严格区分——失败必须可见。
    """
    text = str(spec or "").strip()
    if not text:
        return None
    if len(text) > _SPEC_MAX:
        return None
    if not _SPEC_RE.match(text):
        return None
    return text


def resolve_memory_model(
    configured: str | None = None,
    session_model: str | None = None,
    session_base_url: str | None = None,
    session_api_key: str | None = None,
    memory_base_url: str | None = None,
    memory_api_key: str | None = None,
    model_exists: Callable[[str], bool] | None = None,
    sampling: dict | None = None,
) -> ResolvedMemoryModel:
    """记忆模型解析链（kilocode MemoryModel.port.resolve逐分支移植）。

    参数为None时从src.config.settings取（memory_model/llm_model等）；
    model_exists=可用性探测钩子（kilocode的provider.getModel权威目录判定；
    本侧env声明无权威目录，缺省None=假定可用——如部署了权威探测可注入）。
    """
    from src.config import settings

    sess_model = session_model if session_model is not None else settings.llm_model
    sess_base = session_base_url if session_base_url is not None else settings.llm_base_url
    sess_key = session_api_key if session_api_key is not None else settings.llm_api_key

    spec = configured if configured is not None else getattr(settings, "memory_model", "")
    mem_base = (
        memory_base_url if memory_base_url is not None else getattr(settings, "memory_base_url", "")
    )
    mem_key = (
        memory_api_key if memory_api_key is not None else getattr(settings, "memory_api_key", "")
    )

    if sampling is None:
        sampling = {
            "temperature": getattr(settings, "memory_temperature", None),
            "top_p": getattr(settings, "memory_top_p", None),
            "top_k": getattr(settings, "memory_top_k", None),
        }

    def _session(reason: str | None) -> ResolvedMemoryModel:
        fb = {"reason": reason, "configured": str(spec or "")} if reason else None
        return ResolvedMemoryModel(
            model_id=sess_model,
            base_url=sess_base,
            api_key=sess_key,
            source="session",
            fallback=fb,
            session={"model_id": sess_model, "base_url": sess_base, "api_key": sess_key},
            sampling=dict(sampling or {}),
        )

    raw = str(spec or "").strip()
    if not raw:
        # 未配置：会话模型，无fallback（kilocode：else分支直接sessionModel()）
        return _session(None)

    parsed = parse_memory_model_spec(raw)
    if parsed is None:
        # 配置了但非法：warn可见 + 回退会话模型（kilocode：reason="invalid model"）
        logger.warning("memory model config ignored: reason=invalid model, model=%r", raw)
        return _session("invalid model")

    if model_exists is not None and not model_exists(parsed):
        # 配置合法但模型不可用：warn可见 + 回退（kilocode：reason="model unavailable"）
        logger.warning("memory model config ignored: reason=model unavailable, model=%r", parsed)
        return _session("model unavailable")

    return ResolvedMemoryModel(
        model_id=parsed,
        base_url=str(mem_base or sess_base or ""),
        api_key=str(mem_key or sess_key or ""),
        source="memory_config",
        fallback=None,
        session={"model_id": sess_model, "base_url": sess_base, "api_key": sess_key},
        sampling=dict(sampling or {}),
    )


def _build_router(resolved: ResolvedMemoryModel):
    """fresh ModelRouter（dream_distiller live实证教训：api/gland gateway单例跨event
    loop复用会400——每次调用新建+显式注册providers，本地兜底priority=10
    =CowAgent有序降级链语义，register_local_backup单一真源+探活豁免——与
    api/gland/branch_summary三处同规收敛）。"""
    from src.gland.router import ModelRouter

    router = ModelRouter()
    if resolved.base_url:
        router.add_provider(
            name="openai",
            base_url=resolved.base_url,
            models={"chat": resolved.model_id},
            priority=0,
        )
        if resolved.api_key:
            router.key_manager.add_key("openai", resolved.api_key)
    # 变体备胎入链（priority=5，单一真源register_variant_backup）：激活变体之外
    # 的另一已配置变体（标准API/订阅制双体系两套凭据）——激活端点402余额耗尽/
    # 宕机时的真实备胎。2026-09-25 live实证：standard 402 时 subscription(token-
    # plan) 仍 ok，但此前备胎从未入链，dream/Phase1 全链饿死。fail-safe：配置
    # 解析失败→不注册（行为回到原样）。
    try:
        from src.api.llm import register_variant_backup

        register_variant_backup(router, resolved.model_id)
    except Exception:  # noqa: BLE001 — 备胎注册绝不反噬记忆调用
        pass
    ollama_url = "http://localhost:11434/v1"
    try:
        from src.config import settings as _settings

        ollama_url = getattr(_settings, "ollama_base_url", ollama_url)
    except Exception:  # noqa: BLE001 — 兜底URL，配置读取失败不反噬
        pass
    # 本地兜底入链（priority=10）——register_local_backup单一真源+探活豁免：
    # 不可达→不入链（链上不留幻影备胎，01:35遗留#2/08:48遗留#3销账）。
    try:
        from src.api.llm import register_local_backup

        register_local_backup(router, ollama_url, {"chat": "deepseek-r1:latest"})
    except Exception:  # noqa: BLE001 — 备胎注册绝不反噬记忆调用
        pass
    return router


async def _one_shot(
    resolved: ResolvedMemoryModel,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None,
    max_tokens: int,
    role: str,
    router_factory: Callable[[ResolvedMemoryModel], Any] | None,
) -> str:
    """单模型单次调用（extract_chat_text权威解包）。"""
    from src.gland.router import extract_chat_text

    router = (router_factory or _build_router)(resolved)
    eff_temp = resolved.sampling.get("temperature")
    if eff_temp is None:
        eff_temp = temperature
    result = await router.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # 显式model恒赢（gland _resolve_model: `if model: return model`）——
        # 记忆模型是独立解析链，不许被role/task路由顶掉
        model=resolved.model_id,
        temperature=eff_temp,
        max_tokens=max_tokens,
        role=role,  # Harness Profile: summarize role → own budget/clamp
        top_p=resolved.sampling.get("top_p"),
        top_k=resolved.sampling.get("top_k"),
    )
    return extract_chat_text(result)


def _note_runtime_fallback(reason: str) -> None:
    try:
        _runtime_fallbacks.append({"reason": str(reason)[:300], "at": time.time()})
        # 轻量保留上限（fail-safe，绝不反噬调用方）
        del _runtime_fallbacks[:-20]
    except Exception:  # noqa: BLE001
        pass


async def call_memory_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = 0.3,
    max_tokens: int = 4096,
    timeout_s: float | None = None,
    cancel_event: asyncio.Event | None = None,
    role: str = "summarize",
    resolved: ResolvedMemoryModel | None = None,
    router_factory: Callable[[ResolvedMemoryModel], Any] | None = None,
) -> str:
    """记忆蒸馏LLM调用（kilocode MemoryModel.port.run + memoryText双取消语义）。

    - timeout：端到端预算（settings.memory_llm_timeout_s，kilocode timeoutMs同位），
      超时中止in-flight调用并抛TimeoutError——**显式失败**，绝不静默降级
    - cancel_event：调用方取消（AbortSignal.input.signal同位）；timeout+cancel
      =AbortSignal.any([ctl.signal, input.signal])——任一先到即中止
    - 完成优先：work与timeout/cancel同轮完成时work赢（Promise.race同语义）
    - 记忆模型运行时故障→warn+一次性回退会话模型重试（"失败回退会话模型"）；
      会话模型也故障→异常原样上抛（链耗尽失败必须可见）
    """
    from src.config import settings

    res = resolved or resolve_memory_model()
    sess_resolved = None  # 惰性：只在需要回退时构建

    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError("memory model call cancelled")

    if timeout_s is None:
        timeout_s = float(getattr(settings, "memory_llm_timeout_s", DEFAULT_MEMORY_TIMEOUT_S) or 0)
    timeout_s = max(1.0, float(timeout_s)) if timeout_s and timeout_s > 0 else 0.0

    async def _work() -> str:
        nonlocal sess_resolved
        try:
            return await _one_shot(
                res,
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                role=role,
                router_factory=router_factory,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise
        except Exception as e:  # noqa: BLE001 — 记忆模型故障→回退会话模型
            if res.source != "memory_config" or res.fallback is not None:
                raise  # 本来就是会话模型/已回退过：不再二次回退，失败可见
            sess = res.session or {}
            sess_resolved = ResolvedMemoryModel(
                model_id=str(sess.get("model_id") or ""),
                base_url=str(sess.get("base_url") or ""),
                api_key=str(sess.get("api_key") or ""),
                source="session",
                fallback={"reason": "runtime failure", "configured": res.model_id},
                sampling=dict(res.sampling or {}),
            )
            logger.warning(
                "memory model failed (%s) — falling back to session model %s",
                e,
                sess_resolved.model_id,
            )
            _note_runtime_fallback(str(e))
            return await _one_shot(
                sess_resolved,
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                role=role,
                router_factory=router_factory,
            )

    work_task = asyncio.ensure_future(_work())
    timeout_task = asyncio.ensure_future(asyncio.sleep(timeout_s)) if timeout_s > 0 else None
    cancel_task = asyncio.ensure_future(cancel_event.wait()) if cancel_event is not None else None
    try:
        waiters = {work_task} | {t for t in (timeout_task, cancel_task) if t is not None}
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if work_task in done:
            return work_task.result()
        # timeout或cancel先到：中止in-flight（AbortSignal.abort同构）
        work_task.cancel()
        try:
            await work_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        if cancel_task is not None and cancel_task in done:
            raise asyncio.CancelledError("memory model call cancelled")
        raise TimeoutError(f"memory model timed out after {timeout_s}s")
    finally:
        for t in (timeout_task, cancel_task):
            if t is not None:
                t.cancel()


def describe() -> dict:
    """解析链快照（/api/hippo/health观测字段）。fail-safe：绝不抛出。"""
    try:
        from src.config import settings

        res = resolve_memory_model()
        out = res.describe()
        out["configured_model"] = str(getattr(settings, "memory_model", "") or "")
        out["timeout_s"] = float(
            getattr(settings, "memory_llm_timeout_s", DEFAULT_MEMORY_TIMEOUT_S) or 0
        )
        out["runtime_fallback_count"] = len(_runtime_fallbacks)
        out["runtime_fallback_last"] = _runtime_fallbacks[-1] if _runtime_fallbacks else None
        return out
    except Exception as e:  # noqa: BLE001 — health观测绝不反噬
        return {"error": f"memory_model describe failed: {e}"}
