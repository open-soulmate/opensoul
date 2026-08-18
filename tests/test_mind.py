"""Integration tests for OpenMind (心智) — emotion analysis, personality."""


class TestMindHealth:
    def test_health(self, client):
        resp = client.get("/api/mind/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenMind"


class TestMindEmotion:
    def test_analyze_emotion(self, client):
        resp = client.post(
            "/api/mind/emotion/analyze",
            json={
                "text": "I am so happy today! This is amazing!",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "primary_emotion" in data

    def test_emotion_keywords(self, client):
        resp = client.get("/api/mind/emotion/keywords")
        assert resp.status_code == 200


class TestMindPersonality:
    def test_list_personalities(self, client):
        resp = client.get("/api/mind/personalities")
        assert resp.status_code == 200

    def test_active_personality(self, client):
        resp = client.get("/api/mind/personalities/active")
        assert resp.status_code == 200
