"""Marketplace sources API - configure skill and agent sources.

Supports built-in sources (ClawHub, Tencent SkillHub, etc.) and custom sources.
OpenMate polls this endpoint to sync skills/agents.
"""

import logging
import shutil
import sqlite3
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.skills import SHARED_SKILLS_DIR
from src.api.user import get_current_user
from src.immune.registry_sync import (
    RegistrySyncError,
    download_skill_payload,
    fetch_registry_index,
    plan_registry_entries,
)
from src.immune.skill_guard import (
    SkillSecurityError,
    make_staging_dir,
    promote_staging,
    validate_skill_name,
)

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
    # 迁移：sync失败可见（mem0 §1.1禁止静默假成功）+ registry供应链元数据（kilocode origin钉死）
    for ddl in (
        "ALTER TABLE skill_sources ADD COLUMN last_sync_error TEXT",
        "ALTER TABLE marketplace_skills ADD COLUMN origin TEXT DEFAULT ''",
        "ALTER TABLE marketplace_skills ADD COLUMN download_url TEXT DEFAULT ''",
        "ALTER TABLE marketplace_skills ADD COLUMN security_status TEXT DEFAULT ''",
    ):
        try:
            db.execute(ddl)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise
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
    """List all configured skill sources

    含last_sync_error（mem0 §1.1失败必须可见：列表契约也带失败原因，
    前端marketplace页无需额外请求即可看到"哪个源同步失败、为什么"）。
    """
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
                "last_sync_error": r[13] if len(r) > 13 else None,
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
    """Registry真实同步 — kilocode discovery.ts管线（替换"Simulate sync"占位）。

    index.json真实拉取→逐skill安全计划（name安全段/registry内相对路径逃逸/
    download_url origin钉死在index源）→accepted入库marketplace_skills（含origin
    +security_status），rejected带typed reason随响应返回；
    拉取失败→last_sync_error落库+success=False可见（mem0 §1.1：失败必须可见，
    禁止只更新last_sync时间戳的静默假成功）。
    """
    db = get_marketplace_db()
    existing = db.execute("SELECT * FROM skill_sources WHERE id = ?", (source_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")
    src_type, src_url = existing[2], existing[3]

    try:
        index = fetch_registry_index(src_url, src_type)
    except RegistrySyncError as e:
        db.execute(
            "UPDATE skill_sources SET last_sync = datetime('now'), last_sync_error = ? WHERE id = ?",
            (f"{e.reason}: {e.detail}"[:500], source_id),
        )
        db.commit()
        return {
            "success": False,
            "source_id": source_id,
            "message": f"Sync failed: {e.reason}",
            "error": {"reason": e.reason, "detail": e.detail[:500]},
        }

    accepted, rejected = plan_registry_entries(index)
    for planned in accepted:
        e = planned.entry
        db.execute(
            """
            INSERT INTO marketplace_skills
                (source_id, skill_id, name, description, category, version, origin, security_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted')
            ON CONFLICT(source_id, skill_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                category = excluded.category,
                version = excluded.version,
                origin = excluded.origin,
                security_status = excluded.security_status
            """,
            (source_id, e.name, e.name, e.description, e.category, e.version, planned.origin),
        )
    db.execute(
        "UPDATE skill_sources SET last_sync = datetime('now'), skill_count = ?, last_sync_error = NULL WHERE id = ?",
        (len(accepted), source_id),
    )
    db.commit()
    return {
        "success": True,
        "source_id": source_id,
        "message": f"Synced {len(accepted)} skills from {src_url}",
        "fetched": len(index.entries),
        "accepted": len(accepted),
        "rejected": rejected,
        "skills": [p.entry.name for p in accepted],
        "index_base": index.base_url,
        "guard": "skill_guard/registry_sync (kilocode discovery.ts)",
    }


class MarketplaceInstallRequest(BaseModel):
    force: bool = False  # 换origin源安装须显式force（kilocode origin钉死）


@router.post("/skills/{source_id}/{skill_id}/install")
async def marketplace_install_skill(
    source_id: str,
    skill_id: str,
    body: MarketplaceInstallRequest | None = None,
    user_id: UUID = Depends(get_current_user),
):
    """从registry安装skill — skill_guard供应链防御完整管线（本轮新接线）。

    kilocode discovery.ts：index安全计划重跑→registry内下载（origin钉死index源）
    →staging→promote_staging（security_plan→origin清单→原子swap）晋升shared目录。
    任何一关不过=staging清理、live不动；同名skill换registry源=origin_mismatch
    拒绝（除非显式force）——"同skill换源=供应链攻击信号"。
    """
    db = get_marketplace_db()
    row = db.execute(
        """
        SELECT s.skill_id, s.name, s.origin, src.url, src.type
        FROM marketplace_skills s
        JOIN skill_sources src ON s.source_id = src.id
        WHERE s.source_id = ? AND (s.skill_id = ? OR s.name = ?)
        """,
        (source_id, skill_id, skill_id),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404, detail="Skill not found in marketplace (run sync first)"
        )
    _, skill_name, pinned_origin, src_url, src_type = row
    force = bool(body.force) if body else False

    try:
        validate_skill_name(skill_name)
    except SkillSecurityError as e:
        return {"success": False, "error": f"skill_guard: {e.reason} — {e.detail}"}

    # index重新拉取+安全计划重跑（入库后registry内容可能已被篡改，不信入库快照）
    try:
        index = fetch_registry_index(src_url, src_type)
    except RegistrySyncError as e:
        return {"success": False, "error": f"registry fetch failed: {e.reason} — {e.detail}"}
    accepted, _rejected = plan_registry_entries(index)
    planned = next((p for p in accepted if p.entry.name == skill_name), None)
    if planned is None:
        return {
            "success": False,
            "error": "skill未通过registry安全计划（name/路径/origin校验拒绝）",
        }
    if pinned_origin and planned.origin != pinned_origin and not force:
        return {
            "success": False,
            "error": (
                f"skill_guard: origin_mismatch — 入库origin={pinned_origin!r} "
                f"本次={planned.origin!r}（换源须显式force）"
            ),
        }

    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    staging = make_staging_dir(SHARED_SKILLS_DIR, skill_name)
    try:
        payload = download_skill_payload(planned, index, staging)
    except RegistrySyncError as e:
        shutil.rmtree(staging, ignore_errors=True)
        return {"success": False, "error": f"registry download failed: {e.reason} — {e.detail}"}

    result = promote_staging(
        payload,
        SHARED_SKILLS_DIR,
        origin=planned.origin,
        source_type=f"registry:{src_type}",
        force=force,
        version=planned.entry.version,
    )
    shutil.rmtree(staging, ignore_errors=True)  # 容器清理（负载已被rename走或失败被清）
    if not result.success:
        return {"success": False, "error": f"skill_guard拒绝: {result.errors}"}
    db.execute(
        "UPDATE marketplace_skills SET installed = 1, origin = ? WHERE source_id = ? AND skill_id = ?",
        (planned.origin, source_id, row[0]),
    )
    db.commit()
    return {
        "success": True,
        "skill": skill_name,
        "origin": planned.origin,
        "swapped": result.swapped,
        "skipped": result.skipped,
        "fingerprint": result.fingerprint,
        "guard": "skill_guard (origin钉死+staging+原子swap)",
    }


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
    """Get all available skills from enabled sources (OpenMate polls this)

    字段映射修复：原实现r[2..10]整体错位一位（name→description...r[10]越界
    IndexError）——同步一旦有数据，前端skills页marketplace列表必然500。
    现按SELECT列序精确映射，并补origin/security_status/install上下文供
    前端安装走marketplace安全管线。
    """
    db = get_marketplace_db()
    rows = db.execute("""
        SELECT s.skill_id, s.name, s.description, s.category, s.version, s.downloads, s.rating,
               s.source_id, src.name as source_name, src.type as source_type,
               s.origin, s.security_status, s.installed
        FROM marketplace_skills s
        JOIN skill_sources src ON s.source_id = src.id
        WHERE src.enabled = 1
        ORDER BY s.downloads DESC, s.name
    """).fetchall()

    skills = []
    for r in rows:
        skills.append(
            {
                "skill_id": r[0],
                "id": r[0],
                "name": r[1],
                "description": r[2],
                "category": r[3],
                "version": r[4],
                "downloads": r[5],
                "rating": r[6],
                "source_id": r[7],
                "source_name": r[8],
                "source_type": r[9],
                "origin": r[10],
                "security_status": r[11],
                "installed": bool(r[12]),
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
        skill_sources = db.execute(
            "SELECT COUNT(*) FROM skill_sources WHERE enabled = 1"
        ).fetchone()[0]
        agent_sources = db.execute(
            "SELECT COUNT(*) FROM agent_sources WHERE enabled = 1"
        ).fetchone()[0]
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
