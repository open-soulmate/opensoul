"""Integration tests for Entity API — CRUD and stats."""


class TestEntityHealth:
    def test_health(self, client):
        resp = client.get("/api/entity/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestEntityStats:
    def test_stats(self, client):
        resp = client.get("/api/entity/stats")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)


class TestEntityCRUD:
    def test_list_entities(self, client):
        resp = client.get("/api/entity/")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list)

    def test_get_entity_not_found(self, client):
        resp = client.get("/api/entity/nonexistent-id")
        assert resp.status_code in (404, 401, 422)
