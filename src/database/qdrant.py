from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

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

    def upsert_points(self, points: list[PointStruct]):
        self.client.upsert(
            collection_name=settings.qdrant_collection,
            points=points,
        )

    def search(
        self,
        query_vector: list[float],
        limit: int = 10,
        user_id: UUID | None = None,
        knowledge_id: UUID | None = None,
        score_threshold: float | None = None,
    ) -> list:
        query_filter = None
        conditions = []

        if user_id:
            conditions.append(
                FieldCondition(key="user_id", match=MatchValue(value=str(user_id)))
            )
        if knowledge_id:
            conditions.append(
                FieldCondition(key="knowledge_id", match=MatchValue(value=str(knowledge_id)))
            )

        if conditions:
            query_filter = Filter(must=conditions)

        return self.client.query_points(
            collection_name=settings.qdrant_collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
        )

    def delete_points(self, ids: list[str]):
        self.client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=ids,
        )

    def delete_by_knowledge_id(self, knowledge_id: UUID):
        """Delete all points belonging to a knowledge item."""
        self.client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="knowledge_id",
                        match=MatchValue(value=str(knowledge_id)),
                    )
                ]
            ),
        )


qdrant_client = QdrantStore()
