"""OpenSystem Overview API — single endpoint for complete system status.

Aggregates: organ health, system metrics, recent events, component stats,
active agents, knowledge stats, and plugin status into one response.
Designed for dashboard consumption — one call instead of many.
"""

import time
import asyncio
import logging
from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Organ registry (subset of most important organs) ──────────
_CORE_ORGANS = [
    ("soul", "/api/health"),
    ("cortex", "/api/cortex/health"),
    ("nerve", "/api/nerve/health"),
    ("vein", "/api/vein/health"),
    ("sense", "/api/sense/health"),
    ("will", "/api/will/health"),
    ("immune", "/api/immune/health"),
    ("vital", "/api/vital/health"),
    ("gland", "/api/gland/health"),
    ("gene", "/api/gene/health"),
    ("echo", "/api/echo/health"),
    ("mirror", "/api/mirror/health"),
    ("link", "/api/link/health"),
]


async def _check_organs() -> dict:
    """Check health of core organs in parallel."""
    import httpx
    base = "http://127.0.0.1:8090"
    results = {}

    async with httpx.AsyncClient(timeout=3.0) as client:
        async def _check(name: str, path: str):
            try:
                r = await client.get(f"{base}{path}")
                results[name] = "ok" if r.status_code == 200 else "error"
            except Exception:
                results[name] = "error"

        await asyncio.gather(*[_check(n, p) for n, p in _CORE_ORGANS])

    ok = sum(1 for v in results.values() if v == "ok")
    return {
        "organs": results,
        "healthy_count": ok,
        "total_count": len(results),
        "status": "ok" if ok == len(results) else "degraded" if ok > len(results) // 2 else "critical",
    }


async def _get_system_metrics() -> dict:
    """Get system resource metrics."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": cpu,
            "memory": {
                "total_mb": round(mem.total / 1024 / 1024),
                "used_mb": round(mem.used / 1024 / 1024),
                "percent": mem.percent,
            },
            "disk": {
                "total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
                "used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
                "percent": round(disk.percent, 1),
            },
        }
    except ImportError:
        return {"error": "psutil not installed"}


async def _get_knowledge_stats() -> dict:
    """Get knowledge base statistics."""
    try:
        from src.database.postgres import db_pool
        if db_pool:
            total = await db_pool.fetchval("SELECT COUNT(*) FROM knowledge") or 0
            return {"total_entries": total}
    except Exception:
        pass
    return {"total_entries": -1}


async def _get_plugin_stats() -> dict:
    """Get plugin statistics."""
    try:
        from src.plugin_loader import loaded_plugins
        return {
            "total_plugins": len(loaded_plugins),
            "active_plugins": sum(1 for p in loaded_plugins if p.get("has_backend")),
        }
    except Exception:
        return {"total_plugins": 0, "active_plugins": 0}


async def _get_gland_usage() -> dict:
    """Get model gateway usage summary."""
    try:
        from src.gland.token_meter import TokenMeter
        meter = TokenMeter()
        summary = meter.summary()
        return {
            "total_tokens": summary.get("total_tokens", 0),
            "call_count": summary.get("call_count", 0),
            "by_model": summary.get("by_model", {}),
        }
    except Exception:
        return {"total_tokens": 0, "call_count": 0}


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/health")
async def system_overview_health():
    """System overview health check."""
    return {"status": "ok", "component": "OpenSystem"}


@router.get("/overview")
async def system_overview():
    """Complete system overview — single endpoint for dashboard.

    Returns aggregated data from all subsystems:
    - Organ health status
    - System resource metrics
    - Knowledge base stats
    - Plugin status
    - Model gateway usage
    """
    start = time.time()

    # Run all checks in parallel
    organs, metrics, knowledge, plugins, gland = await asyncio.gather(
        _check_organs(),
        _get_system_metrics(),
        _get_knowledge_stats(),
        _get_plugin_stats(),
        _get_gland_usage(),
        return_exceptions=True,
    )

    # Handle exceptions
    if isinstance(organs, Exception):
        organs = {"organs": {}, "healthy_count": 0, "total_count": 0, "status": "error"}
    if isinstance(metrics, Exception):
        metrics = {"error": str(metrics)}
    if isinstance(knowledge, Exception):
        knowledge = {"total_entries": -1}
    if isinstance(plugins, Exception):
        plugins = {"total_plugins": 0}
    if isinstance(gland, Exception):
        gland = {"total_tokens": 0}

    elapsed = round((time.time() - start) * 1000)

    return {
        "timestamp": time.time(),
        "elapsed_ms": elapsed,
        "version": "2.0.0",
        "system_status": organs.get("status", "unknown"),
        "organs": organs,
        "metrics": metrics,
        "knowledge": knowledge,
        "plugins": plugins,
        "gland": gland,
    }


@router.get("/quick")
async def system_quick_status():
    """Ultra-lightweight status check — just the essentials."""
    from src.database.postgres import db_pool
    try:
        db_ok = db_pool is not None
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "version": "2.0.0",
        "timestamp": time.time(),
    }
