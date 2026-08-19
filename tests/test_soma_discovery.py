"""Tests for Soma Discovery API — system scanning and adapter management."""


class TestDiscoveryHealth:
    def test_discovery_health(self, client):
        resp = client.get("/api/soma/discovery/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestScan:
    def test_scan(self, client):
        resp = client.get("/api/soma/discovery/scan")
        assert resp.status_code == 200
        data = resp.json()
        # Scan should return some structure
        assert isinstance(data, dict)


class TestProcesses:
    def test_list_processes(self, client):
        resp = client.get("/api/soma/discovery/processes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestCLITools:
    def test_list_cli_tools(self, client):
        resp = client.get("/api/soma/discovery/cli-tools")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestServices:
    def test_list_services(self, client):
        resp = client.get("/api/soma/discovery/services")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestAdapters:
    def test_list_adapters(self, client):
        resp = client.get("/api/soma/discovery/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
