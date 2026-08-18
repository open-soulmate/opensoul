"""OpenCapture API — Browser extension capture endpoints.

Receives page/selection captures from the OpenMate browser extension
and stores them in the knowledge base via OpenSoul.
"""

import hashlib
import json
import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.database.postgres import db_pool

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Pydantic models ──────────────────────────────────────────────


class PageCapture(BaseModel):
    title: str
    url: str
    description: str = ""
    keywords: list[str] = []
    content: str = ""
    html: str = ""


class SelectionCapture(BaseModel):
    text: str
    url: str = ""
    title: str = ""
    context: str = ""


class CaptureResponse(BaseModel):
    id: str
    type: str
    title: str
    url: str
    created_at: float
    status: str


# ─── Storage helpers ──────────────────────────────────────────────


async def _ensure_table():
    await db_pool.execute(
        """CREATE TABLE IF NOT EXISTS captures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            capture_type  TEXT    NOT NULL,
            title         TEXT    NOT NULL DEFAULT '',
            url           TEXT    NOT NULL DEFAULT '',
            description   TEXT    NOT NULL DEFAULT '',
            keywords      TEXT    NOT NULL DEFAULT '[]',
            content       TEXT    NOT NULL DEFAULT '',
            content_hash  TEXT    NOT NULL DEFAULT '',
            status        TEXT    NOT NULL DEFAULT 'captured',
            created_at    REAL    NOT NULL,
            user_id       TEXT    NOT NULL DEFAULT 'browser-extension'
        )"""
    )


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "keywords" in d:
        import json

        try:
            d["keywords"] = json.loads(d["keywords"])
        except (json.JSONDecodeError, TypeError):
            d["keywords"] = []
    return d


# ─── Endpoints ─────────────────────────────────────────────────────


@router.get("/health")
async def capture_health():
    """OpenCapture health check."""
    await _ensure_table()
    rows = await db_pool.fetch("SELECT COUNT(*) as cnt FROM captures")
    total = (rows[0]["cnt"] if rows else 0) if rows else 0
    pages = await db_pool.fetch("SELECT COUNT(*) as cnt FROM captures WHERE capture_type = 'page'")
    selections = await db_pool.fetch(
        "SELECT COUNT(*) as cnt FROM captures WHERE capture_type = 'selection'"
    )
    return {
        "status": "ok",
        "component": "OpenCapture",
        "total_captures": total,
        "page_captures": (pages[0]["cnt"] if pages else 0) if pages else 0,
        "selection_captures": (selections[0]["cnt"] if selections else 0) if selections else 0,
    }


@router.get("/stats")
async def capture_stats():
    """Get capture statistics."""
    await _ensure_table()
    try:
        total = await db_pool.fetchval("SELECT COUNT(*) FROM captures") or 0
        pages = (
            await db_pool.fetchval("SELECT COUNT(*) FROM captures WHERE capture_type = 'page'") or 0
        )
        selections = (
            await db_pool.fetchval("SELECT COUNT(*) FROM captures WHERE capture_type = 'selection'")
            or 0
        )
        recent = (
            await db_pool.fetchval(
                "SELECT COUNT(*) FROM captures WHERE created_at > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours')"
            )
            or 0
        )
        return {
            "status": "ok",
            "component": "OpenCapture",
            "total_captures": total,
            "page_captures": pages,
            "selection_captures": selections,
            "recent_24h": recent,
        }
    except Exception:
        return {
            "status": "ok",
            "component": "OpenCapture",
            "total_captures": 0,
            "page_captures": 0,
            "selection_captures": 0,
            "recent_24h": 0,
        }


@router.post("/page", response_model=CaptureResponse)
async def capture_page(req: PageCapture):
    """Capture a full page from the browser extension."""
    await _ensure_table()

    content = req.content or req.description or req.title
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    now = time.time()

    # Check for duplicate
    existing = await db_pool.fetchrow(
        "SELECT id FROM captures WHERE url = $1 AND content_hash = $2",
        req.url,
        content_hash,
    )
    if existing:
        logger.info(f"Duplicate page capture for {req.url}, returning existing")
        return CaptureResponse(
            id=str(existing["id"]),
            type="page",
            title=req.title,
            url=req.url,
            created_at=now,
            status="duplicate",
        )

    import json

    row = await db_pool.fetchrow(
        """INSERT INTO captures (capture_type, title, url, description, keywords, content, content_hash, created_at)
           VALUES ('page', $1, $2, $3, $4, $5, $6, $7) RETURNING *""",
        req.title,
        req.url,
        req.description,
        json.dumps(req.keywords),
        content,
        content_hash,
        now,
    )

    logger.info(f"Captured page: {req.title} ({req.url})")
    return CaptureResponse(
        id=str(row["id"]),
        type="page",
        title=req.title,
        url=req.url,
        created_at=now,
        status="captured",
    )


@router.post("/selection", response_model=CaptureResponse)
async def capture_selection(req: SelectionCapture):
    """Capture selected text from the browser extension."""
    await _ensure_table()

    content_hash = hashlib.sha256(req.text.encode()).hexdigest()[:16]
    now = time.time()

    # Check for duplicate
    existing = await db_pool.fetchrow(
        "SELECT id FROM captures WHERE url = $1 AND content_hash = $2",
        req.url,
        content_hash,
    )
    if existing:
        logger.info(f"Duplicate selection capture for {req.url}")
        return CaptureResponse(
            id=str(existing["id"]),
            type="selection",
            title=req.title or "Selected Text",
            url=req.url,
            created_at=now,
            status="duplicate",
        )

    row = await db_pool.fetchrow(
        """INSERT INTO captures (capture_type, title, url, content, content_hash, created_at)
           VALUES ('selection', $1, $2, $3, $4, $5) RETURNING *""",
        req.title or "Selected Text",
        req.url,
        req.text,
        content_hash,
        now,
    )

    logger.info(f"Captured selection: {len(req.text)} chars from {req.url}")
    return CaptureResponse(
        id=str(row["id"]),
        type="selection",
        title=req.title or "Selected Text",
        url=req.url,
        created_at=now,
        status="captured",
    )


@router.get("/list")
async def list_captures(
    limit: int = 50,
    offset: int = 0,
    capture_type: str = "",
):
    """List all captures with pagination."""
    await _ensure_table()

    if capture_type:
        rows = await db_pool.fetch(
            "SELECT * FROM captures WHERE capture_type = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            capture_type,
            limit,
            offset,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT * FROM captures ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )

    count_rows = await db_pool.fetch("SELECT COUNT(*) as cnt FROM captures")
    total = count_rows[0]["cnt"] if count_rows else 0

    return {
        "captures": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{capture_id}")
async def get_capture(capture_id: int):
    """Get a single capture by ID."""
    await _ensure_table()
    row = await db_pool.fetchrow("SELECT * FROM captures WHERE id = $1", capture_id)
    if not row:
        raise HTTPException(status_code=404, detail="Capture not found")
    return _row_to_dict(row)


@router.delete("/{capture_id}")
async def delete_capture(capture_id: int):
    """Delete a capture."""
    await _ensure_table()
    result = await db_pool.execute("DELETE FROM captures WHERE id = $1", capture_id)
    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"deleted": True, "id": capture_id}


@router.post("/{capture_id}/promote")
async def promote_to_knowledge(capture_id: int, user_id: str = "default"):
    """Promote a capture to the knowledge base."""
    await _ensure_table()
    row = await db_pool.fetchrow("SELECT * FROM captures WHERE id = $1", capture_id)
    if not row:
        raise HTTPException(status_code=404, detail="Capture not found")

    capture = dict(row)

    # Insert into knowledge base
    try:
        import uuid as _uuid

        knowledge_id = str(_uuid.uuid4())
        tags = ["capture", capture["capture_type"], "browser"]
        await db_pool.execute(
            """INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            knowledge_id,
            user_id,
            capture["title"] or f"Captured from {capture['url']}",
            capture["content"],
            f"capture://{capture_id}",
            "text/html",
            json.dumps(
                {"tags": tags, "url": capture["url"], "capture_type": capture["capture_type"]}
            ),
            time.time(),
            time.time(),
        )
        # Add tags to knowledge_tags table
        for tag_name in tags:
            tag_id = str(_uuid.uuid4())
            await db_pool.execute(
                "INSERT INTO tags (id, name, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                tag_id,
                tag_name,
                user_id,
            )
            tag_row = await db_pool.fetchrow(
                "SELECT id FROM tags WHERE name = $1 AND user_id = $2", tag_name, user_id
            )
            if tag_row:
                await db_pool.execute(
                    "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    knowledge_id,
                    tag_row["id"],
                )
    except Exception as e:
        # Fallback: table might not exist or schema differs
        logger.warning(f"Could not promote to knowledge: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to promote: {e}")

    # Update capture status
    await db_pool.execute("UPDATE captures SET status = 'promoted' WHERE id = $1", capture_id)

    return {"promoted": True, "id": capture_id, "user_id": user_id}
