"""Tests for OpenRegistry — component registry and dependency graph."""


class TestRegistryHealth:
    def test_health(self, client):
        resp = client.get("/api/registry/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestRegistryStats:
    def test_stats(self, client):
        resp = client.get("/api/registry/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestRegistryComponents:
    def test_list_components(self, client):
        resp = client.get("/api/registry/components")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_get_nonexistent_component(self, client):
        resp = client.get("/api/registry/components/nonexistent_xyz")
        assert resp.status_code in (200, 404)

    def test_component_dependencies(self, client):
        resp = client.get("/api/registry/components/nonexistent_xyz/dependencies")
        assert resp.status_code in (200, 404)


class TestRegistryGraph:
    def test_graph(self, client):
        resp = client.get("/api/registry/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestRegistryCapabilities:
    def test_capabilities(self, client):
        resp = client.get("/api/registry/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestRegistrySearch:
    def test_search_empty(self, client):
        resp = client.get("/api/registry/search", params={"q": ""})
        assert resp.status_code in (200, 400, 422)

    def test_search_query(self, client):
        resp = client.get("/api/registry/search", params={"q": "test"})
        assert resp.status_code == 200
