"""Workspace API — file operations, directory listing, and command execution for the web client.

Provides the same capabilities that the Tauri desktop bridge offers natively,
so the web version of OpenMate can browse files and run commands server-side.
"""

import asyncio
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

@router.get("/workspace/health")
async def health():
    return {"status": "ok", "component": "OpenWorkspace"}

# ── Safety ───────────────────────────────────────────────────────
ALLOWED_ROOTS = [
    Path.home(),
    Path("/tmp"),
]

def _validate_path(p: str) -> Path:
    """Resolve and validate that path is under an allowed root."""
    resolved = Path(p).expanduser().resolve()
    for root in ALLOWED_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise HTTPException(403, f"Access denied: {p} is outside allowed directories")


# ── Models ───────────────────────────────────────────────────────

class WriteFileRequest(BaseModel):
    path: str
    content: str

class ExecuteRequest(BaseModel):
    cmd: str
    cwd: str | None = None
    timeout: int = 30


# ── Directory Listing ────────────────────────────────────────────

@router.get("/dir")
async def list_directory(path: str = Query("~")):
    """List entries in a directory."""
    target = _validate_path(path)
    if not target.is_dir():
        raise HTTPException(400, f"Not a directory: {path}")

    entries = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                stat = entry.stat()
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "type": "directory" if entry.is_dir() else "file",
                    "size": stat.st_size if entry.is_file() else None,
                    "modified": stat.st_mtime,
                    "hidden": entry.name.startswith("."),
                })
            except (PermissionError, OSError):
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "type": "unknown",
                    "size": None,
                    "modified": None,
                    "hidden": entry.name.startswith("."),
                })
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {path}")

    return {"path": str(target), "entries": entries}


# ── File Read ────────────────────────────────────────────────────

@router.get("/file")
async def read_file(path: str = Query(...)):
    """Read a text file's content."""
    target = _validate_path(path)
    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")
    if target.stat().st_size > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(413, "File too large (>10 MB)")

    try:
        content = target.read_text(errors="replace")
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {path}")

    return {"path": str(target), "content": content, "size": target.stat().st_size}


# ── File Write ───────────────────────────────────────────────────

@router.post("/file")
async def write_file(req: WriteFileRequest):
    """Write content to a file (creates parent dirs if needed)."""
    target = _validate_path(req.path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.content)
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {req.path}")
    except OSError as e:
        raise HTTPException(500, f"Write failed: {e}")

    return {"path": str(target), "size": len(req.content)}


# ── Command Execution ────────────────────────────────────────────

# Blocked commands for safety
BLOCKED_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",  # fork bomb
]

@router.post("/execute")
async def execute_command(req: ExecuteRequest):
    """Execute a shell command server-side (for web clients without Tauri)."""
    cmd = req.cmd.strip()
    if not cmd:
        raise HTTPException(400, "Empty command")

    # Safety check
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd:
            raise HTTPException(403, f"Blocked dangerous command: {blocked}")

    cwd = str(_validate_path(req.cwd)) if req.cwd else str(Path.home())

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env={**os.environ, "TERM": "dumb"},
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=req.timeout
        )
        output = stdout.decode(errors="replace") if stdout else ""
        return {
            "output": output,
            "exit_code": proc.returncode,
            "cmd": cmd,
        }
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(408, f"Command timed out after {req.timeout}s")
    except Exception as e:
        raise HTTPException(500, f"Execution error: {e}")
