"""Integration tests for Config API — configuration management."""


class TestConfigHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["component"] == "OpenSoul"

    def test_config_health_alias(self, client):
        """The /config/health alias should also work."""
        resp = client.get("/api/config/health")
        assert resp.status_code == 200


class TestConfigEndpoints:
    def test_get_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert isinstance(resp.json(), dict)

    def test_get_config_section(self, client):
        resp = client.get("/api/config/daemon")
        assert resp.status_code in (200, 404, 401)

    def test_list_organs(self, client):
        resp = client.get("/api/organs")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (dict, list))
