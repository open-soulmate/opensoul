"""Metrics collector — 系统 / 应用 / 业务指标采集。

Features:
- Periodic system/app/biz metric collection (default 10s)
- Ring-buffer history (default 720 entries ≈ 2 hours @ 10s interval)
- Time-series query: GET /api/vital/history?minutes=60
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import psutil

logger = logging.getLogger(__name__)


@dataclass
class SystemMetrics:
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    net_sent_bytes: int = 0
    net_recv_bytes: int = 0


@dataclass
class AppMetrics:
    request_qps: float = 0.0
    latency_p99_ms: float = 0.0
    error_rate: float = 0.0
    total_requests: int = 0
    total_errors: int = 0


@dataclass
class BizMetrics:
    knowledge_entries: int = 0
    agents_online: int = 0
    search_count: int = 0


@dataclass
class MetricsSnapshot:
    ts: float = field(default_factory=time.time)
    system: SystemMetrics = field(default_factory=SystemMetrics)
    app: AppMetrics = field(default_factory=AppMetrics)
    biz: BizMetrics = field(default_factory=BizMetrics)


class MetricsCollector:
    """定时采集系统 / 应用 / 业务指标，带历史环形缓冲。"""

    def __init__(self, interval: float = 10.0, history_max: int = 720) -> None:
        self._interval = interval
        self._snapshot = MetricsSnapshot()
        self._task: asyncio.Task | None = None

        # 应用指标滑动窗口
        self._request_count = 0
        self._error_count = 0
        self._latencies: list[float] = []
        self._window_start = time.monotonic()

        # Ring-buffer history (default 720 entries ≈ 2 hours @ 10s)
        self._history: deque[dict[str, Any]] = deque(maxlen=history_max)

    @property
    def snapshot(self) -> MetricsSnapshot:
        return self._snapshot

    def record_request(self, latency_ms: float, is_error: bool = False) -> None:
        """由中间件调用，记录每次请求。"""
        self._request_count += 1
        self._latencies.append(latency_ms)
        # 限制列表大小，防止内存泄漏（保留最近10000条）
        if len(self._latencies) > 10000:
            self._latencies = self._latencies[-5000:]
        if is_error:
            self._error_count += 1

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("MetricsCollector started (interval=%.1fs)", self._interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                self._snapshot = await self._collect()
                self._record_history()
            except Exception:
                logger.exception("Failed to collect metrics")
            await asyncio.sleep(self._interval)

    def _record_history(self) -> None:
        """Append current snapshot to the ring buffer."""
        snap = self._snapshot
        entry = {
            "ts": snap.ts,
            "cpu": round(snap.system.cpu_percent, 1),
            "mem": round(snap.system.memory_percent, 1),
            "mem_mb": round(snap.system.memory_used_mb, 0),
            "disk": round(snap.system.disk_percent, 1),
            "qps": round(snap.app.request_qps, 2),
            "p99": round(snap.app.latency_p99_ms, 1),
            "err_rate": round(snap.app.error_rate, 4),
            "requests": snap.app.total_requests,
            "errors": snap.app.total_errors,
            "knowledge": snap.biz.knowledge_entries,
        }
        self._history.append(entry)

    def get_history(self, minutes: int = 60) -> list[dict[str, Any]]:
        """Return history entries within the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        return [e for e in self._history if e["ts"] >= cutoff]

    def get_history_summary(self) -> dict[str, Any]:
        """Return aggregated summary of recent history."""
        if not self._history:
            return {"entries": 0}
        recent = list(self._history)
        cpus = [e["cpu"] for e in recent]
        mems = [e["mem"] for e in recent]
        qps_list = [e["qps"] for e in recent]
        return {
            "entries": len(recent),
            "span_minutes": round((recent[-1]["ts"] - recent[0]["ts"]) / 60, 1)
            if len(recent) > 1
            else 0,
            "cpu": {"min": min(cpus), "max": max(cpus), "avg": round(sum(cpus) / len(cpus), 1)},
            "memory": {"min": min(mems), "max": max(mems), "avg": round(sum(mems) / len(mems), 1)},
            "qps": {
                "min": min(qps_list),
                "max": max(qps_list),
                "avg": round(sum(qps_list) / len(qps_list), 2),
            },
        }

    async def _collect(self) -> MetricsSnapshot:
        snap = MetricsSnapshot()

        # ── System ───────────────────────────────────────
        snap.system.cpu_percent = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        snap.system.memory_percent = mem.percent
        snap.system.memory_used_mb = mem.used / (1024 * 1024)
        snap.system.memory_total_mb = mem.total / (1024 * 1024)
        disk = psutil.disk_usage("/")
        snap.system.disk_percent = disk.percent
        snap.system.disk_used_gb = disk.used / (1024**3)
        snap.system.disk_total_gb = disk.total / (1024**3)
        net = psutil.net_io_counters()
        snap.system.net_sent_bytes = net.bytes_sent
        snap.system.net_recv_bytes = net.bytes_recv

        # ── App ──────────────────────────────────────────
        elapsed = time.monotonic() - self._window_start
        if elapsed > 0:
            snap.app.request_qps = self._request_count / elapsed
            snap.app.error_rate = (
                self._error_count / self._request_count if self._request_count else 0.0
            )
        snap.app.total_requests = self._request_count
        snap.app.total_errors = self._error_count
        if self._latencies:
            sorted_lat = sorted(self._latencies)
            idx = int(len(sorted_lat) * 0.99)
            snap.app.latency_p99_ms = sorted_lat[min(idx, len(sorted_lat) - 1)]

        # ── Biz (从数据库拉取) ─────────────────────────
        try:
            snap.biz = await self._collect_biz()
        except Exception:
            logger.debug("Biz metrics collection skipped (services unavailable)")

        return snap

    async def _collect_biz(self) -> BizMetrics:
        biz = BizMetrics()
        try:
            from src.database.postgres import db_pool

            biz.knowledge_entries = await db_pool.fetchval("SELECT count(*) FROM knowledge")
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        try:
            from src.database.meilisearch import meili_client

            if meili_client.AVAILABLE and meili_client.client is not None:
                stats = meili_client.get_stats()
                biz.search_count = stats.get("numberOfSearches", 0)
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        return biz
