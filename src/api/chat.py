import asyncio
import json
import logging
from uuid import UUID

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.config import settings
from src.cortex.loop_guard import LoopGuard
from src.services.search import semantic_search

logger = logging.getLogger(__name__)

router = APIRouter()


def _llm_headers(api_key: str) -> dict:
    """统一LLM请求头：tp-前缀(token-plan)用api-key头，其余Bearer，空key不带鉴权(local)"""
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.startswith("tp-"):
        headers["api-key"] = api_key
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _route_llm_targets(message: str):
    """模型路由4按钮接线：route_policy决策 → 调用尝试序列(主+备) + routing元数据。
    route_policy不可用时静默回退settings静态配置，不阻断chat。"""
    try:
        from src.gland.route_policy import resolve_target
        d = resolve_target(message)
        attempts = [d["target"]]
        if d.get("backup_target"):
            attempts.append(d["backup_target"])
        meta = {k: d.get(k) for k in ("mode", "prefer", "reason", "complexity") if d.get(k) is not None}
        return attempts, meta
    except Exception as exc:
        logger.debug("route_policy unavailable, fallback to settings: %s", exc)
        return (
            [{"provider": "settings", "base_url": settings.llm_base_url,
              "model": settings.llm_model, "api_key": settings.llm_api_key}],
            {"mode": "fallback-settings"},
        )


def _get_memory_context(query: str) -> str:
    """P0-6: 从hippo长期记忆检索三因子上下文（recency+importance+relevance）。失败时静默降级。"""
    try:
        from src.hippo.long_term_memory import LongTermMemoryStore
        store = LongTermMemoryStore()
        return store.get_context_prompt(query=query, token_budget=300)
    except Exception as exc:
        logger.debug("hippo memory context unavailable: %s", exc)
        return ""


def _redact_outbound(text: str) -> str:
    """P0-4: 出站脱敏，防止API key/token泄漏到LLM provider。失败时原样返回。"""
    try:
        from src.immune.moderator import ContentModerator
        mod = ContentModerator()
        result = mod.moderate(text)
        return result.redacted_text if result.findings else text
    except Exception as exc:
        logger.debug("outbound redaction unavailable: %s", exc)
        return text


async def _compress_if_needed(context: str, budget_chars: int = 12000) -> str:
    """P0-1: 上下文压缩 — 超预算时用ContextCompressor压缩（goose 9段式+kilocode切分）。
    ContextCompressor.compress()签名是messages列表+context_limit，需llm_fn。
    失败时截断降级。"""
    if len(context) <= budget_chars:
        return context
    try:
        import httpx as _httpx
        from src.core.config import settings as _settings
        from src.cortex.context_compression import ContextCompressor

        async def _llm_fn(prompt: str) -> str:
            attempts, _meta = _route_llm_targets(prompt)
            last_exc: Exception | None = None
            for t in attempts:
                if t.get("provider") == "online" and not t.get("api_key"):
                    continue
                try:
                    async with _httpx.AsyncClient(timeout=60) as c:
                        resp = await c.post(
                            f"{t['base_url']}/chat/completions",
                            headers=_llm_headers(t.get("api_key", "")),
                            json={"model": t["model"], "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
                        )
                        resp.raise_for_status()
                        return resp.json()["choices"][0]["message"]["content"]
                except Exception as exc:
                    last_exc = exc
                    continue
            raise RuntimeError(f"all routed LLM targets failed: {last_exc}")

        compressor = ContextCompressor(llm_fn=_llm_fn)
        messages = [{"role": "user", "content": context}]
        result = await compressor.compress(messages, context_limit=budget_chars // 3)
        if hasattr(result, "summary") and result.summary:
            return result.summary
        if hasattr(result, "compressed_messages") and result.compressed_messages:
            return "\n".join(m.get("content", "") for m in result.compressed_messages)
        return context[:budget_chars] + "\n...[compressed]"
    except Exception as exc:
        logger.debug("context compression unavailable, truncating: %s", exc)
        return context[:budget_chars] + "\n...[context truncated]"


# ── P0 cortex循环guard按会话持久化 ──────────────────────────────────────
# 修复接线缺陷：原实现每次请求new LoopGuard()——滑窗/去重集合每次请求清零，
# 重复检测永远不触发（guard接线了但检测是死的）。检测语义要求状态跨请求累积
# （同一用户多轮回答才构成"重复输出"），按session_key持久化实例。
_CHAT_LOOP_GUARDS: dict[str, LoopGuard] = {}
_CHAT_LOOP_GUARDS_MAX = 256  # 有界防泄漏；满时淘汰最旧（dict保持插入序）


def _get_chat_loop_guard(session_key: str) -> LoopGuard:
    """按会话key取持久化guard（同key同实例，状态跨请求累积）。"""
    guard = _CHAT_LOOP_GUARDS.get(session_key)
    if guard is None:
        while len(_CHAT_LOOP_GUARDS) >= _CHAT_LOOP_GUARDS_MAX:
            _CHAT_LOOP_GUARDS.pop(next(iter(_CHAT_LOOP_GUARDS)))
        guard = LoopGuard()
        _CHAT_LOOP_GUARDS[session_key] = guard
    return guard


def _check_loop(response_text: str, session_key: str = "default") -> dict | None:
    """P0 cortex: 循环/重复检测guard（持久化实例）。检测到循环返回警告信息，否则None。"""
    try:
        guard = _get_chat_loop_guard(session_key)
        result = guard.check(text_response=response_text)
        if result and result.is_looping:
            return {"type": "loop_warning", "severity": str(result.severity),
                    "message": result.message}
    except Exception as exc:
        logger.debug("loop guard unavailable: %s", exc)
    return None


@router.get("/health")
async def chat_health():
    """Chat system health check."""
    return {"status": "ok", "component": "ChatSystem"}


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5
    stream: bool = True


async def rag_stream(question: str, user_id: UUID, top_k: int):
    """Generate SSE streaming response for RAG query."""
    answer_parts: list[str] = []  # P0循环guard：累积流式输出，流结束时做重复检测
    # Retrieve relevant chunks (timeout 8s: RAG不可用时降级为空context)
    try:
        results = await asyncio.wait_for(
            semantic_search(question, user_id, limit=top_k), timeout=8
        )
    except (asyncio.TimeoutError, Exception) as _rag_exc:
        logger.warning("RAG unavailable (stream), degrading to LLM-only: %s", _rag_exc)
        results = []

    if not results:
        yield f"data: {json.dumps({'type': 'info', 'content': 'RAG检索不可用，直接使用LLM回答。'})}\n\n"
        # 降级：不返回error，继续走LLM路径（模型路由接线：按mode选target+备target重试）
        prompt = question
        attempts, _meta = _route_llm_targets(question)
        last_exc: Exception | None = None
        streamed_ok = False
        for t in attempts:
            if t.get("provider") == "online" and not t.get("api_key"):
                continue
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST", f"{t['base_url']}/chat/completions",
                        headers=_llm_headers(t.get("api_key", "")),
                        json={"model": t["model"],
                              "messages": [{"role": "user", "content": prompt}],
                              "temperature": 0.3, "stream": True},
                        timeout=60,
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content")
                                if content:
                                    answer_parts.append(content)
                                    yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                            except json.JSONDecodeError:
                                continue
                streamed_ok = True
                break
            except Exception as exc:
                last_exc = exc
                continue
        if not streamed_ok:
            yield f"data: {json.dumps({'type': 'error', 'content': f'LLM也不可用: {last_exc}'})}\n\n"
        else:
            # P0 cortex: 流式路径循环guard（此前仅非流式路径接了guard）
            _lw = _check_loop("".join(answer_parts), session_key=str(user_id))
            if _lw:
                yield f"data: {json.dumps({'type': 'loop_warning', 'content': _lw})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Build context
    context_parts = []
    sources = []
    for i, r in enumerate(results):
        context_parts.append(f"[{i + 1}] {r.get('chunk', '')}")
        sources.append({"id": r.get("id"), "score": r.get("score")})

    # P0-1: 压缩过长上下文
    context = await _compress_if_needed("\n\n".join(context_parts))

    # P0-6: 注入hippo三因子记忆上下文
    memory_ctx = _get_memory_context(question)

    # Send sources first
    yield f"data: {json.dumps({'type': 'sources', 'content': sources})}\n\n"

    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.
{memory_ctx}
Context:
{context}

Question: {question}

Answer:"""

    # P0-4: 出站脱敏
    prompt = _redact_outbound(prompt)

    # 模型路由接线：按mode选LLM target，主target失败自动切备target
    attempts, routing_meta = _route_llm_targets(question)
    last_exc: Exception | None = None
    streamed_ok = False
    for t in attempts:
        if t.get("provider") == "online" and not t.get("api_key"):
            continue
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{t['base_url']}/chat/completions",
                    headers=_llm_headers(t.get("api_key", "")),
                    json={
                        "model": t["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "stream": True,
                    },
                    timeout=120,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                answer_parts.append(content)
                                yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                        except json.JSONDecodeError:
                            continue
            streamed_ok = True
            break
        except Exception as exc:
            last_exc = exc
            continue

    if not streamed_ok:
        yield f"data: {json.dumps({'type': 'error', 'content': f'LLM也不可用: {last_exc}'})}\n\n"
    else:
        # P0 cortex: 流式路径循环guard（此前仅非流式路径接了guard）
        loop_warning = _check_loop("".join(answer_parts), session_key=str(user_id))
        if loop_warning:
            yield f"data: {json.dumps({'type': 'loop_warning', 'content': loop_warning})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/")
async def chat(req: ChatRequest, user_id: UUID):
    """Chat with RAG. Supports SSE streaming."""
    if req.stream:
        return StreamingResponse(
            rag_stream(req.question, user_id, req.top_k),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming fallback
    try:
        results = await asyncio.wait_for(
            semantic_search(req.question, user_id, limit=req.top_k), timeout=8
        )
    except (asyncio.TimeoutError, Exception) as _rag_exc:
        logger.warning("RAG semantic_search unavailable (non-stream), degrading: %s", _rag_exc)
        results = []
    if not results:
        return {"answer": "No relevant knowledge found.", "sources": []}

    context_parts = []
    sources = []
    for i, r in enumerate(results):
        context_parts.append(f"[{i + 1}] {r.get('chunk', '')}")
        sources.append({"id": r.get("id"), "score": r.get("score")})

    # P0-1: 压缩过长上下文
    context = await _compress_if_needed("\n\n".join(context_parts))

    # P0-6: 注入hippo三因子记忆上下文
    memory_ctx = _get_memory_context(req.question)

    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.
{memory_ctx}
Context:
{context}

Question: {req.question}

Answer:"""

    # P0-4: 出站脱敏
    prompt = _redact_outbound(prompt)

    # 模型路由接线：按mode选LLM target，失败自动切备target
    attempts, routing_meta = _route_llm_targets(req.question)
    answer = None
    last_exc: Exception | None = None
    used_target = None
    for t in attempts:
        if t.get("provider") == "online" and not t.get("api_key"):
            continue
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{t['base_url']}/chat/completions",
                    headers=_llm_headers(t.get("api_key", "")),
                    json={
                        "model": t["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                answer = resp.json()["choices"][0]["message"]["content"]
                used_target = {"provider": t.get("provider"), "model": t.get("model")}
                break
        except Exception as exc:
            last_exc = exc
            continue
    if answer is None:
        return {"answer": f"LLM不可用: {last_exc}", "sources": sources,
                "routing": {**routing_meta, "error": str(last_exc)}}

    # 循环检测guard
    loop_warning = _check_loop(answer, session_key=str(user_id))
    result = {"answer": answer, "sources": sources,
              "routing": {**routing_meta, "used": used_target}}
    if loop_warning:
        result["warning"] = loop_warning
    return result
