"""Integration tests for OpenSearch — unified search across all components."""


class TestSearchHealth:
    def test_health(self, client):
        resp = client.get("/api/search/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_stats(self, client):
        resp = client.get("/api/search/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "searchable_entries" in data
        assert "modes" in data
        assert isinstance(data["modes"], list)


class TestSearchGet:
    def test_search_get_requires_query(self, client):
        """GET /api/search/ without q parameter should return 422."""
        resp = client.get("/api/search/")
        assert resp.status_code == 422

    def test_search_get_fulltext_mode(self, client):
        """GET with mode=fulltext should return fulltext results."""
        resp = client.get("/api/search/", params={"q": "test", "mode": "fulltext"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "fulltext"
        assert isinstance(data["results"], list)

    def test_search_get_with_user_id(self, client):
        """GET with user_id should work."""
        resp = client.get(
            "/api/search/",
            params={"q": "test", "mode": "fulltext", "user_id": "default"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data


class TestSearchPost:
    def test_search_post_fulltext(self, client):
        """POST with mode=fulltext should work."""
        resp = client.post("/api/search/", json={"query": "test", "mode": "fulltext"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test"
        assert data["mode"] == "fulltext"
        assert isinstance(data["results"], list)

    def test_search_post_with_limit(self, client):
        """POST with custom limit should be respected."""
        resp = client.post(
            "/api/search/", json={"query": "test", "mode": "fulltext", "limit": 3}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) <= 3


class TestUnifiedSearch:
    def test_unified_search_specific_source(self, client):
        """GET with sources=events should only search events (fast, no DB)."""
        resp = client.get(
            "/api/search/unified",
            params={"q": "test", "sources": "events"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "query" in data
        assert "total" in data
        assert "by_source" in data
        assert "sources_searched" in data
        assert data["query"] == "test"
        assert data["sources_searched"] == ["events"]
        assert "events" in data["by_source"]
        assert isinstance(data["by_source"]["events"], list)

    def test_unified_search_returns_source_icons(self, client):
        """Each result should have source and icon fields."""
        resp = client.get(
            "/api/search/unified",
            params={"q": "test", "sources": "events"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for result in data["by_source"].get("events", []):
            assert "source" in result
            assert "icon" in result

    def test_unified_search_with_limit(self, client):
        """GET with limit should be passed to each source."""
        resp = client.get(
            "/api/search/unified", params={"q": "test", "sources": "events", "limit": 3}
        )
        assert resp.status_code == 200
        data = resp.json()
        for source, results in data["by_source"].items():
            assert len(results) <= 3

    def test_unified_search_multiple_fast_sources(self, client):
        """Search events and agents (both fast, no embedding needed)."""
        resp = client.get(
            "/api/search/unified",
            params={"q": "test", "sources": "events,agents"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data["sources_searched"]
        assert "agents" in data["sources_searched"]
