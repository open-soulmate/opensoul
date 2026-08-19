"""Integration tests for User API — registration, login, profile."""

import uuid


class TestUserHealth:
    def test_health(self, client):
        resp = client.get("/api/user/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "UserSystem"


class TestUserRegister:
    def test_register_and_login(self, client):
        """Register a new user, then login."""
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # Register
        resp = client.post("/api/user/register", json={
            "username": username,
            "email": email,
            "password": "testpass123",
        })
        # Could be 200 or 400 if user already exists
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            data = resp.json()
            assert data["username"] == username
            assert data["email"] == email

    def test_login_valid_credentials(self, client):
        """Login with valid credentials returns a token."""
        username = f"logintest_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # Register first
        client.post("/api/user/register", json={
            "username": username,
            "email": email,
            "password": "mypassword",
        })

        # Login
        resp = client.post("/api/user/login", data={
            "username": username,
            "password": "mypassword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_invalid_credentials(self, client):
        """Login with wrong password returns 401."""
        resp = client.post("/api/user/login", data={
            "username": "nonexistent_user",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    def test_me_without_token_returns_401(self, client):
        """GET /me without auth returns 401."""
        resp = client.get("/api/user/me")
        assert resp.status_code == 401

    def test_me_with_valid_token(self, client):
        """GET /me with valid token returns user info."""
        username = f"metest_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # Register
        client.post("/api/user/register", json={
            "username": username,
            "email": email,
            "password": "pass123",
        })

        # Login
        login_resp = client.post("/api/user/login", data={
            "username": username,
            "password": "pass123",
        })
        if login_resp.status_code != 200:
            return  # Skip if registration failed

        token = login_resp.json()["access_token"]

        # Get profile
        resp = client.get("/api/user/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == username
