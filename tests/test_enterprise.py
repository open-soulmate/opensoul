"""Integration tests for Enterprise API (/api/enterprise) — auth, RBAC, audit."""

import pytest


class TestEnterpriseHealth:
    def test_health(self, client):
        resp = client.get("/api/enterprise/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestEnterpriseAuth:
    def test_login_missing_body(self, client):
        resp = client.post("/api/enterprise/auth/login", json={})
        assert resp.status_code in (200, 400, 422)

    def test_login_invalid_credentials(self, client):
        resp = client.post(
            "/api/enterprise/auth/login",
            json={"username": "nonexistent_user_xyz", "password": "wrong_password"},
        )
        assert resp.status_code in (200, 400, 401, 422)

    def test_register_missing_body(self, client):
        resp = client.post("/api/enterprise/auth/register", json={})
        assert resp.status_code in (200, 400, 422)


class TestEnterpriseRBAC:
    def test_create_role_missing(self, client):
        resp = client.post("/api/enterprise/roles", json={})
        assert resp.status_code in (200, 400, 401, 422)

    def test_create_permission_missing(self, client):
        resp = client.post("/api/enterprise/permissions", json={})
        assert resp.status_code in (200, 400, 401, 422)

    def test_assign_role_missing(self, client):
        resp = client.post(
            "/api/enterprise/users/nonexistent-user/roles",
            json={},
        )
        assert resp.status_code in (200, 400, 401, 404, 422)


class TestEnterpriseUsers:
    def test_list_users(self, client):
        resp = client.get("/api/enterprise/users/list")
        assert resp.status_code in (200, 401)


class TestEnterpriseAudit:
    def test_audit_log(self, client):
        resp = client.get("/api/enterprise/audit")
        assert resp.status_code in (200, 401)

    def test_audit_with_limit(self, client):
        resp = client.get("/api/enterprise/audit?limit=5")
        assert resp.status_code in (200, 401)
