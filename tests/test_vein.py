"""Integration tests for OpenVein (血管) — file storage, cache, chunked upload."""


class TestVeinHealth:
    def test_health(self, client):
        resp = client.get("/api/vein/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenVein"
        assert "store" in data
        assert "cache" in data


class TestVeinFileStore:
    def test_upload_and_retrieve(self, client):
        content = b"Hello OpenVein test content"
        resp = client.post(
            "/api/vein/upload",
            files={"file": ("test.txt", content, "text/plain")},
            data={"tags": "test,integration"},
        )
        assert resp.status_code == 200
        data = resp.json()
        file_id = data["file_id"]
        assert data["name"] == "test.txt"
        assert data["size"] == len(content)
        assert "test" in data["tags"]

        # Retrieve metadata
        resp = client.get(f"/api/vein/files/{file_id}")
        assert resp.status_code == 200
        assert resp.json()["file_id"] == file_id

        # Download
        resp = client.get(f"/api/vein/files/{file_id}/download")
        assert resp.status_code == 200
        assert resp.content == content

        # Cleanup
        client.delete(f"/api/vein/files/{file_id}")

    def test_list_files(self, client):
        resp = client.get("/api/vein/files")
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert "count" in data

    def test_dedup(self, client):
        content = b"dedup test content"
        r1 = client.post(
            "/api/vein/upload",
            files={"file": ("dup1.txt", content, "text/plain")},
        )
        r2 = client.post(
            "/api/vein/upload",
            files={"file": ("dup2.txt", content, "text/plain")},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Same content hash
        assert r1.json()["content_hash"] == r2.json()["content_hash"]
        # Different file IDs
        assert r1.json()["file_id"] != r2.json()["file_id"]

        # Cleanup
        client.delete(f"/api/vein/files/{r1.json()['file_id']}")
        client.delete(f"/api/vein/files/{r2.json()['file_id']}")

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/vein/files/nonexistent_id")
        assert resp.status_code == 404


class TestVeinCache:
    def test_cache_stats(self, client):
        resp = client.get("/api/vein/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "max_size_bytes" in data

    def test_cache_put_get_delete(self, client):
        import base64

        payload = {"key": "test_key_123", "data": base64.b64encode(b"cached data").decode()}
        resp = client.put("/api/vein/cache", json=payload)
        assert resp.status_code == 200

        resp = client.get("/api/vein/cache/test_key_123")
        assert resp.status_code == 200

        resp = client.delete("/api/vein/cache/test_key_123")
        assert resp.status_code == 200


class TestVeinChunkedUpload:
    def test_init_upload_session(self, client):
        resp = client.post(
            "/api/vein/upload/chunked/init",
            json={
                "filename": "big.bin",
                "total_size": 1024,
                "chunk_size": 512,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "upload_id" in data
        assert data["filename"] == "big.bin"

        # Cleanup
        client.delete(f"/api/vein/upload/chunked/{data['upload_id']}")

    def test_list_uploads(self, client):
        resp = client.get("/api/vein/upload/chunked")
        assert resp.status_code == 200
        assert "sessions" in resp.json()


class TestVeinStats:
    def test_stats(self, client):
        resp = client.get("/api/vein/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "store" in data
        assert "cache" in data
        assert "uploads" in data
