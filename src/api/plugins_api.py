"""Plugin management API — install, configure, enable/disable, uninstall."""

import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.database.postgres import db_pool

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Pydantic models ──────────────────────────────────────────────

class PluginInstallRequest(BaseModel):
    name: str
    version: str = "0.0.0"
    description: str = ""
    type: str = "general"
    manifest: dict


class PluginPatchRequest(BaseModel):
    status: str  # "enabled" | "disabled"


class PluginConfigRequest(BaseModel):
    config: dict


class SidebarEntry(BaseModel):
    href: str
    label: str
    icon: str = ""
    group: str = ""
    sort_order: int = 0


# ─── Helper ────────────────────────────────────────────────────────

async def _ensure_table():
    await db_pool.execute(
        """CREATE TABLE IF NOT EXISTS plugins (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL UNIQUE,
            version       TEXT    NOT NULL DEFAULT '0.0.0',
            description   TEXT    NOT NULL DEFAULT '',
            type          TEXT    NOT NULL DEFAULT 'general',
            manifest_json TEXT    NOT NULL DEFAULT '{}',
            status        TEXT    NOT NULL DEFAULT 'enabled',
            installed_at  TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        )"""
    )


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "manifest_json" in d:
        try:
            d["manifest"] = json.loads(d.pop("manifest_json"))
        except (json.JSONDecodeError, TypeError):
            d["manifest"] = d.pop("manifest_json")
    return d


# ─── Endpoints ─────────────────────────────────────────────────────

@router.get("")
async def list_plugins():
    """List all installed plugins."""
    await _ensure_table()
    rows = await db_pool.fetch("SELECT * FROM plugins ORDER BY installed_at DESC")
    return [_row_to_dict(r) for r in rows]


@router.get("/sidebar", response_model=list[SidebarEntry])
async def get_sidebar_entries():
    """Return sidebar entries from all enabled plugins."""
    await _ensure_table()
    rows = await db_pool.fetch(
        "SELECT manifest_json FROM plugins WHERE status = 'enabled'"
    )
    entries: list[SidebarEntry] = []
    for row in rows:
        try:
            manifest = json.loads(row["manifest_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        for item in manifest.get("sidebar", []):
            try:
                entries.append(SidebarEntry(**item))
            except Exception:
                logger.warning("Invalid sidebar entry in manifest: %s", item)
    entries.sort(key=lambda e: e.sort_order)
    return entries


@router.post("/install")
async def install_plugin(req: PluginInstallRequest):
    """Install a new plugin from manifest JSON."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()

    existing = await db_pool.fetchrow("SELECT id FROM plugins WHERE name = $1", req.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Plugin '{req.name}' already installed")

    row = await db_pool.fetchrow(
        """INSERT INTO plugins (name, version, description, type, manifest_json, status, installed_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, 'enabled', $6, $6) RETURNING *""",
        req.name, req.version, req.description, req.type, json.dumps(req.manifest), now,
    )
    return _row_to_dict(row)


@router.get("/{plugin_id}")
async def get_plugin(plugin_id: int):
    """Get plugin details by ID."""
    await _ensure_table()
    row = await db_pool.fetchrow("SELECT * FROM plugins WHERE id = $1", plugin_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return _row_to_dict(row)


@router.patch("/{plugin_id}")
async def toggle_plugin(plugin_id: int, req: PluginPatchRequest):
    """Enable or disable a plugin."""
    await _ensure_table()
    if req.status not in ("enabled", "disabled"):
        raise HTTPException(status_code=400, detail="status must be 'enabled' or 'disabled'")

    now = datetime.now(timezone.utc).isoformat()
    row = await db_pool.fetchrow(
        "UPDATE plugins SET status = $1, updated_at = $2 WHERE id = $3 RETURNING *",
        req.status, now, plugin_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return _row_to_dict(row)


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: int):
    """Uninstall (delete) a plugin."""
    await _ensure_table()
    result = await db_pool.execute("DELETE FROM plugins WHERE id = $1", plugin_id)
    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return {"deleted": True, "id": plugin_id}


@router.post("/{plugin_id}/config")
async def update_plugin_config(plugin_id: int, req: PluginConfigRequest):
    """Update a plugin's manifest/configuration."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()

    existing = await db_pool.fetchrow("SELECT manifest_json FROM plugins WHERE id = $1", plugin_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Plugin not found")

    try:
        current_manifest = json.loads(existing["manifest_json"])
    except (json.JSONDecodeError, TypeError):
        current_manifest = {}

    current_manifest.update(req.config)

    row = await db_pool.fetchrow(
        "UPDATE plugins SET manifest_json = $1, updated_at = $2 WHERE id = $3 RETURNING *",
        json.dumps(current_manifest), now, plugin_id,
    )
    return _row_to_dict(row)
