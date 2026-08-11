import logging

import meilisearch
from meilisearch.errors import MeilisearchApiError

from src.config import settings

logger = logging.getLogger(__name__)


class MeiliStore:
    def __init__(self):
        self._client: meilisearch.Client | None = None

    @property
    def client(self) -> meilisearch.Client:
        if not self._client:
            self._client = meilisearch.Client(settings.meili_url, settings.meili_key)
        return self._client

    def ensure_index(self):
        try:
            self.client.get_index(settings.meili_index)
        except MeilisearchApiError:
            self.client.create_index(settings.meili_index, primary_key="id")
            index = self.client.index(settings.meili_index)
            index.update_searchable_attributes(["title", "content", "tags"])
            index.update_filterable_attributes(["tags", "user_id", "content_type", "created_at"])
            index.update_sortable_attributes(["created_at"])

    def add_documents(self, documents: list[dict]):
        if not documents:
            return None
        return self.client.index(settings.meili_index).add_documents(documents)

    def update_documents(self, documents: list[dict]):
        if not documents:
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
        params: dict = {"limit": limit, "offset": offset}
        if filters:
            params["filter"] = filters
        if sort:
            params["sort"] = sort
        return self.client.index(settings.meili_index).search(query, params)

    def get_document(self, doc_id: str) -> dict:
        return self.client.index(settings.meili_index).get_document(doc_id)

    def delete_document(self, doc_id: str):
        return self.client.index(settings.meili_index).delete_document(doc_id)

    def delete_all_documents(self):
        return self.client.index(settings.meili_index).delete_all_documents()

    def get_stats(self) -> dict:
        return self.client.index(settings.meili_index).get_stats()


meili_client = MeiliStore()
