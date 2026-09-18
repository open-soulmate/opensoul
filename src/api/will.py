"""OpenWill API — 意志系统：工作流编排、条件触发、多分支流程。"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.will.engine import WorkflowEngine
from src.will.dag_planner import DAGPlanner
from src.will.models import (
    NodeType,
    TriggerType,
    WorkflowStatus,
)

router = APIRouter()
engine = WorkflowEngine()

# P3-③ 启动自动seed：首次启动（持久化文件为空）时注册真实系统编排
try:
    if not engine.list_workflows():
        from src.will.orchestration_seeder import seed_system_orchestrations
        _seed_result = seed_system_orchestrations(engine)
except Exception:
    pass  # seed失败不阻塞服务启动
dag_planner = DAGPlanner()


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
    """Execute a workflow with real cross-component action execution."""
    execution = await engine.execute_async(workflow_id, input_vars=req.variables)
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


@router.get("/action-types")
async def list_action_types():
    """List available workflow action types with their config schemas."""
    return {
        "action_types": [
            {
                "type": "http",
                "label": "HTTP Request",
                "description": "Make a real HTTP request to any URL",
                "config_schema": {
                    "url": {
                        "type": "string",
                        "required": True,
                        "description": "Target URL (supports ${var} interpolation)",
                    },
                    "method": {
                        "type": "string",
                        "default": "GET",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    },
                    "headers": {"type": "object", "description": "Request headers"},
                    "body": {"type": "object", "description": "Request body (for POST/PUT/PATCH)"},
                    "timeout": {
                        "type": "integer",
                        "default": 30,
                        "description": "Timeout in seconds",
                    },
                },
            },
            {
                "type": "llm",
                "label": "LLM Call",
                "description": "Call an LLM via OpenGland for AI-powered processing",
                "config_schema": {
                    "prompt": {
                        "type": "string",
                        "required": True,
                        "description": "User prompt (supports ${var})",
                    },
                    "system": {"type": "string", "description": "System prompt"},
                    "model": {
                        "type": "string",
                        "description": "Model name (auto-selected if empty)",
                    },
                    "temperature": {"type": "number", "default": 0.7},
                    "max_tokens": {"type": "integer", "default": 2048},
                    "timeout": {"type": "integer", "default": 60},
                },
            },
            {
                "type": "notify",
                "label": "Send Notification",
                "description": "Send a notification via OpenEcho (webhook, email, etc.)",
                "config_schema": {
                    "title": {"type": "string", "description": "Notification title"},
                    "content": {
                        "type": "string",
                        "required": True,
                        "description": "Notification content",
                    },
                    "channel": {
                        "type": "string",
                        "default": "webhook",
                        "enum": ["webhook", "email", "sms", "dingtalk", "wecom"],
                    },
                    "target": {"type": "string", "description": "Target address/URL"},
                    "priority": {
                        "type": "string",
                        "default": "normal",
                        "enum": ["low", "normal", "high", "urgent"],
                    },
                },
            },
            {
                "type": "knowledge_search",
                "label": "Knowledge Search",
                "description": "Search the OpenSoul knowledge base",
                "config_schema": {
                    "query": {
                        "type": "string",
                        "required": True,
                        "description": "Search query (supports ${var})",
                    },
                    "limit": {"type": "integer", "default": 5},
                },
            },
            {
                "type": "script",
                "label": "Run Script",
                "description": "Execute a shell command with safety constraints",
                "config_schema": {
                    "command": {
                        "type": "string",
                        "required": True,
                        "description": "Shell command (supports ${var})",
                    },
                    "cwd": {"type": "string", "description": "Working directory"},
                    "timeout": {"type": "integer", "default": 30},
                },
            },
            {
                "type": "organ",
                "label": "Organ API Call",
                "description": "Call any OpenSoul organ's API endpoint directly",
                "config_schema": {
                    "endpoint": {
                        "type": "string",
                        "required": True,
                        "description": "API path (e.g. '/api/vein/stats')",
                    },
                    "method": {"type": "string", "default": "GET"},
                    "body": {"type": "object", "description": "Request body or params"},
                    "timeout": {"type": "integer", "default": 30},
                },
            },
        ],
    }


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


# ── DAG Planning ──────────────────────────────────────────────


class DAGPlanRequest(BaseModel):
    goal: str
    llm_response: str


@router.post("/dag/plan")
async def create_dag_plan(req: DAGPlanRequest):
    """Create execution plan from LLM response."""
    plan = dag_planner.create_from_llm_response(req.goal, req.llm_response)
    if not plan:
        raise HTTPException(400, "Failed to parse LLM response into plan")
    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "total_steps": len(plan.steps),
        "progress": plan.progress,
        "mermaid": dag_planner.to_mermaid(plan.plan_id),
    }


@router.get("/dag/{plan_id}/status")
async def dag_status(plan_id: str):
    """Get plan status."""
    plan = dag_planner.get_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "progress": plan.progress,
        "is_complete": plan.is_complete,
        "total_steps": len(plan.steps),
        "steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "tool_name": s.tool_name,
                "status": s.status.value if hasattr(s.status, 'value') else str(s.status),
                "depends_on": s.depends_on,
                "error": s.error,
            }
            for s in plan.steps
        ],
    }


@router.get("/dag/stats")
async def dag_stats():
    """Get DAG planner statistics."""
    return dag_planner.get_stats()


# ── P0-8 后台作业队列（agno job_queue模式）──

# 运行时接线（2026-09-19 cron轮，"写了≠接线了"修复）：handler注册在模块
# 加载时完成（幂等，GET端点零副作用）；worker池懒启动——首次/jobs/submit
# 时start()（agno "executor上线才claim任务"）。此前c1feb676只落了队列基础
# 设施+API，register_handler/start调用点为0，提交的作业永远pending。
from src.will.job_queue import get_job_queue as _get_job_queue
from src.will.job_handlers import register_default_handlers as _register_job_handlers

_register_job_handlers(_get_job_queue())


class JobSubmitRequest(BaseModel):
    name: str
    params: dict = {}
    timeout_s: int = 300
    max_retries: int = 2
    idempotency_key: str = ""  # agno §5.2：同key pending/running不重复执行


@router.post("/seed-system")
async def seed_system():
    """P3-③: 注册真实系统编排为可视化workflow（幂等）"""
    from src.will.orchestration_seeder import seed_system_orchestrations
    result = seed_system_orchestrations(engine)
    return {"ok": not result["errors"], **result}


@router.get("/jobs/health")
async def job_queue_health():
    """Job queue health check."""
    return {"status": "ok", "component": "JobQueue", **_get_job_queue().get_stats()}


@router.post("/jobs/submit")
async def job_submit(req: JobSubmitRequest):
    """Submit a background job. Returns job_id immediately.

    接线语义：首次提交懒启动worker池（start()幂等）；未注册handler的作业
    落FAILED("No handler for job type")——失败可见（mem0 §1.1），不静默吞。
    """
    jq = _get_job_queue()
    _register_job_handlers(jq)  # 幂等兜底（模块加载时已注册）
    await jq.start()
    existing = jq.find_by_idempotency_key(req.idempotency_key)
    job_id = await jq.submit(
        req.name, req.params, req.timeout_s, req.max_retries,
        idempotency_key=req.idempotency_key,
    )
    return {
        "job_id": job_id,
        "status": "submitted",
        "deduped": bool(existing) and existing == job_id,
        "workers": jq.get_stats()["workers"],
    }


@router.post("/jobs/purge")
async def job_purge(status: str = "", name_pattern: str = "", older_than_days: int = 0):
    """清理作业历史 — 集成修复#4: test_job/stress_test等测试噪声积压清理。"""
    from src.will.job_queue import get_job_queue
    purged = get_job_queue().purge(
        status=status, name_pattern=name_pattern, older_than_days=older_than_days
    )
    return {"purged": purged}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    """Get job status and result."""
    from src.will.job_queue import get_job_queue
    result = get_job_queue().get(job_id)
    if not result:
        raise HTTPException(404, "Job not found")
    return result


@router.get("/jobs")
async def job_list(status: str = "", limit: int = 50):
    """List jobs, optionally filtered by status."""
    from src.will.job_queue import get_job_queue
    return {"jobs": get_job_queue().list_jobs(status, limit)}


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    """Cancel a pending job."""
    from src.will.job_queue import get_job_queue
    ok = get_job_queue().cancel(job_id)
    return {"cancelled": ok}
