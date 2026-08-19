"""AI群管理API — 基于Graph层四角色模型(advisor/executor/verifier/human)"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/ai-groups", tags=["ai-groups"])
@router.get("/health")
async def ai_groups_health():
    """AIGroups health check."""
    return {"status": "ok", "component": "AIGroups"}
DB_PATH = Path.home() / "opensoul" / "data" / "ai_groups.db"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_group_agents (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            model TEXT DEFAULT '',
            status TEXT DEFAULT 'online',
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
        CREATE TABLE IF NOT EXISTS ai_group_tasks (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            parent_task_id TEXT,
            goal TEXT NOT NULL,
            constraints TEXT DEFAULT '[]',
            completion_criteria TEXT DEFAULT '[]',
            status TEXT DEFAULT 'planning',
            assigned_agent_id TEXT,
            result TEXT DEFAULT '',
            quality_score REAL DEFAULT 0,
            iteration INTEGER DEFAULT 0,
            max_iterations INTEGER DEFAULT 2,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
    """)
    conn.commit()
    # 为已有表添加 temperature 列（如果不存在）
    try:
        conn.execute("ALTER TABLE ai_group_agents ADD COLUMN temperature REAL DEFAULT 0.7")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.close()


init_db()


# ── Pydantic Models ──────────────────────────────────────
class AgentRole(BaseModel):
    agent_id: str
    name: str
    role: str  # advisor / executor / verifier / human
    model: str = ""


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""
    agents: list[AgentRole] = []


class SubmitTaskRequest(BaseModel):
    goal: str
    constraints: list[str] = []
    completion_criteria: list[str] = []


class AssignTaskRequest(BaseModel):
    agent_id: str


class CompleteTaskRequest(BaseModel):
    result: str = ""
    quality_score: float = 0


class VerifyTaskRequest(BaseModel):
    passed: bool
    reason: str = ""


class UpdateGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class UpdateAgentRequest(BaseModel):
    model: str | None = None
    temperature: float | None = None
    role: str | None = None


# ── Helper ───────────────────────────────────────────────
def row_to_dict(row):
    return dict(row) if row else None


def rows_to_list(rows):
    return [dict(r) for r in rows]


def auto_decompose_task(goal: str) -> list[dict]:
    """自动分解任务为子任务（基于目标关键词分析）"""
    subtasks = []
    keywords_map = {
        "分析": {"goal": "需求分析与信息收集", "role": "advisor"},
        "设计": {"goal": "架构设计与方案制定", "role": "advisor"},
        "方案": {"goal": "方案编写与内容组织", "role": "executor"},
        "实现": {"goal": "代码实现与功能开发", "role": "executor"},
        "测试": {"goal": "质量验证与测试检查", "role": "verifier"},
        "审查": {"goal": "最终审查与质量把关", "role": "advisor"},
        "文档": {"goal": "文档编写与整理", "role": "executor"},
        "部署": {"goal": "部署上线与配置", "role": "executor"},
    }
    matched_roles = set()
    for kw, info in keywords_map.items():
        if kw in goal and info["role"] not in matched_roles:
            subtasks.append(info)
            matched_roles.add(info["role"])

    # 默认分解：规划→执行→验证→审查
    if len(subtasks) < 2:
        subtasks = [
            {"goal": f"规划：分析'{goal}'的需求和步骤", "role": "advisor"},
            {"goal": f"执行：按计划完成'{goal}'", "role": "executor"},
            {"goal": f"验证：检查'{goal}'的完成质量", "role": "verifier"},
            {"goal": f"审查：最终确认'{goal}'的结果", "role": "advisor"},
        ]
    return subtasks


def select_agent_for_role(conn, group_id: str, role: str) -> str | None:
    """为指定角色选择可用Agent"""
    row = conn.execute(
        "SELECT agent_id FROM ai_group_agents WHERE group_id=? AND role=? AND status='online' LIMIT 1",
        (group_id, role),
    ).fetchone()
    if row:
        return row["agent_id"]
    # 回退：选任意在线Agent
    row = conn.execute(
        "SELECT agent_id FROM ai_group_agents WHERE group_id=? AND status='online' LIMIT 1",
        (group_id,),
    ).fetchone()
    return row["agent_id"] if row else None


# ── Endpoints ────────────────────────────────────────────
@router.post("")
def create_group(req: CreateGroupRequest):
    conn = get_db()
    try:
        group_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO ai_groups (id, name, description, status, created_at) VALUES (?,?,?,?,?)",
            (group_id, req.name, req.description, "active", now),
        )
        for agent in req.agents:
            conn.execute(
                "INSERT INTO ai_group_agents (id, group_id, agent_id, name, role, model, status) VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4())[:8],
                    group_id,
                    agent.agent_id,
                    agent.name,
                    agent.role,
                    agent.model,
                    "online",
                ),
            )
        conn.commit()
        return {"id": group_id, "name": req.name, "status": "active"}
    finally:
        conn.close()


@router.get("")
def list_groups():
    conn = get_db()
    try:
        groups = rows_to_list(
            conn.execute("SELECT * FROM ai_groups ORDER BY created_at DESC").fetchall()
        )
        for g in groups:
            g["agents"] = rows_to_list(
                conn.execute(
                    "SELECT * FROM ai_group_agents WHERE group_id=?", (g["id"],)
                ).fetchall()
            )
            g["task_count"] = conn.execute(
                "SELECT COUNT(*) as c FROM ai_group_tasks WHERE group_id=?", (g["id"],)
            ).fetchone()["c"]
        return groups
    finally:
        conn.close()


@router.get("/{group_id}")
def get_group(group_id: str):
    conn = get_db()
    try:
        group = row_to_dict(
            conn.execute("SELECT * FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        )
        if not group:
            raise HTTPException(404, "AI群不存在")
        group["agents"] = rows_to_list(
            conn.execute("SELECT * FROM ai_group_agents WHERE group_id=?", (group_id,)).fetchall()
        )
        group["tasks"] = rows_to_list(
            conn.execute(
                "SELECT * FROM ai_group_tasks WHERE group_id=? ORDER BY created_at DESC",
                (group_id,),
            ).fetchall()
        )
        return group
    finally:
        conn.close()


@router.post("/{group_id}/tasks")
def submit_task(group_id: str, req: SubmitTaskRequest):
    conn = get_db()
    try:
        group = conn.execute("SELECT id FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "AI群不存在")

        now = datetime.now().isoformat()
        task_id = str(uuid.uuid4())[:8]

        # 创建主任务
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, goal, constraints, completion_criteria, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                task_id,
                group_id,
                req.goal,
                json.dumps(req.constraints, ensure_ascii=False),
                json.dumps(req.completion_criteria, ensure_ascii=False),
                "planning",
                now,
                now,
            ),
        )

        # 自动分解子任务
        subtasks = auto_decompose_task(req.goal)
        for st in subtasks:
            agent_id = select_agent_for_role(conn, group_id, st["role"])
            conn.execute(
                "INSERT INTO ai_group_tasks (id, group_id, parent_task_id, goal, status, assigned_agent_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4())[:8],
                    group_id,
                    task_id,
                    st["goal"],
                    "pending",
                    agent_id,
                    now,
                    now,
                ),
            )

        conn.commit()
        return {"task_id": task_id, "subtasks_count": len(subtasks), "status": "planning"}
    finally:
        conn.close()


@router.get("/{group_id}/tasks")
def list_tasks(group_id: str):
    conn = get_db()
    try:
        tasks = rows_to_list(
            conn.execute(
                "SELECT * FROM ai_group_tasks WHERE group_id=? AND parent_task_id IS NULL ORDER BY created_at DESC",
                (group_id,),
            ).fetchall()
        )
        for t in tasks:
            t["subtasks"] = rows_to_list(
                conn.execute(
                    "SELECT * FROM ai_group_tasks WHERE parent_task_id=? ORDER BY created_at",
                    (t["id"],),
                ).fetchall()
            )
            t["constraints"] = json.loads(t.get("constraints", "[]"))
            t["completion_criteria"] = json.loads(t.get("completion_criteria", "[]"))
        return tasks
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/assign")
def assign_task(group_id: str, task_id: str, req: AssignTaskRequest):
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET assigned_agent_id=?, status='executing', updated_at=? WHERE id=? AND group_id=?",
            (req.agent_id, now, task_id, group_id),
        )
        conn.commit()
        return {"status": "executing", "assigned_to": req.agent_id}
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/complete")
def complete_task(group_id: str, task_id: str, req: CompleteTaskRequest):
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET result=?, quality_score=?, status='verifying', updated_at=? WHERE id=? AND group_id=?",
            (req.result, req.quality_score, now, task_id, group_id),
        )
        conn.commit()
        return {"status": "verifying", "quality_score": req.quality_score}
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/verify")
def verify_task(group_id: str, task_id: str, req: VerifyTaskRequest):
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        if req.passed:
            conn.execute(
                "UPDATE ai_group_tasks SET status='completed', updated_at=? WHERE id=?",
                (now, task_id),
            )
            # 检查所有子任务是否完成
            if task["parent_task_id"]:
                pending = conn.execute(
                    "SELECT COUNT(*) as c FROM ai_group_tasks WHERE parent_task_id=? AND status!='completed'",
                    (task["parent_task_id"],),
                ).fetchone()["c"]
                if pending == 0:
                    conn.execute(
                        "UPDATE ai_group_tasks SET status='completed', updated_at=? WHERE id=?",
                        (now, task["parent_task_id"]),
                    )
            conn.commit()
            return {"status": "completed"}
        else:
            iteration = task["iteration"] + 1
            if iteration >= task["max_iterations"]:
                conn.execute(
                    "UPDATE ai_group_tasks SET status='failed', iteration=?, updated_at=? WHERE id=?",
                    (iteration, now, task_id),
                )
                conn.commit()
                return {
                    "status": "failed",
                    "reason": req.reason,
                    "iteration": iteration,
                    "message": "已达最大迭代次数，升级到人工决策",
                }
            else:
                conn.execute(
                    "UPDATE ai_group_tasks SET status='executing', iteration=?, result='', updated_at=? WHERE id=?",
                    (iteration, now, task_id),
                )
                conn.commit()
                return {"status": "retry", "iteration": iteration, "reason": req.reason}
    finally:
        conn.close()


@router.delete("/{group_id}")
def delete_group(group_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM ai_group_tasks WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM ai_group_agents WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM ai_groups WHERE id=?", (group_id,))
        conn.commit()
        return {"status": "deleted"}
    finally:
        conn.close()


@router.post("/{group_id}/agents")
def add_agent(group_id: str, agent: AgentRole):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO ai_group_agents (id, group_id, agent_id, name, role, model, status) VALUES (?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                agent.agent_id,
                agent.name,
                agent.role,
                agent.model,
                "online",
            ),
        )
        conn.commit()
        return {"status": "added"}
    finally:
        conn.close()


@router.patch("/{group_id}")
def update_group(group_id: str, req: UpdateGroupRequest):
    conn = get_db()
    try:
        group = conn.execute("SELECT id FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "AI群不存在")
        updates, params = [], []
        if req.name is not None:
            updates.append("name=?")
            params.append(req.name)
        if req.description is not None:
            updates.append("description=?")
            params.append(req.description)
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.append(group_id)
        conn.execute(f"UPDATE ai_groups SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


@router.delete("/{group_id}/agents/{agent_id}")
def remove_agent(group_id: str, agent_id: str):
    conn = get_db()
    try:
        result = conn.execute(
            "DELETE FROM ai_group_agents WHERE group_id=? AND agent_id=?", (group_id, agent_id)
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(404, "Agent不存在")
        return {"status": "removed"}
    finally:
        conn.close()


@router.patch("/{group_id}/agents/{agent_id}")
def update_agent(group_id: str, agent_id: str, req: UpdateAgentRequest):
    conn = get_db()
    try:
        agent = conn.execute(
            "SELECT id FROM ai_group_agents WHERE group_id=? AND agent_id=?", (group_id, agent_id)
        ).fetchone()
        if not agent:
            raise HTTPException(404, "Agent不存在")
        updates, params = [], []
        if req.model is not None:
            updates.append("model=?")
            params.append(req.model)
        if req.temperature is not None:
            updates.append("temperature=?")
            params.append(req.temperature)
        if req.role is not None:
            updates.append("role=?")
            params.append(req.role)
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.extend([group_id, agent_id])
        conn.execute(
            f"UPDATE ai_group_agents SET {', '.join(updates)} WHERE group_id=? AND agent_id=?",
            params,
        )
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()
