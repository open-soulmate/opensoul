"""Integration tests for OpenVoice (声带) — TTS engine."""


class TestVoiceHealth:
    def test_health(self, client):
        resp = client.get("/api/voice/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenVoice"
        assert "backends" in data


class TestVoiceSynthesis:
    def test_synthesize_json(self, client):
        resp = client.post(
            "/api/voice/synthesize/json",
            json={
                "text": "Hello, this is a test.",
                "voice_id": "zh-CN-XiaoxiaoNeural",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "engine" in data
        assert "format" in data


class TestVoiceProfiles:
    def test_list_profiles(self, client):
        resp = client.get("/api/voice/profiles")
        assert resp.status_code == 200

    def test_list_voices(self, client):
        resp = client.get("/api/voice/voices")
        assert resp.status_code == 200


class TestVoiceOutputs:
    def test_list_outputs(self, client):
        resp = client.get("/api/voice/outputs")
        assert resp.status_code == 200
