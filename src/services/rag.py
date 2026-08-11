from uuid import UUID

import httpx

from src.config import settings
from src.services.search import semantic_search


async def rag_query(question: str, user_id: UUID, top_k: int = 5) -> dict:
    """Answer a question using RAG (Retrieval-Augmented Generation)."""
    # Retrieve relevant chunks
    results = await semantic_search(question, user_id, limit=top_k)
    if not results:
        return {"answer": "No relevant knowledge found.", "sources": []}

    # Build context from chunks
    context_parts = []
    sources = []
    for i, r in enumerate(results):
        context_parts.append(f"[{i+1}] {r.get('chunk', '')}")
        sources.append({"id": r.get("id"), "score": r.get("score")})

    context = "\n\n".join(context_parts)

    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.

Context:
{context}

Question: {question}

Answer:"""

    # Call LLM
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
            timeout=60,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"]

    return {"answer": answer, "sources": sources}
