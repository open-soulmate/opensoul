import meilisearch

from src.config import settings


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
        except meilisearch.errors.MeilisearchApiError:
            self.client.create_index(settings.meili_index, primary_key="id")
            index = self.client.index(settings.meili_index)
            index.update_searchable_attributes(["title", "content", "tags"])
            index.update_filterable_attributes(["tags", "user_id", "created_at"])

    def add_documents(self, documents: list[dict]):
        self.client.index(settings.meili_index).add_documents(documents)

    def search(self, query: str, limit: int = 10, filters: str | None = None):
        return self.client.index(settings.meili_index).search(
            query,
            {"limit": limit, "filter": filters} if filters else {"limit": limit},
        )

    def delete_document(self, doc_id: str):
        self.client.index(settings.meili_index).delete_document(doc_id)


meili_client = MeiliStore()
