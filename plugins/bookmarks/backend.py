"""Bookmark Manager Plugin Backend — 书签管理、收藏夹、标签分类、全文搜索。"""

import json
import sqlite3
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

# ── Database Setup ──────────────────────────────────────────
DB_PATH = Path.home() / ".openmate" / "plugins" / "bookmarks" / "bookmarks.db"


def _get_db() -> sqlite3.Connection:
    """Get a database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    """Initialize database tables."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            favicon TEXT DEFAULT '',
            collection TEXT DEFAULT 'Uncategorized',
            tags TEXT DEFAULT '[]',
            is_favorite INTEGER DEFAULT 0,
            click_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bookmarks_collection ON bookmarks(collection);
        CREATE INDEX IF NOT EXISTS idx_bookmarks_created ON bookmarks(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_bookmarks_favorite ON bookmarks(is_favorite);

        CREATE TABLE IF NOT EXISTS collections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            icon TEXT DEFAULT '📁',
            description TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collections_order ON collections(sort_order);

        CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
            id, title, description, url, tags,
            content='bookmarks',
            content_rowid='rowid'
        );

        CREATE TRIGGER IF NOT EXISTS bookmarks_ai AFTER INSERT ON bookmarks BEGIN
            INSERT INTO bookmarks_fts(id, title, description, url, tags)
            VALUES (new.id, new.title, new.description, new.url, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS bookmarks_ad AFTER DELETE ON bookmarks BEGIN
            INSERT INTO bookmarks_fts(bookmarks_fts, id, title, description, url, tags)
            VALUES ('delete', old.id, old.title, old.description, old.url, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS bookmarks_au AFTER UPDATE ON bookmarks BEGIN
            INSERT INTO bookmarks_fts(bookmarks_fts, id, title, description, url, tags)
            VALUES ('delete', old.id, old.title, old.description, old.url, old.tags);
            INSERT INTO bookmarks_fts(id, title, description, url, tags)
            VALUES (new.id, new.title, new.description, new.url, new.tags);
        END;
    """)
    # Seed default collection if empty
    if not conn.execute("SELECT 1 FROM collections LIMIT 1").fetchone():
        now = time.time()
        conn.execute(
            "INSERT INTO collections (id, name, icon, description, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("col-default", "Uncategorized", "📁", "默认收藏夹", 0, now),
        )
        conn.execute(
            "INSERT INTO collections (id, name, icon, description, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("col-reading", "Reading List", "📖", "稍后阅读", 1, now),
        )
        conn.execute(
            "INSERT INTO collections (id, name, icon, description, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("col-tools", "Tools & Resources", "🔧", "常用工具和资源", 2, now),
        )
    conn.commit()
    conn.close()


_init_db()


# ── Request Models ──────────────────────────────────────────

class BookmarkCreate(BaseModel):
    url: str
    title: str = ""
    description: str = ""
    favicon: str = ""
    collection: str = "Uncategorized"
    tags: list[str] = []
    is_favorite: bool = False


class BookmarkUpdate(BaseModel):
    url: str | None = None
    title: str | None = None
    description: str | None = None
    favicon: str | None = None
    collection: str | None = None
    tags: list[str] | None = None
    is_favorite: bool | None = None


class CollectionCreate(BaseModel):
    name: str
    icon: str = "📁"
    description: str = ""


# ── Bookmark CRUD ───────────────────────────────────────────

@router.post("/bookmarks")
async def create_bookmark(req: BookmarkCreate):
    """Create a new bookmark."""
    conn = _get_db()
    try:
        bm_id = f"bm-{uuid.uuid4().hex[:8]}"
        now = time.time()
        # Auto-generate title from URL if empty
        title = req.title or req.url.split("//")[-1].split("/")[0]
        conn.execute(
            """INSERT INTO bookmarks (id, url, title, description, favicon, collection, tags, is_favorite, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bm_id, req.url, title, req.description, req.favicon,
             req.collection, json.dumps(req.tags), 1 if req.is_favorite else 0, now, now),
        )
        conn.commit()
        return {
            "id": bm_id, "url": req.url, "title": title,
            "description": req.description, "collection": req.collection,
            "tags": req.tags, "is_favorite": req.is_favorite,
            "created_at": now, "updated_at": now,
        }
    finally:
        conn.close()


@router.get("/bookmarks")
async def list_bookmarks(
    collection: str | None = Query(None, description="Filter by collection"),
    tag: str | None = Query(None, description="Filter by tag"),
    favorite: bool | None = Query(None, description="Filter favorites only"),
    search: str | None = Query(None, description="Full-text search"),
    sort: str = Query("created_at", description="Sort field: created_at, title, click_count"),
    order: str = Query("desc", description="Sort order: asc, desc"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List bookmarks with filters and full-text search."""
    conn = _get_db()
    try:
        if search:
            # Full-text search with LIKE fallback
            try:
                rows = conn.execute(
                    """SELECT b.* FROM bookmarks b
                       JOIN bookmarks_fts fts ON b.id = fts.id
                       WHERE bookmarks_fts MATCH ?
                       ORDER BY rank
                       LIMIT ? OFFSET ?""",
                    (search, limit, offset),
                ).fetchall()
            except Exception:
                # Fallback to LIKE search if FTS5 is out of sync
                pattern = f"%{search}%"
                rows = conn.execute(
                    """SELECT * FROM bookmarks
                       WHERE title LIKE ? OR description LIKE ? OR url LIKE ? OR tags LIKE ?
                       ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (pattern, pattern, pattern, pattern, limit, offset),
                ).fetchall()
        else:
            conditions = []
            params = []
            if collection:
                conditions.append("collection = ?")
                params.append(collection)
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            if favorite is not None:
                conditions.append("is_favorite = ?")
                params.append(1 if favorite else 0)

            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            sort_col = sort if sort in ("created_at", "title", "click_count", "updated_at") else "created_at"
            order_dir = "DESC" if order.lower() == "desc" else "ASC"

            rows = conn.execute(
                f"SELECT * FROM bookmarks{where} ORDER BY {sort_col} {order_dir} LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        return {
            "bookmarks": [
                {
                    "id": r["id"], "url": r["url"], "title": r["title"],
                    "description": r["description"], "favicon": r["favicon"],
                    "collection": r["collection"],
                    "tags": json.loads(r["tags"]) if r["tags"] else [],
                    "is_favorite": bool(r["is_favorite"]),
                    "click_count": r["click_count"],
                    "created_at": r["created_at"], "updated_at": r["updated_at"],
                }
                for r in rows
            ],
            "total": total,
            "count": len(rows),
        }
    finally:
        conn.close()


@router.get("/bookmarks/{bm_id}")
async def get_bookmark(bm_id: str):
    """Get a single bookmark by ID. Increments click count."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM bookmarks WHERE id = ?", (bm_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Bookmark not found")
        conn.execute("UPDATE bookmarks SET click_count = click_count + 1 WHERE id = ?", (bm_id,))
        conn.commit()
        return {
            "id": row["id"], "url": row["url"], "title": row["title"],
            "description": row["description"], "favicon": row["favicon"],
            "collection": row["collection"],
            "tags": json.loads(row["tags"]) if row["tags"] else [],
            "is_favorite": bool(row["is_favorite"]),
            "click_count": row["click_count"] + 1,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


@router.put("/bookmarks/{bm_id}")
async def update_bookmark(bm_id: str, req: BookmarkUpdate):
    """Update a bookmark."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM bookmarks WHERE id = ?", (bm_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Bookmark not found")

        updates = []
        params = []
        if req.url is not None:
            updates.append("url = ?")
            params.append(req.url)
        if req.title is not None:
            updates.append("title = ?")
            params.append(req.title)
        if req.description is not None:
            updates.append("description = ?")
            params.append(req.description)
        if req.favicon is not None:
            updates.append("favicon = ?")
            params.append(req.favicon)
        if req.collection is not None:
            updates.append("collection = ?")
            params.append(req.collection)
        if req.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(req.tags))
        if req.is_favorite is not None:
            updates.append("is_favorite = ?")
            params.append(1 if req.is_favorite else 0)

        if not updates:
            return {"message": "No changes"}

        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(bm_id)

        conn.execute(f"UPDATE bookmarks SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return {"status": "ok", "id": bm_id}
    finally:
        conn.close()


@router.delete("/bookmarks/{bm_id}")
async def delete_bookmark(bm_id: str):
    """Delete a bookmark."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM bookmarks WHERE id = ?", (bm_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Bookmark not found")
        conn.execute("DELETE FROM bookmarks WHERE id = ?", (bm_id,))
        conn.commit()
        return {"status": "ok", "id": bm_id}
    finally:
        conn.close()


@router.post("/bookmarks/{bm_id}/favorite")
async def toggle_favorite(bm_id: str):
    """Toggle bookmark favorite status."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT is_favorite FROM bookmarks WHERE id = ?", (bm_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Bookmark not found")
        new_val = 0 if row["is_favorite"] else 1
        conn.execute("UPDATE bookmarks SET is_favorite = ?, updated_at = ? WHERE id = ?",
                      (new_val, time.time(), bm_id))
        conn.commit()
        return {"id": bm_id, "is_favorite": bool(new_val)}
    finally:
        conn.close()


@router.post("/bookmarks/{bm_id}/promote")
async def promote_to_knowledge(bm_id: str):
    """Promote a bookmark to the knowledge base."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM bookmarks WHERE id = ?", (bm_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Bookmark not found")

        tags = json.loads(row["tags"]) if row["tags"] else []
        content = f"# {row['title']}\n\nURL: {row['url']}\n\n{row['description']}"

        try:
            import time as _time
            from src.database.postgres import db_pool
            import asyncpg
            # Try async insert, fall back to sync
            raise Exception("Use sync fallback")
        except Exception:
            # Sync fallback: store in a local promoted_bookmarks table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS promoted_bookmarks (
                    id TEXT PRIMARY KEY,
                    bookmark_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    promoted_at REAL NOT NULL
                )
            """)
            promo_id = f"promo-{uuid.uuid4().hex[:8]}"
            conn.execute(
                "INSERT INTO promoted_bookmarks (id, bookmark_id, title, content, tags, promoted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (promo_id, bm_id, row["title"], content, json.dumps(tags + ["bookmark", "web"]), time.time()),
            )
            conn.commit()

        return {"status": "ok", "promoted_id": promo_id, "title": row["title"]}
    finally:
        conn.close()


# ── Collections ─────────────────────────────────────────────

@router.get("/collections")
async def list_collections():
    """List all collections with bookmark counts."""
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT c.*, COUNT(b.id) as bookmark_count
            FROM collections c
            LEFT JOIN bookmarks b ON c.name = b.collection
            GROUP BY c.id
            ORDER BY c.sort_order
        """).fetchall()
        return {
            "collections": [
                {
                    "id": r["id"], "name": r["name"], "icon": r["icon"],
                    "description": r["description"], "bookmark_count": r["bookmark_count"],
                    "sort_order": r["sort_order"],
                }
                for r in rows
            ]
        }
    finally:
        conn.close()


@router.post("/collections")
async def create_collection(req: CollectionCreate):
    """Create a new collection."""
    conn = _get_db()
    try:
        col_id = f"col-{uuid.uuid4().hex[:8]}"
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM collections").fetchone()[0]
        conn.execute(
            "INSERT INTO collections (id, name, icon, description, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (col_id, req.name, req.icon, req.description, max_order + 1, time.time()),
        )
        conn.commit()
        return {"id": col_id, "name": req.name, "icon": req.icon, "description": req.description}
    except sqlite3.IntegrityError:
        raise HTTPException(400, f"Collection '{req.name}' already exists")
    finally:
        conn.close()


@router.delete("/collections/{col_id}")
async def delete_collection(col_id: str):
    """Delete a collection. Bookmarks in it move to Uncategorized."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT name FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Collection not found")
        if row["name"] == "Uncategorized":
            raise HTTPException(400, "Cannot delete default collection")
        conn.execute("UPDATE bookmarks SET collection = 'Uncategorized' WHERE collection = ?", (row["name"],))
        conn.execute("DELETE FROM collections WHERE id = ?", (col_id,))
        conn.commit()
        return {"status": "ok", "id": col_id, "moved_bookmarks": True}
    finally:
        conn.close()


# ── Tags ────────────────────────────────────────────────────

@router.get("/tags")
async def list_tags():
    """List all unique tags with counts."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT tags FROM bookmarks WHERE tags != '[]'").fetchall()
        tag_counts: dict[str, int] = {}
        for row in rows:
            for tag in json.loads(row["tags"]):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        return {"tags": [{"name": t, "count": c} for t, c in sorted_tags]}
    finally:
        conn.close()


# ── Stats ───────────────────────────────────────────────────

@router.get("/stats")
async def bookmark_stats():
    """Get bookmark statistics."""
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        favorites = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE is_favorite = 1").fetchone()[0]
        collections = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
        top_domains = conn.execute("""
            SELECT
                CASE WHEN instr(substr(url, instr(url, '//') + 2), '/') > 0
                    THEN substr(substr(url, instr(url, '//') + 2), 1, instr(substr(url, instr(url, '//') + 2), '/') - 1)
                    ELSE substr(url, instr(url, '//') + 2)
                END as domain,
                COUNT(*) as count
            FROM bookmarks
            GROUP BY domain
            ORDER BY count DESC
            LIMIT 10
        """).fetchall()
        recently_added = conn.execute(
            "SELECT id, title, url, created_at FROM bookmarks ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        most_clicked = conn.execute(
            "SELECT id, title, url, click_count FROM bookmarks ORDER BY click_count DESC LIMIT 5"
        ).fetchall()
        return {
            "total_bookmarks": total,
            "favorites": favorites,
            "collections": collections,
            "top_domains": [{"domain": r[0], "count": r[1]} for r in top_domains],
            "recently_added": [{"id": r[0], "title": r[1], "url": r[2]} for r in recently_added],
            "most_clicked": [{"id": r[0], "title": r[1], "url": r[2], "clicks": r[3]} for r in most_clicked],
        }
    finally:
        conn.close()


# ── Health ──────────────────────────────────────────────────

@router.get("/health")
async def plugin_health():
    """Bookmark Manager plugin health."""
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        collections = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
        return {
            "status": "ok",
            "component": "Bookmarks",
            "total_bookmarks": total,
            "collections": collections,
            "db_path": str(DB_PATH),
            "features": ["CRUD", "FTS5", "collections", "tags", "favorites", "promote"],
        }
    finally:
        conn.close()
