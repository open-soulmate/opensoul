from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src.config import settings


class QdrantStore:
    def __init__(self):
        self._client: QdrantClient | None = None

    @property
    def client(self) -> QdrantClient:
        if not self._client:
            self._client = QdrantClient(url=settings.qdrant_url)
        return self._client

    def ensure_collection(self):
        collections = [c.name for c in self.client.get_collections().collections]
        if settings.qdrant_collection not in collections:
            self.client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=settings.embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )

    def upsert_points(self, points: list):
        self.client.upsert(
            collection_name=settings.qdrant_collection,
            points=points,
        )

    def search(self, query_vector: list[float], limit: int = 10):
        return self.client.search(
            collection_name=settings.qdrant_collection,
            query_vector=query_vector,
            limit=limit,
        )

    def delete_points(self, ids: list[str]):
        self.client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=ids,
        )


qdrant_client = QdrantStore()
