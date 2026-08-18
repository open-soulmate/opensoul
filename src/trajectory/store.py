"""Trajectory store — records Agent execution events to SQLite, supports replay and fork."""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from src.database.postgres import db_pool

logger = logging.getLogger(__name__)


class EventType(StrEnum):
    """Types of trajectory events."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_INPUT = "user_input"
    LLM_CALL = "llm_call"
    LLM_RESPONSE = "llm_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_DISPATCH = "agent_dispatch"
    AGENT_RESULT = "agent_result"
    ERROR = "error"
    CHECKPOINT = "checkpoint"
    BRANCH = "branch"
    CUSTOM = "custom"


@dataclass
class TrajectoryEvent:
    """A single event in an agent execution trajectory."""

    id: str = ""
    session_id: str = ""
    parent_event_id: str | None = None
    event_type: str = ""
    agent_id: str = ""
    content: str = ""
    metadata_json: str = "{}"
    token_usage: int = 0
    duration_ms: float = 0.0
    status: str = "ok"
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    @property
    def metadata(self) -> dict:
        try:
            return json.loads(self.metadata_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metadata"] = self.metadata
        return d


@dataclass
class TrajectorySession:
    """A complete agent execution session with its events."""

    id: str = ""
    agent_id: str = ""
    task_description: str = ""
    status: str = "running"
    forked_from: str | None = None
    fork_point_event_id: str | None = None
    total_events: int = 0
    total_tokens: int = 0
    total_duration_ms: float = 0.0
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    ended_at: str | None = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class TrajectoryStore:
    """Persistent storage for trajectory sessions and events."""

    async def ensure_tables(self):
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS trajectory_sessions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL DEFAULT '',
                task_description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                forked_from TEXT,
                fork_point_event_id TEXT,
                total_events INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                total_duration_ms REAL NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                ended_at TEXT
            )
        """)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS trajectory_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_event_id TEXT,
                event_type TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                token_usage INTEGER NOT NULL DEFAULT 0,
                duration_ms REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                created_at TEXT NOT NULL
            )
        """)
        await db_pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_events_session
            ON trajectory_events(session_id, created_at)
        """)

    # ── Session CRUD ─────────────────────────────────────────

    async def create_session(
        self,
        agent_id: str = "",
        task_description: str = "",
        forked_from: str | None = None,
        fork_point_event_id: str | None = None,
        tags: list[str] | None = None,
    ) -> TrajectorySession:
        await self.ensure_tables()
        s = TrajectorySession(
            agent_id=agent_id,
            task_description=task_description,
            forked_from=forked_from,
            fork_point_event_id=fork_point_event_id,
            tags=tags or [],
        )
        await db_pool.execute(
            """INSERT INTO trajectory_sessions
               (id, agent_id, task_description, status, forked_from, fork_point_event_id,
                total_events, total_tokens, total_duration_ms, tags_json, created_at, ended_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            s.id,
            s.agent_id,
            s.task_description,
            s.status,
            s.forked_from,
            s.fork_point_event_id,
            s.total_events,
            s.total_tokens,
            s.total_duration_ms,
            json.dumps(s.tags),
            s.created_at,
            s.ended_at,
        )
        return s

    async def get_session(self, session_id: str) -> TrajectorySession | None:
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_sessions WHERE id = ?", session_id)
        if not row:
            return None
        return self._row_to_session(row)

    async def list_sessions(
        self, agent_id: str = "", status: str = "", limit: int = 50, offset: int = 0
    ) -> list[TrajectorySession]:
        await self.ensure_tables()
        clauses = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([limit, offset])
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_sessions{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            *params,
        )
        return [self._row_to_session(r) for r in rows]

    async def end_session(self, session_id: str, status: str = "completed"):
        await db_pool.execute(
            """UPDATE trajectory_sessions
               SET status = ?, ended_at = ?
               WHERE id = ?""",
            status,
            datetime.now(UTC).isoformat(),
            session_id,
        )

    async def delete_session(self, session_id: str):
        await db_pool.execute("DELETE FROM trajectory_events WHERE session_id = ?", session_id)
        await db_pool.execute("DELETE FROM trajectory_sessions WHERE id = ?", session_id)

    async def count_sessions(self) -> int:
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT COUNT(*) as cnt FROM trajectory_sessions")
        return row["cnt"] if row else 0

    # ── Event CRUD ───────────────────────────────────────────

    async def add_event(self, event: TrajectoryEvent) -> TrajectoryEvent:
        await self.ensure_tables()
        if not event.session_id:
            raise ValueError("event.session_id is required")
        await db_pool.execute(
            """INSERT INTO trajectory_events
               (id, session_id, parent_event_id, event_type, agent_id,
                content, metadata_json, token_usage, duration_ms, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            event.id,
            event.session_id,
            event.parent_event_id,
            event.event_type,
            event.agent_id,
            event.content,
            event.metadata_json,
            event.token_usage,
            event.duration_ms,
            event.status,
            event.created_at,
        )
        # Update session counters
        await db_pool.execute(
            """UPDATE trajectory_sessions
               SET total_events = total_events + 1,
                   total_tokens = total_tokens + ?
               WHERE id = ?""",
            event.token_usage,
            event.session_id,
        )
        return event

    async def get_events(self, session_id: str, limit: int = 500) -> list[TrajectoryEvent]:
        await self.ensure_tables()
        rows = await db_pool.fetch(
            """SELECT * FROM trajectory_events
               WHERE session_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            session_id,
            limit,
        )
        return [self._row_to_event(r) for r in rows]

    async def get_event(self, event_id: str) -> TrajectoryEvent | None:
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_events WHERE id = ?", event_id)
        return self._row_to_event(row) if row else None

    async def search_events(
        self, session_id: str = "", event_type: str = "", keyword: str = "", limit: int = 50
    ) -> list[TrajectoryEvent]:
        await self.ensure_tables()
        clauses = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if keyword:
            clauses.append("content LIKE ?")
            params.append(f"%{keyword}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_events{where} ORDER BY created_at DESC LIMIT ?",
            *params,
        )
        return [self._row_to_event(r) for r in rows]

    # ── Fork (Branch) ────────────────────────────────────────

    async def fork_session(
        self, source_session_id: str, fork_point_event_id: str, new_agent_id: str = ""
    ) -> TrajectorySession:
        """Fork a session at a specific event — copies events up to the fork point
        and creates a new session for branching execution."""
        source = await self.get_session(source_session_id)
        if not source:
            raise ValueError(f"Source session {source_session_id} not found")

        # Create new session marked as fork
        new_session = await self.create_session(
            agent_id=new_agent_id or source.agent_id,
            task_description=f"[Fork] {source.task_description}",
            forked_from=source_session_id,
            fork_point_event_id=fork_point_event_id,
            tags=source.tags + ["fork"],
        )

        # Copy events up to and including the fork point
        events = await self.get_events(source_session_id)
        copying = True
        for ev in events:
            if not copying:
                break
            new_ev = TrajectoryEvent(
                session_id=new_session.id,
                parent_event_id=ev.parent_event_id,
                event_type=ev.event_type,
                agent_id=ev.agent_id,
                content=ev.content,
                metadata_json=ev.metadata_json,
                token_usage=ev.token_usage,
                duration_ms=ev.duration_ms,
                status=ev.status,
            )
            await self.add_event(new_ev)
            if ev.id == fork_point_event_id:
                copying = False

        # Add a branch marker event
        await self.add_event(
            TrajectoryEvent(
                session_id=new_session.id,
                event_type=EventType.BRANCH.value,
                content=f"Branched from session {source_session_id} at event {fork_point_event_id}",
                metadata_json=json.dumps(
                    {
                        "source_session": source_session_id,
                        "fork_event": fork_point_event_id,
                    }
                ),
            )
        )

        return new_session

    # ── Stats ────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        await self.ensure_tables()
        total = await db_pool.fetchrow("SELECT COUNT(*) as cnt FROM trajectory_sessions")
        running = await db_pool.fetchrow(
            "SELECT COUNT(*) as cnt FROM trajectory_sessions WHERE status = 'running'"
        )
        events = await db_pool.fetchrow("SELECT COUNT(*) as cnt FROM trajectory_events")
        tokens = await db_pool.fetchrow(
            "SELECT COALESCE(SUM(total_tokens), 0) as total FROM trajectory_sessions"
        )
        return {
            "total_sessions": total["cnt"] if total else 0,
            "running_sessions": running["cnt"] if running else 0,
            "total_events": events["cnt"] if events else 0,
            "total_tokens": tokens["total"] if tokens else 0,
        }

    # ── Analytics ──────────────────────────────────────────────

    async def get_tool_analytics(self, limit: int = 50) -> dict:
        """Get tool usage frequency and success rate analytics."""
        await self.ensure_tables()

        # Tool usage frequency
        tool_rows = await db_pool.fetch(
            """
            SELECT
                COALESCE(json_extract(metadata_json, '$.tool_name'), 'unknown') as tool_name,
                COUNT(*) as usage_count,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count,
                AVG(duration_ms) as avg_duration_ms,
                SUM(token_usage) as total_tokens
            FROM trajectory_events
            WHERE event_type IN ('tool_call', 'tool_result')
            GROUP BY tool_name
            ORDER BY usage_count DESC
            LIMIT ?
        """,
            limit,
        )

        tools = []
        for row in tool_rows:
            usage = row["usage_count"] or 0
            success = row["success_count"] or 0
            tools.append(
                {
                    "tool_name": row["tool_name"],
                    "usage_count": usage,
                    "success_count": success,
                    "error_count": row["error_count"] or 0,
                    "success_rate": round(success / usage * 100, 1) if usage > 0 else 0,
                    "avg_duration_ms": round(row["avg_duration_ms"] or 0, 1),
                    "total_tokens": row["total_tokens"] or 0,
                }
            )

        return {"tools": tools, "total_tools": len(tools)}

    async def get_agent_analytics(self, limit: int = 50) -> dict:
        """Get per-agent performance analytics."""
        await self.ensure_tables()

        agent_rows = await db_pool.fetch(
            """
            SELECT
                agent_id,
                COUNT(*) as event_count,
                SUM(token_usage) as total_tokens,
                SUM(duration_ms) as total_duration_ms,
                AVG(duration_ms) as avg_duration_ms,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
            FROM trajectory_events
            WHERE agent_id != ''
            GROUP BY agent_id
            ORDER BY event_count DESC
            LIMIT ?
        """,
            limit,
        )

        agents = []
        for row in agent_rows:
            events = row["event_count"] or 0
            success = row["success_count"] or 0
            agents.append(
                {
                    "agent_id": row["agent_id"],
                    "event_count": events,
                    "total_tokens": row["total_tokens"] or 0,
                    "total_duration_ms": round(row["total_duration_ms"] or 0, 1),
                    "avg_duration_ms": round(row["avg_duration_ms"] or 0, 1),
                    "success_count": success,
                    "error_count": row["error_count"] or 0,
                    "success_rate": round(success / events * 100, 1) if events > 0 else 0,
                }
            )

        return {"agents": agents, "total_agents": len(agents)}

    async def get_event_type_analytics(self) -> dict:
        """Get event type distribution analytics."""
        await self.ensure_tables()

        type_rows = await db_pool.fetch("""
            SELECT
                event_type,
                COUNT(*) as count,
                SUM(token_usage) as total_tokens,
                AVG(duration_ms) as avg_duration_ms
            FROM trajectory_events
            GROUP BY event_type
            ORDER BY count DESC
        """)

        types = []
        for row in type_rows:
            types.append(
                {
                    "event_type": row["event_type"],
                    "count": row["count"] or 0,
                    "total_tokens": row["total_tokens"] or 0,
                    "avg_duration_ms": round(row["avg_duration_ms"] or 0, 1),
                }
            )

        return {"event_types": types}

    async def get_token_analytics(self, days: int = 30) -> dict:
        """Get token usage over time (daily breakdown)."""
        await self.ensure_tables()

        daily_rows = await db_pool.fetch(
            """
            SELECT
                DATE(created_at) as day,
                SUM(token_usage) as tokens,
                COUNT(*) as events
            FROM trajectory_events
            WHERE created_at >= datetime('now', ?)
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            LIMIT ?
        """,
            f"-{days} days",
            days,
        )

        daily = []
        for row in daily_rows:
            daily.append(
                {
                    "day": row["day"],
                    "tokens": row["tokens"] or 0,
                    "events": row["events"] or 0,
                }
            )

        # Summary
        total_tokens = sum(d["tokens"] for d in daily)
        total_events = sum(d["events"] for d in daily)
        avg_daily = round(total_tokens / len(daily), 0) if daily else 0

        return {
            "daily": daily,
            "summary": {
                "total_tokens": total_tokens,
                "total_events": total_events,
                "avg_daily_tokens": avg_daily,
                "days_tracked": len(daily),
            },
        }

    # ── Internal helpers ─────────────────────────────────────

    def _row_to_session(self, row) -> TrajectorySession:
        d = dict(row)
        tags = d.get("tags_json", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        return TrajectorySession(
            id=d["id"],
            agent_id=d.get("agent_id", ""),
            task_description=d.get("task_description", ""),
            status=d.get("status", "running"),
            forked_from=d.get("forked_from"),
            fork_point_event_id=d.get("fork_point_event_id"),
            total_events=d.get("total_events", 0),
            total_tokens=d.get("total_tokens", 0),
            total_duration_ms=d.get("total_duration_ms", 0),
            tags=tags,
            created_at=d.get("created_at", ""),
            ended_at=d.get("ended_at"),
        )

    def _row_to_event(self, row) -> TrajectoryEvent:
        d = dict(row)
        return TrajectoryEvent(
            id=d["id"],
            session_id=d.get("session_id", ""),
            parent_event_id=d.get("parent_event_id"),
            event_type=d.get("event_type", ""),
            agent_id=d.get("agent_id", ""),
            content=d.get("content", ""),
            metadata_json=d.get("metadata_json", "{}"),
            token_usage=d.get("token_usage", 0),
            duration_ms=d.get("duration_ms", 0),
            status=d.get("status", "ok"),
            created_at=d.get("created_at", ""),
        )


# Singleton
trajectory_store = TrajectoryStore()
