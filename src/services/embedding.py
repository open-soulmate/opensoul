import httpx

from src.config import settings


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for a single text."""
    api_key = settings.embedding_api_key or settings.llm_api_key
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.embedding_base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": text, "model": settings.embedding_model},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embedding vectors for multiple texts."""
    api_key = settings.embedding_api_key or settings.llm_api_key
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.embedding_base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": texts, "model": settings.embedding_model},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
