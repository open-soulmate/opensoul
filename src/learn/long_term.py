"""Long-term Learning — 从经验中提取可复用的行为模式。

不是记住所有事，而是提取模式：
- 哪类任务用什么策略最有效？
- 哪些文件组合经常一起修改？
- 什么时间段用户活跃？
- 哪些操作容易失败？怎么避免？
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class LongTermLearning:
    """长期学习：从经验中提取模式，优化Agent行为。"""

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
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    pattern_key TEXT NOT NULL,
                    pattern_data TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    sample_count INTEGER DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(tenant_id, agent_id, pattern_type, pattern_key)
                )
            """)
        self._initialized = True

    async def extract_patterns(self):
        """从经验表中提取模式（定期运行）"""
        await self._ensure_table()
        if not self.db:
            return

        # 1. 提取"意图→策略"模式
        await self._extract_strategy_patterns()

        # 2. 提取"文件组合"模式
        await self._extract_file_combo_patterns()

        # 3. 提取"失败规避"模式
        await self._extract_failure_patterns()

    async def _extract_strategy_patterns(self):
        """提取：什么意图用什么策略最成功"""
        rows = await self.db.fetch("""
            SELECT intent_summary, outcome, COUNT(*) as cnt
            FROM experiences
            WHERE tenant_id = ? AND agent_id = ?
            GROUP BY intent_summary, outcome
            HAVING cnt >= 3
        """, (self.tenant_id, self.agent_id))

        strategy_map: dict[str, dict] = {}
        for row in rows:
            intent = row["intent_summary"]
            if intent not in strategy_map:
                strategy_map[intent] = {"success": 0, "failure": 0}
            strategy_map[intent][row["outcome"]] = row["cnt"]

        for intent, counts in strategy_map.items():
            total = counts["success"] + counts["failure"]
            confidence = counts["success"] / total if total > 0 else 0.5
            await self._upsert_pattern(
                "strategy", intent,
                {"success_rate": confidence, "total": total, "recommendation": "patch" if confidence > 0.7 else "stepwise"},
                confidence, total,
            )

    async def _extract_file_combo_patterns(self):
        """提取：哪些文件经常一起修改"""
        rows = await self.db.fetch("""
            SELECT action FROM experiences
            WHERE tenant_id = ? AND agent_id = ? AND outcome = 'success'
            ORDER BY created_at DESC LIMIT 200
        """, (self.tenant_id, self.agent_id))

        # 简单的文件共现分析
        import re
        file_combos: dict[str, int] = {}
        for row in rows:
            files = re.findall(r'[\w/\\.-]+\.\w+', row["action"])
            if len(files) >= 2:
                files.sort()
                key = "+".join(files[:3])  # 最多3个文件的组合
                file_combos[key] = file_combos.get(key, 0) + 1

        for combo, count in file_combos.items():
            if count >= 3:
                await self._upsert_pattern(
                    "file_combo", combo,
                    {"files": combo.split("+"), "co_occurrence": count},
                    min(1.0, count / 10), count,
                )

    async def _extract_failure_patterns(self):
        """提取：什么操作容易失败，怎么避免"""
        rows = await self.db.fetch("""
            SELECT action, error, fix FROM experiences
            WHERE tenant_id = ? AND agent_id = ? AND outcome = 'failure'
            AND error IS NOT NULL
            ORDER BY created_at DESC LIMIT 100
        """, (self.tenant_id, self.agent_id))

        failure_types: dict[str, dict] = {}
        for row in rows:
            error = row["error"] or "unknown"
            # 归类错误
            if "syntax" in error.lower():
                error_type = "syntax_error"
            elif "timeout" in error.lower():
                error_type = "timeout"
            elif "permission" in error.lower():
                error_type = "permission"
            elif "not found" in error.lower():
                error_type = "not_found"
            else:
                error_type = "other"

            if error_type not in failure_types:
                failure_types[error_type] = {"count": 0, "fixes": []}
            failure_types[error_type]["count"] += 1
            if row["fix"]:
                failure_types[error_type]["fixes"].append(row["fix"])

        for error_type, data in failure_types.items():
            if data["count"] >= 2:
                await self._upsert_pattern(
                    "failure", error_type,
                    {"count": data["count"], "common_fixes": list(set(data["fixes"]))[:3]},
                    min(1.0, data["count"] / 5), data["count"],
                )

    async def _upsert_pattern(self, pattern_type: str, pattern_key: str,
                               pattern_data: dict, confidence: float, sample_count: int):
        await self.db.execute("""
            INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, pattern_key, pattern_data, confidence, sample_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, agent_id, pattern_type, pattern_key)
            DO UPDATE SET pattern_data = ?, confidence = ?, sample_count = ?, updated_at = ?
        """, (
            self.tenant_id, self.agent_id, pattern_type, pattern_key,
            json.dumps(pattern_data), confidence, sample_count, time.time(), time.time(),
            json.dumps(pattern_data), confidence, sample_count, time.time(),
        ))

    async def get_recommendations(self, intent_summary: str = "") -> list[dict]:
        """获取学习到的推荐"""
        await self._ensure_table()
        if not self.db:
            return []

        conditions = "tenant_id = ? AND agent_id = ?"
        params: list = [self.tenant_id, self.agent_id]
        if intent_summary:
            conditions += " AND pattern_key = ?"
            params.append(intent_summary)

        rows = await self.db.fetch(
            f"""SELECT pattern_type, pattern_key, pattern_data, confidence, sample_count
                FROM learned_patterns
                WHERE {conditions}
                ORDER BY confidence DESC, sample_count DESC
                LIMIT 10""",
            params,
        )

        return [
            {
                "type": row["pattern_type"],
                "key": row["pattern_key"],
                "data": json.loads(row["pattern_data"]),
                "confidence": row["confidence"],
                "samples": row["sample_count"],
            }
            for row in rows
        ]

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"patterns": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM learned_patterns WHERE tenant_id = ? AND agent_id = ?",
            (self.tenant_id, self.agent_id),
        )
        return {"patterns": count}
