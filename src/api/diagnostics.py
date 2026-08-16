"""OpenSoul System Diagnostics API — deep system inspection and organ testing."""

import asyncio
import os
import platform
import time
from fastapi import APIRouter, Query
import httpx

router = APIRouter()

_BASE = "http://127.0.0.1:8090"

_ORGANS = {
    "soul":      {"label": "🧠 Soul",      "endpoint": "/api/health",           "category": "core"},
    "cortex":    {"label": "🧩 Cortex",    "endpoint": "/api/cortex/health",    "category": "core"},
    "nerve":     {"label": "⚡ Nerve",      "endpoint": "/api/nerve/health",     "category": "core"},
    "vein":      {"label": "🩸 Vein",      "endpoint": "/api/vein/health",      "category": "core"},
    "sense":     {"label": "👁 Sense",      "endpoint": "/api/sense/health",     "category": "core"},
    "will":      {"label": "✨ Will",       "endpoint": "/api/will/health",      "category": "core"},
    "immune":    {"label": "🛡 Immune",     "endpoint": "/api/immune/health",    "category": "platform"},
    "vital":     {"label": "📊 Vital",      "endpoint": "/api/vital/health",     "category": "platform"},
    "marrow":    {"label": "🦴 Marrow",     "endpoint": "/api/marrow/health",    "category": "platform"},
    "gland":     {"label": "🧪 Gland",      "endpoint": "/api/gland/health",     "category": "platform"},
    "gene":      {"label": "🧬 Gene",       "endpoint": "/api/gene/health",      "category": "platform"},
    "echo":      {"label": "🔊 Echo",       "endpoint": "/api/echo/health",      "category": "platform"},
    "mirror":    {"label": "🪞 Mirror",     "endpoint": "/api/mirror/health",    "category": "platform"},
    "link":      {"label": "🔗 Link",       "endpoint": "/api/link/health",      "category": "platform"},
    "hippo":     {"label": "🧠 Hippo",      "endpoint": "/api/hippo/health",     "category": "advanced"},
    "reflex":    {"label": "⚡ Reflex",      "endpoint": "/api/reflex/health",    "category": "advanced"},
    "heredity":  {"label": "🔗 Heredity",   "endpoint": "/api/heredity/health",  "category": "advanced"},
    "pulse":     {"label": "💓 Pulse",      "endpoint": "/api/pulse/health",     "category": "advanced"},
    "nest":      {"label": "🏠 Nest",       "endpoint": "/api/nest/health",      "category": "advanced"},
    "limb":      {"label": "💪 Limb",       "endpoint": "/api/limb/health",      "category": "advanced"},
    "voice":     {"label": "🎤 Voice",      "endpoint": "/api/voice/health",     "category": "advanced"},
    "vision":    {"label": "🎨 Vision",     "endpoint": "/api/vision/health",    "category": "advanced"},
    "mind":      {"label": "💭 Mind",       "endpoint": "/api/mind/health",      "category": "advanced"},
    "trajectory":{"label": "📊 Trajectory", "endpoint": "/api/trajectory/health","category": "system"},
    "mcp":       {"label": "🔌 MCP",        "endpoint": "/api/mcp/health",       "category": "system"},
    "learn":     {"label": "📚 Learn",      "endpoint": "/api/learn/health",     "category": "system"},
    "plugins":   {"label": "🔌 Plugins",    "endpoint": "/api/plugins/health",   "category": "system"},
    # ── Newer organs added post-initial build ──
    "soma":      {"label": "🤖 Soma",       "endpoint": "/api/soma/health",      "category": "core"},
    "capture":   {"label": "📸 Capture",    "endpoint": "/api/capture/health",   "category": "platform"},
    "intelligence":{"label": "🧠 Intelligence","endpoint": "/api/intelligence/health","category": "system"},
    "events":    {"label": "📡 Events",     "endpoint": "/api/events/health",    "category": "system"},
    "admin":     {"label": "⚙️ Admin",      "endpoint": "/api/admin/health",     "category": "system"},
    # ── Core APIs with health endpoints ──
    "graph":     {"label": "🕸 Graph",       "endpoint": "/api/graph/health",     "category": "core"},
    "entity":    {"label": "📦 Entity",      "endpoint": "/api/entity/health",    "category": "core"},
    "tag":       {"label": "🏷 Tag",         "endpoint": "/api/tags/health",      "category": "core"},
    "user":      {"label": "👤 User",        "endpoint": "/api/user/health",      "category": "core"},
    "llm":       {"label": "🤖 LLM",        "endpoint": "/api/llm/health",       "category": "core"},
    "agent":     {"label": "🤖 Agent",       "endpoint": "/api/agent/health",     "category": "core"},
    "export":    {"label": "📤 Export",      "endpoint": "/api/export/health",    "category": "system"},
    "search":    {"label": "🔍 Search",      "endpoint": "/api/search/health",    "category": "core"},
    "chat":      {"label": "💬 Chat",        "endpoint": "/api/chat/health",      "category": "core"},
    "pipeline":  {"label": "🔄 Pipeline",    "endpoint": "/api/pipeline/health",  "category": "platform"},
}


def _get_system_info() -> dict:
    """Gather host system information."""
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "cpu_percent": cpu_percent,
            "memory_total_gb": round(mem.total / (1024**3), 2),
            "memory_used_gb": round(mem.used / (1024**3), 2),
            "memory_percent": mem.percent,
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_percent": round(disk.percent, 1),
            "uptime_seconds": time.time() - psutil.boot_time(),
            "has_psutil": True,
        }
    except ImportError:
        # Fallback without psutil
        try:
            load = os.getloadavg()
        except (OSError, AttributeError):
            load = (0, 0, 0)
        return {
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "load_avg_1m": load[0],
            "load_avg_5m": load[1],
            "load_avg_15m": load[2],
            "has_psutil": False,
        }


@router.get("/info")
async def system_info():
    """Get detailed system information."""
    return {
        "system": _get_system_info(),
        "version": "0.1.0",
        "component_count": len(_ORGANS),
        "categories": list(set(o["category"] for o in _ORGANS.values())),
    }


@router.get("/organs")
async def list_organs():
    """List all organs with metadata."""
    organs = []
    for key, info in _ORGANS.items():
        organs.append({
            "key": key,
            "label": info["label"],
            "category": info["category"],
            "endpoint": info["endpoint"],
        })
    return {"organs": organs, "total": len(organs)}


@router.get("/organs/{organ_key}")
async def organ_detail(organ_key: str):
    """Get detailed info about a specific organ — hit its health endpoint and return the full response."""
    info = _ORGANS.get(organ_key)
    if not info:
        return {"error": f"Unknown organ: {organ_key}"}

    url = f"{_BASE}{info['endpoint']}"
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
            elapsed_ms = round((time.time() - start) * 1000, 1)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:500]}
            return {
                "organ": organ_key,
                "label": info["label"],
                "category": info["category"],
                "status": "ok" if r.status_code == 200 else "error",
                "status_code": r.status_code,
                "response_time_ms": elapsed_ms,
                "detail": body,
            }
    except Exception as e:
        elapsed_ms = round((time.time() - start) * 1000, 1)
        return {
            "organ": organ_key,
            "label": info["label"],
            "category": info["category"],
            "status": "error",
            "status_code": 0,
            "response_time_ms": elapsed_ms,
            "error": str(e),
        }


@router.get("/check-all")
async def check_all_detailed():
    """Deep health check — hit every organ and return detailed response + timing."""

    async def _check(key: str, info: dict):
        url = f"{_BASE}{info['endpoint']}"
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
                elapsed_ms = round((time.time() - start) * 1000, 1)
                return {
                    "key": key,
                    "label": info["label"],
                    "category": info["category"],
                    "status": "ok" if r.status_code == 200 else "error",
                    "status_code": r.status_code,
                    "response_time_ms": elapsed_ms,
                }
        except Exception as e:
            elapsed_ms = round((time.time() - start) * 1000, 1)
            return {
                "key": key,
                "label": info["label"],
                "category": info["category"],
                "status": "error",
                "status_code": 0,
                "response_time_ms": elapsed_ms,
                "error": str(e),
            }

    results = await asyncio.gather(*[_check(k, v) for k, v in _ORGANS.items()])
    ok = sum(1 for r in results if r["status"] == "ok")
    avg_ms = round(sum(r["response_time_ms"] for r in results) / len(results), 1) if results else 0
    max_ms = max((r["response_time_ms"] for r in results), default=0)

    return {
        "summary": {
            "total": len(results),
            "healthy": ok,
            "unhealthy": len(results) - ok,
            "avg_response_ms": avg_ms,
            "max_response_ms": max_ms,
            "overall": "ok" if ok == len(results) else "degraded",
        },
        "system": _get_system_info(),
        "organs": results,
    }


@router.get("/config-diff")
async def config_diff():
    """Compare current config with defaults — show what's been customized."""
    from src.config_manager import config_manager
    from src.config_manager import DEFAULT_CONFIG

    current = config_manager.get() or {}

    def _diff(default: dict, current: dict, path: str = "") -> list:
        changes = []
        for key in set(list(default.keys()) + list(current.keys())):
            full_key = f"{path}.{key}" if path else key
            d_val = default.get(key)
            c_val = current.get(key)
            if isinstance(d_val, dict) and isinstance(c_val, dict):
                changes.extend(_diff(d_val, c_val, full_key))
            elif d_val != c_val:
                changes.append({
                    "key": full_key,
                    "default": d_val,
                    "current": c_val,
                })
        return changes

    diffs = _diff(DEFAULT_CONFIG, current)
    return {
        "has_customization": len(diffs) > 0,
        "changes": diffs,
        "config_path": str(os.path.expanduser("~/.openmate/config.yaml")),
    }


@router.get("/health")
async def diagnostics_health():
    """Diagnostics module health check."""
    return {"status": "ok", "component": "OpenDiagnostics"}
