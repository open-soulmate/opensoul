from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.database.postgres import pg_pool
from src.services.rag import rag_query
from src.services import knowledge as knowledge_service
from src.models.knowledge import KnowledgeCreate

router = APIRouter()


# ── Agent node management ──────────────────────────────────────────────

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


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(req: AgentRegisterRequest):
    """Register a new agent node."""
    agent_id = uuid4()
    token = uuid4().hex  # Simple token for demo; use JWT in production

    row = await pg_pool.fetchrow(
        "INSERT INTO agents (id, name, agent_type, capabilities, metadata, token, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'active') "
        "RETURNING id, name, token, created_at as registered_at",
        agent_id, req.name, req.agent_type, req.capabilities, req.metadata, token,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to register agent")
    return dict(row)


@router.post("/heartbeat")
async def agent_heartbeat(req: AgentHeartbeatRequest):
    """Update agent heartbeat."""
    result = await pg_pool.execute(
        "UPDATE agents SET status = $1, last_heartbeat = NOW() WHERE id = $2",
        req.status, req.agent_id,
    )
    if "UPDATE 0" in result:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": req.agent_id, "status": req.status, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.post("/report")
async def agent_report(req: AgentReportRequest):
    """Agent reports data back to the system."""
    await pg_pool.execute(
        "INSERT INTO agent_reports (agent_id, report_type, data) VALUES ($1, $2, $3)",
        req.agent_id, req.report_type, req.data,
    )
    return {"status": "received", "agent_id": req.agent_id}


@router.get("/nodes")
async def list_agent_nodes(status: str | None = None):
    """List all registered agent nodes."""
    if status:
        rows = await pg_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at "
            "FROM agents WHERE status = $1 ORDER BY created_at DESC",
            status,
        )
    else:
        rows = await pg_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at "
            "FROM agents ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


# ── Agent memory operations (legacy) ──────────────────────────────────

class AgentRememberRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = []


class AgentRecallRequest(BaseModel):
    question: str
    top_k: int = 5


@router.post("/remember")
async def remember(req: AgentRememberRequest, user_id: UUID):
    """Agent stores a new memory."""
    data = KnowledgeCreate(title=req.title, content=req.content, tags=req.tags)
    row = await knowledge_service.create_knowledge(data, user_id)
    return {"status": "remembered", "id": row["id"]}


@router.post("/recall")
async def recall(req: AgentRecallRequest, user_id: UUID):
    """Agent retrieves relevant memories."""
    result = await rag_query(req.question, user_id, req.top_k)
    return result
