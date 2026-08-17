"""Mirror Sandbox Templates — pre-defined sandbox configurations for common testing scenarios."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SandboxTemplate:
    template_id: str
    name: str
    description: str
    icon: str
    config: dict
    variables: dict
    tags: list[str]
    category: str  # "workflow", "agent", "connector", "custom"
    usage_count: int = 0
    created_at: float = 0


class SandboxTemplateEngine:
    """Manage sandbox templates for quick sandbox creation."""

    def __init__(self, db_path: str | None = None):
        db = db_path or os.path.expanduser("~/opensoul/data/mirror/templates.db")
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._seed_defaults()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sandbox_templates (
                template_id    TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                description    TEXT DEFAULT '',
                icon           TEXT DEFAULT '🧪',
                config         TEXT DEFAULT '{}',
                variables      TEXT DEFAULT '{}',
                tags           TEXT DEFAULT '[]',
                category       TEXT DEFAULT 'custom',
                usage_count    INTEGER DEFAULT 0,
                created_at     REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sboxtpl_cat ON sandbox_templates(category);
        """)
        self._db.commit()

    def _seed_defaults(self):
        count = self._db.execute("SELECT COUNT(*) FROM sandbox_templates").fetchone()[0]
        if count > 0:
            return

        defaults = [
            {
                "template_id": "stpl-workflow-test",
                "name": "工作流测试沙箱",
                "description": "用于测试工作流的隔离环境，预设工作流相关变量",
                "icon": "⚡",
                "config": {"ttl_seconds": 7200, "auto_snapshot": True},
                "variables": {"workflow_id": "", "trigger_type": "manual", "test_mode": "true"},
                "tags": ["workflow", "test"],
                "category": "workflow",
            },
            {
                "template_id": "stpl-agent-debug",
                "name": "Agent调试沙箱",
                "description": "用于调试Agent行为的隔离环境，启用详细日志",
                "icon": "🤖",
                "config": {"ttl_seconds": 3600, "log_level": "debug"},
                "variables": {"agent_id": "", "debug_mode": "true", "max_steps": "10"},
                "tags": ["agent", "debug"],
                "category": "agent",
            },
            {
                "template_id": "stpl-connector-test",
                "name": "连接器测试沙箱",
                "description": "用于测试外部连接器的沙箱，模拟外部系统响应",
                "icon": "🔌",
                "config": {"ttl_seconds": 1800},
                "variables": {"connector_type": "", "mock_response": "true", "endpoint": ""},
                "tags": ["connector", "integration"],
                "category": "connector",
            },
            {
                "template_id": "stpl-knowledge-import",
                "name": "知识导入沙箱",
                "description": "用于测试知识导入流程的隔离环境",
                "icon": "📚",
                "config": {"ttl_seconds": 3600},
                "variables": {"source_type": "", "auto_tag": "true", "dedup_enabled": "true"},
                "tags": ["knowledge", "import"],
                "category": "workflow",
            },
            {
                "template_id": "stpl-prompt-experiment",
                "name": "Prompt实验沙箱",
                "description": "用于测试不同Prompt策略的隔离环境",
                "icon": "🧪",
                "config": {"ttl_seconds": 1800, "log_level": "debug"},
                "variables": {"model": "", "temperature": "0.7", "max_tokens": "2000", "system_prompt": ""},
                "tags": ["prompt", "experiment"],
                "category": "agent",
            },
            {
                "template_id": "stpl-api-stress",
                "name": "API压测沙箱",
                "description": "用于API压力测试的隔离环境",
                "icon": "🏋️",
                "config": {"ttl_seconds": 900},
                "variables": {"endpoint": "", "concurrent": "10", "total_requests": "100"},
                "tags": ["api", "stress-test"],
                "category": "connector",
            },
        ]

        now = time.time()
        for tpl in defaults:
            self._db.execute(
                """INSERT OR IGNORE INTO sandbox_templates
                   (template_id, name, description, icon, config, variables, tags, category, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tpl["template_id"], tpl["name"], tpl["description"], tpl["icon"],
                    json.dumps(tpl["config"]), json.dumps(tpl["variables"]),
                    json.dumps(tpl["tags"]), tpl["category"], now,
                ),
            )
        self._db.commit()

    def create(
        self,
        name: str,
        description: str = "",
        icon: str = "🧪",
        config: dict | None = None,
        variables: dict | None = None,
        tags: list[str] | None = None,
        category: str = "custom",
    ) -> SandboxTemplate:
        template_id = f"stpl-{int(time.time())}-{hash(name) % 10000:04d}"
        now = time.time()

        self._db.execute(
            """INSERT INTO sandbox_templates
               (template_id, name, description, icon, config, variables, tags, category, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (template_id, name, description, icon,
             json.dumps(config or {}), json.dumps(variables or {}),
             json.dumps(tags or []), category, now),
        )
        self._db.commit()

        return SandboxTemplate(
            template_id=template_id, name=name, description=description,
            icon=icon, config=config or {}, variables=variables or {},
            tags=tags or [], category=category, created_at=now,
        )

    def get(self, template_id: str) -> SandboxTemplate | None:
        row = self._db.execute(
            "SELECT * FROM sandbox_templates WHERE template_id = ?", (template_id,)
        ).fetchone()
        if not row:
            return None
        return SandboxTemplate(
            template_id=row["template_id"], name=row["name"], description=row["description"],
            icon=row["icon"], config=json.loads(row["config"]),
            variables=json.loads(row["variables"]), tags=json.loads(row["tags"]),
            category=row["category"], usage_count=row["usage_count"],
            created_at=row["created_at"],
        )

    def list_templates(self, category: str | None = None) -> list[dict]:
        query = "SELECT * FROM sandbox_templates"
        params: list = []
        if category:
            query += " WHERE category = ?"
            params.append(category)
        query += " ORDER BY usage_count DESC, created_at DESC"

        rows = self._db.execute(query, params).fetchall()
        return [
            {
                "template_id": r["template_id"],
                "name": r["name"],
                "description": r["description"],
                "icon": r["icon"],
                "config": json.loads(r["config"]),
                "variables": json.loads(r["variables"]),
                "tags": json.loads(r["tags"]),
                "category": r["category"],
                "usage_count": r["usage_count"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete(self, template_id: str) -> bool:
        cursor = self._db.execute(
            "DELETE FROM sandbox_templates WHERE template_id = ?", (template_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def record_usage(self, template_id: str):
        self._db.execute(
            "UPDATE sandbox_templates SET usage_count = usage_count + 1 WHERE template_id = ?",
            (template_id,),
        )
        self._db.commit()

    def instantiate(self, template_id: str, overrides: dict | None = None) -> dict | None:
        """Get template config for creating a sandbox from it."""
        tpl = self.get(template_id)
        if not tpl:
            return None

        self.record_usage(template_id)

        config = {**tpl.config}
        variables = {**tpl.variables}
        if overrides:
            variables.update(overrides)

        return {
            "name": f"{tpl.icon} {tpl.name}",
            "description": tpl.description,
            "config": config,
            "variables": variables,
            "tags": tpl.tags,
            "ttl_seconds": config.get("ttl_seconds", 3600),
        }

    def stats(self) -> dict:
        total = self._db.execute("SELECT COUNT(*) FROM sandbox_templates").fetchone()[0]
        by_category = {}
        for row in self._db.execute("SELECT category, COUNT(*) as cnt FROM sandbox_templates GROUP BY category"):
            by_category[row["category"]] = row["cnt"]
        return {
            "total_templates": total,
            "by_category": by_category,
        }
