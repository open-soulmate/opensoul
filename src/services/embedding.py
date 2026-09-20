import asyncio
import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 256
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
# 单次尝试超时：embedding请求不允许长时间挂起（此前60s×3次重试≈3分钟挂死调用方）
EMBEDDING_ATTEMPT_TIMEOUT = 15.0
# 整批调用的总墙钟预算：超时即降级返回空向量+可见日志，调用方永不挂死
EMBEDDING_TOTAL_BUDGET = 12.0


def _get_api_key() -> str:
    return settings.embedding_api_key or settings.llm_api_key


async def _call_embedding_api(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
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
            resp = await client.post(
                url, headers=headers, json=payload, timeout=EMBEDDING_ATTEMPT_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Embedding API error (attempt %d/%d): %s, retrying in %.1fs",
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    raise RuntimeError(f"Embedding API failed after {MAX_RETRIES} retries: {last_exc}")


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for a single text (bounded by EMBEDDING_TOTAL_BUDGET)."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.wait_for(
            _call_embedding_api(client, [text]), timeout=EMBEDDING_TOTAL_BUDGET
        )
        return results[0]


async def _embed_all(texts: list[str]) -> list[list[float]]:
    """Call embedding API for all batches. Raises on failure (caller handles)."""
    all_embeddings: list[list[float]] = []
    async with httpx.AsyncClient() as client:
        for i in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[i : i + MAX_BATCH_SIZE]
            batch_embeddings = await _call_embedding_api(client, batch)
            all_embeddings.extend(batch_embeddings)
    return all_embeddings


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embedding vectors for multiple texts.

    失败契约（mem0 §1.1"失败可见，禁止静默降级"）：
    - 永不raise、永不挂死——超时/异常一律降级为空向量；
    - 降级必须写warning日志（可见），调用方（knowledge入库/搜索）继续工作，
      向量缺失由调用方跳过qdrant写入，meilisearch关键词索引兜底。
    """
    if not texts or not _get_api_key():
        return [[] for _ in texts]
    try:
        return await asyncio.wait_for(_embed_all(texts), timeout=EMBEDDING_TOTAL_BUDGET)
    except TimeoutError:
        logger.warning(
            "Embedding batch timed out after %.0fs (%d texts) — degraded to empty vectors, "
            "vector index skipped, keyword index unaffected",
            EMBEDDING_TOTAL_BUDGET,
            len(texts),
        )
        return [[] for _ in texts]
    except Exception as exc:
        logger.warning(
            "Embedding batch failed (%d texts): %s — degraded to empty vectors",
            len(texts),
            exc,
        )
        return [[] for _ in texts]
