"""Tests for Permission — RBAC policy management."""


class TestPermissionHealth:
    def test_health(self, client):
        resp = client.get("/api/permission/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestPermissionCheck:
    def test_check(self, client):
        resp = client.get("/api/permission/check")
        assert resp.status_code in (200, 400, 401, 403, 422)


class TestPermissionRoles:
    def test_get_roles_nonexistent_user(self, client):
        resp = client.get("/api/permission/roles/nonexistent_user_xyz")
        assert resp.status_code in (200, 401, 404)

    def test_create_role_validation(self, client):
        resp = client.post("/api/permission/role", json={})
        assert resp.status_code in (400, 401, 403, 422)


class TestPermissionPolicies:
    def test_list_policies(self, client):
        resp = client.get("/api/permission/policies")
        assert resp.status_code in (200, 401, 403)
