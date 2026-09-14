"""User Memory — 用户偏好、操作习惯、风险容忍度。"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class UserMemory:
    """用户记忆：偏好、习惯、风险容忍度"""

    def __init__(self, storage_path: str = ""):
        self.storage_path = Path(storage_path) if storage_path else Path(
            os.path.expanduser("~/.opensoul/user_memory.json")
        )
        self.preferences: dict = {
            "edit_mode_preference": "patch",
            "risk_tolerance": "medium",
            "auto_confirm_low_risk": True,
            "always_backup_before_edit": True,
            "preferred_languages": [],
            "notification_level": "normal",
        }
        self.feedback_history: list = []
        self._load()

    def get(self, key: str, default=None):
        return self.preferences.get(key, default)

    def set(self, key: str, value):
        self.preferences[key] = value
        self._save()

    def record_feedback(self, action: str, feedback: str, rating: int):
        self.feedback_history.append({"action": action, "feedback": feedback, "rating": rating})
        self._save()

    def should_auto_execute(self, risk_level: str) -> bool:
        tolerance = self.preferences.get("risk_tolerance", "medium")
        if risk_level == "low":
            return self.preferences.get("auto_confirm_low_risk", True)
        elif risk_level == "medium":
            return tolerance == "high"
        return False

    def _load(self):
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text())
            self.preferences.update(data.get("preferences", {}))
            self.feedback_history = data.get("feedback_history", [])
        except Exception as e:
            logger.warning("加载用户记忆失败: %s", e)

    def _save(self):
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(json.dumps({
                "preferences": self.preferences,
                "feedback_history": self.feedback_history[-200:],
            }, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error("保存用户记忆失败: %s", e)
