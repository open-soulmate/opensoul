"""Preference learner for OpenMind.

Automatically learns user preferences from conversation messages.
Borrowed from MemoryBank + ChatGPT Memory + Generative Agents.

Fuses with existing UserMemory: UserMemory stores explicit preferences,
PreferenceLearner infers preferences from conversation patterns.
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opensoul.mind.preference")


@dataclass
class LearnedPreference:
    pref_id: str
    category: str  # "communication", "technical", "workflow", "personal"
    key: str
    value: str
    confidence: float = 0.5
    source: str = "inferred"  # "explicit" or "inferred"
    evidence: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    use_count: int = 0


class PreferenceLearner:
    """Learns user preferences from conversation messages."""

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "mind"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "preferences.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_preferences (
                    pref_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    source TEXT DEFAULT 'inferred',
                    evidence TEXT DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    use_count INTEGER DEFAULT 0,
                    UNIQUE(category, key)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pref_category
                ON learned_preferences(category, confidence)
            """)
            conn.commit()

    def learn_from_message(self, message: str) -> list[LearnedPreference]:
        """Extract preferences from a user message."""
        learned = []

        # Explicit preference patterns
        explicit_patterns = [
            (r"(?:我|我喜欢|我想要|请用|请使用|prefer|I prefer)\s*(.+?)(?:[。，,.]|$)", "explicit"),
            (r"(?:不要|别用|别再|我不喜欢|don't|never)\s*(.+?)(?:[。，,.]|$)", "explicit_negative"),
            (r"(?:以后|以后都|以后请|from now on)\s*(.+?)(?:[。，,.]|$)", "explicit"),
            (r"(?:记住|remember|记住这个)\s*(.+?)(?:[。，,.]|$)", "explicit"),
        ]

        for pattern, source in explicit_patterns:
            for match in re.finditer(pattern, message, re.IGNORECASE):
                content = match.group(1).strip()
                if len(content) > 2:
                    pref = self._upsert(
                        category="communication"
                        if "用" in content or "prefer" in content.lower()
                        else "general",
                        key=self._extract_key(content),
                        value=content,
                        confidence=0.9 if source == "explicit" else 0.3,
                        source=source,
                        evidence=[message[:200]],
                    )
                    if pref:
                        learned.append(pref)

        # Inference patterns
        inference_patterns = [
            (r"(?:简短|简洁|short|concise|brief)", "communication", "response_style", "concise"),
            (
                r"(?:详细|详细点|detailed|thorough|comprehensive)",
                "communication",
                "response_style",
                "detailed",
            ),
            (r"(?:中文|Chinese)", "communication", "language", "zh-CN"),
            (r"(?:英文|English)", "communication", "language", "en-US"),
            (r"(?:代码|code|编程|programming)", "technical", "domain", "coding"),
            (r"(?:Python)", "technical", "preferred_language", "Python"),
            (r"(?:TypeScript|TS)", "technical", "preferred_language", "TypeScript"),
            (r"(?:快速|快点|hurry|quick|fast)", "workflow", "speed", "fast"),
        ]

        for pattern, category, key, value in inference_patterns:
            if re.search(pattern, message, re.IGNORECASE):
                pref = self._upsert(
                    category=category,
                    key=key,
                    value=value,
                    confidence=0.4,
                    source="inferred",
                    evidence=[message[:200]],
                )
                if pref:
                    learned.append(pref)

        if learned:
            logger.debug(f"Learned {len(learned)} preferences from message")
        return learned

    def _upsert(
        self,
        category: str,
        key: str,
        value: str,
        confidence: float,
        source: str,
        evidence: list[str],
    ) -> LearnedPreference | None:
        pref_id = f"pref_{hashlib.sha256(f'{category}:{key}'.encode()).hexdigest()[:12]}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT * FROM learned_preferences WHERE category = ? AND key = ?",
                (category, key),
            ).fetchone()

            if existing:
                old_conf = existing["confidence"]
                new_conf = (old_conf + confidence) / 2
                old_evidence = json.loads(existing["evidence"])
                all_evidence = (old_evidence + evidence)[-5:]

                conn.execute(
                    """UPDATE learned_preferences SET
                       value = ?, confidence = ?, source = ?, evidence = ?, updated_at = ?
                       WHERE category = ? AND key = ?""",
                    (
                        value,
                        new_conf,
                        source,
                        json.dumps(all_evidence, ensure_ascii=False),
                        time.time(),
                        category,
                        key,
                    ),
                )
                conn.commit()
                return LearnedPreference(
                    pref_id=pref_id,
                    category=category,
                    key=key,
                    value=value,
                    confidence=new_conf,
                    source=source,
                    evidence=all_evidence,
                )
            else:
                conn.execute(
                    """INSERT INTO learned_preferences
                       (pref_id, category, key, value, confidence, source, evidence, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pref_id,
                        category,
                        key,
                        value,
                        confidence,
                        source,
                        json.dumps(evidence, ensure_ascii=False),
                        time.time(),
                        time.time(),
                    ),
                )
                conn.commit()
                return LearnedPreference(
                    pref_id=pref_id,
                    category=category,
                    key=key,
                    value=value,
                    confidence=confidence,
                    source=source,
                    evidence=evidence,
                )

    def get_preferences(self, category: str = "", min_confidence: float = 0.3) -> list[dict]:
        sql = "SELECT * FROM learned_preferences WHERE confidence >= ?"
        params: list = [min_confidence]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY confidence DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_context_prompt(self) -> str:
        """Generate preference context for system prompt injection."""
        prefs = self.get_preferences(min_confidence=0.5)
        if not prefs:
            return ""

        lines = ["## 用户偏好（自动学习）\n"]
        for p in prefs[:10]:
            source_icon = "📌" if p["source"] == "explicit" else "💡"
            lines.append(
                f"- {source_icon} [{p['category']}] {p['key']}: {p['value']} "
                f"(置信度: {p['confidence']:.0%})"
            )
        return "\n".join(lines)

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM learned_preferences").fetchone()[0]
            by_category = conn.execute(
                "SELECT category, COUNT(*) FROM learned_preferences GROUP BY category"
            ).fetchall()
            explicit = conn.execute(
                "SELECT COUNT(*) FROM learned_preferences WHERE source = 'explicit'"
            ).fetchone()[0]

        return {
            "total_preferences": total,
            "explicit_preferences": explicit,
            "inferred_preferences": total - explicit,
            "by_category": {c[0]: c[1] for c in by_category},
        }

    def _extract_key(self, text: str) -> str:
        return text[:20].strip()
