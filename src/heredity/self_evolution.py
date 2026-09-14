"""Self-Evolution — 自我进化引擎。

Agent自动优化自己的策略：
- 分析成功/失败模式 → 调整prompt策略
- 识别低效行为 → 替换为更优方案
- 从用户反馈中学习 → 调整交互风格
- 定期自评 → 发现改进空间
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SelfEvolution:
    """自我进化：自动优化Agent策略"""

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
                CREATE TABLE IF NOT EXISTS evolution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    evolution_type TEXT NOT NULL,
                    before_state TEXT,
                    after_state TEXT,
                    reason TEXT,
                    impact_score REAL DEFAULT 0.0,
                    created_at REAL NOT NULL
                )
            """)
        self._initialized = True

    async def analyze_and_evolve(self) -> list[dict]:
        """分析当前状态，自动进化"""
        await self._ensure_table()
        if not self.db:
            return []

        evolutions = []

        # 1. 分析成功率趋势
        success_trend = await self._analyze_success_trend()
        if success_trend["declining"]:
            evolution = {
                "type": "strategy_adjustment",
                "reason": f"成功率下降: {success_trend['current']:.0%} vs {success_trend['previous']:.0%}",
                "action": "切换到更保守的策略",
            }
            await self._log_evolution(evolution)
            evolutions.append(evolution)

        # 2. 分析高频失败模式
        failure_patterns = await self._analyze_failure_patterns()
        for pattern in failure_patterns:
            evolution = {
                "type": "failure_avoidance",
                "reason": f"高频失败: {pattern['error_type']} ({pattern['count']}次)",
                "action": f"自动规避: {pattern['recommendation']}",
            }
            await self._log_evolution(evolution)
            evolutions.append(evolution)

        # 3. 分析用户反馈
        feedback_trend = await self._analyze_feedback()
        if feedback_trend["negative_trend"]:
            evolution = {
                "type": "style_adjustment",
                "reason": f"用户满意度下降: {feedback_trend['avg_rating']:.1f}/5",
                "action": "调整交互风格",
            }
            await self._log_evolution(evolution)
            evolutions.append(evolution)

        return evolutions

    async def _analyze_success_trend(self) -> dict:
        """分析成功率趋势"""
        recent = await self.db.fetch("""
            SELECT outcome, COUNT(*) as cnt FROM experiences
            WHERE tenant_id = ? AND agent_id = ?
            AND created_at > ?
            GROUP BY outcome
        """, (self.tenant_id, self.agent_id, time.time() - 7 * 86400))

        previous = await self.db.fetch("""
            SELECT outcome, COUNT(*) as cnt FROM experiences
            WHERE tenant_id = ? AND agent_id = ?
            AND created_at BETWEEN ? AND ?
            GROUP BY outcome
        """, (self.tenant_id, self.agent_id, time.time() - 14 * 86400, time.time() - 7 * 86400))

        def calc_rate(rows):
            total = sum(r["cnt"] for r in rows)
            success = sum(r["cnt"] for r in rows if r["outcome"] == "success")
            return success / total if total > 0 else 0.5

        current_rate = calc_rate(recent)
        previous_rate = calc_rate(previous)

        return {
            "current": current_rate,
            "previous": previous_rate,
            "declining": current_rate < previous_rate - 0.1,
        }

    async def _analyze_failure_patterns(self) -> list[dict]:
        """分析高频失败模式"""
        rows = await self.db.fetch("""
            SELECT error, COUNT(*) as cnt FROM experiences
            WHERE tenant_id = ? AND agent_id = ? AND outcome = 'failure'
            AND created_at > ? AND error IS NOT NULL
            GROUP BY error
            HAVING cnt >= 3
            ORDER BY cnt DESC
            LIMIT 5
        """, (self.tenant_id, self.agent_id, time.time() - 30 * 86400))

        patterns = []
        for row in rows:
            error = row["error"] or ""
            recommendation = "增加前置检查"
            if "syntax" in error.lower():
                recommendation = "执行前做语法检查"
            elif "timeout" in error.lower():
                recommendation = "增加超时时间或分步执行"
            patterns.append({
                "error_type": error[:100],
                "count": row["cnt"],
                "recommendation": recommendation,
            })
        return patterns

    async def _analyze_feedback(self) -> dict:
        """分析用户反馈趋势"""
        rows = await self.db.fetch("""
            SELECT rating FROM user_feedback
            WHERE tenant_id = ? AND user_id = ?
            AND created_at > ?
            ORDER BY created_at DESC
        """, (self.tenant_id, self.agent_id, time.time() - 30 * 86400))

        if not rows:
            return {"avg_rating": 3.0, "negative_trend": False}

        ratings = [r["rating"] for r in rows if r["rating"] is not None]
        avg = sum(ratings) / len(ratings) if ratings else 3.0

        return {
            "avg_rating": avg,
            "negative_trend": avg < 3.0,
        }

    async def _log_evolution(self, evolution: dict):
        await self.db.execute(
            """INSERT INTO evolution_log
               (tenant_id, agent_id, evolution_type, reason, impact_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (self.tenant_id, self.agent_id, evolution["type"], evolution["reason"], 0.5, time.time()),
        )

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"evolutions": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM evolution_log WHERE tenant_id = ? AND agent_id = ?",
            (self.tenant_id, self.agent_id),
        )
        return {"evolutions": count}
