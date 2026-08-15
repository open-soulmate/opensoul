"""Sessions API — unified session management.

Provides session search, delete, and message retrieval at /api/sessions.
Reads directly from the Hermes state SQLite database.
"""

from __future__ import annotations

import os
import sqlite3
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.user import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

_DB_PATH = os.path.expanduser("~/.hermes/state.db")


def _get_db():
    """Get a connection to the Hermes state database."""
    if not os.path.exists(_DB_PATH):
        return None
    db = sqlite3.connect(_DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _ts_to_iso(ts):
    """Convert a unix timestamp (REAL) to ISO string."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return str(ts)


@router.get("")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List recent sessions (no auth required for dashboard)."""
    db = _get_db()
    if not db:
        return {"sessions": [], "total": 0}

    try:
        rows = db.execute("""
            SELECT id, title, started_at, last_activity_at, source,
                   message_count, input_tokens, output_tokens
            FROM sessions
            WHERE archived = 0
            ORDER BY last_activity_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        count_row = db.execute("SELECT COUNT(*) as cnt FROM sessions WHERE archived = 0").fetchone()
        total = count_row["cnt"] if count_row else 0

        sessions = []
        for r in rows:
            sessions.append({
                "id": r["id"],
                "title": r["title"] or "Untitled",
                "created_at": _ts_to_iso(r["started_at"]),
                "updated_at": _ts_to_iso(r["last_activity_at"]),
                "source": r["source"],
                "message_count": r["message_count"] or 0,
                "input_tokens": r["input_tokens"] or 0,
                "output_tokens": r["output_tokens"] or 0,
            })

        return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error("list_sessions error: %s", e)
        return {"sessions": [], "error": str(e)}
    finally:
        db.close()


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
        title_matches = db.execute("""
            SELECT DISTINCT s.id, s.title, s.started_at, s.last_activity_at, s.source
            FROM sessions s
            WHERE s.archived = 0 AND s.title LIKE ?
            ORDER BY s.started_at DESC
            LIMIT 20
        """, (f"%{q}%",)).fetchall()

        # Search message content
        msg_matches = db.execute("""
            SELECT DISTINCT m.session_id, s.title, s.started_at, s.last_activity_at, s.source,
                   m.content as matched_content
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE s.archived = 0 AND m.active = 1 AND m.content LIKE ?
            ORDER BY s.started_at DESC
            LIMIT 20
        """, (f"%{q}%",)).fetchall()

        # Merge results, dedup by session_id
        seen = set()
        results = []

        for r in title_matches:
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append({
                    "id": r["id"], "title": r["title"],
                    "created_at": _ts_to_iso(r["started_at"]),
                    "updated_at": _ts_to_iso(r["last_activity_at"]),
                    "source": r["source"], "match_type": "title",
                    "snippet": None,
                })

        for r in msg_matches:
            if r["session_id"] not in seen:
                seen.add(r["session_id"])
                content = r["matched_content"] or ""
                idx = content.lower().find(q.lower())
                start = max(0, idx - 40)
                end = min(len(content), idx + len(q) + 40)
                snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")

                results.append({
                    "id": r["session_id"], "title": r["title"],
                    "created_at": _ts_to_iso(r["started_at"]),
                    "updated_at": _ts_to_iso(r["last_activity_at"]),
                    "source": r["source"], "match_type": "content",
                    "snippet": snippet,
                })

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
    """Get session details."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=404, detail="Session database not found")

    try:
        row = db.execute("""
            SELECT id, title, started_at, last_activity_at, source,
                   message_count, input_tokens, output_tokens, estimated_cost_usd
            FROM sessions WHERE id = ? AND archived = 0
        """, (session_id,)).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_session error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Delete a session and all its messages."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=404, detail="Session database not found")

    try:
        row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

        # Delete messages first
        db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        # Delete session
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        # Try to delete from session_lineage too
        try:
            db.execute("DELETE FROM session_lineage WHERE session_id = ?", (session_id,))
        except Exception:
            pass
        db.commit()

        return {"success": True, "deleted_session": session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_session error: %s", e)
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Get messages for a session."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=404, detail="Session database not found")

    try:
        rows = db.execute("""
            SELECT id, role, content, tool_calls, tool_name, timestamp
            FROM messages
            WHERE session_id = ? AND active = 1 AND compacted = 0
            ORDER BY id
        """, (session_id,)).fetchall()

        messages = []
        for r in rows:
            role = r["role"]
            content = r["content"] or ""
            # Skip tool messages and empty assistant messages
            if role == "tool":
                continue
            if role == "assistant" and not content.strip():
                continue
            messages.append({
                "id": str(r["id"]),
                "role": role,
                "content": content,
                "timestamp": _ts_to_iso(r["timestamp"]),
                "source": "hermes-db",
            })

        return {"messages": messages, "total": len(messages)}
    except Exception as e:
        logger.error("get_session_messages error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
