"""Integration tests for OpenVision (视觉) — chart/mindmap generation."""

import pytest


class TestVisionHealth:
    def test_health(self, client):
        resp = client.get("/api/vision/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenVision"


class TestVisionCharts:
    def test_bar_chart_json(self, client):
        resp = client.post("/api/vision/chart/bar/json", json={
            "title": "Test Chart",
            "labels": ["A", "B", "C"],
            "values": [10, 20, 30],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "png"
        assert data["chart_type"] == "bar"

    def test_pie_chart(self, client):
        resp = client.post("/api/vision/chart/pie", json={
            "title": "Test Pie",
            "labels": ["X", "Y"],
            "values": [40, 60],
        })
        assert resp.status_code == 200


class TestVisionMindmap:
    def test_mindmap_json(self, client):
        resp = client.post("/api/vision/mindmap/json", json={
            "root": {
                "label": "Root",
                "children": [
                    {"label": "A"},
                    {"label": "B"},
                ],
            },
        })
        assert resp.status_code == 200


class TestVisionOutputs:
    def test_list_outputs(self, client):
        resp = client.get("/api/vision/outputs")
        assert resp.status_code == 200
