"""P0-8: 后台作业队列 — 参照agno job_queue模式

功能：
- 提交后台作业（异步执行，立即返回job_id）
- 查询作业状态（pending/running/completed/failed）
- 获取作业结果
- 作业列表（按状态过滤）
- 超时控制 + 失败重试

参照: agno job_queue/store.py 300行 + langfuse BullMQ + kilocode BackgroundJob

运行时接线（2026-09-19 cron轮，"写了≠接线了"修复）：
- handler注册表：src/will/job_handlers.py（api/will.py模块加载时注册）
- worker池懒启动：首次submit时start()（agno "executor上线才claim任务"）
- idempotency_key去重：同key的pending/running作业不重复创建（agno §5.2幂等键）
- stale回收：进程重启后DB里的pending/running作业由新进程重排队
  （agno原版靠heartbeat lease回收；本部署单进程，重启即全部回收，
    差异在此注明，不假装有分布式租约）
- list_jobs/get/get_stats以SQLite为真源：重启后monitoring面板仍可见历史
"""
import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import logging
logger = logging.getLogger("opensoul.job_queue")


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    name: str
    status: JobStatus = JobStatus.PENDING
    params: dict = field(default_factory=dict)
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0
    finished_at: float = 0
    timeout_s: int = 300
    retries: int = 0
    max_retries: int = 2

    @property
    def duration_s(self) -> float:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 2)
        return 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": str(self.status),
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "retries": self.retries,
            "timeout_s": self.timeout_s,
        }


class JobQueue:
    """后台作业队列 — SQLite持久化 + asyncio worker池"""

    def __init__(self, db_path: str = "", max_workers: int = 3):
        self._db_path = db_path or str(Path.home() / "opensoul" / "data" / "job_queue.db")
        self._jobs: dict[str, Job] = {}
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._max_workers = max_workers
        self._running = False
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    params TEXT DEFAULT '{}',
                    result TEXT,
                    error TEXT DEFAULT '',
                    created_at REAL,
                    started_at REAL DEFAULT 0,
                    finished_at REAL DEFAULT 0,
                    timeout_s INTEGER DEFAULT 300,
                    retries INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 2,
                    idempotency_key TEXT DEFAULT ''
                )
            """)
            # 幂等键列迁移（既有库补列；重复列错误按消息匹配安全吞掉）
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT DEFAULT ''")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_idem ON jobs(idempotency_key, status)"
            )

    def register_handler(self, name: str, handler: Callable[..., Awaitable[Any]]):
        """注册作业处理器"""
        self._handlers[name] = handler

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[str]:
        """按幂等键查pending/running作业id（API层duplicate回执用，agno §5.2）"""
        if not idempotency_key:
            return None
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT id FROM jobs WHERE idempotency_key = ? AND status IN ('pending','running') LIMIT 1",
                    (idempotency_key,),
                ).fetchone()
            return row["id"] if row else None
        except Exception as e:
            logger.error(f"find_by_idempotency_key failed: {e}")
            return None

    async def start(self):
        """启动worker池"""
        if self._running:
            return
        self._running = True
        # stale回收：上一个进程遗留的pending/running作业重新排队
        await self._recover_from_db()
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(task)
        logger.info(f"JobQueue started: {self._max_workers} workers")

    async def _recover_from_db(self):
        """重启恢复：DB为真源——pending重新排队，running视为死进程遗留（agno stale回收简化版）。

        同时把DB历史行载入内存索引，get()/worker取作业在重启后不再失明。
        """
        try:
            with self._conn() as conn:
                rows = conn.execute("SELECT * FROM jobs").fetchall()
        except Exception as e:
            logger.error(f"job recovery read failed: {e}")
            return
        recovered = 0
        for row in rows:
            job_id = row["id"]
            if job_id in self._jobs:
                continue
            job = self._row_to_job(row)
            self._jobs[job_id] = job
            if job.status == JobStatus.RUNNING:
                # 死进程遗留：回pending重跑（retries保留，不静默重置计数）
                job.status = JobStatus.PENDING
                job.error = "stale recovery: previous process exited mid-run"
                self._persist(job)
            if job.status == JobStatus.PENDING:
                await self._queue.put(job_id)
                recovered += 1
        if recovered:
            logger.info(f"JobQueue recovery: re-queued {recovered} stale jobs")

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        """DB行 → Job对象（params/result宽容反序列化，坏JSON不炸恢复流程）"""
        try:
            params = json.loads(row["params"] or "{}")
        except Exception:
            params = {}
        result: Any = row["result"]
        if result:
            try:
                result = json.loads(result)
            except Exception:
                pass  # 保留原文，绝不因坏JSON丢结果
        try:
            status = JobStatus(str(row["status"]))
        except Exception:
            status = JobStatus.FAILED
        return Job(
            id=row["id"],
            name=row["name"],
            status=status,
            params=params if isinstance(params, dict) else {},
            result=result,
            error=row["error"] or "",
            created_at=row["created_at"] or 0,
            started_at=row["started_at"] or 0,
            finished_at=row["finished_at"] or 0,
            timeout_s=row["timeout_s"] or 300,
            retries=row["retries"] or 0,
            max_retries=row["max_retries"] if row["max_retries"] is not None else 2,
        )

    async def stop(self):
        """停止worker池"""
        self._running = False
        for task in self._workers:
            task.cancel()
        self._workers.clear()
        logger.info("JobQueue stopped")

    async def submit(self, name: str, params: dict | None = None,
                     timeout_s: int = 300, max_retries: int = 2,
                     idempotency_key: str = "") -> str:
        """提交后台作业，立即返回job_id。

        idempotency_key（agno §5.2）：同key已有pending/running作业时返回既有job_id，
        不静默重复执行；已完成/失败的同key作业不拦新提交（重试是合法意图）。
        """
        if idempotency_key:
            with self._conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE idempotency_key = ? AND status IN ('pending','running') LIMIT 1",
                    (idempotency_key,),
                ).fetchone()
            if existing:
                logger.info(f"Job submit deduped by idempotency_key={idempotency_key} -> {existing['id']}")
                return existing["id"]
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job = Job(
            id=job_id,
            name=name,
            params=params or {},
            timeout_s=timeout_s,
            max_retries=max_retries,
        )
        self._jobs[job_id] = job
        self._persist(job, idempotency_key=idempotency_key)
        await self._queue.put(job_id)
        logger.info(f"Job submitted: {job_id} ({name})")
        return job_id

    def get(self, job_id: str) -> Optional[dict]:
        """查询作业状态"""
        job = self._jobs.get(job_id)
        if job:
            return job.to_dict()
        # Try loading from DB
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                job = self._row_to_job(row)
                self._jobs[job_id] = job
                return job.to_dict()
        return None

    def list_jobs(self, status: str = "", limit: int = 50) -> list[dict]:
        """列出作业（SQLite为真源——重启后历史仍可见，monitoring面板依赖）"""
        query = "SELECT * FROM jobs"
        args: list[Any] = []
        if status:
            query += " WHERE status = ?"
            args.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        try:
            with self._conn() as conn:
                rows = conn.execute(query, args).fetchall()
        except Exception as e:
            logger.error(f"list_jobs db read failed: {e}")
            return []
        jobs = []
        for row in rows:
            job = self._jobs.get(row["id"])
            jobs.append(job.to_dict() if job else self._row_to_job(row).to_dict())
        return jobs

    def cancel(self, job_id: str) -> bool:
        """取消作业"""
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.PENDING:
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            self._persist(job)
            return True
        return False

    def get_stats(self) -> dict:
        """队列统计（SQLite为真源 + 进程内实时字段）"""
        counts: dict[str, int] = {}
        total = 0
        try:
            with self._conn() as conn:
                for row in conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"):
                    counts[str(row["status"])] = int(row["c"])
                    total += int(row["c"])
        except Exception as e:
            logger.error(f"get_stats db read failed: {e}")
        return {
            "total": total,
            "by_status": counts,
            "queue_size": self._queue.qsize(),
            "workers": len(self._workers),
            "handlers": sorted(self._handlers.keys()),
            "running": self._running,
        }

    async def _worker_loop(self, worker_name: str):
        """Worker循环：从队列取作业并执行"""
        while self._running:
            try:
                job_id = await asyncio.wait_for(self._queue.get(), timeout=5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            job = self._jobs.get(job_id)
            if not job or job.status == JobStatus.CANCELLED:
                continue

            handler = self._handlers.get(job.name)
            if not handler:
                job.status = JobStatus.FAILED
                job.error = f"No handler for job type '{job.name}'"
                job.finished_at = time.time()
                self._persist(job)
                continue

            # Execute
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            self._persist(job)

            try:
                result = await asyncio.wait_for(handler(**job.params), timeout=job.timeout_s)
                job.status = JobStatus.COMPLETED
                job.result = result
            except asyncio.TimeoutError:
                job.status = JobStatus.TIMEOUT
                job.error = f"Job timed out after {job.timeout_s}s"
                if job.retries < job.max_retries:
                    job.retries += 1
                    job.status = JobStatus.PENDING
                    self._persist(job)
                    await self._queue.put(job_id)
                    logger.warning(f"Job {job_id} retry {job.retries}/{job.max_retries}")
                    continue
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)[:500]
                if job.retries < job.max_retries:
                    job.retries += 1
                    job.status = JobStatus.PENDING
                    self._persist(job)
                    await self._queue.put(job_id)
                    logger.warning(f"Job {job_id} retry {job.retries}/{job.max_retries}: {e}")
                    continue

            job.finished_at = time.time()
            self._persist(job)
            logger.info(f"[{worker_name}] Job {job_id} ({job.name}): {job.status} in {job.duration_s}s")

    def _persist(self, job: Job, idempotency_key: str = ""):
        """持久化作业状态到SQLite"""
        try:
            with self._conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO jobs 
                    (id, name, status, params, result, error, created_at, started_at, finished_at, timeout_s, retries, max_retries, idempotency_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, (SELECT idempotency_key FROM jobs WHERE id = ?), ''))
                """, (
                    job.id, job.name, str(job.status),
                    json.dumps(job.params, ensure_ascii=False),
                    json.dumps(job.result, ensure_ascii=False, default=str) if job.result is not None else None,
                    job.error, job.created_at, job.started_at, job.finished_at,
                    job.timeout_s, job.retries, job.max_retries,
                    idempotency_key or None, job.id,
                ))
        except Exception as e:
            logger.error(f"Failed to persist job {job.id}: {e}")


# ── 全局单例 ──
_job_queue: Optional[JobQueue] = None


def get_job_queue() -> JobQueue:
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue()
    return _job_queue
