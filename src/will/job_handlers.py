"""P0-8 后台作业队列运行时接线 — 真实handler注册表

调研来源（SUMMARY.md P0-8"后台作业/长任务引擎 7方互证" / 42-agno-source.md job_queue/store.py /
evolution-engine-patterns.md §5.2 "agno durable JobQueue"）：
- agno JobQueue "注释即规格"：每条接线设计写明为什么
- kilocode BackgroundJob注册表：作业必须有真实执行语义，不是日志占位
- mem0 §1.1 "失败必须可见"：handler异常由worker捕获进FAILED状态+error字段，绝不静默

本轮补的是"写了≠接线了"缺口（c1feb676只落了队列基础设施+API端点）：
grep确认 register_handler 调用点=0、JobQueue.start() 调用点=0、submit生产者=0
——此前任何作业提交后永远pending，队列在运行时是死代码。
本模块是handlers的唯一注册点：api/will.py模块加载时调用register_default_handlers()，
api/hippo.py dream endpoint background=True 是第一个真实生产者。

handler采用懒import（函数体内import src.api.*）：
api.heredity/api.hippo在main.py启动时已加载，此处延迟import
避免will→api→will的模块级循环导入（will/__init__已被api/will.py导入）。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.will.job_queue import JobQueue

logger = logging.getLogger("opensoul.job_handlers")


async def _heredity_evaluate_triggers(**params: Any) -> dict:
    """heredity.evaluate_triggers：进化触发器评估
    （CowAgent idle触发三条件合取 + agno错误风暴熔断），纯SQLite无LLM依赖。"""
    from src.api.heredity import evolution_engine

    return evolution_engine.evaluate_triggers(
        idle_seconds=float(params.get("idle_seconds", 0.0)),
        context_pressure=float(params.get("context_pressure", 0.0)),
        budget_remaining=float(params.get("budget_remaining", 1.0)),
        recent_errors=list(params.get("recent_errors") or []),
        auto_propose=bool(params.get("auto_propose", False)),
    )


async def _hippo_dream(**params: Any) -> dict:
    """hippo.dream：Dream记忆蒸馏后台作业
    （CowAgent五步蒸馏prompt + kilocode回声阻断；LLM长任务的典型后台化场景）。

    复用api/hippo.py的_dream_distiller单例——同步端点与后台作业共享
    回声阻断状态/审计轨迹/统计，不产生第二份状态。
    """
    from src.api.hippo import _dream_distiller

    result = await _dream_distiller.dream(
        messages=list(params.get("messages") or []),
        force=bool(params.get("force", False)),
    )
    return result.to_dict()


async def _hippo_memory_pipeline(**params: Any) -> dict:
    """hippo.memory_pipeline：codex两阶段记忆管线后台作业
    （11-openai-codex-source.md #1：Phase1提取→Phase2 consolidation agent→
    MemoryVersion版本化→workspace diff；LLM管线的典型后台化场景）。

    复用api/hippo.py的_memory_pipeline单例——同步端点与后台作业共享
    pipeline_runs运行记录，不产生第二份状态。
    """
    from src.api.hippo import _memory_pipeline

    result = await _memory_pipeline.run(
        messages=list(params.get("messages") or []),
        candidates=list(params.get("candidates") or []),
        session_id=str(params.get("session_id", "")),
        apply=bool(params.get("apply", True)),
        use_llm_phase2=bool(params.get("use_llm_phase2", True)),
        force=bool(params.get("force", False)),
    )
    return result.to_dict()


async def _learn_collect_experiences(**params: Any) -> dict:
    """learn.collect_experiences：P0-7进化数据层采集后台作业
    （TradingAgents outcome回填：agent_messages/jobs/eval实验 → experiences表）。

    brain/evolve端点每次分析前也会同步采集（真实HTTP路径）；本handler供
    cron/monitoring按计划采集（采集与分析解耦——分析随时有最新数据可读）。
    同步sqlite3采集用to_thread隔离（不阻塞事件循环；单进程队列无并发写冲突）。"""
    from src.learn.experience_collector import ExperienceCollector

    collector = ExperienceCollector(
        tenant_id=str(params.get("tenant_id", "default")),
        agent_id=str(params.get("agent_id", "default")),
    )
    return await asyncio.to_thread(collector.collect)


# handler注册表：job name → async callable(**params) -> JSON-serializable dict
# 命名约定 <organ>.<action>：与OpenSoul器官分层一致，作业名即归属声明
HANDLER_SPECS: dict[str, Callable[..., Awaitable[Any]]] = {
    "heredity.evaluate_triggers": _heredity_evaluate_triggers,
    "hippo.dream": _hippo_dream,
    "hippo.memory_pipeline": _hippo_memory_pipeline,
    "learn.collect_experiences": _learn_collect_experiences,
}


def register_default_handlers(jq: "JobQueue") -> list[str]:
    """注册全部默认handler到队列（幂等：register_handler同名覆盖同一函数对象）。

    Returns:
        已注册的handler名字列表（monitoring面板/jobs/health直接展示）。
    """
    for name, fn in HANDLER_SPECS.items():
        jq.register_handler(name, fn)
    logger.debug("job_handlers registered: %s", sorted(HANDLER_SPECS))
    return list(HANDLER_SPECS.keys())
