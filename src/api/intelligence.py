"""OpenIntelligence API — System intelligence: cross-component analytics, anomaly detection, optimization insights."""

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.intelligence.analyzer import InsightType, Severity, SystemIntelligence

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
intelligence = SystemIntelligence()


class MetricsRecordRequest(BaseModel):
    component: str
    health: str = "ok"
    response_time_ms: float = 0
    request_count: int = 0
    error_count: int = 0
    custom: dict = {}


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    """OpenIntelligence health check."""
    return {
        "status": "ok",
        "component": "OpenIntelligence",
        "tracked_components": len(intelligence._component_metrics),
        "total_insights": len(intelligence._insights),
    }


@router.get("/stats")
async def intelligence_stats():
    """Get Intelligence statistics."""
    return {
        "status": "ok",
        "component": "OpenIntelligence",
        "tracked_components": len(intelligence._component_metrics),
        "total_insights": len(intelligence._insights),
        "insight_types": list(
            set(
                str(i.insight_type.value) if hasattr(i, "insight_type") else ""
                for i in intelligence._insights
            )
        ),
    }


# ── System Summary ─────────────────────────────────────────


@router.get("/summary")
async def get_summary():
    """Get overall system intelligence summary with health score."""
    return intelligence.get_system_summary()


# ── Auto-Collect from All Organs ───────────────────────────

_ORGAN_ENDPOINTS = [
    ("soul", "/api/health"),
    ("cortex", "/api/cortex/health"),
    ("cortex-enhanced", "/api/cortex/enhanced/health"),
    ("nerve", "/api/nerve/health"),
    ("vein", "/api/vein/health"),
    ("sense", "/api/sense/health"),
    ("will", "/api/will/health"),
    ("immune", "/api/immune/health"),
    ("vital", "/api/vital/health"),
    ("marrow", "/api/marrow/health"),
    ("gland", "/api/gland/health"),
    ("gene", "/api/gene/health"),
    ("echo", "/api/echo/health"),
    ("mirror", "/api/mirror/health"),
    ("link", "/api/link/health"),
    ("hippo", "/api/hippo/health"),
    ("reflex", "/api/reflex/health"),
    ("heredity", "/api/heredity/health"),
    ("pulse", "/api/pulse/health"),
    ("nest", "/api/nest/health"),
    ("limb", "/api/limb/health"),
    ("voice", "/api/voice/health"),
    ("vision", "/api/vision/health"),
    ("mind", "/api/mind/health"),
    ("trajectory", "/api/trajectory/health"),
    ("mcp", "/api/mcp/health"),
    ("learn", "/api/learn/health"),
    ("diagnostics", "/api/diagnostics/health"),
    ("soma-connector", "/api/soma/health"),
    ("event-stream", "/api/events/health"),
]


@router.post("/collect")
async def collect_metrics():
    """Collect metrics from all organ health endpoints and analyze."""
    base = "http://127.0.0.1:8090"
    collected = 0
    errors = 0

    async with httpx.AsyncClient(timeout=5.0) as client:

        async def _collect(name: str, path: str):
            nonlocal collected, errors
            start = time.time()
            try:
                r = await client.get(f"{base}{path}")
                elapsed_ms = (time.time() - start) * 1000
                data = r.json() if r.status_code == 200 else {}

                intelligence.record_metrics(
                    name,
                    {
                        "health": "ok" if r.status_code == 200 else "error",
                        "response_time_ms": elapsed_ms,
                        "custom": {k: v for k, v in data.items() if k != "status"},
                    },
                )
                collected += 1
            except Exception:
                elapsed_ms = (time.time() - start) * 1000
                intelligence.record_metrics(
                    name,
                    {
                        "health": "error",
                        "response_time_ms": elapsed_ms,
                        "error_count": 1,
                    },
                )
                errors += 1

        await asyncio.gather(*[_collect(n, p) for n, p in _ORGAN_ENDPOINTS])

    return {
        "collected": collected,
        "errors": errors,
        "total_endpoints": len(_ORGAN_ENDPOINTS),
        "timestamp": time.time(),
    }


# ── Insights ───────────────────────────────────────────────


@router.get("/insights")
async def get_insights(
    component: str | None = Query(None),
    type: str | None = Query(None, description="anomaly, optimization, trend, warning, info"),
    severity: str | None = Query(None, description="low, medium, high, critical"),
    limit: int = Query(50, ge=1, le=200),
):
    """Get system insights with optional filters."""
    insight_type = None
    if type:
        try:
            insight_type = InsightType(type)
        except ValueError:
            pass

    sev = None
    if severity:
        try:
            sev = Severity(severity)
        except ValueError:
            pass

    return {
        "insights": intelligence.get_insights(
            component=component,
            insight_type=insight_type,
            severity=sev,
            limit=limit,
        )
    }


# ── Trends ─────────────────────────────────────────────────


@router.get("/trends/{component}")
async def get_trends(
    component: str,
    metric: str = Query(default="response_time"),
    duration: int = Query(default=3600, description="Duration in seconds"),
):
    """Get trend data for a component metric."""
    return {
        "component": component,
        "metric": metric,
        "duration_seconds": duration,
        "data": intelligence.get_trends(component, metric, duration),
    }


# ── Component Details ──────────────────────────────────────


@router.get("/components")
async def get_component_details():
    """Get detailed metrics for all tracked components."""
    return {"components": intelligence.get_component_details()}


# ── Recommendations ────────────────────────────────────────


@router.get("/recommendations")
async def get_recommendations():
    """Get optimization recommendations based on collected data."""
    return {"recommendations": intelligence.generate_recommendations()}


# ── Manual Record ──────────────────────────────────────────


@router.post("/record")
async def record_metrics(req: MetricsRecordRequest):
    """Manually record metrics for a component."""
    intelligence.record_metrics(
        req.component,
        {
            "health": req.health,
            "response_time_ms": req.response_time_ms,
            "request_count": req.request_count,
            "error_count": req.error_count,
            "custom": req.custom,
        },
    )
    return {"status": "ok", "component": req.component}
