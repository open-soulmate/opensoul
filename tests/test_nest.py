"""Integration tests for OpenNest (巢穴) — multi-tenant isolation."""


class TestNestHealth:
    def test_health(self, client):
        resp = client.get("/api/nest/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenNest"


class TestNestTenants:
    def test_create_list_delete_tenant(self, client):
        resp = client.post(
            "/api/nest/tenants",
            json={
                "name": "test_tenant",
                "tier": "free",
                "config": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        tid = data["tenant_id"]

        resp = client.get("/api/nest/tenants")
        assert resp.status_code == 200

        resp = client.get(f"/api/nest/tenants/{tid}")
        assert resp.status_code == 200

        # Update
        resp = client.patch(
            f"/api/nest/tenants/{tid}",
            json={
                "tier": "pro",
            },
        )
        assert resp.status_code == 200

        # Quota
        resp = client.get(f"/api/nest/tenants/{tid}/quota")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/nest/tenants/{tid}")
        assert resp.status_code == 200


class TestNestPolicies:
    def test_list_policies(self, client):
        resp = client.get("/api/nest/policies")
        assert resp.status_code == 200


class TestNestAudit:
    def test_audit(self, client):
        resp = client.get("/api/nest/audit")
        assert resp.status_code == 200
