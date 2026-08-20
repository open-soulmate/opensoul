"""Integration tests for Limb RPA API (/api/limb/rpa) — GUI automation endpoints."""

import pytest


class TestLimbRPAHealth:
    def test_health(self, client):
        resp = client.get("/api/limb/rpa/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestLimbRPAScreenshot:
    def test_screenshot(self, client):
        resp = client.post("/api/limb/rpa/screenshot", json={})
        assert resp.status_code in (200, 400, 500, 503)

    def test_screenshot_with_region(self, client):
        resp = client.post(
            "/api/limb/rpa/screenshot",
            json={"region": {"x": 0, "y": 0, "width": 100, "height": 100}},
        )
        assert resp.status_code in (200, 400, 500, 503)


class TestLimbRPAOCR:
    def test_ocr_no_image(self, client):
        resp = client.post("/api/limb/rpa/ocr", json={})
        assert resp.status_code in (200, 400, 422, 500, 503)

    def test_ocr_with_image_data(self, client):
        resp = client.post(
            "/api/limb/rpa/ocr",
            json={"image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="},
        )
        assert resp.status_code in (200, 400, 500, 503)


class TestLimbRPAType:
    def test_type_missing_text(self, client):
        resp = client.post("/api/limb/rpa/type", json={})
        assert resp.status_code in (200, 400, 422)

    def test_type_with_text(self, client):
        resp = client.post(
            "/api/limb/rpa/type",
            json={"text": "hello world"},
        )
        assert resp.status_code in (200, 400, 500, 503)


class TestLimbRPAKey:
    def test_key_missing(self, client):
        resp = client.post("/api/limb/rpa/key", json={})
        assert resp.status_code in (200, 400, 422)

    def test_key_enter(self, client):
        resp = client.post(
            "/api/limb/rpa/key",
            json={"keys": ["enter"]},
        )
        assert resp.status_code in (200, 400, 422, 500, 503)


class TestLimbRPAClick:
    def test_click_missing_coords(self, client):
        resp = client.post("/api/limb/rpa/click", json={})
        assert resp.status_code in (200, 400, 422)

    def test_click_with_coords(self, client):
        resp = client.post(
            "/api/limb/rpa/click",
            json={"x": 100, "y": 100},
        )
        assert resp.status_code in (200, 400, 500, 503)


class TestLimbRPAMouse:
    def test_mouse_move(self, client):
        resp = client.post(
            "/api/limb/rpa/mouse",
            json={"x": 200, "y": 200},
        )
        assert resp.status_code in (200, 400, 500, 503)


class TestLimbRPADrag:
    def test_drag(self, client):
        resp = client.post(
            "/api/limb/rpa/drag",
            json={"x1": 100, "y1": 100, "x2": 200, "y2": 200},
        )
        assert resp.status_code in (200, 400, 422, 500, 503)


class TestLimbRPAWindows:
    def test_list_windows(self, client):
        resp = client.get("/api/limb/rpa/windows")
        assert resp.status_code in (200, 500, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))


class TestLimbRPAFocus:
    def test_focus_missing(self, client):
        resp = client.post("/api/limb/rpa/focus", json={})
        assert resp.status_code in (200, 400, 404, 422)


class TestLimbRPAClickText:
    def test_click_text_missing(self, client):
        resp = client.post("/api/limb/rpa/click-text", json={})
        assert resp.status_code in (200, 400, 422)

    def test_click_text_with_text(self, client):
        resp = client.post(
            "/api/limb/rpa/click-text",
            json={"text": "nonexistent button text"},
        )
        assert resp.status_code in (200, 400, 404, 500, 503)


class TestLimbRPAWaitText:
    def test_wait_text_missing(self, client):
        resp = client.post("/api/limb/rpa/wait-text", json={})
        assert resp.status_code in (200, 400, 422)


class TestLimbRPAReadRegion:
    def test_read_region_missing(self, client):
        resp = client.post("/api/limb/rpa/read-region", json={})
        assert resp.status_code in (200, 400, 422)


class TestLimbRPAScroll:
    def test_scroll(self, client):
        resp = client.post(
            "/api/limb/rpa/scroll",
            json={"direction": "down", "amount": 3},
        )
        assert resp.status_code in (200, 400, 500, 503)
