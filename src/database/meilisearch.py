import logging

from src.config import settings

logger = logging.getLogger(__name__)

try:
    import meilisearch
    from meilisearch.errors import MeilisearchApiError
except ImportError:
    meilisearch = None
    MeilisearchApiError = None
    logger.warning("meilisearch not installed — full-text search disabled")


class MeiliStore:
    AVAILABLE = meilisearch is not None

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if not self.AVAILABLE:
            return None
        if not self._client:
            self._client = meilisearch.Client(settings.meili_url, settings.meili_key)
        return self._client

    def ensure_index(self):
        if not self.AVAILABLE:
            logger.debug("Meilisearch unavailable, skipping ensure_index")
            return
        try:
            self.client.get_index(settings.meili_index)
        except MeilisearchApiError:
            self.client.create_index(settings.meili_index, primary_key="id")
            index = self.client.index(settings.meili_index)
            index.update_searchable_attributes(["title", "content", "tags"])
            index.update_filterable_attributes(["tags", "user_id", "content_type", "created_at"])
            index.update_sortable_attributes(["created_at"])

    def add_documents(self, documents: list[dict]):
        if not self.AVAILABLE or not documents:
            return None
        return self.client.index(settings.meili_index).add_documents(documents)

    def update_documents(self, documents: list[dict]):
        if not self.AVAILABLE or not documents:
            return None
        return self.client.index(settings.meili_index).update_documents(documents)

    def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: str | None = None,
        sort: list[str] | None = None,
    ) -> dict:
        if not self.AVAILABLE:
            return {}
        params: dict = {"limit": limit, "offset": offset}
        if filters:
            params["filter"] = filters
        if sort:
            params["sort"] = sort
        return self.client.index(settings.meili_index).search(query, params)

    def get_document(self, doc_id: str) -> dict:
        if not self.AVAILABLE:
            return {}
        return self.client.index(settings.meili_index).get_document(doc_id)

    def delete_document(self, doc_id: str):
        if not self.AVAILABLE:
            return None
        return self.client.index(settings.meili_index).delete_document(doc_id)

    def delete_all_documents(self):
        if not self.AVAILABLE:
            return None
        return self.client.index(settings.meili_index).delete_all_documents()

    def get_stats(self) -> dict:
        if not self.AVAILABLE:
            return {}
        return self.client.index(settings.meili_index).get_stats()


meili_client = MeiliStore()
