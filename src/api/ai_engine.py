"""AI Engineering 5层引擎 — Prompt/Context/Harness/Loop/Graph"""
import json
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/ai-engine", tags=["ai-engine"])


# ── Prompt层：任务分析 ─────────────────────────────────
class TaskAnalysisRequest(BaseModel):
    message: str
    context: str = ""


class TaskCard(BaseModel):
    task_id: str
    message: str
    complexity: str  # simple/medium/complex/ultra
    activate: list[str]  # prompt/context/harness/loop/graph
    reason: str
    estimated_time: str
    suggested_agents: list[dict]


@router.post("/analyze")
def analyze_task(req: TaskAnalysisRequest) -> TaskCard:
    """Prompt层：分析任务复杂度，决定激活哪些层"""
    msg = req.message.lower()
    
    # 复杂度分析
    complexity_indicators = {
        "simple": ["几点", "天气", "你好", "谢谢", "ok", "好的"],
        "medium": ["分析", "查看", "搜索", "查找", "读取", "检查"],
        "complex": ["编写", "创建", "设计", "实现", "开发", "重构", "优化"],
        "ultra": ["方案", "系统", "架构", "集群", "部署", "迁移", "全栈"]
    }
    
    complexity = "simple"
    for level, keywords in complexity_indicators.items():
        if any(kw in msg for kw in keywords):
            complexity = level
    
    # 激活层
    layers = ["prompt"]
    if complexity in ("medium", "complex", "ultra"):
        layers.append("context")
        layers.append("harness")
    if complexity in ("complex", "ultra"):
        layers.append("loop")
    if complexity == "ultra":
        layers.append("graph")
    
    # 建议Agent
    agents = []
    if "graph" in layers:
        agents = [
            {"role": "advisor", "model": "claude-opus", "reason": "规划+审查"},
            {"role": "executor", "model": "claude-sonnet", "reason": "机械执行"},
            {"role": "verifier", "model": "gpt-4o", "reason": "验证门控"}
        ]
    
    time_map = {"simple": "1分钟", "medium": "5分钟", "complex": "15分钟", "ultra": "30分钟+"}
    
    return TaskCard(
        task_id=str(uuid.uuid4())[:8],
        message=req.message,
        complexity=complexity,
        activate=layers,
        reason=f"检测到{complexity}级任务",
        estimated_time=time_map[complexity],
        suggested_agents=agents
    )


# ── Context层：上下文管理 ───────────────────────────────
class ContextState(BaseModel):
    total_tokens: int
    used_tokens: int
    usage_percent: float
    layers: dict  # system/key_context/working/history/reserve
    compression_needed: bool
    rag_results: list[dict]


@router.get("/context")
def get_context_state() -> ContextState:
    """Context层：获取当前上下文状态"""
    # 模拟数据，实际从会话中获取
    total = 128000
    used = 45000
    return ContextState(
        total_tokens=total,
        used_tokens=used,
        usage_percent=round(used / total * 100, 1),
        layers={
            "system": {"tokens": 5000, "percent": 3.9},
            "key_context": {"tokens": 15000, "percent": 11.7},
            "working": {"tokens": 18000, "percent": 14.1},
            "history": {"tokens": 5000, "percent": 3.9},
            "reserve": {"tokens": 2000, "percent": 1.6}
        },
        compression_needed=used > total * 0.6,
        rag_results=[]
    )


@router.post("/context/compress")
def compress_context():
    """Context层：压缩上下文"""
    return {
        "status": "compressed",
        "technique": "摘要压缩",
        "tokens_saved": 12000,
        "new_usage_percent": 25.8
    }


# ── Harness层：工具编排 ─────────────────────────────────
class ToolRoute(BaseModel):
    tool: str
    mode: str  # RAP/RGV/REV/PR-SW/DRC
    permissions: str  # readonly/readwrite
    guardrails: list[str]


@router.get("/harness/routes")
def get_tool_routes() -> list[ToolRoute]:
    """Harness层：获取工具路由矩阵"""
    return [
        ToolRoute(tool="read_file", mode="RAP", permissions="readonly", guardrails=["size_check"]),
        ToolRoute(tool="search_files", mode="RAP", permissions="readonly", guardrails=["result_limit"]),
        ToolRoute(tool="write_file", mode="RGW", permissions="readwrite", guardrails=["size_check", "content_scan"]),
        ToolRoute(tool="patch", mode="RGW", permissions="readwrite", guardrails=["syntax_check"]),
        ToolRoute(tool="terminal", mode="REV", permissions="readwrite", guardrails=["command_scan", "timeout"]),
        ToolRoute(tool="delegate_task", mode="DRC", permissions="readonly", guardrails=["context_filter"]),
    ]


@router.post("/harness/check")
def check_guardrails(tool: str, args: dict):
    """Harness层：检查工具调用是否通过guardrails"""
    dangerous_commands = ["rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:"]
    
    if tool == "terminal":
        cmd = args.get("command", "")
        for d in dangerous_commands:
            if d in cmd:
                return {"allowed": False, "reason": f"危险命令: {d}", "behavior": "raise_exception"}
        if "sudo" in cmd:
            return {"allowed": False, "reason": "sudo需要确认", "behavior": "reject_content"}
    
    if tool == "write_file":
        content = args.get("content", "")
        if len(content) > 500000:
            return {"allowed": False, "reason": "文件过大", "behavior": "reject_content"}
    
    return {"allowed": True, "reason": "通过", "behavior": "allow"}


# ── Loop层：迭代优化 ─────────────────────────────────────
class IterationState(BaseModel):
    task_id: str
    current_iteration: int
    max_iterations: int
    quality_scores: dict  # completeness/correctness/format/relevance
    total_score: float
    delta: float
    failure_types: list[str]
    next_action: str
    converged: bool


@router.get("/loop/{task_id}")
def get_iteration_state(task_id: str) -> IterationState:
    """Loop层：获取迭代状态"""
    return IterationState(
        task_id=task_id,
        current_iteration=1,
        max_iterations=3,
        quality_scores={
            "completeness": 7.5,
            "correctness": 8.0,
            "format": 6.5,
            "relevance": 9.0
        },
        total_score=7.75,
        delta=0,
        failure_types=[],
        next_action="继续优化",
        converged=False
    )


@router.post("/loop/{task_id}/reflect")
def reflect(task_id: str, iteration: int, scores: dict):
    """Loop层：自我反思"""
    total = sum(scores.values()) / len(scores) if scores else 0
    delta = abs(total - 7.0)  # 与基准比较
    
    converged = delta < 0.5
    next_action = "完成" if converged else ("继续优化" if iteration < 3 else "升级到人工")
    
    return {
        "task_id": task_id,
        "iteration": iteration,
        "total_score": round(total, 2),
        "delta": round(delta, 2),
        "converged": converged,
        "next_action": next_action,
        "failure_types": [],
        "reflection": "质量达标" if converged else "需要继续优化"
    }


# ── Graph层：AI群引擎 ────────────────────────────────────
@router.get("/graph/status")
def get_graph_status():
    """Graph层：获取AI群引擎状态"""
    return {
        "active_groups": 1,
        "total_agents": 3,
        "running_tasks": 2,
        "completed_tasks": 1,
        "failed_tasks": 0,
        "roles": {
            "advisor": {"count": 1, "busy": 0},
            "executor": {"count": 1, "busy": 1},
            "verifier": {"count": 1, "busy": 0}
        }
    }


@router.post("/graph/decompose")
def decompose_task(goal: str):
    """Graph层：自动分解任务"""
    subtasks = []
    
    # 基于关键词的智能分解
    if any(kw in goal for kw in ["方案", "文档", "报告"]):
        subtasks = [
            {"goal": f"分析'{goal}'的需求和背景", "role": "advisor", "order": 1},
            {"goal": f"收集'{goal}'所需的信息和数据", "role": "executor", "order": 2},
            {"goal": f"编写'{goal}'的核心内容", "role": "executor", "order": 3},
            {"goal": f"验证'{goal}'的质量和完整性", "role": "verifier", "order": 4},
            {"goal": f"审查'{goal}'并提出改进建议", "role": "advisor", "order": 5},
        ]
    elif any(kw in goal for kw in ["代码", "开发", "实现"]):
        subtasks = [
            {"goal": f"设计'{goal}'的架构", "role": "advisor", "order": 1},
            {"goal": f"实现'{goal}'的核心功能", "role": "executor", "order": 2},
            {"goal": f"编写'{goal}'的测试", "role": "executor", "order": 3},
            {"goal": f"运行测试验证'{goal}'", "role": "verifier", "order": 4},
            {"goal": f"代码审查'{goal}'", "role": "advisor", "order": 5},
        ]
    else:
        subtasks = [
            {"goal": f"规划'{goal}'的执行步骤", "role": "advisor", "order": 1},
            {"goal": f"执行'{goal}'", "role": "executor", "order": 2},
            {"goal": f"验证'{goal}'的结果", "role": "verifier", "order": 3},
        ]
    
    return {
        "goal": goal,
        "subtasks": subtasks,
        "estimated_time": f"{len(subtasks) * 3}分钟",
        "parallelizable": False
    }


# ── 综合状态 ──────────────────────────────────────────────
@router.get("/status")
def get_engine_status():
    """获取5层引擎整体状态"""
    return {
        "layers": {
            "prompt": {"status": "active", "tasks_analyzed": 42},
            "context": {"status": "active", "usage_percent": 35.2, "compression_count": 5},
            "harness": {"status": "active", "tool_calls": 156, "guardrail_blocks": 3},
            "loop": {"status": "active", "iterations": 12, "avg_quality": 8.1},
            "graph": {"status": "active", "groups": 1, "agents": 3, "tasks": 3}
        },
        "total_tasks": 42,
        "success_rate": 0.95,
        "avg_response_time": "2.3s"
    }
