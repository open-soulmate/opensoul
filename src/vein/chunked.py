"""Chunked upload manager for large files.

Splits large files into chunks, tracks upload progress,
and reassembles when all chunks arrive.
"""

import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class UploadSession:
    upload_id: str
    filename: str
    total_size: int
    chunk_size: int
    total_chunks: int
    received_chunks: set[int] = field(default_factory=set)
    content_hash: str | None = None
    mime_type: str = "application/octet-stream"
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    @property
    def progress(self) -> float:
        if self.total_chunks == 0:
            return 100.0
        return round(len(self.received_chunks) / self.total_chunks * 100, 2)

    @property
    def is_complete(self) -> bool:
        return len(self.received_chunks) >= self.total_chunks

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_at or time.time()
        return round(end - self.created_at, 2)

    def to_dict(self) -> dict:
        return {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "total_size": self.total_size,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
            "received_chunks": sorted(self.received_chunks),
            "progress": self.progress,
            "is_complete": self.is_complete,
            "elapsed_seconds": self.elapsed_seconds,
            "mime_type": self.mime_type,
            "tags": self.tags,
        }


class ChunkedUploader:
    """Manages chunked file uploads with resumable sessions."""

    DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5MB

    def __init__(self, temp_dir: str | None = None, max_sessions: int = 100):
        self._temp = Path(temp_dir or os.path.expanduser("~/opensoul/data/vein/uploads"))
        self._temp.mkdir(parents=True, exist_ok=True)
        self._max_sessions = max_sessions
        self._sessions: dict[str, UploadSession] = {}

    def create_session(
        self,
        filename: str,
        total_size: int,
        chunk_size: int | None = None,
        mime_type: str = "application/octet-stream",
        tags: list[str] | None = None,
    ) -> UploadSession:
        """Create a new upload session."""
        self._cleanup_old_sessions()

        cs = chunk_size or self.DEFAULT_CHUNK_SIZE
        total_chunks = (total_size + cs - 1) // cs
        upload_id = uuid.uuid4().hex[:12]

        session = UploadSession(
            upload_id=upload_id,
            filename=filename,
            total_size=total_size,
            chunk_size=cs,
            total_chunks=total_chunks,
            mime_type=mime_type,
            tags=tags or [],
        )

        # Create temp dir for this upload
        chunk_dir = self._temp / upload_id
        chunk_dir.mkdir(parents=True, exist_ok=True)

        self._sessions[upload_id] = session
        return session

    def upload_chunk(
        self, upload_id: str, chunk_index: int, data: bytes
    ) -> UploadSession | None:
        """Upload a single chunk. Returns session or None if not found."""
        session = self._sessions.get(upload_id)
        if not session:
            return None

        chunk_path = self._temp / upload_id / f"chunk_{chunk_index:06d}"
        chunk_path.write_bytes(data)

        session.received_chunks.add(chunk_index)
        if session.is_complete:
            session.completed_at = time.time()

        return session

    def get_session(self, upload_id: str) -> UploadSession | None:
        return self._sessions.get(upload_id)

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def assemble(self, upload_id: str) -> tuple[bytes, UploadSession] | None:
        """Reassemble chunks into complete file. Returns (data, session) or None."""
        session = self._sessions.get(upload_id)
        if not session or not session.is_complete:
            return None

        chunk_dir = self._temp / upload_id
        hasher = hashlib.sha256()
        assembled = bytearray()

        for i in range(session.total_chunks):
            chunk_path = chunk_dir / f"chunk_{i:06d}"
            if not chunk_path.exists():
                return None
            chunk_data = chunk_path.read_bytes()
            assembled.extend(chunk_data)
            hasher.update(chunk_data)

        session.content_hash = hasher.hexdigest()
        return bytes(assembled), session

    def cleanup(self, upload_id: str) -> bool:
        """Remove upload session and temp files."""
        session = self._sessions.pop(upload_id, None)
        if not session:
            return False

        chunk_dir = self._temp / upload_id
        if chunk_dir.exists():
            import shutil
            shutil.rmtree(chunk_dir)

        return True

    def _cleanup_old_sessions(self, max_age: float = 3600):
        """Remove sessions older than max_age seconds."""
        now = time.time()
        stale = [
            uid for uid, s in self._sessions.items()
            if now - s.created_at > max_age
        ]
        for uid in stale:
            self.cleanup(uid)

        # Also enforce max sessions
        while len(self._sessions) > self._max_sessions:
            oldest = min(self._sessions, key=lambda k: self._sessions[k].created_at)
            self.cleanup(oldest)
