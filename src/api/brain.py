"""OpenSoul Brain API — 认知层服务，多租户多Agent共享。

任何Agent调用 /brain/think 即可获得意图理解+风险评估+策略决策。
调用 /brain/verify 完成执行后反思+学习。
数据按 tenant_id + agent_id 隔离。
"""

import time

from fastapi import APIRouter
from pydantic import BaseModel

from src.mind.brain import SoulBrain
from src.models.cognitive import TaskContext

router = APIRouter()

# ── 按租户+Agent隔离的SoulBrain实例池 ──────────────────
_brain_pool: dict[str, SoulBrain] = {}


def _get_brain(tenant_id: str, agent_id: str, repo_root: str = "/home/climbing/opensoul") -> SoulBrain:
    """获取或创建SoulBrain实例（按tenant+agent隔离）"""
    key = f"{tenant_id}:{agent_id}"
    if key not in _brain_pool:
        _brain_pool[key] = SoulBrain(repo_root)
    return _brain_pool[key]


# ── 请求/响应模型 ──────────────────────────────────────

class ThinkRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    user_input: str
    task_id: str = ""
    repo_root: str = "/home/climbing/opensoul"


class VerifyRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    task_id: str
    action: str
    result: dict = {}


# ── API ────────────────────────────────────────────────

@router.get("/health")
async def health():
    """Brain健康检查"""
    return {
        "status": "ok",
        "component": "OpenSoulBrain",
        "instances": len(_brain_pool),
        "tenants": list(set(k.split(":")[0] for k in _brain_pool)),
    }


@router.post("/think")
async def think(req: ThinkRequest):
    """思考：用户输入 → 意图理解 + 风险评估 + 策略决策

    多租户：按 tenant_id + agent_id 隔离SoulBrain实例。
    """
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
    """验证：执行后反思 + 学习

    异常也进入复盘，保证每次交互都参与学习。
    """
    brain = _get_brain(req.tenant_id, req.agent_id)
    ctx = TaskContext(task_id=req.task_id)

    # 从brain的状态恢复task_ctx（简化版，实际应持久化）
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


@router.get("/status")
async def status(tenant_id: str = "default", agent_id: str = "default"):
    """获取SoulBrain状态"""
    key = f"{tenant_id}:{agent_id}"
    if key not in _brain_pool:
        return {"status": "not_initialized"}
    brain = _brain_pool[key]
    return brain.get_status()


@router.post("/refresh")
async def refresh(tenant_id: str = "default", agent_id: str = "default", repo_root: str = "/home/climbing/opensoul"):
    """增量刷新项目记忆"""
    brain = _get_brain(tenant_id, agent_id, repo_root)
    await brain.project_memory.refresh_incremental()
    return {"status": "refreshed", **brain.project_memory.get_stats()}
