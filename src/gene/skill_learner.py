"""Skill learner for OpenGene.

Automatically extracts reusable skills from successful executions,
forming a skill library for future use.

Borrowed from: Voyager skill library, CodeAct
"""

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opensoul.gene.skill_learner")


@dataclass
class Skill:
    skill_id: str
    name: str
    description: str
    category: str = "general"
    code_template: str = ""
    parameters: list[str] = field(default_factory=list)
    usage_count: int = 0
    success_count: int = 0
    avg_duration_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    last_used_at: float = 0.0
    tags: list[str] = field(default_factory=list)
    source_session: str = ""

    @property
    def success_rate(self) -> float:
        return self.success_count / self.usage_count if self.usage_count else 0.0


class SkillLearner:
    """Extracts and manages reusable skills from executions."""

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "gene"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "skills.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT 'general',
                    code_template TEXT DEFAULT '',
                    parameters TEXT DEFAULT '[]',
                    usage_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    avg_duration_ms REAL DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_used_at REAL DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    source_session TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_skills_category
                ON learned_skills(category, success_count)
            """)
            conn.commit()

    def extract_from_execution(
        self,
        session_id: str,
        task_description: str,
        tool_calls: list[dict],
        success: bool,
    ) -> Optional[Skill]:
        """Extract a reusable skill from a successful execution."""
        if not success or not tool_calls:
            return None

        tools_used = [tc.get("function", {}).get("name", "") for tc in tool_calls]
        tool_sequence = " → ".join(t for t in tools_used if t)

        # Only extract if meaningful pattern detected
        meaningful_patterns = [
            ("read_file", "write_file"),
            ("terminal", "read_file"),
            ("web_search", "read_file"),
            ("browser_exec", "terminal"),
        ]
        is_pattern = any(
            all(t in tools_used for t in pattern)
            for pattern in meaningful_patterns
        )
        if len(tools_used) < 2 and not is_pattern:
            return None

        skill_id = f"skill_{hashlib.sha256(f'{task_description}:{tool_sequence}'.encode()).hexdigest()[:12]}"
        parameters = self._extract_parameters(tool_calls)
        code_template = self._generate_template(tool_calls)

        skill = Skill(
            skill_id=skill_id,
            name=task_description[:50],
            description=f"从执行中学习: {tool_sequence}",
            category=self._categorize(task_description),
            code_template=code_template,
            parameters=parameters,
            tags=list(set(t for t in tools_used if t)),
            source_session=session_id,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO learned_skills
                   (skill_id, name, description, category, code_template,
                    parameters, usage_count, success_count, avg_duration_ms,
                    created_at, last_used_at, tags, source_session)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill.skill_id, skill.name, skill.description, skill.category,
                 skill.code_template, json.dumps(skill.parameters),
                 1, 1, 0, time.time(), time.time(),
                 json.dumps(skill.tags), session_id),
            )
            conn.commit()

        logger.info(f"Extracted skill: {skill.name} ({skill.category})")
        return skill

    def _extract_parameters(self, tool_calls: list[dict]) -> list[str]:
        params = set()
        for tc in tool_calls:
            args = tc.get("function", {}).get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            for key in ("path", "command", "url", "query", "content"):
                if key in args:
                    params.add(key)
        return list(params)

    def _generate_template(self, tool_calls: list[dict]) -> str:
        lines = []
        for tc in tool_calls[:5]:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            param_args = {}
            for k, v in args.items():
                if isinstance(v, str) and len(v) > 10:
                    param_args[k] = f"{{{{{k}}}}}"
                else:
                    param_args[k] = v
            lines.append(f"{name}({json.dumps(param_args, ensure_ascii=False)})")
        return "\n".join(lines)

    def _categorize(self, description: str) -> str:
        desc = description.lower()
        if any(kw in desc for kw in ["file", "write", "read", "edit", "文件"]):
            return "file_operations"
        if any(kw in desc for kw in ["web", "search", "browser", "网页", "搜索"]):
            return "web_operations"
        if any(kw in desc for kw in ["terminal", "command", "shell", "命令"]):
            return "system_operations"
        if any(kw in desc for kw in ["code", "debug", "test", "代码", "测试"]):
            return "coding"
        return "general"

    def find_relevant(self, task_description: str, limit: int = 3) -> list[dict]:
        """Find skills relevant to a task description."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM learned_skills
                   ORDER BY usage_count DESC, success_count DESC
                   LIMIT ?""",
                (limit * 3,),
            ).fetchall()

        task_lower = task_description.lower()
        scored = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["parameters"] = json.loads(d.get("parameters", "[]"))
            score = d["success_count"] * 0.3 + d["usage_count"] * 0.1
            if any(kw in d["name"].lower() for kw in task_lower.split()):
                score += 0.5
            if any(kw in d["description"].lower() for kw in task_lower.split()):
                score += 0.3
            d["relevance_score"] = score
            scored.append(d)

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored[:limit]

    def get_context_prompt(self, task_description: str) -> str:
        """Generate skill context for system prompt injection."""
        skills = self.find_relevant(task_description, limit=2)
        if not skills:
            return ""

        lines = ["## 可复用技能（从历史执行中学习）\n"]
        for skill in skills:
            lines.append(f"### {skill['name']}")
            lines.append(f"描述: {skill['description']}")
            if skill["code_template"]:
                lines.append(f"模板:\n```\n{skill['code_template']}\n```")
            lines.append(f"成功率: {skill['success_count']}/{skill['usage_count']}")
            lines.append("")

        return "\n".join(lines)

    def record_usage(self, skill_id: str, success: bool):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE learned_skills SET
                   usage_count = usage_count + 1,
                   success_count = success_count + ?,
                   last_used_at = ?
                   WHERE skill_id = ?""",
                (1 if success else 0, time.time(), skill_id),
            )
            conn.commit()

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0]
            by_category = conn.execute(
                "SELECT category, COUNT(*) FROM learned_skills GROUP BY category"
            ).fetchall()
            top = conn.execute(
                """SELECT name, usage_count, success_count
                   FROM learned_skills ORDER BY usage_count DESC LIMIT 5"""
            ).fetchall()

        return {
            "total_skills": total,
            "by_category": {c[0]: c[1] for c in by_category},
            "top_skills": [
                {"name": s[0], "uses": s[1], "successes": s[2]}
                for s in top
            ],
        }
