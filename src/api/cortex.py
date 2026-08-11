from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.cortex.task_planner import TaskPlanner
from src.cortex.multi_agent import MultiAgent
from src.cortex.chain_of_thought import ChainOfThought

router = APIRouter()


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
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Planning failed: {e}")


@router.post("/agent")
async def agent(req: AgentRequest):
    """Run the multi-agent pipeline (Researcher → Analyzer → Writer)."""
    try:
        pipeline = MultiAgent()
        result = await pipeline.run(req.topic)
        return {"topic": req.topic, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent pipeline failed: {e}")


@router.post("/think")
async def think(req: ThinkRequest):
    """Chain-of-thought reasoning with self-reflection."""
    try:
        cot = ChainOfThought()
        result = await cot.think(req.question, req.context)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Reasoning failed: {e}")
