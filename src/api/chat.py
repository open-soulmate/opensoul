import json
from uuid import UUID

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.config import settings
from src.services.search import semantic_search

router = APIRouter()


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
        context_parts.append(f"[{i+1}] {r.get('chunk', '')}")
        sources.append({"id": r.get("id"), "score": r.get("score")})

    context = "\n\n".join(context_parts)

    # Send sources first
    yield f"data: {json.dumps({'type': 'sources', 'content': sources})}\n\n"

    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.

Context:
{context}

Question: {question}

Answer:"""

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
            headers={"Authorization": f"Bearer {api_key}"},
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
        context_parts.append(f"[{i+1}] {r.get('chunk', '')}")
        sources.append({"id": r.get("id"), "score": r.get("score")})

    context = "\n\n".join(context_parts)
    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.

Context:
{context}

Question: {req.question}

Answer:"""

    api_key = settings.llm_api_key
    if not api_key:
        return {"answer": "LLM API key not configured.", "sources": sources}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=120,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"]

    return {"answer": answer, "sources": sources}
