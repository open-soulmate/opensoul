"""Experience Memory — 成功/失败模式记录，关键词检索、老化衰减。"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.cognitive import Experience, Intent, TaskContext, Verification

logger = logging.getLogger(__name__)


class ExperienceMemory:
    """经验记忆：成功/失败模式，关键词检索、老化衰减"""

    def __init__(self, storage_path: str = ""):
        self.storage_path = Path(storage_path) if storage_path else Path(
            __import__("os").path.expanduser("~/.opensoul/experience.json")
        )
        self.experiences: list[Experience] = []
        self._load()

    def record(self, action: str, result: dict, verification: Verification, task_ctx: TaskContext):
        exp = Experience(
            action=action,
            intent_summary=task_ctx.intent.goal if task_ctx.intent else "unknown",
            outcome="success" if verification.success else "failure",
            error=verification.error,
            fix=verification.fix,
        )
        self.experiences.append(exp)
        self._aging_prune()
        self._save()

    def get_relevant_experience(self, intent: Intent) -> list[Experience]:
        relevant = []
        for exp in self.experiences:
            score = self._compute_relevance(exp, intent)
            if score > 0.2:
                exp.relevance_score = score
                relevant.append(exp)
        relevant.sort(key=lambda e: e.relevance_score, reverse=True)
        return relevant[:10]

    def get_similar_failures(self, intent: Intent) -> list[Experience]:
        return [e for e in self.get_relevant_experience(intent) if e.outcome == "failure"]

    def _compute_relevance(self, exp: Experience, intent: Intent) -> float:
        score = 0.0
        if exp.intent_summary == intent.goal:
            score += 0.5
        for f in intent.target_files:
            if f in exp.action:
                score += 0.3
                break
        age_hours = (datetime.now() - exp.timestamp).total_seconds() / 3600
        time_decay = max(0.1, 1.0 - age_hours / (24 * 30))
        score *= time_decay
        if exp.outcome == "failure":
            score += 0.15
        return min(1.0, score)

    def _aging_prune(self):
        now = datetime.now()
        self.experiences = [e for e in self.experiences if (now - e.timestamp).days < 90]
        if len(self.experiences) > 1000:
            self.experiences.sort(key=lambda e: e.relevance_score, reverse=True)
            self.experiences = self.experiences[:1000]

    def _load(self):
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text())
            self.experiences = [Experience.from_dict(d) for d in data]
        except Exception as e:
            logger.warning("加载经验失败: %s", e)

    def _save(self):
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(
                json.dumps([e.to_dict() for e in self.experiences], indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error("保存经验失败: %s", e)

    def get_stats(self) -> dict:
        now = datetime.now()
        return {
            "total": len(self.experiences),
            "success_count": sum(1 for e in self.experiences if e.outcome == "success"),
            "failure_count": sum(1 for e in self.experiences if e.outcome == "failure"),
        }
