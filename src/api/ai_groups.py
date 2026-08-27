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
    # 为已有表添加 capabilities 列（如果不存在）
    try:
        conn.execute("ALTER TABLE ai_group_agents ADD COLUMN capabilities TEXT DEFAULT '[]'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 讨论与评分相关表
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_capabilities (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            avg_score REAL DEFAULT 0,
            task_count INTEGER DEFAULT 0,
            trend TEXT DEFAULT 'stable',
            updated_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id),
            UNIQUE(group_id, agent_id, capability)
        );
        CREATE TABLE IF NOT EXISTS agent_scores (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            scored_agent_id TEXT NOT NULL,
            scorer_agent_id TEXT NOT NULL,
            score INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            capability TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
        CREATE TABLE IF NOT EXISTS discussion_messages (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            agent_name TEXT DEFAULT '',
            intent TEXT DEFAULT 'comment',
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            round_num INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
    """)
    conn.commit()
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


class StartDiscussionRequest(BaseModel):
    goal: str
    constraints: list[str] = []
    completion_criteria: list[str] = []


class DiscussionResponseRequest(BaseModel):
    agent_id: str
    agent_name: str = ""
    intent: str = "comment"  # claim/suggest/refer/comment
    content: str


class DiscussionAssignment(BaseModel):
    agent_id: str
    subgoal: str


class DecideDiscussionRequest(BaseModel):
    assignments: list[DiscussionAssignment]


class ReviewTaskRequest(BaseModel):
    result: str = ""


class ScoreTaskRequest(BaseModel):
    scorer_agent_id: str
    score: int
    reason: str = ""
    capability: str = ""


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


def update_agent_capability(group_id: str, agent_id: str, capability: str, score: int):
    """更新Agent能力画像（运行平均值 + 趋势计算）"""
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        row = conn.execute(
            "SELECT * FROM agent_capabilities WHERE group_id=? AND agent_id=? AND capability=?",
            (group_id, agent_id, capability),
        ).fetchone()

        if row:
            old_avg = row["avg_score"]
            old_count = row["task_count"]
            new_count = old_count + 1
            new_avg = (old_avg * old_count + score) / new_count
            # 趋势：最近分数 vs 历史均值
            if score > old_avg + 0.5:
                trend = "up"
            elif score < old_avg - 0.5:
                trend = "down"
            else:
                trend = "stable"
            conn.execute(
                "UPDATE agent_capabilities SET avg_score=?, task_count=?, trend=?, updated_at=? WHERE id=?",
                (round(new_avg, 2), new_count, trend, now, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO agent_capabilities (id, group_id, agent_id, capability, avg_score, task_count, trend, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4())[:8], group_id, agent_id, capability, score, 1, "stable", now),
            )
        conn.commit()
    finally:
        conn.close()


def calculate_task_avg_score(task_id: str) -> float | None:
    """计算任务平均分（去掉最高最低分）"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT score FROM agent_scores WHERE task_id=?", (task_id,)
        ).fetchall()
        scores = [r["score"] for r in rows]
        if len(scores) == 0:
            return None
        if len(scores) <= 2:
            return round(sum(scores) / len(scores), 2)
        scores.sort()
        trimmed = scores[1:-1]
        return round(sum(trimmed) / len(trimmed), 2)
    finally:
        conn.close()


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


# ── Discussion & Scoring Endpoints ─────────────────────────


@router.post("/{group_id}/discuss")
def start_discussion(group_id: str, req: StartDiscussionRequest):
    """发起群组讨论式任务"""
    conn = get_db()
    try:
        group = conn.execute("SELECT id FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "AI群不存在")

        now = datetime.now().isoformat()
        task_id = str(uuid.uuid4())[:8]

        # 创建任务（状态为 discussing）
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, goal, constraints, completion_criteria, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                task_id,
                group_id,
                req.goal,
                json.dumps(req.constraints, ensure_ascii=False),
                json.dumps(req.completion_criteria, ensure_ascii=False),
                "discussing",
                now,
                now,
            ),
        )

        # 创建系统消息
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                "system",
                "系统",
                "comment",
                f"新任务已发布：{req.goal}。请各Agent讨论并认领子任务。",
                now,
            ),
        )

        conn.commit()
        return {"task_id": task_id, "status": "discussing", "round": 1}
    finally:
        conn.close()


@router.post("/{group_id}/discuss/{task_id}/respond")
def discussion_respond(group_id: str, task_id: str, req: DiscussionResponseRequest):
    """Agent提交讨论回复"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT id, status FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] != "discussing":
            raise HTTPException(400, "任务不在讨论状态")

        now = datetime.now().isoformat()

        # 计算当前轮次
        last = conn.execute(
            "SELECT MAX(round_num) as r FROM discussion_messages WHERE task_id=?", (task_id,)
        ).fetchone()
        current_round = (last["r"] or 1) if last else 1

        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, round_num, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                req.agent_id,
                req.agent_name,
                req.intent,
                req.content,
                current_round,
                now,
            ),
        )
        conn.commit()
        return {"status": "recorded", "round": current_round}
    finally:
        conn.close()


@router.post("/{group_id}/discuss/{task_id}/decide")
def decide_discussion(group_id: str, task_id: str, req: DecideDiscussionRequest):
    """结束讨论，分配子任务"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT id, status FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        now = datetime.now().isoformat()

        # 创建系统消息标记讨论结束
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                "system",
                "系统",
                "comment",
                f"讨论结束，共分配 {len(req.assignments)} 个子任务。",
                now,
            ),
        )

        # 创建子任务
        for assign in req.assignments:
            conn.execute(
                "INSERT INTO ai_group_tasks (id, group_id, parent_task_id, goal, status, assigned_agent_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4())[:8],
                    group_id,
                    task_id,
                    assign.subgoal,
                    "pending",
                    assign.agent_id,
                    now,
                    now,
                ),
            )

        # 更新主任务状态
        conn.execute(
            "UPDATE ai_group_tasks SET status='assigned', updated_at=? WHERE id=?",
            (now, task_id),
        )
        conn.commit()
        return {"status": "assigned", "subtasks_count": len(req.assignments)}
    finally:
        conn.close()


@router.get("/{group_id}/discuss/{task_id}/messages")
def get_discussion_messages(group_id: str, task_id: str):
    """获取任务讨论消息列表"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT id FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        messages = rows_to_list(
            conn.execute(
                "SELECT * FROM discussion_messages WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        )
        return messages
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/review")
def review_task(group_id: str, task_id: str, req: ReviewTaskRequest):
    """提交任务结果供群组评审"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET result=?, status='reviewing', updated_at=? WHERE id=?",
            (req.result, now, task_id),
        )

        # 创建系统消息通知各Agent评分
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                "system",
                "系统",
                "comment",
                f"任务「{task['goal']}」已完成，请各Agent对结果进行评分。",
                now,
            ),
        )

        conn.commit()
        return {"status": "reviewing", "task_id": task_id}
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/score")
def score_task(group_id: str, task_id: str, req: ScoreTaskRequest):
    """Agent对任务结果评分"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        if req.score < 1 or req.score > 10:
            raise HTTPException(400, "分数必须在1-10之间")

        now = datetime.now().isoformat()
        assigned_agent = task["assigned_agent_id"] or ""

        # 记录评分
        conn.execute(
            "INSERT INTO agent_scores (id, group_id, task_id, scored_agent_id, scorer_agent_id, score, reason, capability, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                assigned_agent,
                req.scorer_agent_id,
                req.score,
                req.reason,
                req.capability,
                now,
            ),
        )

        # 在讨论中记录评分消息
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                req.scorer_agent_id,
                "",
                "score",
                f"评分 {req.score}/10: {req.reason}",
                json.dumps({"score": req.score, "target_agent_id": assigned_agent}, ensure_ascii=False),
                now,
            ),
        )

        conn.commit()

        # 计算平均分
        avg = calculate_task_avg_score(task_id)

        # 如果有能力和被评分Agent，更新能力画像
        if req.capability and assigned_agent:
            update_agent_capability(group_id, assigned_agent, req.capability, req.score)

        # 检查是否所有Agent都已评分
        agent_count = conn.execute(
            "SELECT COUNT(*) as c FROM ai_group_agents WHERE group_id=? AND status='online'",
            (group_id,),
        ).fetchone()["c"]
        score_count = conn.execute(
            "SELECT COUNT(DISTINCT scorer_agent_id) as c FROM agent_scores WHERE task_id=?",
            (task_id,),
        ).fetchone()["c"]

        # 所有Agent都评分后，更新任务状态
        if score_count >= agent_count and avg is not None:
            conn.execute(
                "UPDATE ai_group_tasks SET quality_score=?, status='scored', updated_at=? WHERE id=?",
                (avg, now, task_id),
            )
            conn.commit()

        return {"status": "scored", "avg_score": avg}
    finally:
        conn.close()


@router.get("/{group_id}/agents/{agent_id}/capabilities")
def get_agent_capabilities(group_id: str, agent_id: str):
    """获取Agent能力画像"""
    conn = get_db()
    try:
        caps = rows_to_list(
            conn.execute(
                "SELECT * FROM agent_capabilities WHERE group_id=? AND agent_id=? ORDER BY avg_score DESC",
                (group_id, agent_id),
            ).fetchall()
        )

        if not caps:
            return {
                "agent_id": agent_id,
                "overall_rank": 0,
                "capabilities": [],
                "strengths": [],
                "weaknesses": [],
            }

        # 整体排名 = 所有能力平均分的加权平均
        total_weight = sum(c["task_count"] for c in caps) or 1
        overall = sum(c["avg_score"] * c["task_count"] for c in caps) / total_weight

        strengths = [c["capability"] for c in caps if c["avg_score"] >= 7.5]
        weaknesses = [c["capability"] for c in caps if c["avg_score"] < 5.0]

        return {
            "agent_id": agent_id,
            "overall_rank": round(overall, 2),
            "capabilities": [
                {
                    "capability": c["capability"],
                    "avg_score": c["avg_score"],
                    "task_count": c["task_count"],
                    "trend": c["trend"],
                }
                for c in caps
            ],
            "strengths": strengths,
            "weaknesses": weaknesses,
        }
    finally:
        conn.close()
