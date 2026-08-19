"""Integration tests for Graph API — entities, relations, graph data."""


class TestGraphHealth:
    def test_health(self, client):
        resp = client.get("/api/graph/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestGraphStats:
    def test_stats(self, client):
        resp = client.get("/api/graph/stats")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)


class TestGraphEntities:
    def test_list_entities(self, client):
        resp = client.get("/api/graph/entities")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list)


class TestGraphRelations:
    def test_list_relations(self, client):
        resp = client.get("/api/graph/relations")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list)


class TestGraphFull:
    def test_full_graph(self, client):
        resp = client.get("/api/graph/full")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert "nodes" in data or "entities" in data

    def test_graph_root(self, client):
        resp = client.get("/api/graph/")
        assert resp.status_code in (200, 401)
