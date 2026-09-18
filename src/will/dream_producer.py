"""Dream记忆蒸馏的周期生产者 — 集成修复：hippo.dream此前无调度，全系统仅执行1次。

消息源：opensoul.db agent_messages（acp-proxy ws_chat写入的真实聊天记录，
与session_importer P3-②同源）。每24h一轮，idempotency_key按日期防重复提交。
"""
import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("will.dream-producer")

OPENSOUL_DB = str(Path(__file__).resolve().parent.parent.parent / "data" / "opensoul.db")


def fetch_recent_messages(hours: int = 24, max_messages: int = 200) -> list[dict]:
    """从opensoul.db agent_messages拉最近N小时对话。"""
    if not Path(OPENSOUL_DB).exists():
        return []
    cutoff = time.time() - hours * 3600
    try:
        with sqlite3.connect(OPENSOUL_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT role, content FROM agent_messages
                   WHERE timestamp >= ? AND content != ''
                   ORDER BY timestamp ASC""",
                (cutoff,),
            ).fetchall()
    except Exception as e:
        logger.warning("dream-producer fetch failed: %s", e)
        return []
    msgs = [
        {"role": r["role"], "content": r["content"][:2000]}
        for r in rows
        if r["role"] in ("user", "assistant", "human", "ai")
    ]
    return msgs[-max_messages:]


async def run_daily_dream() -> str | None:
    """拉最近24h对话→提交hippo.dream后台作业（幂等键=daily-dream-YYYYMMDD）。"""
    messages = fetch_recent_messages()
    if not messages:
        logger.info("dream-producer: 最近24h无新对话，跳过本轮蒸馏")
        return None
    from src.will.job_handlers import register_default_handlers
    from src.will.job_queue import get_job_queue

    jq = get_job_queue()
    register_default_handlers(jq)
    await jq.start()
    date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    job_id = await jq.submit(
        "hippo.dream",
        {"messages": messages, "force": False},
        timeout_s=600,
        idempotency_key=f"daily-dream-{date_key}",
    )
    logger.info(
        "dream-producer: 提交daily dream job=%s messages=%d条", job_id, len(messages)
    )
    return job_id


async def dream_producer_loop():
    """main.py lifespan挂载：启动5分钟后首轮，此后每24h一轮。"""
    await asyncio.sleep(300)
    while True:
        try:
            await run_daily_dream()
        except Exception as e:
            logger.error("dream-producer loop error: %s", e)
        await asyncio.sleep(24 * 3600)
