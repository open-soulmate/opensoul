"""Integration tests for Pipeline API — file upload and smart processing pipeline."""

import io


class TestPipelineHealth:
    def test_pipeline_history(self, client):
        resp = client.get("/api/pipeline/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "pipelines" in data
        assert "total" in data
        assert isinstance(data["pipelines"], list)

    def test_pipeline_history_limit(self, client):
        resp = client.get("/api/pipeline/history?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["pipelines"]) <= 5


class TestPipelineUpload:
    def test_upload_text_file(self, client):
        """Upload a simple text file through the pipeline."""
        files = {"file": ("test.txt", b"Hello world, this is a test document for pipeline.", "text/plain")}
        data = {"pipeline": "text", "skip_immune": "true", "skip_knowledge": "true"}
        resp = client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] in ("completed", "partial")
        assert "pipeline_id" in result
        assert "steps" in result
        assert len(result["steps"]) >= 1

    def test_upload_auto_detect(self, client):
        """Auto-detect pipeline type from file extension."""
        files = {"file": ("readme.md", b"# Test markdown content for auto-detection", "text/markdown")}
        data = {"pipeline": "auto", "skip_immune": "true", "skip_knowledge": "true"}
        resp = client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["pipeline_type"] == "text"

    def test_upload_image_auto_detect(self, client):
        """Auto-detect should select OCR for image files."""
        # Minimal 1x1 PNG
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        files = {"file": ("test.png", png_data, "image/png")}
        data = {"pipeline": "auto", "skip_immune": "true", "skip_knowledge": "true"}
        resp = client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["pipeline_type"] == "ocr"

    def test_upload_with_tags(self, client):
        """Tags should be included in pipeline metadata."""
        files = {"file": ("tagged.txt", b"Content with tags", "text/plain")}
        data = {"pipeline": "text", "tags": "test,integration", "skip_immune": "true", "skip_knowledge": "true"}
        resp = client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] in ("completed", "partial")


class TestPipelineRun:
    def test_run_nonexistent_file(self, client):
        """Running pipeline on a non-existent file should fail."""
        resp = client.post(
            "/api/pipeline/run",
            json={
                "file_id": "nonexistent-file-id",
                "pipeline": "text",
                "skip_immune": True,
                "skip_knowledge": True,
            },
        )
        assert resp.status_code in (404, 500)


class TestPipelineDetection:
    def test_detect_pipeline_types(self):
        """Test the pipeline auto-detection function."""
        from src.api.pipeline import _detect_pipeline

        assert _detect_pipeline("image/png", "test.png") == "ocr"
        assert _detect_pipeline("audio/wav", "test.wav") == "asr"
        assert _detect_pipeline("video/mp4", "test.mp4") == "video"
        assert _detect_pipeline("text/plain", "test.txt") == "text"
        assert _detect_pipeline("application/json", "data.json") == "text"
        assert _detect_pipeline("application/pdf", "doc.pdf") == "text"
        # Fallback by extension
        assert _detect_pipeline("application/octet-stream", "photo.jpg") == "ocr"
        assert _detect_pipeline("application/octet-stream", "song.mp3") == "asr"
        assert _detect_pipeline("application/octet-stream", "clip.mp4") == "video"
        assert _detect_pipeline("application/octet-stream", "readme.md") == "text"
        # Unknown fallback
        assert _detect_pipeline("application/octet-stream", "unknown.xyz") == "text"
