"""Sessions API — unified session management.

Provides session search, delete, and message retrieval at /api/sessions.
Reads from both Hermes state SQLite database and OpenSoul agent_sessions.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.user import get_current_user

router = APIRouter()
@router.get("/health")
async def sessions_api_health():
    """SessionsAPI health check."""
    return {"status": "ok", "component": "SessionsAPI"}
logger = logging.getLogger(__name__)

_DB_PATH = os.path.expanduser("~/.hermes/state.db")
_OPENSOUL_DB = "/home/climbing/opensoul/data/opensoul.db"


def _get_db():
    """Get a connection to the Hermes state database."""
    if not os.path.exists(_DB_PATH):
        return None
    db = sqlite3.connect(_DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _get_agent_db():
    """Get a connection to the OpenSoul database for agent sessions."""
    if not os.path.exists(_OPENSOUL_DB):
        return None
    db = sqlite3.connect(_OPENSOUL_DB)
    db.row_factory = sqlite3.Row
    return db


def _ensure_session_tags_table(db: sqlite3.Connection):
    """Create session_tags table if it doesn't exist."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS session_tags (
            session_id TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (session_id, tag_name)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_tags_name ON session_tags(tag_name)"
    )
    db.commit()


def _get_session_tags_map(db: sqlite3.Connection, session_ids: list[str]) -> dict[str, list[str]]:
    """Get tags for multiple sessions in one query. Returns {session_id: [tag_names]}."""
    if not session_ids:
        return {}
    _ensure_session_tags_table(db)
    placeholders = ",".join("?" for _ in session_ids)
    rows = db.execute(
        f"SELECT session_id, tag_name FROM session_tags WHERE session_id IN ({placeholders}) ORDER BY tag_name",
        session_ids,
    ).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["session_id"], []).append(r["tag_name"])
    return result


def _ts_to_iso(ts):
    """Convert a unix timestamp (REAL) to ISO string."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (ValueError, OSError):
        return str(ts)


def _get_agent_sessions(limit: int = 100, offset: int = 0) -> list[dict]:
    """Get agent proxy sessions from OpenSoul DB."""
    adb = _get_agent_db()
    if not adb:
        return []
    try:
        rows = adb.execute(
            """
            SELECT id, agent_id, title, created_at, last_activity_at, message_count
            FROM agent_sessions
            WHERE archived = 0
            ORDER BY last_activity_at DESC
            LIMIT ? OFFSET ?
        """,
            (limit, offset),
        ).fetchall()
        sessions = []
        for r in rows:
            sessions.append(
                {
                    "id": r["id"],
                    "title": r["title"] or "Untitled",
                    "created_at": _ts_to_iso(r["created_at"]),
                    "updated_at": _ts_to_iso(r["last_activity_at"]),
                    "source": r["agent_id"],  # agent_id as source for frontend grouping
                    "message_count": r["message_count"] or 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            )
        return sessions
    except Exception as e:
        logger.error("get_agent_sessions error: %s", e)
        return []
    finally:
        adb.close()


@router.get("")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tag: str = Query(default=None, description="Filter sessions by tag name"),
):
    """List recent sessions — merges Hermes sessions and agent proxy sessions."""
    all_sessions = []

    # 1. Hermes sessions
    db = _get_db()
    if db:
        try:
            rows = db.execute(
                """
                SELECT id, title, started_at, last_activity_at, source,
                       message_count, input_tokens, output_tokens
                FROM sessions
                WHERE archived = 0
                ORDER BY last_activity_at DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            ).fetchall()
            for r in rows:
                all_sessions.append(
                    {
                        "id": r["id"],
                        "title": r["title"] or "Untitled",
                        "created_at": _ts_to_iso(r["started_at"]),
                        "updated_at": _ts_to_iso(r["last_activity_at"]),
                        "source": r["source"],
                        "message_count": r["message_count"] or 0,
                        "input_tokens": r["input_tokens"] or 0,
                        "output_tokens": r["output_tokens"] or 0,
                    }
                )
        except Exception as e:
            logger.error("list_sessions error: %s", e)
        finally:
            db.close()

    # 2. Agent proxy sessions
    agent_sessions = _get_agent_sessions(limit, offset)
    all_sessions.extend(agent_sessions)

    # 3. Sort by last_activity_at descending
    all_sessions.sort(key=lambda s: s.get("updated_at") or "", reverse=True)

    # 4. Attach tags to each session
    tag_db = _get_agent_db()
    if tag_db:
        try:
            session_ids = [s["id"] for s in all_sessions if s.get("id")]
            tags_map = _get_session_tags_map(tag_db, session_ids)
            for s in all_sessions:
                s["tags"] = tags_map.get(s["id"], [])

            # Filter by tag if requested
            if tag:
                all_sessions = [s for s in all_sessions if tag.lower() in [t.lower() for t in s.get("tags", [])]]
        finally:
            tag_db.close()

    return {"sessions": all_sessions[:limit], "total": len(all_sessions), "limit": limit, "offset": offset}


@router.get("/search")
async def search_sessions(
    q: str = "",
    user_id: UUID = Depends(get_current_user),
):
    """Search sessions by title and message content."""
    if not q:
        return {"sessions": [], "query": q}

    db = _get_db()
    if not db:
        return {"sessions": [], "query": q, "error": "No session database found"}

    try:
        # Search session titles
        title_matches = db.execute(
            """
            SELECT DISTINCT s.id, s.title, s.started_at, s.last_activity_at, s.source
            FROM sessions s
            WHERE s.archived = 0 AND s.title LIKE ?
            ORDER BY s.started_at DESC
            LIMIT 20
        """,
            (f"%{q}%",),
        ).fetchall()

        # Search message content
        msg_matches = db.execute(
            """
            SELECT DISTINCT m.session_id, s.title, s.started_at, s.last_activity_at, s.source,
                   m.content as matched_content
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE s.archived = 0 AND m.active = 1 AND m.content LIKE ?
            ORDER BY s.started_at DESC
            LIMIT 20
        """,
            (f"%{q}%",),
        ).fetchall()

        # Merge results, dedup by session_id
        seen = set()
        results = []

        for r in title_matches:
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(
                    {
                        "id": r["id"],
                        "title": r["title"],
                        "created_at": _ts_to_iso(r["started_at"]),
                        "updated_at": _ts_to_iso(r["last_activity_at"]),
                        "source": r["source"],
                        "match_type": "title",
                        "snippet": None,
                    }
                )

        for r in msg_matches:
            if r["session_id"] not in seen:
                seen.add(r["session_id"])
                content = r["matched_content"] or ""
                idx = content.lower().find(q.lower())
                start = max(0, idx - 40)
                end = min(len(content), idx + len(q) + 40)
                snippet = (
                    ("..." if start > 0 else "")
                    + content[start:end]
                    + ("..." if end < len(content) else "")
                )

                results.append(
                    {
                        "id": r["session_id"],
                        "title": r["title"],
                        "created_at": _ts_to_iso(r["started_at"]),
                        "updated_at": _ts_to_iso(r["last_activity_at"]),
                        "source": r["source"],
                        "match_type": "content",
                        "snippet": snippet,
                    }
                )

        return {"sessions": results, "query": q, "total": len(results)}
    except Exception as e:
        logger.error("search_sessions error: %s", e)
        return {"sessions": [], "error": str(e)}
    finally:
        db.close()


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Get session details — checks both Hermes and agent sessions."""
    # Try Hermes first
    db = _get_db()
    if db:
        try:
            row = db.execute(
                """
                SELECT id, title, started_at, last_activity_at, source,
                       message_count, input_tokens, output_tokens, estimated_cost_usd
                FROM sessions WHERE id = ? AND archived = 0
            """,
                (session_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": _ts_to_iso(row["started_at"]),
                    "updated_at": _ts_to_iso(row["last_activity_at"]),
                    "source": row["source"],
                    "message_count": row["message_count"] or 0,
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "estimated_cost_usd": row["estimated_cost_usd"] or 0,
                }
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            row = adb.execute(
                """
                SELECT id, agent_id, title, created_at, last_activity_at, message_count
                FROM agent_sessions WHERE id = ? AND archived = 0
            """,
                (session_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": _ts_to_iso(row["created_at"]),
                    "updated_at": _ts_to_iso(row["last_activity_at"]),
                    "source": row["agent_id"],
                    "message_count": row["message_count"] or 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0,
                }
        finally:
            adb.close()

    raise HTTPException(status_code=404, detail="Session not found")


@router.patch("/{session_id}")
async def rename_session(
    session_id: str,
    body: dict,
):
    """Rename a session (update title) — works for both Hermes and agent sessions."""
    new_title = body.get("title", "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title is required")

    # Try Hermes first
    db = _get_db()
    if db:
        try:
            row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                db.execute("UPDATE sessions SET title = ? WHERE id = ?", (new_title, session_id))
                db.commit()
                return {"success": True, "id": session_id, "title": new_title}
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            row = adb.execute("SELECT id FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                adb.execute("UPDATE agent_sessions SET title = ? WHERE id = ?", (new_title, session_id))
                adb.commit()
                return {"success": True, "id": session_id, "title": new_title}
        finally:
            adb.close()

    raise HTTPException(status_code=404, detail="Session not found")


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Delete a session and all its messages — works for both Hermes and agent sessions."""
    # Try Hermes first
    db = _get_db()
    if db:
        try:
            row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                try:
                    db.execute("DELETE FROM session_lineage WHERE session_id = ?", (session_id,))
                except Exception as exc:
                    logging.getLogger(__name__).debug("probe skipped: %s", exc)
                db.commit()
                return {"success": True, "deleted_session": session_id}
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            row = adb.execute("SELECT id FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                adb.execute("DELETE FROM agent_messages WHERE session_id = ?", (session_id,))
                adb.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
                adb.commit()
                return {"success": True, "deleted_session": session_id}
        finally:
            adb.close()

    return {"success": False, "error": "Session not found"}


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Get messages for a session — works for both Hermes and agent sessions."""
    # Try Hermes first
    db = _get_db()
    if db:
        try:
            rows = db.execute(
                """
                SELECT id, role, content, tool_calls, tool_name, timestamp
                FROM messages
                WHERE session_id = ? AND active = 1 AND compacted = 0
                ORDER BY id
            """,
                (session_id,),
            ).fetchall()
            if rows:
                messages = []
                for r in rows:
                    role = r["role"]
                    content = r["content"] or ""
                    if role == "tool":
                        continue
                    if role == "assistant" and not content.strip():
                        continue
                    messages.append(
                        {
                            "id": str(r["id"]),
                            "role": role,
                            "content": content,
                            "timestamp": _ts_to_iso(r["timestamp"]),
                            "source": "hermes-db",
                        }
                    )
                return {"messages": messages, "total": len(messages)}
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            rows = adb.execute(
                """
                SELECT id, role, content, timestamp
                FROM agent_messages
                WHERE session_id = ?
                ORDER BY id
            """,
                (session_id,),
            ).fetchall()
            messages = []
            for r in rows:
                messages.append(
                    {
                        "id": str(r["id"]),
                        "role": r["role"],
                        "content": r["content"] or "",
                        "timestamp": _ts_to_iso(r["timestamp"]),
                        "source": "agent-db",
                    }
                )
            return {"messages": messages, "total": len(messages)}
        finally:
            adb.close()

    return {"messages": [], "total": 0}


# ── Session Tags API ──────────────────────────────────────────

class TagRequest(BaseModel):
    tag_name: str


@router.get("/tags/all")
async def list_all_tags():
    """List all unique tag names across all sessions."""
    db = _get_agent_db()
    if not db:
        return {"tags": []}
    try:
        _ensure_session_tags_table(db)
        rows = db.execute(
            """SELECT tag_name, COUNT(*) as count
               FROM session_tags GROUP BY tag_name ORDER BY tag_name"""
        ).fetchall()
        return {"tags": [{"name": r["tag_name"], "count": r["count"]} for r in rows]}
    except Exception as e:
        logger.error("list_all_tags error: %s", e)
        return {"tags": []}
    finally:
        db.close()


@router.post("/{session_id}/tags")
async def add_session_tag(session_id: str, body: TagRequest):
    """Add a tag to a session."""
    tag_name = body.tag_name.strip().lower()
    if not tag_name or len(tag_name) > 50:
        raise HTTPException(400, "Tag name must be 1-50 characters")
    db = _get_agent_db()
    if not db:
        raise HTTPException(500, "Database not available")
    try:
        _ensure_session_tags_table(db)
        db.execute(
            "INSERT OR IGNORE INTO session_tags (session_id, tag_name) VALUES (?, ?)",
            (session_id, tag_name),
        )
        db.commit()
        return {"session_id": session_id, "tag": tag_name, "status": "added"}
    except Exception as e:
        logger.error("add_session_tag error: %s", e)
        raise HTTPException(500, str(e))
    finally:
        db.close()


@router.delete("/{session_id}/tags/{tag_name}")
async def remove_session_tag(session_id: str, tag_name: str):
    """Remove a tag from a session."""
    db = _get_agent_db()
    if not db:
        raise HTTPException(500, "Database not available")
    try:
        _ensure_session_tags_table(db)
        db.execute(
            "DELETE FROM session_tags WHERE session_id = ? AND tag_name = ?",
            (session_id, tag_name.strip().lower()),
        )
        db.commit()
        return {"session_id": session_id, "tag": tag_name, "status": "removed"}
    except Exception as e:
        logger.error("remove_session_tag error: %s", e)
        raise HTTPException(500, str(e))
    finally:
        db.close()


@router.get("/{session_id}/tags")
async def get_session_tags(session_id: str):
    """Get all tags for a specific session."""
    db = _get_agent_db()
    if not db:
        return {"tags": []}
    try:
        _ensure_session_tags_table(db)
        rows = db.execute(
            "SELECT tag_name, created_at FROM session_tags WHERE session_id = ? ORDER BY tag_name",
            (session_id,),
        ).fetchall()
        return {"tags": [{"name": r["tag_name"], "created_at": r["created_at"]} for r in rows]}
    finally:
        db.close()
