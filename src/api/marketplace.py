"""Marketplace sources API - configure skill and agent sources.

Supports built-in sources (ClawHub, Tencent SkillHub, etc.) and custom sources.
OpenMate polls this endpoint to sync skills/agents.
"""

import logging
import sqlite3
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "opensoul.db"

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenMarketplace"}


logger = logging.getLogger(__name__)

# Built-in skill sources
BUILTIN_SKILL_SOURCES = [
    {
        "id": "hermes-official",
        "name": "Hermes Official Skills",
        "type": "hermes",
        "url": "https://github.com/NousResearch/hermes-agent-skills",
        "description": "Hermes Agent官方技能库",
        "enabled": True,
        "builtin": True,
        "auto_sync": True,
        "sync_interval": 3600,
    },
    {
        "id": "clawhub",
        "name": "ClawHub",
        "type": "openclaw",
        "url": "https://clawhub.com/api/v1/skills",
        "description": "OpenClaw社区技能市场",
        "enabled": True,
        "builtin": True,
        "auto_sync": True,
        "sync_interval": 3600,
    },
    {
        "id": "tencent-skillhub",
        "name": "腾讯 SkillHub",
        "type": "tencent",
        "url": "https://skillhub.tencent.com/api/v1/skills",
        "description": "腾讯云AI技能市场",
        "enabled": False,
        "builtin": True,
        "auto_sync": False,
        "sync_interval": 7200,
    },
    {
        "id": "aliyun-agentmarket",
        "name": "百炼 Agent 市场",
        "type": "aliyun",
        "url": "https://bailian.console.aliyun.com/api/v1/agents",
        "description": "阿里云百炼平台Agent市场",
        "enabled": False,
        "builtin": True,
        "auto_sync": False,
        "sync_interval": 7200,
    },
    {
        "id": "openmate-community",
        "name": "OpenMate 社区",
        "type": "github",
        "url": "https://github.com/open-soulmate/skills-registry",
        "description": "OpenMate社区技能仓库",
        "enabled": True,
        "builtin": True,
        "auto_sync": True,
        "sync_interval": 1800,
    },
]

# Built-in agent sources
BUILTIN_AGENT_SOURCES = [
    {
        "id": "official-agents",
        "name": "官方 Agent 列表",
        "type": "builtin",
        "url": "",
        "description": "OpenSoul内置的Agent检测列表",
        "enabled": True,
        "builtin": True,
        "auto_update": False,
    },
    {
        "id": "hermes-agents",
        "name": "Hermes Agent Hub",
        "type": "hermes",
        "url": "https://github.com/NousResearch/hermes-agent-hub",
        "description": "Hermes官方Agent仓库",
        "enabled": True,
        "builtin": True,
        "auto_update": True,
    },
    {
        "id": "openclaw-agents",
        "name": "OpenClaw Agent Hub",
        "type": "openclaw",
        "url": "https://clawhub.com/api/v1/agents",
        "description": "OpenClaw社区Agent市场",
        "enabled": True,
        "builtin": True,
        "auto_update": True,
    },
]


# ─── Pydantic Models ─────────────────────────────────────────────


class SourceCreate(BaseModel):
    name: str
    type: str  # hermes, openclaw, tencent, aliyun, github, custom
    url: str
    description: str = ""
    enabled: bool = True
    auto_sync: bool = True
    sync_interval: int = 3600


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    description: str | None = None
    enabled: bool | None = None
    auto_sync: bool | None = None
    sync_interval: int | None = None


# ─── Database Setup ───────────────────────────────────────────────


def init_marketplace_tables(db: sqlite3.Connection):
    """Create marketplace tables if not exist"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS skill_sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            url TEXT NOT NULL,
            description TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            builtin INTEGER DEFAULT 0,
            auto_sync INTEGER DEFAULT 1,
            sync_interval INTEGER DEFAULT 3600,
            last_sync TEXT,
            skill_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS agent_sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            url TEXT NOT NULL,
            description TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            builtin INTEGER DEFAULT 0,
            auto_update INTEGER DEFAULT 0,
            last_sync TEXT,
            agent_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT '',
            version TEXT DEFAULT '',
            downloads INTEGER DEFAULT 0,
            rating REAL DEFAULT 0,
            installed INTEGER DEFAULT 0,
            UNIQUE(source_id, skill_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT '',
            icon TEXT DEFAULT '',
            version TEXT DEFAULT '',
            installed INTEGER DEFAULT 0,
            UNIQUE(source_id, agent_id)
        )
    """)
    db.commit()


def seed_builtin_sources(db: sqlite3.Connection):
    """Insert built-in sources if not exist"""
    for src in BUILTIN_SKILL_SOURCES:
        db.execute(
            """
            INSERT OR IGNORE INTO skill_sources (id, name, type, url, description, enabled, builtin, auto_sync, sync_interval)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                src["id"],
                src["name"],
                src["type"],
                src["url"],
                src["description"],
                1 if src["enabled"] else 0,
                1,
                1 if src["auto_sync"] else 0,
                src["sync_interval"],
            ),
        )

    for src in BUILTIN_AGENT_SOURCES:
        db.execute(
            """
            INSERT OR IGNORE INTO agent_sources (id, name, type, url, description, enabled, builtin, auto_update)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                src["id"],
                src["name"],
                src["type"],
                src["url"],
                src["description"],
                1 if src["enabled"] else 0,
                1,
                1 if src["auto_update"] else 0,
            ),
        )
    db.commit()


def get_marketplace_db() -> sqlite3.Connection:
    """Get marketplace database connection"""
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    init_marketplace_tables(db)
    seed_builtin_sources(db)
    return db


# ─── Skill Sources API ───────────────────────────────────────────


@router.get("/skills/sources")
async def list_skill_sources(user_id: UUID = Depends(get_current_user)):
    """List all configured skill sources"""
    db = get_marketplace_db()
    rows = db.execute("SELECT * FROM skill_sources ORDER BY builtin DESC, name").fetchall()
    sources = []
    for r in rows:
        sources.append(
            {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "url": r[3],
                "description": r[4],
                "enabled": bool(r[5]),
                "builtin": bool(r[6]),
                "auto_sync": bool(r[7]),
                "sync_interval": r[8],
                "last_sync": r[9],
                "skill_count": r[10],
            }
        )
    return {"sources": sources}


@router.post("/skills/sources")
async def create_skill_source(src: SourceCreate, user_id: UUID = Depends(get_current_user)):
    """Add a custom skill source"""
    db = get_marketplace_db()
    source_id = f"custom-{src.type}-{hash(src.url) % 10000:04d}"
    db.execute(
        """
        INSERT OR REPLACE INTO skill_sources (id, name, type, url, description, enabled, builtin, auto_sync, sync_interval)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
    """,
        (
            source_id,
            src.name,
            src.type,
            src.url,
            src.description,
            1 if src.enabled else 0,
            1 if src.auto_sync else 0,
            src.sync_interval,
        ),
    )
    db.commit()
    return {"success": True, "id": source_id}


@router.put("/skills/sources/{source_id}")
async def update_skill_source(
    source_id: str, update: SourceUpdate, user_id: UUID = Depends(get_current_user)
):
    """Update a skill source configuration"""
    db = get_marketplace_db()
    existing = db.execute("SELECT id FROM skill_sources WHERE id = ?", (source_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")

    updates = []
    params = []
    for field, value in update.model_dump(exclude_none=True).items():
        if field in ("name", "url", "description", "sync_interval"):
            updates.append(f"{field} = ?")
            params.append(value)
        elif field in ("enabled", "auto_sync"):
            updates.append(f"{field} = ?")
            params.append(1 if value else 0)

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(source_id)
        db.execute(f"UPDATE skill_sources SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    return {"success": True}


@router.delete("/skills/sources/{source_id}")
async def delete_skill_source(source_id: str, user_id: UUID = Depends(get_current_user)):
    """Delete a custom skill source (builtin sources cannot be deleted)"""
    db = get_marketplace_db()
    existing = db.execute("SELECT builtin FROM skill_sources WHERE id = ?", (source_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")
    if existing[0]:
        raise HTTPException(status_code=400, detail="Cannot delete built-in source")
    db.execute("DELETE FROM skill_sources WHERE id = ?", (source_id,))
    db.execute("DELETE FROM marketplace_skills WHERE source_id = ?", (source_id,))
    db.commit()
    return {"success": True}


@router.post("/skills/sources/{source_id}/sync")
async def sync_skill_source(source_id: str, user_id: UUID = Depends(get_current_user)):
    """Manually trigger sync for a skill source"""
    db = get_marketplace_db()
    existing = db.execute("SELECT * FROM skill_sources WHERE id = ?", (source_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")

    # Simulate sync - in production, fetch from source URL
    # For now, just update last_sync time
    db.execute("UPDATE skill_sources SET last_sync = datetime('now') WHERE id = ?", (source_id,))
    db.commit()
    return {"success": True, "message": f"Synced {source_id}"}


# ─── Agent Sources API ───────────────────────────────────────────


@router.get("/agents/sources")
async def list_agent_sources(user_id: UUID = Depends(get_current_user)):
    """List all configured agent sources"""
    db = get_marketplace_db()
    rows = db.execute("SELECT * FROM agent_sources ORDER BY builtin DESC, name").fetchall()
    sources = []
    for r in rows:
        sources.append(
            {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "url": r[3],
                "description": r[4],
                "enabled": bool(r[5]),
                "builtin": bool(r[6]),
                "auto_update": bool(r[7]),
                "last_sync": r[8],
                "agent_count": r[9],
            }
        )
    return {"sources": sources}


@router.post("/agents/sources")
async def create_agent_source(src: SourceCreate, user_id: UUID = Depends(get_current_user)):
    """Add a custom agent source"""
    db = get_marketplace_db()
    source_id = f"custom-agent-{hash(src.url) % 10000:04d}"
    db.execute(
        """
        INSERT OR REPLACE INTO agent_sources (id, name, type, url, description, enabled, builtin, auto_update)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
    """,
        (
            source_id,
            src.name,
            src.type,
            src.url,
            src.description,
            1 if src.enabled else 0,
            1 if src.auto_sync else 0,
        ),
    )
    db.commit()
    return {"success": True, "id": source_id}


@router.put("/agents/sources/{source_id}")
async def update_agent_source(
    source_id: str, update: SourceUpdate, user_id: UUID = Depends(get_current_user)
):
    """Update an agent source configuration"""
    db = get_marketplace_db()
    existing = db.execute("SELECT id FROM agent_sources WHERE id = ?", (source_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")

    updates = []
    params = []
    for field, value in update.model_dump(exclude_none=True).items():
        if field in ("name", "url", "description"):
            updates.append(f"{field} = ?")
            params.append(value)
        elif field == "enabled":
            updates.append("enabled = ?")
            params.append(1 if value else 0)
        elif field == "auto_sync":
            updates.append("auto_update = ?")
            params.append(1 if value else 0)

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(source_id)
        db.execute(f"UPDATE agent_sources SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    return {"success": True}


@router.delete("/agents/sources/{source_id}")
async def delete_agent_source(source_id: str, user_id: UUID = Depends(get_current_user)):
    """Delete a custom agent source"""
    db = get_marketplace_db()
    existing = db.execute("SELECT builtin FROM agent_sources WHERE id = ?", (source_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")
    if existing[0]:
        raise HTTPException(status_code=400, detail="Cannot delete built-in source")
    db.execute("DELETE FROM agent_sources WHERE id = ?", (source_id,))
    db.commit()
    return {"success": True}


# ─── Sync API (for OpenMate to poll) ─────────────────────────────


@router.get("/sync/skills")
async def get_synced_skills(user_id: UUID = Depends(get_current_user)):
    """Get all available skills from enabled sources (OpenMate polls this)"""
    db = get_marketplace_db()
    rows = db.execute("""
        SELECT s.id, s.name, s.description, s.category, s.version, s.downloads, s.rating,
               s.source_id, src.name as source_name, src.type as source_type
        FROM marketplace_skills s
        JOIN skill_sources src ON s.source_id = src.id
        WHERE src.enabled = 1
        ORDER BY s.downloads DESC, s.name
    """).fetchall()

    skills = []
    for r in rows:
        skills.append(
            {
                "id": r[0],
                "name": r[2],
                "description": r[3],
                "category": r[4],
                "version": r[5],
                "downloads": r[6],
                "rating": r[7],
                "source_id": r[8],
                "source_name": r[9],
                "source_type": r[10],
            }
        )
    return {"skills": skills, "total": len(skills)}


@router.get("/sync/agents")
async def get_synced_agents(user_id: UUID = Depends(get_current_user)):
    """Get all available agents from enabled sources (OpenMate polls this)"""
    db = get_marketplace_db()
    rows = db.execute("""
        SELECT a.id, a.name, a.description, a.category, a.icon, a.version,
               a.source_id, src.name as source_name
        FROM marketplace_agents a
        JOIN agent_sources src ON a.source_id = src.id
        WHERE src.enabled = 1
        ORDER BY a.name
    """).fetchall()

    agents = []
    for r in rows:
        agents.append(
            {
                "id": r[0],
                "name": r[1],
                "description": r[2],
                "category": r[3],
                "icon": r[4],
                "version": r[5],
                "source_id": r[6],
                "source_name": r[7],
            }
        )
    return {"agents": agents, "total": len(agents)}


# ─── Admin Stats ─────────────────────────────────────────────────


@router.get("/stats")
async def get_marketplace_stats(user_id: UUID = Depends(get_current_user)):
    """Get marketplace statistics"""
    try:
        db = get_marketplace_db()
        skill_sources = db.execute("SELECT COUNT(*) FROM skill_sources WHERE enabled = 1").fetchone()[0]
        agent_sources = db.execute("SELECT COUNT(*) FROM agent_sources WHERE enabled = 1").fetchone()[0]
        total_skills = db.execute("SELECT COUNT(*) FROM marketplace_skills").fetchone()[0]
        total_agents = db.execute("SELECT COUNT(*) FROM marketplace_agents").fetchone()[0]
        return {
            "skill_sources": skill_sources,
            "agent_sources": agent_sources,
            "total_skills": total_skills,
            "total_agents": total_agents,
        }
    except Exception as e:
        logger.error("marketplace stats error: %s", e, exc_info=True)
        return {
            "skill_sources": 0,
            "agent_sources": 0,
            "total_skills": 0,
            "total_agents": 0,
            "error": str(e),
        }
