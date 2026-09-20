"""User Memory — SQLite存储版本，多租户隔离。"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class UserMemory:
    """用户记忆：SQLite存储，按tenant+user隔离。"""

    def __init__(self, db_pool=None, tenant_id: str = "default", user_id: str = "default"):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._initialized = False
        self._preferences: dict | None = None

    async def _ensure_table(self):
        if self._initialized:
            return
        if self.db:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS user_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(tenant_id, user_id, key)
                )
            """)
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS user_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    feedback TEXT,
                    rating INTEGER,
                    created_at REAL NOT NULL
                )
            """)
        self._initialized = True

    async def get(self, key: str, default=None):
        await self._ensure_table()
        if not self.db:
            return default
        row = await self.db.fetchrow(
            "SELECT value FROM user_memory WHERE tenant_id = ? AND user_id = ? AND key = ?",
            (self.tenant_id, self.user_id, key),
        )
        if row:
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return row["value"]
        return default

    async def set(self, key: str, value):
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """INSERT INTO user_memory (tenant_id, user_id, key, value, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id, user_id, key)
               DO UPDATE SET value = ?, updated_at = ?""",
            (
                self.tenant_id,
                self.user_id,
                key,
                json.dumps(value),
                time.time(),
                json.dumps(value),
                time.time(),
            ),
        )

    async def record_feedback(self, action: str, feedback: str, rating: int):
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            "INSERT INTO user_feedback (tenant_id, user_id, action, feedback, rating, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.tenant_id, self.user_id, action[:500], feedback, rating, time.time()),
        )

    async def should_auto_execute(self, risk_level: str) -> bool:
        tolerance = await self.get("risk_tolerance", "medium")
        if risk_level == "low":
            return await self.get("auto_confirm_low_risk", True)
        elif risk_level == "medium":
            return tolerance == "high"
        return False

    async def get_preferences(self) -> dict:
        defaults = {
            "edit_mode_preference": "patch",
            "risk_tolerance": "medium",
            "auto_confirm_low_risk": True,
            "always_backup_before_edit": True,
            "notification_level": "normal",
        }
        for key in list(defaults.keys()):
            val = await self.get(key)
            if val is not None:
                defaults[key] = val
        return defaults

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"preferences": {}, "feedback_count": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM user_feedback WHERE tenant_id = ? AND user_id = ?",
            (self.tenant_id, self.user_id),
        )
        return {"preferences": await self.get_preferences(), "feedback_count": count}
