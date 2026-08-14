"""Backup manager — snapshot creation, listing, restore."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BackupManifest:
    backup_id: str
    name: str
    created_at: float
    size_bytes: int
    file_count: int
    description: str = ""
    tags: list[str] = field(default_factory=list)
    checksum: str = ""
    status: str = "complete"


class BackupManager:
    """Manage backup snapshots of knowledge base data."""

    def __init__(self, backup_dir: str | Path | None = None):
        self.backup_dir = Path(backup_dir or os.path.expanduser("~/.opensoul/backups"))
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._manifest_file = self.backup_dir / "manifests.json"
        self._manifests: dict[str, BackupManifest] = {}
        self._load_manifests()

    def _load_manifests(self):
        if self._manifest_file.exists():
            try:
                data = json.loads(self._manifest_file.read_text())
                for k, v in data.items():
                    self._manifests[k] = BackupManifest(**v)
            except Exception:
                self._manifests = {}

    def _save_manifests(self):
        data = {k: {
            "backup_id": v.backup_id,
            "name": v.name,
            "created_at": v.created_at,
            "size_bytes": v.size_bytes,
            "file_count": v.file_count,
            "description": v.description,
            "tags": v.tags,
            "checksum": v.checksum,
            "status": v.status,
        } for k, v in self._manifests.items()}
        self._manifest_file.write_text(json.dumps(data, indent=2))

    def create_backup(
        self,
        source_dirs: list[str | Path],
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
    ) -> BackupManifest:
        """Create a tar.gz backup of specified directories."""
        timestamp = int(time.time())
        backup_id = f"backup_{timestamp}"
        backup_name = name or f"backup_{time.strftime('%Y%m%d_%H%M%S')}"
        archive_path = self.backup_dir / f"{backup_id}.tar.gz"

        file_count = 0
        with tarfile.open(archive_path, "w:gz") as tar:
            for src in source_dirs:
                src_path = Path(src)
                if src_path.exists():
                    for item in src_path.rglob("*"):
                        if item.is_file():
                            tar.add(item, arcname=str(item.relative_to(src_path.parent)))
                            file_count += 1

        size = archive_path.stat().st_size
        checksum = self._checksum(archive_path)

        manifest = BackupManifest(
            backup_id=backup_id,
            name=backup_name,
            created_at=time.time(),
            size_bytes=size,
            file_count=file_count,
            description=description,
            tags=tags or [],
            checksum=checksum,
        )

        with self._lock:
            self._manifests[backup_id] = manifest
            self._save_manifests()

        return manifest

    def restore_backup(self, backup_id: str, target_dir: str | Path) -> dict:
        """Restore a backup to target directory."""
        with self._lock:
            manifest = self._manifests.get(backup_id)
        if not manifest:
            return {"success": False, "error": f"Backup '{backup_id}' not found"}

        archive_path = self.backup_dir / f"{backup_id}.tar.gz"
        if not archive_path.exists():
            return {"success": False, "error": "Archive file missing"}

        # Verify checksum
        current_checksum = self._checksum(archive_path)
        if current_checksum != manifest.checksum:
            return {"success": False, "error": "Checksum mismatch — archive may be corrupted"}

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=target)

        return {
            "success": True,
            "backup_id": backup_id,
            "target": str(target),
            "files_restored": manifest.file_count,
        }

    def list_backups(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "backup_id": m.backup_id,
                    "name": m.name,
                    "created_at": m.created_at,
                    "size_bytes": m.size_bytes,
                    "file_count": m.file_count,
                    "description": m.description,
                    "tags": m.tags,
                    "status": m.status,
                }
                for m in sorted(self._manifests.values(), key=lambda x: x.created_at, reverse=True)
            ]

    def delete_backup(self, backup_id: str) -> dict:
        with self._lock:
            manifest = self._manifests.pop(backup_id, None)
            if not manifest:
                return {"success": False, "error": "Not found"}
            self._save_manifests()

        archive_path = self.backup_dir / f"{backup_id}.tar.gz"
        if archive_path.exists():
            archive_path.unlink()

        return {"success": True, "backup_id": backup_id}

    def get_backup(self, backup_id: str) -> dict | None:
        with self._lock:
            m = self._manifests.get(backup_id)
        if not m:
            return None
        return {
            "backup_id": m.backup_id,
            "name": m.name,
            "created_at": m.created_at,
            "size_bytes": m.size_bytes,
            "file_count": m.file_count,
            "description": m.description,
            "tags": m.tags,
            "checksum": m.checksum,
            "status": m.status,
        }

    def stats(self) -> dict:
        with self._lock:
            total_size = sum(m.size_bytes for m in self._manifests.values())
            return {
                "total_backups": len(self._manifests),
                "total_size_bytes": total_size,
                "backup_dir": str(self.backup_dir),
            }

    @staticmethod
    def _checksum(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
