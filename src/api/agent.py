from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.database.postgres import db_pool
from src.middleware.auth import get_current_user, require_agent, require_role
from src.services.rag import rag_query
from src.services import knowledge as knowledge_service
from src.models.knowledge import KnowledgeCreate

router = APIRouter()


# ── Request / Response models ──────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    name: str
    agent_type: str = "generic"
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class AgentRegisterResponse(BaseModel):
    agent_id: UUID
    name: str
    token: str
    registered_at: datetime


class AgentHeartbeatRequest(BaseModel):
    agent_id: UUID
    status: str = "active"


class AgentReportRequest(BaseModel):
    agent_id: UUID
    report_type: str
    data: dict


# ── Agent node management ──────────────────────────────────────────────

@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    req: AgentRegisterRequest,
    current_user: dict = Depends(require_role("admin")),
):
    """Register a new agent node — admin only. Returns agent token."""
    agent_id = uuid4()
    token = uuid4().hex

    row = await db_pool.fetchrow(
        "INSERT INTO agents (id, name, agent_type, capabilities, metadata, token, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'active') "
        "RETURNING id, name, token, created_at as registered_at",
        agent_id, req.name, req.agent_type, req.capabilities, req.metadata, token,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to register agent")
    return dict(row)


@router.post("/heartbeat")
async def agent_heartbeat(
    req: AgentHeartbeatRequest,
    agent: dict = Depends(require_agent),
):
    """Update agent heartbeat — requires valid X-Agent-Token header."""
    result = await db_pool.execute(
        "UPDATE agents SET status = $1, last_heartbeat = NOW() WHERE id = $2",
        req.status, req.agent_id,
    )
    if "UPDATE 0" in result:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": req.agent_id,
        "status": req.status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/report")
async def agent_report(
    req: AgentReportRequest,
    agent: dict = Depends(require_agent),
):
    """Agent reports Soma-collected data — stored and queued for processing."""
    await db_pool.execute(
        "INSERT INTO agent_reports (agent_id, report_type, data) VALUES ($1, $2, $3)",
        req.agent_id, req.report_type, req.data,
    )
    return {"status": "received", "agent_id": req.agent_id}


@router.get("/nodes")
async def list_agent_nodes(
    status: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List all registered agent nodes with online/offline status."""
    if status:
        rows = await db_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at "
            "FROM agents WHERE status = $1 ORDER BY created_at DESC",
            status,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at "
            "FROM agents ORDER BY created_at DESC"
        )
    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        d = dict(r)
        hb = d.get("last_heartbeat")
        if hb and (now - hb).total_seconds() < 120:
            d["online"] = True
        else:
            d["online"] = d["status"] == "active" and hb is not None and (now - hb).total_seconds() < 120
        result.append(d)
    return result


@router.delete("/nodes/{node_id}")
async def delete_agent_node(
    node_id: UUID,
    current_user: dict = Depends(require_role("admin")),
):
    """Delete an agent node — admin only."""
    result = await db_pool.execute("DELETE FROM agents WHERE id = $1", node_id)
    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted", "agent_id": str(node_id)}


# ── Agent memory operations ────────────────────────────────────────────

class AgentRememberRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = []


class AgentRecallRequest(BaseModel):
    question: str
    top_k: int = 5


@router.post("/remember")
async def remember(req: AgentRememberRequest, current_user: dict = Depends(get_current_user)):
    """Agent stores a new memory."""
    data = KnowledgeCreate(title=req.title, content=req.content, tags=req.tags)
    row = await knowledge_service.create_knowledge(data, current_user["id"])
    return {"status": "remembered", "id": row["id"]}


@router.post("/recall")
async def recall(req: AgentRecallRequest, current_user: dict = Depends(get_current_user)):
    """Agent retrieves relevant memories."""
    result = await rag_query(req.question, current_user["id"], req.top_k)
    return result
