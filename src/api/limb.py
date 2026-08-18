"""OpenLimb API — 四肢：RPA执行器、浏览器自动化、表单填报。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.limb.executor import RPAExecutor

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
executor = RPAExecutor()


# ── Request Schemas ────────────────────────────────────────


class TaskCreateRequest(BaseModel):
    name: str
    actions: list[dict]
    priority: int = 5
    tags: list[str] = []
    variables: dict = {}


class TemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "custom"
    actions: list[dict] = []
    variables: list[dict] = []
    tags: list[str] = []


class TemplateInstantiateRequest(BaseModel):
    variables: dict = {}
    name: str = ""


# ── Task Endpoints ─────────────────────────────────────────


@router.post("/tasks")
async def create_task(req: TaskCreateRequest):
    """Create a new RPA task."""
    task = executor.create_task(
        name=req.name,
        actions=req.actions,
        priority=req.priority,
        tags=req.tags,
        variables=req.variables,
    )
    return task.to_dict()


@router.get("/tasks")
async def list_tasks(
    status: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """List RPA tasks."""
    return {"tasks": executor.list_tasks(status=status, limit=limit)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get task details with results."""
    task = executor.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.to_dict(include_results=True)


@router.post("/tasks/{task_id}/execute")
async def execute_task(task_id: str):
    """Execute a queued task."""
    task = executor.execute_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.to_dict(include_results=True)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a task."""
    if not executor.cancel_task(task_id):
        raise HTTPException(400, "Task cannot be cancelled")
    return {"message": "cancelled", "task_id": task_id}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a task."""
    if not executor.delete_task(task_id):
        raise HTTPException(400, "Cannot delete running task")
    return {"message": "deleted", "task_id": task_id}


# ── Template Endpoints ─────────────────────────────────────


@router.get("/templates")
async def list_templates(category: str = Query(default=None)):
    """List available RPA task templates."""
    return {"templates": executor.list_templates(category=category)}


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get template details."""
    template = executor.get_template(template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    return template.to_dict()


@router.post("/templates")
async def create_template(req: TemplateCreateRequest):
    """Create a custom template."""
    template = executor.create_template(req.model_dump())
    return template.to_dict()


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    """Delete a custom template."""
    if not executor.delete_template(template_id):
        raise HTTPException(400, "Cannot delete built-in template")
    return {"message": "deleted"}


@router.post("/templates/{template_id}/instantiate")
async def instantiate_template(template_id: str, req: TemplateInstantiateRequest):
    """Create a task from a template."""
    task = executor.create_from_template(template_id, req.variables, req.name)
    if not task:
        raise HTTPException(404, "Template not found")
    return task.to_dict()


# ── History ─────────────────────────────────────────────────


@router.get("/history")
async def get_history(limit: int = Query(default=50)):
    """Get task execution history."""
    return {"history": executor.get_history(limit=limit)}


# ── Health / Stats ─────────────────────────────────────────


@router.get("/health")
async def limb_health():
    """OpenLimb health check."""
    return {
        "status": "ok",
        "component": "OpenLimb",
        **executor.stats(),
    }


@router.get("/stats")
async def limb_stats():
    """Get OpenLimb statistics."""
    return executor.stats()
