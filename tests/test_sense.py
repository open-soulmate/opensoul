"""Integration tests for OpenSense (感官) — OCR, ASR, multimodal."""


class TestSenseHealth:
    def test_health(self, client):
        resp = client.get("/api/sense/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenSense"
        assert "engines" in data
        assert data["engines"]["ocr"]["available"] is True
        assert data["engines"]["multimodal"]["available"] is True


class TestSenseOCR:
    def test_ocr_languages(self, client):
        resp = client.get("/api/sense/ocr/languages")
        assert resp.status_code == 200
        data = resp.json()
        assert "languages" in data
        assert len(data["languages"]) > 0

    def test_ocr_image_requires_file(self, client):
        resp = client.post("/api/sense/ocr/image")
        assert resp.status_code == 422  # missing required field


class TestSenseASR:
    def test_asr_models(self, client):
        resp = client.get("/api/sense/asr/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
