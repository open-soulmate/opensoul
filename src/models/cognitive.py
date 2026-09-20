"""
OpenSoul 认知层数据模型 — Intent, Risk, Decision, Verification, TaskContext
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


@dataclass
class Intent:
    user_prompt: str
    target_files: list[str]
    modify_scope: Literal["single_method", "single_file", "multi_file", "project_wide"]
    goal: str  # "fix_bug" / "add_feature" / "refactor" / "create" / "rewrite"
    change_size: Literal["small", "medium", "large"]


@dataclass
class Risk:
    title: str
    level: Literal["low", "medium", "high", "critical"]
    desc: str


@dataclass
class RiskAssessment:
    risks: list[Risk]
    overall_level: Literal["low", "medium", "high", "critical"]
    recommendation: str


@dataclass
class Decision:
    intent: Intent
    risk: RiskAssessment
    edit_mode: Literal["patch", "full", "stepwise"]
    execute_mode: Literal["auto", "confirm_required", "deny"]
    confirm_prompt: str | None = None


@dataclass
class CheckItem:
    name: str
    passed: bool
    error: str | None = None


@dataclass
class Verification:
    success: bool
    checks: list[CheckItem]
    error: str | None = None
    fix: str | None = None


@dataclass
class FileInfo:
    path: str
    language: str
    lines: int
    size_bytes: int
    last_modified: datetime
    is_core: bool = False


@dataclass
class ImpactAnalysis:
    direct_impact: list[str]
    indirect_impact: list[str]
    risk_level: Literal["low", "medium", "high", "critical"]


@dataclass
class TaskContext:
    task_id: str = ""
    user_input: str = ""
    intent: Intent | None = None
    risk: RiskAssessment | None = None
    decision: Decision | None = None
    tool_result: dict | None = None
    verification: Verification | None = None
    history: list[dict] = field(default_factory=list)


@dataclass
class Experience:
    action: str
    intent_summary: str
    outcome: Literal["success", "failure", "partial"]
    error: str | None = None
    fix: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    relevance_score: float = 1.0

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "intent_summary": self.intent_summary,
            "outcome": self.outcome,
            "error": self.error,
            "fix": self.fix,
            "timestamp": self.timestamp.isoformat(),
            "relevance_score": self.relevance_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Experience":
        return cls(
            action=data["action"],
            intent_summary=data["intent_summary"],
            outcome=data["outcome"],
            error=data.get("error"),
            fix=data.get("fix"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            relevance_score=data.get("relevance_score", 1.0),
        )
