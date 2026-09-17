import json
import logging
from uuid import UUID

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.config import settings
from src.services.search import semantic_search

logger = logging.getLogger(__name__)

router = APIRouter()


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
    ContextCompressor.compress()签名是messages列表，对RAG文本场景用单条user message包装。
    失败时截断降级。"""
    if len(context) <= budget_chars:
        return context
    try:
        from src.cortex.context_compression import ContextCompressor
        compressor = ContextCompressor()
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


def _check_loop(response_text: str) -> dict | None:
    """P0-7附属: 循环/重复检测guard。检测到循环返回警告信息，否则None。"""
    try:
        from src.cortex.loop_guard import LoopGuard
        guard = LoopGuard()
        result = guard.check(text_response=response_text)
        if result and result.is_looping:
            return {"type": "loop_warning", "severity": str(result.severity)}
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
    # Retrieve relevant chunks
    results = await semantic_search(question, user_id, limit=top_k)

    if not results:
        yield f"data: {json.dumps({'type': 'error', 'content': 'No relevant knowledge found.'})}\n\n"
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

    api_key = settings.llm_api_key
    if not api_key:
        yield f"data: {json.dumps({'type': 'error', 'content': 'LLM API key not configured.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Stream from LLM
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{settings.llm_base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                **({"api-key": api_key} if api_key.startswith("tp-") else {"Authorization": f"Bearer {api_key}"}),
            },
            json={
                "model": settings.llm_model,
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
                        yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                except json.JSONDecodeError:
                    continue

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
    results = await semantic_search(req.question, user_id, limit=req.top_k)
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

    api_key = settings.llm_api_key
    if not api_key:
        return {"answer": "LLM API key not configured.", "sources": sources}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                **({"api-key": api_key} if api_key.startswith("tp-") else {"Authorization": f"Bearer {api_key}"}),
            },
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=120,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"]

    # 循环检测guard
    loop_warning = _check_loop(answer)
    if loop_warning:
        return {"answer": answer, "sources": sources, "warning": loop_warning}

    return {"answer": answer, "sources": sources}
