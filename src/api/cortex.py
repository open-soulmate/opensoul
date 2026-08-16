from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import time as _time

from src.cortex.task_planner import TaskPlanner
from src.cortex.multi_agent import MultiAgent
from src.cortex.chain_of_thought import ChainOfThought

router = APIRouter()

# ── Usage counters ─────────────────────────────────────────
_usage = {
    "plan_calls": 0,
    "agent_calls": 0,
    "think_calls": 0,
    "total_calls": 0,
    "errors": 0,
    "last_activity": None,
}


@router.get("/health")
async def cortex_health():
    """OpenCortex health check."""
    return {
        "status": "ok",
        "component": "OpenCortex",
        "modules": {
            "task_planner": "available",
            "multi_agent": "available",
            "chain_of_thought": "available",
        },
    }


@router.get("/stats")
async def cortex_stats():
    """Get OpenCortex usage statistics."""
    return {
        "status": "ok",
        "component": "OpenCortex",
        "modules": {
            "task_planner": "available",
            "multi_agent": "available",
            "chain_of_thought": "available",
            "graphrag": "available",
            "recommendation": "available",
            "quality": "available",
        },
        "usage": _usage,
    }


class PlanRequest(BaseModel):
    goal: str


class AgentRequest(BaseModel):
    topic: str


class ThinkRequest(BaseModel):
    question: str
    context: str = ""


@router.post("/plan")
async def plan(req: PlanRequest):
    """Decompose a goal into sub-tasks."""
    try:
        planner = TaskPlanner()
        tasks = await planner.plan(req.goal)
        _usage["plan_calls"] += 1
        _usage["total_calls"] += 1
        _usage["last_activity"] = _time.time()
        return {
            "goal": req.goal,
            "tasks": [
                {
                    "index": i,
                    "description": t.description,
                    "dependencies": t.dependencies,
                    "priority": t.priority,
                }
                for i, t in enumerate(tasks)
            ],
        }
    except ValueError as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=502, detail=f"Planning failed: {e}")


@router.post("/agent")
async def agent(req: AgentRequest):
    """Run the multi-agent pipeline (Researcher → Analyzer → Writer)."""
    try:
        pipeline = MultiAgent()
        result = await pipeline.run(req.topic)
        _usage["agent_calls"] += 1
        _usage["total_calls"] += 1
        _usage["last_activity"] = _time.time()
        return {"topic": req.topic, **result}
    except ValueError as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=502, detail=f"Agent pipeline failed: {e}")


@router.post("/think")
async def think(req: ThinkRequest):
    """Chain-of-thought reasoning with self-reflection."""
    try:
        cot = ChainOfThought()
        result = await cot.think(req.question, req.context)
        _usage["think_calls"] += 1
        _usage["total_calls"] += 1
        _usage["last_activity"] = _time.time()
        return result
    except ValueError as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=502, detail=f"Reasoning failed: {e}")
