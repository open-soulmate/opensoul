import asyncio
import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 256
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


def _get_api_key() -> str:
    return settings.embedding_api_key or settings.llm_api_key


async def _call_embedding_api(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float]]:
    """Call the embedding API with retry logic."""
    api_key = _get_api_key()
    url = f"{settings.embedding_base_url}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "input": texts,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Embedding API error (attempt %d/%d): %s, retrying in %.1fs",
                               attempt + 1, MAX_RETRIES, exc, delay)
                await asyncio.sleep(delay)

    raise RuntimeError(f"Embedding API failed after {MAX_RETRIES} retries: {last_exc}")


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for a single text."""
    async with httpx.AsyncClient() as client:
        results = await _call_embedding_api(client, [text])
        return results[0]


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embedding vectors for multiple texts. Returns empty if no API key."""
    if not texts or not _get_api_key():
        return [[] for _ in texts]
    try:
        all_embeddings: list[list[float]] = []
        async with httpx.AsyncClient() as client:
            for i in range(0, len(texts), MAX_BATCH_SIZE):
                batch = texts[i : i + MAX_BATCH_SIZE]
                batch_embeddings = await _call_embedding_api(client, batch)
                all_embeddings.extend(batch_embeddings)
        return all_embeddings
    except Exception:
        return [[] for _ in texts]
