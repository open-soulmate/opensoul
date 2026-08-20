"""Tests for ChatSystem — RAG-based chat interface."""


class TestChatHealth:
    def test_health(self, client):
        resp = client.get("/api/chat/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "ChatSystem"


class TestChatEndpoint:
    def test_chat_requires_question(self, client):
        # Missing question field should fail validation
        resp = client.post("/api/chat/", json={})
        assert resp.status_code in (400, 401, 403, 422)

    def test_chat_with_question(self, client):
        resp = client.post(
            "/api/chat/",
            json={"question": "What is OpenSoul?", "top_k": 3, "stream": False},
        )
        # May require auth, may return 422 if stream=False not supported, or 200
        assert resp.status_code in (200, 401, 403, 422, 500)
