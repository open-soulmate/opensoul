"""Memory intake redaction — 采集前脱敏（kilocode MemoryRedact 移植）.

来源：kilocode-source-supplement3.md #6「采集前脱敏（MemoryRedact）：进入记忆的文本
先过redact，命中→"[redacted]"」——OpenSoul现状"immune/moderator有PII但不接记忆"，
凭据（API key/令牌/JWT）会原样落进 hippo 长期记忆 SQLite 明文库。

设计：
- 复用 immune.moderator.ContentModerator（Warp secret_redaction 20正则已在
  gland/router.py 出站路径验证过），本模块只做"写记忆前"的统一咽喉封装。
- 默认阈值 MEMORY_REDACT_MIN_RISK="high"：凭据类（critical）+ 高危PII
  （身份证/银行卡/JWT/带凭据URL）进记忆前必掩码；email/电话/IP等低危PII保留
  ——"记住我的邮箱"是合法记忆，掩码会毁掉记忆可用性。可调到"low"全掩码。
- findings 只回传 type/risk/label（绝不回传命中原文），日志/metadata同样只留
  类型摘要——审计可见但不二次泄漏（对齐 redact_messages 的安全日志约定）。
- fail-safe 降级必须可见（mem0 §1.1）：脱敏器初始化/执行失败时放行原文并打
  WARNING（阻断记忆写入比漏脱敏更伤），绝不静默。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("opensoul.hippo.memory_redact")

_lock = threading.Lock()
_redactor = None
_init_failed = False

# Minimum risk level redacted before text enters memory storage.
# "high" = credentials + high-risk PII (id card / bank card / JWT / URL with
# embedded credentials); "critical" = credentials only; "low" = everything
# including email/phone/IP. Aligns with gland.router.OUTBOUND_REDACT_MIN_RISK.
MEMORY_REDACT_MIN_RISK = "high"

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _get_redactor():
    """Lazy singleton ContentModerator (same fail-safe pattern as
    gland.router._outbound_redactor: import/compile failure disables redaction
    with a visible warning, never blocks the memory write path)."""
    global _redactor, _init_failed
    if _redactor is not None:
        return _redactor
    with _lock:
        if _redactor is not None:
            return _redactor
        if _init_failed:
            return None
        try:
            from src.immune.moderator import ContentModerator

            _redactor = ContentModerator()
            return _redactor
        except Exception as exc:  # pragma: no cover - defensive
            _init_failed = True
            logger.warning("MemoryRedact disabled (init failed, storing raw text): %s", exc)
            return None


def redact_for_memory(
    text: str | None, min_risk: str = MEMORY_REDACT_MIN_RISK
) -> tuple[str, list[dict]]:
    """Redact sensitive spans before text enters any memory store.

    Returns (redacted_text, findings_summary) where findings_summary is a list
    of {"type", "risk", "label"} — never the matched secret itself (safe for
    logs / metadata / audit rows). Clean text is returned unchanged with an
    empty summary.
    """
    if not text or not isinstance(text, str):
        return "", []
    try:
        mod = _get_redactor()
        if mod is None:
            return text, []
        result = mod.moderate(text)
        threshold = _RISK_ORDER.get(min_risk, _RISK_ORDER["high"])
        actionable = [f for f in result.findings if _RISK_ORDER.get(f["risk"], 0) >= threshold]
        if not actionable:
            return text, []
        # Overlap-merged masking (Warp merge_sorted_ranges semantics) over the
        # threshold-filtered findings only — low-risk spans survive when the
        # threshold is "high".
        redacted = mod._redact(text, actionable)
        summary = [{"type": f["type"], "risk": f["risk"], "label": f["label"]} for f in actionable]
        return redacted, summary
    except Exception as exc:  # pragma: no cover - defensive
        # Fail-safe but visible (mem0 §1.1: silent degradation = poisoned memory).
        logger.warning("MemoryRedact failed (storing raw text): %s", exc)
        return text, []


def redact_message_bodies(
    messages: list | None, min_risk: str = MEMORY_REDACT_MIN_RISK
) -> tuple[list, int]:
    """Redact each conversation message body BEFORE it enters a memory LLM prompt.

    kilocode MemoryRedact 采集前脱敏收口（supplement3 #6）：kilocode ports.ts text()
    对每条正文过 MemoryRedact.text——脱敏发生在**进入记忆处理的源端**（Phase1提取/
    Dream蒸馏的prompt拼装之前），凭据绝不抵达记忆蒸馏LLM（MEMORY_MODEL可能是
    第三方小模型）。本此前只在store()落库前脱敏——库是干净的，但蒸馏prompt里
    凭据原文仍在出境。

    行为语义：
    - 非破坏性：返回新列表，输入不被就地修改；仅content为str的条目参与脱敏，
      其余形态（OpenAI parts列表等）原样保留（这些路径的载荷契约是str）。
    - 先脱敏后截断：调用方的token预算截断（_format_messages）发生在脱敏之后，
      避免把密钥拦腰截断成不再匹配规则的半个密钥漏出。
    - 计数返回命中span数（0=无命中）——调用方必须在结果里显式透出（mem0 §1.1
      降级/处理必须可见）。
    - fail-safe绝不抛出（与redact_for_memory同源：采集端异常不反噬宿主会话流）。
    """
    if not messages:
        return [], 0
    out: list = []
    total = 0
    try:
        for msg in messages:
            if not isinstance(msg, dict):
                out.append(msg)
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                out.append(msg)
                continue
            redacted, findings = redact_for_memory(content, min_risk=min_risk)
            if not findings:
                out.append(msg)
                continue
            new_msg = dict(msg)
            new_msg["content"] = redacted
            out.append(new_msg)
            total += len(findings)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("redact_message_bodies failed (passing raw messages): %s", exc)
        return list(messages or []), total
    return out, total
