"""P0-8: 后台作业队列 — 参照agno job_queue模式

功能：
- 提交后台作业（异步执行，立即返回job_id）
- 查询作业状态（pending/running/completed/failed）
- 获取作业结果
- 作业列表（按状态过滤）
- 超时控制 + 失败重试

参照: agno job_queue/store.py 300行 + langfuse BullMQ + kilocode BackgroundJob
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
                    max_retries INTEGER DEFAULT 2
                )
            """)

    def register_handler(self, name: str, handler: Callable[..., Awaitable[Any]]):
        """注册作业处理器"""
        self._handlers[name] = handler

    async def start(self):
        """启动worker池"""
        if self._running:
            return
        self._running = True
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(task)
        logger.info(f"JobQueue started: {self._max_workers} workers")

    async def stop(self):
        """停止worker池"""
        self._running = False
        for task in self._workers:
            task.cancel()
        self._workers.clear()
        logger.info("JobQueue stopped")

    async def submit(self, name: str, params: dict | None = None,
                     timeout_s: int = 300, max_retries: int = 2) -> str:
        """提交后台作业，立即返回job_id"""
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job = Job(
            id=job_id,
            name=name,
            params=params or {},
            timeout_s=timeout_s,
            max_retries=max_retries,
        )
        self._jobs[job_id] = job
        self._persist(job)
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
                return dict(row)
        return None

    def list_jobs(self, status: str = "", limit: int = 50) -> list[dict]:
        """列出作业"""
        jobs = []
        for job in sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True):
            if status and str(job.status) != status:
                continue
            jobs.append(job.to_dict())
            if len(jobs) >= limit:
                break
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
        """队列统计"""
        counts = {}
        for job in self._jobs.values():
            s = str(job.status)
            counts[s] = counts.get(s, 0) + 1
        return {
            "total": len(self._jobs),
            "by_status": counts,
            "queue_size": self._queue.qsize(),
            "workers": len(self._workers),
            "handlers": list(self._handlers.keys()),
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
                    await self._queue.put(job_id)
                    logger.warning(f"Job {job_id} retry {job.retries}/{job.max_retries}")
                    continue
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)[:500]
                if job.retries < job.max_retries:
                    job.retries += 1
                    job.status = JobStatus.PENDING
                    await self._queue.put(job_id)
                    logger.warning(f"Job {job_id} retry {job.retries}/{job.max_retries}: {e}")
                    continue

            job.finished_at = time.time()
            self._persist(job)
            logger.info(f"[{worker_name}] Job {job_id} ({job.name}): {job.status} in {job.duration_s}s")

    def _persist(self, job: Job):
        """持久化作业状态到SQLite"""
        try:
            with self._conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO jobs 
                    (id, name, status, params, result, error, created_at, started_at, finished_at, timeout_s, retries, max_retries)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job.id, job.name, str(job.status),
                    json.dumps(job.params, ensure_ascii=False),
                    json.dumps(job.result, ensure_ascii=False, default=str) if job.result is not None else None,
                    job.error, job.created_at, job.started_at, job.finished_at,
                    job.timeout_s, job.retries, job.max_retries,
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
