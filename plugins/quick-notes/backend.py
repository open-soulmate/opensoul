"""Quick Notes plugin — markdown note-taking with knowledge base promotion."""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

DATA_DIR = Path.home() / ".openmate" / "plugins" / "quick-notes" / "data"
DB_PATH = DATA_DIR / "notes.db"


def _get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_notes_updated
        ON notes(updated_at DESC)
    """)
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["pinned"] = bool(d.get("pinned", 0))
    try:
        d["tags"] = json.loads(d.get("tags", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    return d


# ── Models ────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    title: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    pinned: bool | None = None


class PromoteRequest(BaseModel):
    knowledge_base: str = "default"
    folder: str = "quick-notes"


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/notes")
async def list_notes(
    tag: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """List notes with optional tag filter and search."""
    conn = _get_db()
    try:
        conditions = []
        params: list = []

        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if q:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT id, title, content, tags, pinned, created_at, updated_at
            FROM notes {where}
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM notes {where}", params[:-2]).fetchone()[0]

        return {
            "notes": [_row_to_dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


@router.post("/notes", status_code=201)
async def create_note(req: NoteCreate):
    """Create a new note."""
    conn = _get_db()
    try:
        note_id = uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO notes (id, title, content, tags, pinned, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (note_id, req.title, req.content, json.dumps(req.tags), int(req.pinned), now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


@router.get("/notes/{note_id}")
async def get_note(note_id: str):
    """Get a single note."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
        return _row_to_dict(row)
    finally:
        conn.close()


@router.put("/notes/{note_id}")
async def update_note(note_id: str, req: NoteUpdate):
    """Update a note."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")

        updates = []
        params: list = []
        if req.title is not None:
            updates.append("title = ?")
            params.append(req.title)
        if req.content is not None:
            updates.append("content = ?")
            params.append(req.content)
        if req.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(req.tags))
        if req.pinned is not None:
            updates.append("pinned = ?")
            params.append(int(req.pinned))

        if not updates:
            return _row_to_dict(row)

        now = datetime.now(timezone.utc).isoformat()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(note_id)

        conn.execute(f"UPDATE notes SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str):
    """Delete a note."""
    conn = _get_db()
    try:
        result = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(404, "Note not found")
        return {"status": "deleted", "id": note_id}
    finally:
        conn.close()


@router.post("/notes/{note_id}/promote")
async def promote_to_knowledge(note_id: str, req: PromoteRequest = PromoteRequest()):
    """Promote a note to the OpenSoul knowledge base."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
    finally:
        conn.close()

    note = _row_to_dict(row)

    # Call OpenSoul knowledge API to ingest the note
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "http://127.0.0.1:8090/api/knowledge/ingest",
                json={
                    "title": note["title"] or f"Quick Note {note['id']}",
                    "content": note["content"],
                    "source": "quick-notes",
                    "tags": note["tags"],
                    "metadata": {
                        "note_id": note["id"],
                        "promoted_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
            )
            if resp.status_code in (200, 201):
                return {
                    "status": "promoted",
                    "note_id": note_id,
                    "knowledge": resp.json(),
                }
            else:
                return {
                    "status": "partial",
                    "note_id": note_id,
                    "error": f"Knowledge API returned {resp.status_code}",
                }
    except Exception as e:
        return {
            "status": "partial",
            "note_id": note_id,
            "error": str(e),
        }


@router.get("/tags")
async def list_tags():
    """Get all unique tags across notes."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT tags FROM notes").fetchall()
        all_tags: set[str] = set()
        for row in rows:
            try:
                tags = json.loads(row["tags"])
                all_tags.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
        return {"tags": sorted(all_tags), "count": len(all_tags)}
    finally:
        conn.close()


@router.get("/stats")
async def note_stats():
    """Get note statistics."""
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        pinned = conn.execute("SELECT COUNT(*) FROM notes WHERE pinned = 1").fetchone()[0]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE created_at LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]
        return {
            "total": total,
            "pinned": pinned,
            "created_today": today_count,
        }
    finally:
        conn.close()


@router.get("/health")
async def health():
    """Quick Notes health check."""
    try:
        conn = _get_db()
        count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        conn.close()
        return {
            "status": "ok",
            "component": "quick-notes",
            "notes_count": count,
            "db_path": str(DB_PATH),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
