"""Sandbox manager — isolated testing environments."""

from __future__ import annotations

import copy
import json
import os
import shutil
import time
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Sandbox:
    sandbox_id: str
    name: str
    created_at: float
    status: str = "active"  # "active", "paused", "destroyed"
    config: dict = field(default_factory=dict)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    # Sandbox state
    variables: dict = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    snapshot_count: int = 0
    ttl_seconds: int = 3600  # auto-cleanup after 1 hour
    data_dir: str = ""


class SandboxManager:
    """Manage isolated sandboxes for testing workflows, agents, and connectors."""

    def __init__(self, sandbox_dir: str | Path | None = None):
        self.sandbox_dir = Path(sandbox_dir or os.path.expanduser("~/.opensoul/sandboxes"))
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._sandboxes: dict[str, Sandbox] = {}
        self._lock = threading.Lock()

    def create(
        self,
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
        config: dict | None = None,
        ttl_seconds: int = 3600,
    ) -> Sandbox:
        """Create a new sandbox."""
        sandbox_id = f"sandbox-{uuid.uuid4().hex[:8]}"
        data_dir = self.sandbox_dir / sandbox_id
        data_dir.mkdir(parents=True, exist_ok=True)

        sandbox = Sandbox(
            sandbox_id=sandbox_id,
            name=name or f"Sandbox {sandbox_id}",
            created_at=time.time(),
            config=config or {},
            description=description,
            tags=tags or [],
            ttl_seconds=ttl_seconds,
            data_dir=str(data_dir),
        )

        with self._lock:
            self._sandboxes[sandbox_id] = sandbox

        return sandbox

    def get(self, sandbox_id: str) -> Sandbox | None:
        with self._lock:
            return self._sandboxes.get(sandbox_id)

    def list_sandboxes(self, status: str | None = None) -> list[dict]:
        with self._lock:
            sandboxes = list(self._sandboxes.values())
        if status:
            sandboxes = [s for s in sandboxes if s.status == status]

        return [
            {
                "sandbox_id": s.sandbox_id,
                "name": s.name,
                "status": s.status,
                "created_at": s.created_at,
                "description": s.description,
                "tags": s.tags,
                "variables_count": len(s.variables),
                "log_entries": len(s.log),
                "snapshot_count": s.snapshot_count,
                "ttl_seconds": s.ttl_seconds,
                "expires_at": s.created_at + s.ttl_seconds,
            }
            for s in sorted(sandboxes, key=lambda x: x.created_at, reverse=True)
        ]

    def destroy(self, sandbox_id: str) -> bool:
        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
        if not sandbox:
            return False

        # Clean up data directory
        if sandbox.data_dir and os.path.exists(sandbox.data_dir):
            shutil.rmtree(sandbox.data_dir, ignore_errors=True)

        return True

    def log_action(self, sandbox_id: str, action: str, detail: dict | None = None) -> bool:
        """Log an action in the sandbox."""
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox or sandbox.status != "active":
            return False

        entry = {
            "timestamp": time.time(),
            "action": action,
            "detail": detail or {},
        }
        sandbox.log.append(entry)
        return True

    def set_variable(self, sandbox_id: str, key: str, value: Any) -> bool:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox or sandbox.status != "active":
            return False
        sandbox.variables[key] = value
        return True

    def get_variable(self, sandbox_id: str, key: str) -> Any:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            return None
        return sandbox.variables.get(key)

    def snapshot(self, sandbox_id: str, name: str = "") -> dict | None:
        """Take a snapshot of sandbox state."""
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            return None

        snapshot_id = f"snap-{uuid.uuid4().hex[:8]}"
        snapshot_data = {
            "snapshot_id": snapshot_id,
            "name": name or f"Snapshot {sandbox.snapshot_count + 1}",
            "created_at": time.time(),
            "variables": copy.deepcopy(sandbox.variables),
            "log_length": len(sandbox.log),
        }

        # Save snapshot to disk
        if sandbox.data_dir:
            snap_path = Path(sandbox.data_dir) / f"{snapshot_id}.json"
            snap_path.write_text(json.dumps(snapshot_data, ensure_ascii=False, indent=2))

        sandbox.snapshot_count += 1
        return snapshot_data

    def get_log(self, sandbox_id: str, limit: int = 100) -> list[dict]:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            return []
        return sandbox.log[-limit:]

    def pause(self, sandbox_id: str) -> bool:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            return False
        sandbox.status = "paused"
        return True

    def resume(self, sandbox_id: str) -> bool:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox or sandbox.status != "paused":
            return False
        sandbox.status = "active"
        return True

    def cleanup_expired(self) -> int:
        """Clean up expired sandboxes."""
        now = time.time()
        expired = []
        with self._lock:
            for sid, s in self._sandboxes.items():
                if now > s.created_at + s.ttl_seconds:
                    expired.append(sid)

        count = 0
        for sid in expired:
            if self.destroy(sid):
                count += 1
        return count

    def stats(self) -> dict:
        with self._lock:
            total = len(self._sandboxes)
            active = sum(1 for s in self._sandboxes.values() if s.status == "active")
            paused = sum(1 for s in self._sandboxes.values() if s.status == "paused")
            return {
                "total_sandboxes": total,
                "active": active,
                "paused": paused,
                "sandbox_dir": str(self.sandbox_dir),
            }
