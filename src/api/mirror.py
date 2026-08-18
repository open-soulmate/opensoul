"""OpenMirror API — 镜像系统：沙箱测试环境管理、沙箱模板。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.mirror.sandbox import SandboxManager
from src.mirror.templates import SandboxTemplateEngine
from src.nerve.event_bridge import push_event

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
manager = SandboxManager()
template_engine = SandboxTemplateEngine()


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


@router.post("/sandboxes")
async def create_sandbox(req: SandboxCreateRequest):
    """Create a new isolated sandbox."""
    sandbox = manager.create(
        name=req.name,
        description=req.description,
        tags=req.tags,
        config=req.config,
        ttl_seconds=req.ttl_seconds,
    )
    push_event(
        {
            "organ": "mirror",
            "emoji": "🪞",
            "type": "sandbox_created",
            "summary": f"🆕 Sandbox created: {sandbox.name or sandbox.sandbox_id}",
            "detail": {"sandbox_id": sandbox.sandbox_id, "name": sandbox.name},
        }
    )

    return {
        "sandbox_id": sandbox.sandbox_id,
        "name": sandbox.name,
        "status": sandbox.status,
        "ttl_seconds": sandbox.ttl_seconds,
    }


@router.get("/sandboxes")
async def list_sandboxes(status: str = Query(default=None)):
    """List all sandboxes."""
    return {"sandboxes": manager.list_sandboxes(status=status)}


@router.get("/sandboxes/{sandbox_id}")
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


@router.delete("/sandboxes/{sandbox_id}")
async def destroy_sandbox(sandbox_id: str):
    """Destroy a sandbox and clean up resources."""
    if not manager.destroy(sandbox_id):
        raise HTTPException(404, "Sandbox not found")
    return {"message": f"Sandbox '{sandbox_id}' destroyed"}


@router.post("/sandboxes/{sandbox_id}/pause")
async def pause_sandbox(sandbox_id: str):
    """Pause a sandbox."""
    if not manager.pause(sandbox_id):
        raise HTTPException(404, "Sandbox not found or not active")
    return {"message": "paused"}


@router.post("/sandboxes/{sandbox_id}/resume")
async def resume_sandbox(sandbox_id: str):
    """Resume a paused sandbox."""
    if not manager.resume(sandbox_id):
        raise HTTPException(400, "Sandbox not found or not paused")
    return {"message": "resumed"}


# ── Sandbox Actions ────────────────────────────────────────


@router.post("/sandboxes/{sandbox_id}/log")
async def log_action(sandbox_id: str, req: LogActionRequest):
    """Log an action in the sandbox."""
    if not manager.log_action(sandbox_id, req.action, req.detail):
        raise HTTPException(404, "Sandbox not found or not active")
    return {"message": "logged"}


@router.get("/sandboxes/{sandbox_id}/logs")
async def get_logs(sandbox_id: str, limit: int = Query(default=100)):
    """Get sandbox action log."""
    entries = manager.get_log(sandbox_id, limit)
    if not entries and not manager.get(sandbox_id):
        raise HTTPException(404, "Sandbox not found")
    return {"logs": entries}


@router.post("/sandboxes/{sandbox_id}/variables")
async def set_variable(sandbox_id: str, req: VariableRequest):
    """Set a variable in the sandbox."""
    if not manager.set_variable(sandbox_id, req.key, req.value):
        raise HTTPException(404, "Sandbox not found or not active")
    return {"message": "set", "key": req.key}


@router.get("/sandboxes/{sandbox_id}/variables")
async def get_variables(sandbox_id: str):
    """Get all variables from the sandbox."""
    sandbox = manager.get(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox not found")
    return {"variables": sandbox.variables}


@router.post("/sandboxes/{sandbox_id}/snapshot")
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


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def mirror_stats():
    """Get OpenMirror statistics."""
    return {
        "status": "ok",
        "component": "OpenMirror",
        **manager.stats(),
        "templates": template_engine.stats(),
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def mirror_health():
    """OpenMirror health check."""
    return {
        "status": "ok",
        "component": "OpenMirror",
        **manager.stats(),
        "templates": template_engine.stats(),
    }


# ── Sandbox Template Schemas ───────────────────────────────


class SandboxTemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    icon: str = "🧪"
    config: dict = {}
    variables: dict = {}
    tags: list[str] = []
    category: str = "custom"


class SandboxFromTemplateRequest(BaseModel):
    template_id: str
    name: str = ""  # override name
    variables: dict = {}  # override variables


# ── Sandbox Template Endpoints ─────────────────────────────


@router.get("/templates")
async def list_sandbox_templates(category: str = Query(default=None)):
    """List all sandbox templates."""
    return {"templates": template_engine.list_templates(category=category)}


@router.get("/templates/{template_id}")
async def get_sandbox_template(template_id: str):
    """Get a specific sandbox template."""
    tpl = template_engine.get(template_id)
    if not tpl:
        raise HTTPException(404, "Template not found")
    return {
        "template_id": tpl.template_id,
        "name": tpl.name,
        "description": tpl.description,
        "icon": tpl.icon,
        "config": tpl.config,
        "variables": tpl.variables,
        "tags": tpl.tags,
        "category": tpl.category,
        "usage_count": tpl.usage_count,
        "created_at": tpl.created_at,
    }


@router.post("/templates")
async def create_sandbox_template(req: SandboxTemplateCreateRequest):
    """Create a new sandbox template."""
    tpl = template_engine.create(
        name=req.name,
        description=req.description,
        icon=req.icon,
        config=req.config,
        variables=req.variables,
        tags=req.tags,
        category=req.category,
    )
    push_event(
        {
            "organ": "mirror",
            "emoji": "🪞",
            "type": "sandbox_template_created",
            "summary": f"📋 Sandbox template created: {tpl.name}",
            "detail": {"template_id": tpl.template_id, "name": tpl.name},
        }
    )
    return {
        "template_id": tpl.template_id,
        "name": tpl.name,
    }


@router.delete("/templates/{template_id}")
async def delete_sandbox_template(template_id: str):
    """Delete a sandbox template."""
    if not template_engine.delete(template_id):
        raise HTTPException(404, "Template not found")
    return {"message": "deleted", "template_id": template_id}


@router.post("/templates/{template_id}/instantiate")
async def create_sandbox_from_template(template_id: str, req: SandboxFromTemplateRequest):
    """Create a sandbox from a template with optional overrides."""
    instance = template_engine.instantiate(template_id, overrides=req.variables)
    if not instance:
        raise HTTPException(404, "Template not found")

    # Override name if provided
    if req.name:
        instance["name"] = req.name

    # Create the sandbox
    sandbox = manager.create(
        name=instance["name"],
        description=instance["description"],
        tags=instance["tags"],
        config=instance["config"],
        ttl_seconds=instance["ttl_seconds"],
    )

    # Set template variables as sandbox variables
    for k, v in instance["variables"].items():
        if v:  # only set non-empty values
            manager.set_variable(sandbox.sandbox_id, k, v)

    push_event(
        {
            "organ": "mirror",
            "emoji": "🪞",
            "type": "sandbox_from_template",
            "summary": f"🆕 Sandbox created from template: {instance['name']}",
            "detail": {
                "sandbox_id": sandbox.sandbox_id,
                "template_id": template_id,
                "name": sandbox.name,
            },
        }
    )

    return {
        "sandbox_id": sandbox.sandbox_id,
        "name": sandbox.name,
        "status": sandbox.status,
        "template_id": template_id,
        "variables": instance["variables"],
    }
