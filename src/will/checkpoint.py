"""Workflow execution checkpoint store — STORM分阶段落盘 + agno /continue断点续跑。

调研来源：
- SUMMARY.md §五 will差距表 P1："分阶段checkpoint断点续跑 | STORM分阶段落盘+agno /continue?continue_from"
- 40-storm-source.md #17（P0）："Runner分阶段+本地断点续跑（run_knowledge_curation/run_outline_generation/
  run_article_generation/run_article_polishing可分开跑；_load_information_table_from_local_fs等从磁盘
  恢复上一阶段产物）——长研究任务必须：跑到第3阶段崩了不用重跑前2阶段"
- 65-ag2-source.md #8（P0）："checkpoint()持久化resume state，resume_from=prior_task_id读回checkpoint
  断点续跑（HubBackedCheckpointStore）"
- evolution-engine-patterns.md §5.2 agno job_queue："continue的budget grant = max_attempts = attempt+1
  （用户触发的一次续跑绝不静默重跑）"；"paused tickets are retention-exempt—must outlive arbitrary
  human latency"（可续跑的执行豁免自动清理）
- 21-langflow-source.md #3："持久化图Checkpoint：暂停的图序列化为GraphCheckpoint按(job_id,'graph')
  一行落库，进程重启后按run_id恢复；跨用户扫描风险显式raise防泄漏"
- evolution-engine-patterns.md §1.1 mem0："失败必须可见禁止静默降级"——interrupted执行显式标记
  WAITING+error原因，绝不静默消失
- evolution-engine-patterns.md §4.5 LangGraph："关键写入sync、批量试验async"——每阶段checkpoint
  同步落盘

设计：
- 每个WorkflowExecution一行（execution_id主键），每个DAG节点执行完即save（分阶段落盘）
- 步骤输出（script output / LLM response / HTTP response）随checkpoint持久化——
  续跑时已完成阶段的产物从盘上恢复进variables，不重新执行（STORM语义）
- 进程重启后：RUNNING/PENDING的执行由engine恢复为WAITING（可续跑+原因可见），
  终态执行原样恢复（重启不丢失执行历史——可观测性）
- prune只清理success/cancelled终态；WAITING/FAILED可续跑执行豁免（agno retention-exempt）
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ExecutionStatus, StepExecution, WorkflowExecution

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "will_checkpoints.db"

# 可续跑状态（resume准入）：WAITING=被重启打断 / FAILED=节点失败待修复后重跑
RESUMABLE_STATES = frozenset({ExecutionStatus.WAITING, ExecutionStatus.FAILED})
# prune豁免状态（agno retention-exempt：可续跑的执行必须活过任意长的人工延迟）
_PRUNE_EXEMPT = RESUMABLE_STATES | {ExecutionStatus.RUNNING, ExecutionStatus.PENDING}


class CheckpointStore:
    """SQLite-backed execution checkpoint store（Langflow GraphCheckpoint按行落库语义）。

    单文件SQLite；写入为同步调用（LangGraph"关键写入sync"）——每次节点执行完落盘一次。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS will_checkpoints (
                        execution_id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        workflow_name TEXT DEFAULT '',
                        status TEXT NOT NULL,
                        started_at TEXT DEFAULT '',
                        updated_at TEXT DEFAULT '',
                        completed_at TEXT,
                        variables TEXT DEFAULT '{}',
                        steps TEXT DEFAULT '[]',
                        error TEXT,
                        trigger_type TEXT DEFAULT 'manual',
                        resume_count INTEGER DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ckpt_workflow "
                    "ON will_checkpoints(workflow_id, started_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ckpt_status ON will_checkpoints(status)"
                )
        except Exception as exc:
            logger.warning("Checkpoint store init failed (non-fatal): %s", exc)

    # ── Core CRUD ───────────────────────────────────────────────

    def save(self, execution: WorkflowExecution) -> None:
        """Upsert一次execution全量状态（每阶段落盘后调用）。

        resume_count从execution对象读取（models.WorkflowExecution.resume_count）。
        写盘失败只记日志不阻塞执行（与engine._save同哲学：持久化故障不阻塞运行），
        但绝不静默假装成功——logger.warning即失败可见。
        """
        try:
            data = execution.model_dump(mode="json")
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO will_checkpoints (
                        execution_id, workflow_id, workflow_name, status,
                        started_at, updated_at, completed_at,
                        variables, steps, error, trigger_type, resume_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(execution_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        completed_at=excluded.completed_at,
                        variables=excluded.variables,
                        steps=excluded.steps,
                        error=excluded.error,
                        resume_count=excluded.resume_count
                    """,
                    (
                        execution.id,
                        execution.workflow_id,
                        execution.workflow_name,
                        execution.status.value
                        if isinstance(execution.status, ExecutionStatus)
                        else str(execution.status),
                        execution.started_at,
                        datetime.now(UTC).isoformat(),
                        execution.completed_at,
                        json.dumps(data.get("variables", {}), ensure_ascii=False),
                        json.dumps(data.get("steps", []), ensure_ascii=False),
                        execution.error,
                        execution.trigger_type,
                        int(getattr(execution, "resume_count", 0) or 0),
                    ),
                )
        except Exception as exc:
            logger.warning("Checkpoint save failed for %s: %s", execution.id, exc)

    def load(self, execution_id: str) -> WorkflowExecution | None:
        """按execution_id恢复（STORM _load_*_from_local_fs语义 / ag2 resume_from读回）。

        坏行宽容跳过（单行损坏不炸整个恢复流程）但日志可见。
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM will_checkpoints WHERE execution_id = ?", (execution_id,)
                ).fetchone()
        except Exception as exc:
            logger.warning("Checkpoint load failed for %s: %s", execution_id, exc)
            return None
        if row is None:
            return None
        return self._row_to_execution(row)

    def list_checkpoints(
        self, workflow_id: str | None = None, resumable_only: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        """列出checkpoint行（dict形态，供API/monitoring直接消费）。

        resumable_only=True只返回可续跑执行（WAITING/FAILED）。
        跨workflow扫描仅返回本store内的行——store本身按部署隔离（Langflow跨用户
        扫描风险在本系统对应"多租户"，当前单租户本地部署，差异如实注明不假装）。
        """
        try:
            with self._connect() as conn:
                if workflow_id:
                    rows = conn.execute(
                        "SELECT * FROM will_checkpoints WHERE workflow_id = ? "
                        "ORDER BY started_at DESC LIMIT ?",
                        (workflow_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM will_checkpoints ORDER BY started_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        except Exception as exc:
            logger.warning("Checkpoint list failed: %s", exc)
            return []
        out = []
        for row in rows:
            item = self._row_to_dict(row)
            if resumable_only and item["status"] not in {s.value for s in RESUMABLE_STATES}:
                continue
            out.append(item)
        return out

    def delete(self, execution_id: str) -> bool:
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM will_checkpoints WHERE execution_id = ?", (execution_id,)
                )
                return cur.rowcount > 0
        except Exception as exc:
            logger.warning("Checkpoint delete failed for %s: %s", execution_id, exc)
            return False

    def prune(self, keep: int = 200) -> int:
        """保留最近keep条终态（success/cancelled）checkpoint，更旧的删除。

        可续跑/进行中状态豁免（agno："paused tickets are retention-exempt—
        must outlive arbitrary human latency"）。返回删除行数。
        """
        exempt = tuple(s.value for s in _PRUNE_EXEMPT)
        placeholders = ",".join("?" for _ in exempt)
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    f"""
                    DELETE FROM will_checkpoints WHERE execution_id IN (
                        SELECT execution_id FROM will_checkpoints
                        WHERE status NOT IN ({placeholders})
                        ORDER BY started_at DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (*exempt, keep),
                )
                return cur.rowcount
        except Exception as exc:
            logger.warning("Checkpoint prune failed: %s", exc)
            return 0

    def stats(self) -> dict[str, Any]:
        """checkpoint统计（monitoring可观测：有多少可续跑/被打断的执行）。"""
        try:
            with self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) AS c FROM will_checkpoints").fetchone()["c"]
                by_status_rows = conn.execute(
                    "SELECT status, COUNT(*) AS c FROM will_checkpoints GROUP BY status"
                ).fetchall()
        except Exception as exc:
            logger.warning("Checkpoint stats failed: %s", exc)
            return {"total": 0, "by_status": {}, "resumable": 0}
        by_status = {r["status"]: r["c"] for r in by_status_rows}
        resumable = sum(by_status.get(s.value, 0) for s in RESUMABLE_STATES)
        return {"total": total, "by_status": by_status, "resumable": resumable}

    # ── Deserialization helpers ─────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "execution_id": row["execution_id"],
            "workflow_id": row["workflow_id"],
            "workflow_name": row["workflow_name"],
            "status": row["status"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "error": row["error"],
            "trigger_type": row["trigger_type"],
            "resume_count": row["resume_count"],
            "steps_count": len(json.loads(row["steps"] or "[]")),
        }

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> WorkflowExecution | None:
        try:
            steps_raw = json.loads(row["steps"] or "[]")
            variables = json.loads(row["variables"] or "{}")
            steps = [StepExecution(**s) for s in steps_raw]
            status_raw = row["status"]
            try:
                status = ExecutionStatus(status_raw)
            except ValueError:
                # 未知状态值fail-closed降级为WAITING（可续跑+人工可见），不静默当成功
                logger.warning(
                    "Unknown checkpoint status %r for %s — treating as waiting",
                    status_raw,
                    row["execution_id"],
                )
                status = ExecutionStatus.WAITING
            return WorkflowExecution(
                id=row["execution_id"],
                workflow_id=row["workflow_id"],
                workflow_name=row["workflow_name"] or "",
                status=status,
                started_at=row["started_at"] or datetime.now(UTC).isoformat(),
                completed_at=row["completed_at"],
                steps=steps,
                variables=variables,
                error=row["error"],
                trigger_type=row["trigger_type"] or "manual",
                resume_count=int(row["resume_count"] or 0),
            )
        except Exception as exc:
            logger.warning("Checkpoint row deserialize failed for %s: %s", row["execution_id"], exc)
            return None
