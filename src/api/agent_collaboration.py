"""Agent Collaboration Protocol — registration, messaging, handoff, WebSocket channel."""

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenNerve"}


DB_PATH = Path.home() / "opensoul" / "data" / "agent_collaboration.db"

# In-memory WebSocket connection registry
_ws_connections: dict[str, WebSocket] = {}


# ── Database ───────────────────────────────────────────────────────────


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            model      TEXT DEFAULT '',
            role       TEXT DEFAULT '',
            endpoint   TEXT DEFAULT '',
            token      TEXT NOT NULL,
            status     TEXT DEFAULT 'online',
            last_seen  TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_capabilities (
            agent_id      TEXT NOT NULL,
            capability    TEXT NOT NULL,
            PRIMARY KEY (agent_id, capability),
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS agent_messages (
            id         TEXT PRIMARY KEY,
            from_agent TEXT NOT NULL,
            to_agent   TEXT NOT NULL,
            msg_type   TEXT NOT NULL,
            payload    TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


_init_db()


# ── Pydantic models ────────────────────────────────────────────────────


class AgentRegisterRequest(BaseModel):
    agent_id: str
    name: str
    capabilities: list[str] = Field(default_factory=list)
    model: str = ""
    role: str = ""
    endpoint: str = ""  # WebSocket address


class AgentRegisterResponse(BaseModel):
    agent_id: str
    token: str
    status: str


class MessageRequest(BaseModel):
    from_agent: str
    to_agent: str
    type: str  # task | result | query
    payload: dict = Field(default_factory=dict)


class MessageResponse(BaseModel):
    message_id: str
    status: str


class HandoffRequest(BaseModel):
    source_agent: str
    target_agent: str
    full_context: str
    goal: str


class HandoffResponse(BaseModel):
    filtered_context: str
    removed_sections: list[str]


class AgentStatus(BaseModel):
    agent_id: str
    name: str
    model: str
    role: str
    capabilities: list[str]
    status: str
    last_seen: str


# ── 1. POST /api/agents/register ──────────────────────────────────────


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(req: AgentRegisterRequest):
    """Register an agent and receive a token for subsequent communication."""
    now = datetime.now(UTC).isoformat()
    token = uuid.uuid4().hex

    conn = _get_db()
    try:
        # Upsert agent record
        conn.execute(
            "INSERT INTO agents (agent_id, name, model, role, endpoint, token, status, last_seen, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET "
            "  name=excluded.name, model=excluded.model, role=excluded.role, "
            "  endpoint=excluded.endpoint, token=excluded.token, status='online', last_seen=excluded.last_seen",
            (req.agent_id, req.name, req.model, req.role, req.endpoint, token, now, now),
        )
        # Refresh capabilities
        conn.execute("DELETE FROM agent_capabilities WHERE agent_id = ?", (req.agent_id,))
        for cap in req.capabilities:
            conn.execute(
                "INSERT OR IGNORE INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
                (req.agent_id, cap),
            )
        conn.commit()
    finally:
        conn.close()

    logger.info("Agent registered: %s (%s)", req.agent_id, req.name)
    return AgentRegisterResponse(agent_id=req.agent_id, token=token, status="online")


# ── 2. POST /api/agents/message ───────────────────────────────────────


@router.post("/message", response_model=MessageResponse)
async def send_message(req: MessageRequest):
    """Send a message between agents; persisted to SQLite and forwarded via WebSocket if online."""
    if req.type not in ("task", "result", "query"):
        raise HTTPException(status_code=400, detail="type must be task | result | query")

    msg_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()

    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO agent_messages (id, from_agent, to_agent, msg_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, req.from_agent, req.to_agent, req.type, json.dumps(req.payload), now),
        )
        conn.commit()
    finally:
        conn.close()

    # Attempt real-time delivery via WebSocket
    ws = _ws_connections.get(req.to_agent)
    if ws:
        try:
            await ws.send_json(
                {
                    "event": "message",
                    "message_id": msg_id,
                    "from_agent": req.from_agent,
                    "type": req.type,
                    "payload": req.payload,
                    "created_at": now,
                }
            )
        except Exception:
            logger.warning("Failed to deliver WS message to %s", req.to_agent)

    return MessageResponse(message_id=msg_id, status="delivered")


# ── 3. POST /api/agents/handoff ───────────────────────────────────────


@router.post("/handoff", response_model=HandoffResponse)
async def handoff_context(req: HandoffRequest):
    """Filter full_context down to what's relevant for the target agent's goal."""
    # Goal-driven keyword extraction for filtering
    goal_words = set(_tokenize(req.goal.lower()))
    sections = _split_sections(req.full_context)

    kept: list[str] = []
    removed: list[str] = []

    for title, body in sections:
        section_text = (title + " " + body).lower()
        section_words = set(_tokenize(section_text))
        overlap = len(goal_words & section_words)
        # Keep section if it shares >= 1 keyword with goal, or if it's short enough to keep
        if overlap > 0 or len(body.strip()) < 40:
            kept.append(f"## {title}\n{body}" if title else body)
        else:
            removed.append(title or body[:60])

    # If everything was removed, fall back to first 30% of context as a safety net
    if not kept:
        cutoff = max(1, len(sections) // 3)
        for title, body in sections[:cutoff]:
            kept.append(f"## {title}\n{body}" if title else body)
        removed = removed[: len(sections) - cutoff]

    return HandoffResponse(
        filtered_context="\n\n".join(kept),
        removed_sections=removed,
    )


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation splitter."""
    import re

    return [w for w in re.split(r"[^\w]+", text) if len(w) > 2]


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown-style text into (title, body) pairs."""
    import re

    parts = re.split(r"^(#{1,4}\s+.+)$", text, flags=re.MULTILINE)
    sections: list[tuple[str, str]] = []
    i = 0
    # Leading text before any heading
    if parts and not parts[0].startswith("#"):
        sections.append(("", parts[0]))
        i = 1
    while i < len(parts):
        title = parts[i].strip("# ").strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections.append((title, body))
        i += 2
    return sections


# ── 4. GET /api/agents/status ─────────────────────────────────────────


@router.get("/status", response_model=list[AgentStatus])
async def get_agent_status():
    """Return all registered agents with their current status."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall()
        result: list[AgentStatus] = []
        for row in rows:
            caps = conn.execute(
                "SELECT capability FROM agent_capabilities WHERE agent_id = ?",
                (row["agent_id"],),
            ).fetchall()
            result.append(
                AgentStatus(
                    agent_id=row["agent_id"],
                    name=row["name"],
                    model=row["model"],
                    role=row["role"],
                    capabilities=[c["capability"] for c in caps],
                    status=row["status"],
                    last_seen=row["last_seen"],
                )
            )
        return result
    finally:
        conn.close()


# ── 5. WebSocket /ws/agent/{agent_id} ─────────────────────────────────


@router.websocket("/ws/agent/{agent_id}")
async def agent_ws(websocket: WebSocket, agent_id: str):
    """Real-time bidirectional channel for an agent."""
    await websocket.accept()
    _ws_connections[agent_id] = websocket
    logger.info("Agent WS connected: %s", agent_id)

    # Mark agent online
    now = datetime.now(UTC).isoformat()
    conn = _get_db()
    conn.execute(
        "UPDATE agents SET status = 'online', last_seen = ? WHERE agent_id = ?", (now, agent_id)
    )
    conn.commit()
    conn.close()

    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("event", "")

            if event_type == "ping":
                await websocket.send_json({"event": "pong"})
            elif event_type == "message":
                # Forward message through the message endpoint logic
                target = data.get("to_agent")
                if target:
                    msg_id = uuid.uuid4().hex
                    msg_now = datetime.now(UTC).isoformat()
                    conn = _get_db()
                    conn.execute(
                        "INSERT INTO agent_messages (id, from_agent, to_agent, msg_type, payload, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            msg_id,
                            agent_id,
                            target,
                            data.get("type", "query"),
                            json.dumps(data.get("payload", {})),
                            msg_now,
                        ),
                    )
                    conn.commit()
                    conn.close()
                    # Deliver to target if connected
                    target_ws = _ws_connections.get(target)
                    if target_ws:
                        try:
                            await target_ws.send_json(
                                {
                                    "event": "message",
                                    "message_id": msg_id,
                                    "from_agent": agent_id,
                                    "type": data.get("type", "query"),
                                    "payload": data.get("payload", {}),
                                    "created_at": msg_now,
                                }
                            )
                        except Exception as exc:
                            logging.getLogger(__name__).debug("probe skipped: %s", exc)
            elif event_type == "heartbeat":
                hb_now = datetime.now(UTC).isoformat()
                conn = _get_db()
                conn.execute(
                    "UPDATE agents SET last_seen = ? WHERE agent_id = ?", (hb_now, agent_id)
                )
                conn.commit()
                conn.close()
            else:
                await websocket.send_json(
                    {"event": "error", "detail": f"Unknown event: {event_type}"}
                )

    except WebSocketDisconnect:
        logger.info("Agent WS disconnected: %s", agent_id)
    except Exception as exc:
        logger.warning("Agent WS error for %s: %s", agent_id, exc)
    finally:
        _ws_connections.pop(agent_id, None)
        offline_now = datetime.now(UTC).isoformat()
        conn = _get_db()
        conn.execute(
            "UPDATE agents SET status = 'offline', last_seen = ? WHERE agent_id = ?",
            (offline_now, agent_id),
        )
        conn.commit()
        conn.close()
