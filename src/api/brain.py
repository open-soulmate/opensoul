"""OpenSoul Brain API — 认知层服务，多租户多Agent共享。"""

import time
import os

from fastapi import APIRouter
from pydantic import BaseModel

from src.database.postgres import db_pool
from src.mind.brain import SoulBrain
from src.models.cognitive import TaskContext

router = APIRouter()

# ── 按租户+Agent隔离的SoulBrain实例池 ──────────────────
_brain_pool: dict[str, SoulBrain] = {}


def _get_brain(tenant_id: str, agent_id: str, repo_root: str = "") -> SoulBrain:
    key = f"{tenant_id}:{agent_id}"
    if key not in _brain_pool:
        _brain_pool[key] = SoulBrain(
            tenant_id=tenant_id, agent_id=agent_id,
            repo_root=repo_root, db_pool=db_pool,
        )
    return _brain_pool[key]


# ── 请求模型 ──────────────────────────────────────────

class ThinkRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    user_input: str
    task_id: str = ""
    repo_root: str = ""


class VerifyRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    task_id: str
    action: str
    result: dict = {}


class FeedbackRequest(BaseModel):
    tenant_id: str = "default"
    user_id: str = "default"
    action: str
    feedback: str
    rating: int = 0


# ── API ────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenSoulBrain", "instances": len(_brain_pool)}


@router.post("/think")
async def think(req: ThinkRequest):
    """思考：用户输入 → 意图理解 + 风险评估 + 策略决策"""
    brain = _get_brain(req.tenant_id, req.agent_id, req.repo_root)
    ctx = TaskContext(task_id=req.task_id, user_input=req.user_input)

    start = time.time()
    decision = await brain.think(req.user_input, ctx)
    elapsed_ms = int((time.time() - start) * 1000)

    return {
        "task_id": ctx.task_id,
        "elapsed_ms": elapsed_ms,
        "intent": {
            "goal": decision.intent.goal,
            "target_files": decision.intent.target_files,
            "modify_scope": decision.intent.modify_scope,
            "change_size": decision.intent.change_size,
        },
        "risk": {
            "overall_level": decision.risk.overall_level,
            "recommendation": decision.risk.recommendation,
            "risks": [{"title": r.title, "level": r.level, "desc": r.desc} for r in decision.risk.risks],
        },
        "decision": {
            "edit_mode": decision.edit_mode,
            "execute_mode": decision.execute_mode,
            "confirm_prompt": decision.confirm_prompt,
        },
    }


@router.post("/verify")
async def verify(req: VerifyRequest):
    """验证：执行后反思 + 学习"""
    brain = _get_brain(req.tenant_id, req.agent_id)
    ctx = TaskContext(task_id=req.task_id)

    start = time.time()
    verification = await brain.verify(req.action, req.result, ctx)
    elapsed_ms = int((time.time() - start) * 1000)

    return {
        "success": verification.success,
        "checks": [{"name": c.name, "passed": c.passed, "error": c.error} for c in verification.checks],
        "error": verification.error,
        "fix": verification.fix,
        "elapsed_ms": elapsed_ms,
    }


@router.post("/feedback")
async def feedback(req: FeedbackRequest):
    """记录用户反馈"""
    brain = _get_brain(req.tenant_id, "default")
    await brain._ensure_init()
    await brain._user_memory.record_feedback(req.action, req.feedback, req.rating)
    return {"status": "recorded"}


@router.get("/status")
async def status(tenant_id: str = "default", agent_id: str = "default"):
    key = f"{tenant_id}:{agent_id}"
    if key not in _brain_pool:
        return {"status": "not_initialized"}
    return await _brain_pool[key].get_status()


@router.post("/refresh")
async def refresh(tenant_id: str = "default", agent_id: str = "default", repo_root: str = ""):
    brain = _get_brain(tenant_id, agent_id, repo_root)
    await brain._ensure_init()
    if brain._project_memory:
        await brain._project_memory.refresh_incremental()
        return {"status": "refreshed", **brain._project_memory.get_stats()}
    return {"status": "no_project_memory"}
