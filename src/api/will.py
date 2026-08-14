"""OpenWill API — 意志系统：工作流编排、条件触发、多分支流程。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any

from src.will.engine import WorkflowEngine
from src.will.models import (
    ExecutionStatus,
    NodeType,
    TriggerType,
    WorkflowStatus,
)

router = APIRouter()
engine = WorkflowEngine()


# ── Request Schemas ────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    name: str
    description: str = ""
    trigger: TriggerType = TriggerType.MANUAL
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: WorkflowStatus | None = None
    variables: dict[str, Any] | None = None


class NodeCreate(BaseModel):
    node_type: NodeType
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})


class EdgeCreate(BaseModel):
    source_node_id: str
    target_node_id: str
    condition: str | None = None
    label: str = ""


class ExecuteRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


# ── Workflow Endpoints ─────────────────────────────────────────

@router.post("/workflows")
async def create_workflow(req: WorkflowCreate):
    """Create a new workflow."""
    wf = engine.create_workflow(
        name=req.name,
        description=req.description,
        trigger=req.trigger,
        trigger_config=req.trigger_config,
        variables=req.variables,
    )
    return _workflow_dict(wf)


@router.get("/workflows")
async def list_workflows(status: str = Query(default=None)):
    """List all workflows."""
    ws = engine.list_workflows()
    if status:
        try:
            s = WorkflowStatus(status)
            ws = engine.list_workflows(status=s)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    return {"workflows": [_workflow_dict(w) for w in ws], "count": len(ws)}


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get workflow details."""
    wf = engine.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return _workflow_dict(wf)


@router.put("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, req: WorkflowUpdate):
    """Update workflow metadata."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    wf = engine.update_workflow(workflow_id, **updates)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return _workflow_dict(wf)


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str):
    """Delete a workflow."""
    if not engine.delete_workflow(workflow_id):
        raise HTTPException(404, "Workflow not found")
    return {"status": "ok", "workflow_id": workflow_id}


# ── Node Endpoints ─────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/nodes")
async def add_node(workflow_id: str, req: NodeCreate):
    """Add a node to a workflow."""
    node = engine.add_node(
        workflow_id,
        node_type=req.node_type,
        label=req.label,
        config=req.config,
        position=req.position,
    )
    if not node:
        raise HTTPException(404, "Workflow not found")
    return node.model_dump()


@router.delete("/workflows/{workflow_id}/nodes/{node_id}")
async def remove_node(workflow_id: str, node_id: str):
    """Remove a node from a workflow."""
    if not engine.remove_node(workflow_id, node_id):
        raise HTTPException(404, "Node or workflow not found")
    return {"status": "ok"}


# ── Edge Endpoints ─────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/edges")
async def add_edge(workflow_id: str, req: EdgeCreate):
    """Add an edge (connection) between two nodes."""
    edge = engine.add_edge(
        workflow_id,
        source_node_id=req.source_node_id,
        target_node_id=req.target_node_id,
        condition=req.condition,
        label=req.label,
    )
    if not edge:
        raise HTTPException(404, "Workflow or nodes not found")
    return edge.model_dump()


@router.delete("/workflows/{workflow_id}/edges/{edge_id}")
async def remove_edge(workflow_id: str, edge_id: str):
    """Remove an edge from a workflow."""
    if not engine.remove_edge(workflow_id, edge_id):
        raise HTTPException(404, "Edge or workflow not found")
    return {"status": "ok"}


# ── Validation ─────────────────────────────────────────────────

@router.get("/workflows/{workflow_id}/validate")
async def validate_workflow(workflow_id: str):
    """Validate a workflow DAG."""
    wf = engine.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    errors = wf.validate_dag()
    return {"valid": len(errors) == 0, "errors": errors}


# ── Execution Endpoints ────────────────────────────────────────

@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, req: ExecuteRequest = ExecuteRequest()):
    """Execute a workflow."""
    execution = engine.execute(workflow_id, input_vars=req.variables)
    if not execution:
        raise HTTPException(404, "Workflow not found")
    return _execution_dict(execution)


@router.get("/executions")
async def list_executions(
    workflow_id: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """List workflow executions."""
    execs = engine.list_executions(workflow_id=workflow_id, limit=limit)
    return {"executions": [_execution_dict(e) for e in execs], "count": len(execs)}


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str):
    """Get execution details."""
    exec = engine.get_execution(execution_id)
    if not exec:
        raise HTTPException(404, "Execution not found")
    return _execution_dict(exec)


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str):
    """Cancel a running execution."""
    if not engine.cancel_execution(execution_id):
        raise HTTPException(400, "Execution cannot be cancelled")
    return {"status": "cancelled", "execution_id": execution_id}


# ── Health / Stats ─────────────────────────────────────────────

@router.get("/health")
async def will_health():
    """OpenWill health check."""
    return {
        "status": "ok",
        "component": "OpenWill",
        "engine": engine.stats(),
    }


@router.get("/stats")
async def will_stats():
    """Get OpenWill statistics."""
    return engine.stats()


# ── Helpers ────────────────────────────────────────────────────

def _workflow_dict(wf: Any) -> dict:
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "status": wf.status.value,
        "trigger": wf.trigger.value,
        "trigger_config": wf.trigger_config,
        "variables": wf.variables,
        "nodes": [n.model_dump() for n in wf.nodes],
        "edges": [e.model_dump() for e in wf.edges],
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
        "run_count": wf.run_count,
        "last_run_at": wf.last_run_at,
    }


def _execution_dict(exec: Any) -> dict:
    return {
        "id": exec.id,
        "workflow_id": exec.workflow_id,
        "workflow_name": exec.workflow_name,
        "status": exec.status.value,
        "started_at": exec.started_at,
        "completed_at": exec.completed_at,
        "steps": [s.model_dump() for s in exec.steps],
        "variables": exec.variables,
        "error": exec.error,
        "trigger_type": exec.trigger_type,
    }
