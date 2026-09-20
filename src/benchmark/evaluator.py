"""Agent capability evaluator for OpenBenchmark.

Multi-dimensional evaluation of agent capabilities: accuracy, efficiency,
completeness, safety, helpfulness. Tracks trends and identifies weaknesses.

Borrowed from: AgentBench, HELM, Holistic Evaluation
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opensoul.benchmark.evaluator")


@dataclass
class EvaluationDimension:
    dimension_id: str
    name: str
    description: str
    weight: float = 1.0


@dataclass
class EvaluationResult:
    eval_id: str
    session_id: str
    task_type: str
    dimensions: dict[str, float]
    overall_score: float = 0.0
    timestamp: float = field(default_factory=time.time)
    notes: str = ""


DEFAULT_DIMENSIONS = [
    EvaluationDimension("accuracy", "准确性", "回答/执行结果是否正确", 0.3),
    EvaluationDimension("efficiency", "效率", "完成任务的速度和token消耗", 0.2),
    EvaluationDimension("completeness", "完整性", "是否完整解决了用户问题", 0.2),
    EvaluationDimension("safety", "安全性", "是否遵守安全规则", 0.15),
    EvaluationDimension("helpfulness", "帮助性", "对用户的实际帮助程度", 0.15),
]


class CapabilityEvaluator:
    """Evaluates agent capabilities across multiple dimensions."""

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "benchmark"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "evaluations.db")
        self.db_path = db_path
        self.dimensions = {d.dimension_id: d for d in DEFAULT_DIMENSIONS}
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evaluations (
                    eval_id TEXT PRIMARY KEY,
                    session_id TEXT DEFAULT '',
                    task_type TEXT DEFAULT 'general',
                    dimensions TEXT DEFAULT '{}',
                    overall_score REAL DEFAULT 0,
                    timestamp REAL NOT NULL,
                    notes TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_eval_time
                ON evaluations(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_eval_task
                ON evaluations(task_type, timestamp)
            """)
            conn.commit()

    def record(
        self,
        session_id: str,
        task_type: str,
        dimension_scores: dict[str, float],
        notes: str = "",
    ) -> EvaluationResult:
        """Record an evaluation."""
        total_weight = sum(
            self.dimensions[d].weight for d in dimension_scores if d in self.dimensions
        )
        weighted_sum = sum(
            dimension_scores.get(d, 0) * self.dimensions[d].weight
            for d in dimension_scores
            if d in self.dimensions
        )
        overall = weighted_sum / total_weight if total_weight else 0

        eval_id = f"eval_{int(time.time() * 1000)}"
        result = EvaluationResult(
            eval_id=eval_id,
            session_id=session_id,
            task_type=task_type,
            dimensions=dimension_scores,
            overall_score=overall,
            notes=notes,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO evaluations
                   (eval_id, session_id, task_type, dimensions, overall_score, timestamp, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    eval_id,
                    session_id,
                    task_type,
                    json.dumps(dimension_scores),
                    overall,
                    time.time(),
                    notes,
                ),
            )
            conn.commit()

        logger.info(f"Recorded evaluation: {task_type} overall={overall:.2f}")
        return result

    def get_trend(self, days: int = 7, task_type: str = "") -> dict:
        """Get capability trend over time."""
        cutoff = time.time() - days * 86400

        sql = "SELECT * FROM evaluations WHERE timestamp > ?"
        params: list = [cutoff]
        if task_type:
            sql += " AND task_type = ?"
            params.append(task_type)
        sql += " ORDER BY timestamp"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        if not rows:
            return {"message": "No evaluations in this period"}

        by_day: dict[str, list[float]] = {}
        dim_by_day: dict[str, dict[str, list[float]]] = {}

        for row in rows:
            day = datetime.fromtimestamp(row["timestamp"]).strftime("%m-%d")
            by_day.setdefault(day, []).append(row["overall_score"])
            dims = json.loads(row["dimensions"] or "{}")
            for d, score in dims.items():
                dim_by_day.setdefault(day, {}).setdefault(d, []).append(score)

        daily_avg = {day: round(sum(s) / len(s), 3) for day, s in by_day.items()}

        values = list(daily_avg.values())
        trend = "stable"
        if len(values) >= 2:
            recent = sum(values[-3:]) / min(3, len(values))
            earlier = sum(values[:3]) / min(3, len(values))
            if recent > earlier + 0.05:
                trend = "improving"
            elif recent < earlier - 0.05:
                trend = "declining"

        return {
            "daily_avg": daily_avg,
            "overall_trend": trend,
            "total_evaluations": len(rows),
            "latest_score": values[-1] if values else 0,
        }

    def get_weak_dimensions(self, task_type: str = "", min_samples: int = 5) -> list[dict]:
        """Identify dimensions scoring below 70%."""
        sql = "SELECT dimensions FROM evaluations"
        params: list = []
        if task_type:
            sql += " WHERE task_type = ?"
            params.append(task_type)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()

        dim_scores: dict[str, list[float]] = {}
        for row in rows:
            dims = json.loads(row[0] or "{}")
            for d, score in dims.items():
                dim_scores.setdefault(d, []).append(score)

        weak = []
        for d, scores in dim_scores.items():
            if len(scores) >= min_samples:
                avg = sum(scores) / len(scores)
                if avg < 0.7:
                    weak.append(
                        {
                            "dimension": d,
                            "name": self.dimensions.get(d, EvaluationDimension(d, d, "")).name,
                            "avg_score": round(avg, 3),
                            "sample_count": len(scores),
                        }
                    )

        weak.sort(key=lambda x: x["avg_score"])
        return weak

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
            avg = conn.execute("SELECT AVG(overall_score) FROM evaluations").fetchone()[0]
            recent = conn.execute(
                "SELECT AVG(overall_score) FROM evaluations WHERE timestamp > ?",
                (time.time() - 7 * 86400,),
            ).fetchone()[0]

        return {
            "total_evaluations": total,
            "avg_score": round(avg, 3) if avg else 0,
            "recent_avg_score": round(recent, 3) if recent else 0,
            "dimensions": list(self.dimensions.keys()),
        }
