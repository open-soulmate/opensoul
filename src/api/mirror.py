"""OpenMirror API — 镜像系统：沙箱测试环境管理。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.mirror.sandbox import SandboxManager

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
manager = SandboxManager()


# ── Request Schemas ────────────────────────────────────────

class SandboxCreateRequest(BaseModel):
    name: str = ""
    description: str = ""
    tags: list[str] = []
    config: dict = {}
    ttl_seconds: int = 3600


class LogActionRequest(BaseModel):
    action: str
    detail: dict = {}


class VariableRequest(BaseModel):
    key: str
    value: str = ""


class SnapshotRequest(BaseModel):
    name: str = ""


# ── Sandbox Endpoints ──────────────────────────────────────

@router.post("/sandbox")
async def create_sandbox(req: SandboxCreateRequest):
    """Create a new isolated sandbox."""
    sandbox = manager.create(
        name=req.name,
        description=req.description,
        tags=req.tags,
        config=req.config,
        ttl_seconds=req.ttl_seconds,
    )
    return {
        "sandbox_id": sandbox.sandbox_id,
        "name": sandbox.name,
        "status": sandbox.status,
        "ttl_seconds": sandbox.ttl_seconds,
    }


@router.get("/sandbox")
async def list_sandboxes(status: str = Query(default=None)):
    """List all sandboxes."""
    return {"sandboxes": manager.list_sandboxes(status=status)}


@router.get("/sandbox/{sandbox_id}")
async def get_sandbox(sandbox_id: str):
    """Get sandbox details."""
    sandbox = manager.get(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox not found")
    return {
        "sandbox_id": sandbox.sandbox_id,
        "name": sandbox.name,
        "status": sandbox.status,
        "created_at": sandbox.created_at,
        "description": sandbox.description,
        "tags": sandbox.tags,
        "variables": sandbox.variables,
        "log_count": len(sandbox.log),
        "snapshot_count": sandbox.snapshot_count,
        "ttl_seconds": sandbox.ttl_seconds,
    }


@router.delete("/sandbox/{sandbox_id}")
async def destroy_sandbox(sandbox_id: str):
    """Destroy a sandbox and clean up resources."""
    if not manager.destroy(sandbox_id):
        raise HTTPException(404, "Sandbox not found")
    return {"message": f"Sandbox '{sandbox_id}' destroyed"}


@router.post("/sandbox/{sandbox_id}/pause")
async def pause_sandbox(sandbox_id: str):
    """Pause a sandbox."""
    if not manager.pause(sandbox_id):
        raise HTTPException(404, "Sandbox not found or not active")
    return {"message": "paused"}


@router.post("/sandbox/{sandbox_id}/resume")
async def resume_sandbox(sandbox_id: str):
    """Resume a paused sandbox."""
    if not manager.resume(sandbox_id):
        raise HTTPException(400, "Sandbox not found or not paused")
    return {"message": "resumed"}


# ── Sandbox Actions ────────────────────────────────────────

@router.post("/sandbox/{sandbox_id}/log")
async def log_action(sandbox_id: str, req: LogActionRequest):
    """Log an action in the sandbox."""
    if not manager.log_action(sandbox_id, req.action, req.detail):
        raise HTTPException(404, "Sandbox not found or not active")
    return {"message": "logged"}


@router.get("/sandbox/{sandbox_id}/log")
async def get_log(sandbox_id: str, limit: int = Query(default=100)):
    """Get sandbox action log."""
    entries = manager.get_log(sandbox_id, limit)
    if not entries and not manager.get(sandbox_id):
        raise HTTPException(404, "Sandbox not found")
    return {"log": entries}


@router.post("/sandbox/{sandbox_id}/variable")
async def set_variable(sandbox_id: str, req: VariableRequest):
    """Set a variable in the sandbox."""
    if not manager.set_variable(sandbox_id, req.key, req.value):
        raise HTTPException(404, "Sandbox not found or not active")
    return {"message": "set", "key": req.key}


@router.get("/sandbox/{sandbox_id}/variable/{key}")
async def get_variable(sandbox_id: str, key: str):
    """Get a variable from the sandbox."""
    value = manager.get_variable(sandbox_id, key)
    if value is None and not manager.get(sandbox_id):
        raise HTTPException(404, "Sandbox not found")
    return {"key": key, "value": value}


@router.post("/sandbox/{sandbox_id}/snapshot")
async def take_snapshot(sandbox_id: str, req: SnapshotRequest):
    """Take a snapshot of sandbox state."""
    result = manager.snapshot(sandbox_id, req.name)
    if not result:
        raise HTTPException(404, "Sandbox not found")
    return result


# ── Maintenance ────────────────────────────────────────────

@router.post("/cleanup")
async def cleanup_expired():
    """Clean up expired sandboxes."""
    count = manager.cleanup_expired()
    return {"cleaned": count}


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def mirror_health():
    """OpenMirror health check."""
    return {
        "status": "ok",
        "component": "OpenMirror",
        **manager.stats(),
    }
