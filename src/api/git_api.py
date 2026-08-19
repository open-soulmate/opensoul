"""Git API — lightweight git operations for the web client.

Provides status and commit endpoints so the GitStatusBar component
in OpenMate can show branch info, modified files, and quick commits.
"""

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
@router.get("/health")
async def git_api_health():
    """GitAPI health check."""
    return {"status": "ok", "component": "GitAPI"}


async def _git(args: list[str], cwd: str | None = None) -> tuple[str, int]:
    """Run a git command and return (stdout, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd or str(Path.home()),
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    stdout, _ = await proc.communicate()
    return (stdout.decode(errors="replace").strip(), proc.returncode or 0)


# ── Status ───────────────────────────────────────────────────────


@router.get("/status")
async def git_status(cwd: str | None = None):
    """Get git working-tree status."""
    work_dir = cwd or str(Path.home())

    # Branch
    branch_out, rc = await _git(["rev-parse", "--abbrev-ref", "HEAD"], work_dir)
    branch = branch_out if rc == 0 and branch_out else "unknown"

    # Porcelain status
    status_out, _ = await _git(["status", "--porcelain"], work_dir)
    modified = staged = untracked = 0
    for line in status_out.splitlines():
        if not line:
            continue
        index_status = line[0] if len(line) > 0 else " "
        worktree_status = line[1] if len(line) > 1 else " "
        if index_status in ("M", "A", "D", "R", "C"):
            staged += 1
        if worktree_status == "M":
            modified += 1
        if index_status == "?" and worktree_status == "?":
            untracked += 1

    # Ahead/behind
    ahead = behind = 0
    upstream_out, rc = await _git(["rev-parse", "--abbrev-ref", "@{upstream}"], work_dir)
    if rc == 0 and upstream_out:
        rev_out, _ = await _git(
            ["rev-list", "--left-right", "--count", f"HEAD...{upstream_out}"], work_dir
        )
        parts = rev_out.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])

    return {
        "branch": branch,
        "modified": modified,
        "staged": staged,
        "untracked": untracked,
        "ahead": ahead,
        "behind": behind,
    }


# ── Commit ───────────────────────────────────────────────────────


class CommitRequest(BaseModel):
    message: str
    add_all: bool = True
    cwd: str | None = None


@router.post("/commit")
async def git_commit(req: CommitRequest):
    """Stage all changes and commit."""
    work_dir = req.cwd or str(Path.home())
    msg = req.message.strip()
    if not msg:
        raise HTTPException(400, "Commit message required")

    if req.add_all:
        await _git(["add", "-A"], work_dir)

    out, rc = await _git(["commit", "-m", msg], work_dir)
    if rc != 0:
        # Probably nothing to commit
        return {"success": False, "message": out or "Nothing to commit"}

    return {"success": True, "message": out}
