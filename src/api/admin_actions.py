"""OpenSoul Admin Actions API — one-click system maintenance operations.

Provides common admin tasks as single API calls:
- Clear all caches
- Run immediate backup
- Clean up expired data across all components
- Export/import system configuration
- Bulk health check with detailed diagnostics
"""

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import httpx

router = APIRouter()

_BASE = "http://127.0.0.1:8090"


# ── Request Schemas ─────────────────────────────────────────

class BackupRequest(BaseModel):
    name: str = "admin-backup"
    description: str = "Manual backup via admin actions"
    include_knowledge: bool = True
    include_config: bool = True
    include_memories: bool = True


# ── Cache Management ────────────────────────────────────────

@router.post("/caches/clear")
async def clear_all_caches():
    """Clear all component caches in one call."""
    results = {}
    cache_endpoints = {
        "vein": "/api/vein/cache/clear",
        "reflex": "/api/reflex/cache/clear",
        "voice": "/api/voice/cache/clear",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, endpoint in cache_endpoints.items():
            try:
                res = await client.post(f"{_BASE}{endpoint}")
                results[name] = {
                    "status": "ok" if res.status_code == 200 else "error",
                    "code": res.status_code,
                    "data": res.json() if res.status_code == 200 else None,
                }
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}

    cleared = sum(1 for r in results.values() if r["status"] == "ok")
    return {
        "action": "clear_caches",
        "results": results,
        "cleared": cleared,
        "total": len(results),
    }


# ── Cleanup Expired Data ────────────────────────────────────

@router.post("/cleanup")
async def cleanup_expired():
    """Clean up expired data across all components."""
    results = {}
    cleanup_endpoints = {
        "vein_cache": ("/api/vein/cache/cleanup", "POST"),
        "mirror_sandboxes": ("/api/mirror/cleanup", "POST"),
        "hippo_sessions": ("/api/hippo/sessions/cleanup", "POST"),
        "reflex_cache": ("/api/reflex/cache/cleanup", "POST"),
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, (endpoint, method) in cleanup_endpoints.items():
            try:
                if method == "POST":
                    res = await client.post(f"{_BASE}{endpoint}")
                else:
                    res = await client.get(f"{_BASE}{endpoint}")
                results[name] = {
                    "status": "ok" if res.status_code == 200 else "error",
                    "code": res.status_code,
                    "data": res.json() if res.status_code == 200 else None,
                }
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}

    cleaned = sum(1 for r in results.values() if r["status"] == "ok")
    return {
        "action": "cleanup_expired",
        "results": results,
        "cleaned": cleaned,
        "total": len(results),
    }


# ── Backup ──────────────────────────────────────────────────

@router.post("/backup")
async def run_backup(req: BackupRequest):
    """Run an immediate backup of system data."""
    source_dirs = []

    if req.include_knowledge:
        source_dirs.extend([
            str(Path.home() / "opensoul" / "data"),
        ])

    if req.include_config:
        source_dirs.extend([
            str(Path.home() / "opensoul" / "src" / "config.py"),
            str(Path.home() / ".hermes"),
        ])

    if req.include_memories:
        source_dirs.append(str(Path.home() / "opensoul" / "data" / "vein"))

    if not source_dirs:
        raise HTTPException(400, "No sources selected for backup")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            res = await client.post(f"{_BASE}/api/marrow/backups", json={
                "source_dirs": source_dirs,
                "name": req.name,
                "description": req.description,
                "tags": ["admin", "manual"],
            })
            if res.status_code == 200:
                return {
                    "action": "backup",
                    "status": "ok",
                    "data": res.json(),
                }
            else:
                return {
                    "action": "backup",
                    "status": "error",
                    "code": res.status_code,
                    "error": res.text,
                }
        except Exception as e:
            return {
                "action": "backup",
                "status": "error",
                "error": str(e),
            }


# ── System Overview ─────────────────────────────────────────

@router.get("/overview")
async def system_overview():
    """Get a comprehensive system overview in a single call."""
    overview = {
        "timestamp": time.time(),
        "health": {},
        "stats": {},
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        # Health check
        try:
            res = await client.get(f"{_BASE}/api/health/all")
            if res.status_code == 200:
                overview["health"] = res.json()
        except Exception:
            overview["health"] = {"status": "error"}

        # Stats from key components
        stat_endpoints = {
            "vein": "/api/vein/stats",
            "gland": "/api/gland/usage",
            "gene": "/api/gene/stats",
            "hippo": "/api/hippo/stats",
            "immune": "/api/immune/audit/stats",
            "trajectory": "/api/trajectory/stats",
        }

        for name, endpoint in stat_endpoints.items():
            try:
                res = await client.get(f"{_BASE}{endpoint}")
                if res.status_code == 200:
                    overview["stats"][name] = res.json()
            except Exception:
                pass

    return overview


# ── Export System Config ─────────────────────────────────────

@router.get("/export/config")
async def export_config():
    """Export system configuration as JSON."""
    config = {
        "exported_at": time.time(),
        "version": "1.0.0",
        "components": {},
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        health_endpoints = {
            "soul": "/api/health",
            "gland": "/api/gland/health",
            "gene": "/api/gene/health",
            "vital": "/api/vital/health",
            "nerve": "/api/nerve/health",
            "immune": "/api/immune/health",
        }

        for name, endpoint in health_endpoints.items():
            try:
                res = await client.get(f"{_BASE}{endpoint}")
                if res.status_code == 200:
                    config["components"][name] = res.json()
            except Exception:
                pass

    # Include gland providers
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{_BASE}/api/gland/providers")
            if res.status_code == 200:
                config["providers"] = res.json()
    except Exception:
        pass

    # Include gene templates count
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{_BASE}/api/gene/stats")
            if res.status_code == 200:
                config["templates"] = res.json()
    except Exception:
        pass

    return config


# ── Health ──────────────────────────────────────────────────

@router.get("/health")
async def admin_health():
    """Admin actions health check."""
    return {
        "status": "ok",
        "component": "AdminActions",
        "actions": [
            "clear_caches",
            "cleanup",
            "backup",
            "overview",
            "export_config",
        ],
    }
