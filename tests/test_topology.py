"""Tests for OpenTopology — system architecture visualization."""


class TestTopologyHealth:
    def test_health(self, client):
        resp = client.get("/api/topology/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestTopologyStats:
    def test_stats(self, client):
        resp = client.get("/api/topology/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestTopologyGraph:
    def test_graph(self, client):
        resp = client.get("/api/topology/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestTopologyClusters:
    def test_clusters(self, client):
        resp = client.get("/api/topology/clusters")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_nonexistent_dependencies(self, client):
        resp = client.get("/api/topology/dependencies/nonexistent_component")
        assert resp.status_code in (200, 404)
