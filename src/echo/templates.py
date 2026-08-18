"""Echo Message Templates — pre-defined notification templates with variable substitution."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MessageTemplate:
    template_id: str
    name: str
    description: str
    channel: str  # preferred channel, or "any"
    title_template: str
    content_template: str
    variables: list[str]  # extracted {{var}} names
    category: str  # "system", "task", "alert", "custom"
    icon: str  # emoji
    created_at: float
    usage_count: int = 0
    last_used: float = 0


class TemplateEngine:
    """Manage and render message templates with {{variable}} substitution."""

    def __init__(self, db_path: str | None = None):
        db = db_path or os.path.expanduser("~/opensoul/data/echo/templates.db")
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._seed_defaults()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS templates (
                template_id    TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                description    TEXT DEFAULT '',
                channel        TEXT DEFAULT 'any',
                title_template TEXT NOT NULL,
                content_template TEXT NOT NULL,
                variables      TEXT DEFAULT '[]',
                category       TEXT DEFAULT 'custom',
                icon           TEXT DEFAULT '📨',
                created_at     REAL NOT NULL,
                usage_count    INTEGER DEFAULT 0,
                last_used      REAL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_templates_cat ON templates(category);
        """)
        self._db.commit()

    def _seed_defaults(self):
        """Seed built-in templates if table is empty."""
        count = self._db.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        if count > 0:
            return

        defaults = [
            {
                "template_id": "tpl-task-complete",
                "name": "任务完成通知",
                "description": "当Agent完成任务时发送通知",
                "channel": "any",
                "title_template": "✅ 任务完成: {{task_name}}",
                "content_template": "任务 **{{task_name}}** 已完成。\n\n- 耗时: {{duration}}\n- 结果: {{result}}\n- Agent: {{agent}}",
                "category": "task",
                "icon": "✅",
            },
            {
                "template_id": "tpl-task-failed",
                "name": "任务失败告警",
                "description": "当Agent任务失败时发送告警",
                "channel": "any",
                "title_template": "❌ 任务失败: {{task_name}}",
                "content_template": "任务 **{{task_name}}** 执行失败。\n\n- 错误: {{error}}\n- Agent: {{agent}}\n- 重试次数: {{retry_count}}",
                "category": "alert",
                "icon": "❌",
            },
            {
                "template_id": "tpl-system-health",
                "name": "系统健康报告",
                "description": "定期系统健康状态报告",
                "channel": "any",
                "title_template": "💚 系统健康报告",
                "content_template": "## 系统状态: {{status}}\n\n- CPU: {{cpu}}%\n- 内存: {{memory}}%\n- 器官在线: {{organs_online}}/{{organs_total}}\n- 知识库条目: {{knowledge_count}}",
                "category": "system",
                "icon": "💚",
            },
            {
                "template_id": "tpl-knowledge-added",
                "name": "知识入库通知",
                "description": "新知识条目添加到知识库时通知",
                "channel": "any",
                "title_template": "📚 新知识入库: {{title}}",
                "content_template": "新知识条目已添加到知识库。\n\n- 标题: {{title}}\n- 来源: {{source}}\n- 标签: {{tags}}\n- 字数: {{word_count}}",
                "category": "system",
                "icon": "📚",
            },
            {
                "template_id": "tpl-backup-complete",
                "name": "备份完成通知",
                "description": "定时备份完成时发送通知",
                "channel": "any",
                "title_template": "🦴 备份完成",
                "content_template": "系统备份已完成。\n\n- 备份大小: {{size}}\n- 耗时: {{duration}}\n- 备份路径: {{path}}",
                "category": "system",
                "icon": "🦴",
            },
            {
                "template_id": "tpl-security-alert",
                "name": "安全告警",
                "description": "安全事件触发时发送告警",
                "channel": "any",
                "title_template": "🛡️ 安全告警: {{alert_type}}",
                "content_template": "安全事件检测到。\n\n- 类型: {{alert_type}}\n- 来源IP: {{source_ip}}\n- 详情: {{detail}}\n- 时间: {{timestamp}}",
                "category": "alert",
                "icon": "🛡️",
            },
            {
                "template_id": "tpl-daily-digest",
                "name": "每日摘要",
                "description": "每日活动摘要报告",
                "channel": "any",
                "title_template": "📊 每日摘要 — {{date}}",
                "content_template": "## 今日活动摘要\n\n- 新知识: {{new_knowledge}}条\n- Agent任务: {{tasks_completed}}完成 / {{tasks_failed}}失败\n- 消息推送: {{messages_sent}}条\n- 活跃用户: {{active_users}}人",
                "category": "system",
                "icon": "📊",
            },
            {
                "template_id": "tpl-workflow-trigger",
                "name": "工作流触发通知",
                "description": "工作流被触发时发送通知",
                "channel": "any",
                "title_template": "⚡ 工作流触发: {{workflow_name}}",
                "content_template": "工作流 **{{workflow_name}}** 已触发。\n\n- 触发条件: {{trigger}}\n- 执行步骤: {{steps}}\n- 预计耗时: {{estimated_time}}",
                "category": "task",
                "icon": "⚡",
            },
        ]

        now = time.time()
        for tpl in defaults:
            variables = self._extract_variables(
                tpl["title_template"] + " " + tpl["content_template"]
            )
            self._db.execute(
                """INSERT OR IGNORE INTO templates
                   (template_id, name, description, channel, title_template, content_template,
                    variables, category, icon, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tpl["template_id"],
                    tpl["name"],
                    tpl["description"],
                    tpl["channel"],
                    tpl["title_template"],
                    tpl["content_template"],
                    json.dumps(variables),
                    tpl["category"],
                    tpl["icon"],
                    now,
                ),
            )
        self._db.commit()

    @staticmethod
    def _extract_variables(text: str) -> list[str]:
        """Extract {{variable}} names from template text."""
        return list(set(re.findall(r"\{\{(\w+)\}\}", text)))

    @staticmethod
    def render(template_text: str, variables: dict[str, Any]) -> str:
        """Render a template by substituting {{variables}}."""

        def replacer(match):
            key = match.group(1)
            return str(variables.get(key, match.group(0)))

        return re.sub(r"\{\{(\w+)\}\}", replacer, template_text)

    def create(
        self,
        name: str,
        title_template: str,
        content_template: str,
        description: str = "",
        channel: str = "any",
        category: str = "custom",
        icon: str = "📨",
    ) -> MessageTemplate:
        """Create a new template."""
        template_id = f"tpl-{int(time.time())}-{hash(name) % 10000:04d}"
        variables = self._extract_variables(title_template + " " + content_template)
        now = time.time()

        self._db.execute(
            """INSERT INTO templates
               (template_id, name, description, channel, title_template, content_template,
                variables, category, icon, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                template_id,
                name,
                description,
                channel,
                title_template,
                content_template,
                json.dumps(variables),
                category,
                icon,
                now,
            ),
        )
        self._db.commit()

        return MessageTemplate(
            template_id=template_id,
            name=name,
            description=description,
            channel=channel,
            title_template=title_template,
            content_template=content_template,
            variables=variables,
            category=category,
            icon=icon,
            created_at=now,
        )

    def get(self, template_id: str) -> MessageTemplate | None:
        row = self._db.execute(
            "SELECT * FROM templates WHERE template_id = ?", (template_id,)
        ).fetchone()
        if not row:
            return None
        return MessageTemplate(
            template_id=row["template_id"],
            name=row["name"],
            description=row["description"],
            channel=row["channel"],
            title_template=row["title_template"],
            content_template=row["content_template"],
            variables=json.loads(row["variables"]),
            category=row["category"],
            icon=row["icon"],
            created_at=row["created_at"],
            usage_count=row["usage_count"],
            last_used=row["last_used"],
        )

    def list_templates(self, category: str | None = None) -> list[dict]:
        query = "SELECT * FROM templates"
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
                "channel": r["channel"],
                "title_template": r["title_template"],
                "content_template": r["content_template"],
                "variables": json.loads(r["variables"]),
                "category": r["category"],
                "icon": r["icon"],
                "usage_count": r["usage_count"],
                "last_used": r["last_used"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete(self, template_id: str) -> bool:
        cursor = self._db.execute("DELETE FROM templates WHERE template_id = ?", (template_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def update(self, template_id: str, **kwargs) -> bool:
        tpl = self.get(template_id)
        if not tpl:
            return False

        allowed = {
            "name",
            "description",
            "channel",
            "title_template",
            "content_template",
            "category",
            "icon",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False

        # Recalculate variables if templates changed
        if "title_template" in updates or "content_template" in updates:
            title = updates.get("title_template", tpl.title_template)
            content = updates.get("content_template", tpl.content_template)
            updates["variables"] = json.dumps(self._extract_variables(title + " " + content))

        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [template_id]
        self._db.execute(f"UPDATE templates SET {sets} WHERE template_id = ?", values)
        self._db.commit()
        return True

    def record_usage(self, template_id: str):
        """Record that a template was used."""
        self._db.execute(
            "UPDATE templates SET usage_count = usage_count + 1, last_used = ? WHERE template_id = ?",
            (time.time(), template_id),
        )
        self._db.commit()

    def render_template(self, template_id: str, variables: dict[str, Any]) -> dict | None:
        """Render a template with variables, returning title and content."""
        tpl = self.get(template_id)
        if not tpl:
            return None

        self.record_usage(template_id)

        return {
            "title": self.render(tpl.title_template, variables),
            "content": self.render(tpl.content_template, variables),
            "channel": tpl.channel,
            "template_id": template_id,
            "template_name": tpl.name,
        }

    def stats(self) -> dict:
        total = self._db.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        by_category = {}
        for row in self._db.execute(
            "SELECT category, COUNT(*) as cnt FROM templates GROUP BY category"
        ):
            by_category[row["category"]] = row["cnt"]
        total_usage = self._db.execute(
            "SELECT COALESCE(SUM(usage_count), 0) FROM templates"
        ).fetchone()[0]
        return {
            "total_templates": total,
            "by_category": by_category,
            "total_usage": total_usage,
        }
