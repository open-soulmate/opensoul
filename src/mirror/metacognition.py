"""Metacognition — 元认知：思考自己的思考。

Agent能：
- 审视自己的决策过程（为什么选了这个策略？）
- 评估自己的信心程度（我对这个答案有多确定？）
- 识别自己的盲区（我不擅长什么？）
- 调整自己的策略（上次失败了，换个方法试试）
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class Metacognition:
    """元认知引擎 — 自我审视、自我纠错"""

    def __init__(self, db_pool=None, tenant_id: str = "default", agent_id: str = "default"):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self._initialized = False

    async def _ensure_table(self):
        if self._initialized:
            return
        if self.db:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS metacognition_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    decision TEXT,
                    reasoning TEXT,
                    confidence REAL,
                    outcome TEXT,
                    lesson TEXT,
                    created_at REAL NOT NULL
                )
            """)
        self._initialized = True

    async def log_decision(self, decision: str, reasoning: str, confidence: float):
        """记录一个决策及其推理过程"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """INSERT INTO metacognition_log
               (tenant_id, agent_id, event_type, decision, reasoning, confidence, created_at)
               VALUES (?, ?, 'decision', ?, ?, ?, ?)""",
            (
                self.tenant_id,
                self.agent_id,
                decision[:500],
                reasoning[:1000],
                confidence,
                time.time(),
            ),
        )

    async def log_outcome(self, decision: str, outcome: str, lesson: str = ""):
        """记录决策的结果和教训"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """INSERT INTO metacognition_log
               (tenant_id, agent_id, event_type, decision, outcome, lesson, created_at)
               VALUES (?, ?, 'outcome', ?, ?, ?, ?)""",
            (self.tenant_id, self.agent_id, decision[:500], outcome, lesson[:500], time.time()),
        )

    async def reflect_recent(self, limit: int = 10) -> list[dict]:
        """审视最近的决策"""
        await self._ensure_table()
        if not self.db:
            return []
        rows = await self.db.fetch(
            """SELECT event_type, decision, reasoning, confidence, outcome, lesson, created_at
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (self.tenant_id, self.agent_id, limit),
        )
        return [dict(row) for row in rows]

    async def get_blind_spots(self) -> list[dict]:
        """识别盲区：经常失败的领域"""
        await self._ensure_table()
        if not self.db:
            return []
        rows = await self.db.fetch(
            """SELECT decision, COUNT(*) as fail_count
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ? AND event_type = 'outcome' AND outcome LIKE '%fail%'
               GROUP BY decision
               HAVING fail_count >= 2
               ORDER BY fail_count DESC
               LIMIT 5""",
            (self.tenant_id, self.agent_id),
        )
        return [{"area": row["decision"], "fail_count": row["fail_count"]} for row in rows]

    async def get_confidence_calibration(self) -> dict:
        """校准信心：预测信心 vs 实际成功率"""
        await self._ensure_table()
        if not self.db:
            return {"avg_confidence": 0, "calibration": "no_data"}
        rows = await self.db.fetch(
            """SELECT confidence, outcome
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ? AND confidence IS NOT NULL
               ORDER BY created_at DESC LIMIT 50""",
            (self.tenant_id, self.agent_id),
        )
        if not rows:
            return {"avg_confidence": 0, "calibration": "no_data"}

        confidences = [r["confidence"] for r in rows]
        avg_conf = sum(confidences) / len(confidences)

        # 简单校准：高信心决策的成功率
        high_conf = [r for r in rows if r["confidence"] and r["confidence"] > 0.7]
        high_conf_success = sum(
            1 for r in high_conf if r["outcome"] and "success" in str(r["outcome"])
        )
        success_rate = high_conf_success / len(high_conf) if high_conf else 0

        return {
            "avg_confidence": round(avg_conf, 2),
            "high_confidence_count": len(high_conf),
            "high_confidence_success_rate": round(success_rate, 2),
            "calibration": "good" if abs(avg_conf - success_rate) < 0.2 else "miscalibrated",
        }

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"decisions": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM metacognition_log WHERE tenant_id = ? AND agent_id = ?",
            (self.tenant_id, self.agent_id),
        )
        return {"decisions": count}
