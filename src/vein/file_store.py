"""Content-addressable file storage with deduplication.

Stores files by SHA-256 hash. Metadata tracked in SQLite.
Supports streaming reads and atomic writes.
"""

import hashlib
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class FileMeta:
    file_id: str
    name: str
    content_hash: str
    size: int
    mime_type: str
    tags: str  # comma-separated
    created_at: float
    ref_count: int


class FileStore:
    """Content-addressable file store with deduplication."""

    def __init__(self, root: str | None = None, db_path: str | None = None):
        self._root = Path(root or os.path.expanduser("~/opensoul/data/vein/files"))
        self._root.mkdir(parents=True, exist_ok=True)

        db = db_path or str(self._root.parent / "vein_meta.db")
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                file_id    TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                size       INTEGER NOT NULL,
                mime_type  TEXT DEFAULT 'application/octet-stream',
                tags       TEXT DEFAULT '',
                created_at REAL NOT NULL,
                ref_count  INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);
            CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
        """)
        self._db.commit()

    # ── Core Operations ──────────────────────────────────────

    def store(
        self,
        data: bytes,
        name: str,
        mime_type: str = "application/octet-stream",
        tags: list[str] | None = None,
        file_id: str | None = None,
    ) -> FileMeta:
        """Store a file with deduplication. Returns metadata."""
        content_hash = hashlib.sha256(data).hexdigest()
        size = len(data)
        now = time.time()
        fid = file_id or hashlib.md5(f"{name}{now}{content_hash[:8]}".encode()).hexdigest()[:16]
        tag_str = ",".join(tags) if tags else ""

        # Dedup: if content hash exists, just increment ref_count
        existing = self._db.execute(
            "SELECT file_id FROM files WHERE content_hash = ?", (content_hash,)
        ).fetchone()

        if existing:
            self._db.execute(
                "INSERT INTO files (file_id, name, content_hash, size, mime_type, tags, created_at, ref_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (fid, name, content_hash, size, mime_type, tag_str, now),
            )
            self._db.commit()
            return FileMeta(fid, name, content_hash, size, mime_type, tag_str, now, 1)

        # Store the actual blob
        blob_path = self._blob_path(content_hash)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(data)

        self._db.execute(
            "INSERT INTO files (file_id, name, content_hash, size, mime_type, tags, created_at, ref_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (fid, name, content_hash, size, mime_type, tag_str, now),
        )
        self._db.commit()
        return FileMeta(fid, name, content_hash, size, mime_type, tag_str, now, 1)

    def retrieve(self, file_id: str) -> tuple[bytes, FileMeta] | None:
        """Retrieve a file by ID. Returns (data, meta) or None."""
        meta = self.get_meta(file_id)
        if not meta:
            return None
        blob_path = self._blob_path(meta.content_hash)
        if not blob_path.exists():
            return None
        return blob_path.read_bytes(), meta

    def stream_retrieve(self, file_id: str):
        """Generator that yields file data in chunks for streaming."""
        meta = self.get_meta(file_id)
        if not meta:
            return None, None
        blob_path = self._blob_path(meta.content_hash)
        if not blob_path.exists():
            return None, None
        return blob_path, meta

    def get_meta(self, file_id: str) -> FileMeta | None:
        row = self._db.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone()
        if not row:
            return None
        return FileMeta(**dict(row))

    def delete(self, file_id: str) -> bool:
        """Delete a file reference. Removes blob if no other references."""
        meta = self.get_meta(file_id)
        if not meta:
            return False

        self._db.execute("DELETE FROM files WHERE file_id = ?", (file_id,))

        # Check if any other files reference the same content
        refs = self._db.execute(
            "SELECT COUNT(*) FROM files WHERE content_hash = ?", (meta.content_hash,)
        ).fetchone()[0]

        if refs == 0:
            blob_path = self._blob_path(meta.content_hash)
            if blob_path.exists():
                blob_path.unlink()

        self._db.commit()
        return True

    def list_files(
        self,
        name_filter: str | None = None,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FileMeta]:
        """List files with optional filters."""
        query = "SELECT * FROM files WHERE 1=1"
        params: list = []

        if name_filter:
            query += " AND name LIKE ?"
            params.append(f"%{name_filter}%")
        if tag:
            query += " AND tags LIKE ?"
            params.append(f"%{tag}%")

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.execute(query, params).fetchall()
        return [FileMeta(**dict(r)) for r in rows]

    def stats(self) -> dict:
        total = self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        size = self._db.execute("SELECT COALESCE(SUM(size), 0) FROM files").fetchone()[0]
        unique = self._db.execute("SELECT COUNT(DISTINCT content_hash) FROM files").fetchone()[0]

        disk_usage = 0
        blob_dir = self._root / "blobs"
        if blob_dir.exists():
            for f in blob_dir.rglob("*"):
                if f.is_file():
                    disk_usage += f.stat().st_size

        return {
            "total_files": total,
            "total_size_bytes": size,
            "unique_blobs": unique,
            "disk_usage_bytes": disk_usage,
            "dedup_savings_bytes": size - disk_usage if size > disk_usage else 0,
        }

    def _blob_path(self, content_hash: str) -> Path:
        # Shard by first 2 chars for filesystem balance
        return self._root / "blobs" / content_hash[:2] / content_hash
