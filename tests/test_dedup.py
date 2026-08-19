"""Integration tests for Dedup API — deduplication endpoints."""


class TestDedupHealth:
    def test_health(self, client):
        resp = client.get("/api/dedup/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "Dedup"


class TestDedupEndpoints:
    def test_list_duplicates(self, client):
        resp = client.get("/api/dedup/duplicates")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert "total_pairs" in data
            assert "duplicates" in data
            assert isinstance(data["duplicates"], list)

    def test_run_deduplication(self, client):
        resp = client.post("/api/dedup/deduplicate")
        assert resp.status_code in (200, 401)
