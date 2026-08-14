"""Metrics collector — 系统 / 应用 / 业务指标采集。"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

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
    """定时采集系统 / 应用 / 业务指标。"""

    def __init__(self, interval: float = 10.0) -> None:
        self._interval = interval
        self._snapshot = MetricsSnapshot()
        self._task: asyncio.Task | None = None

        # 应用指标滑动窗口
        self._request_count = 0
        self._error_count = 0
        self._latencies: list[float] = []
        self._window_start = time.monotonic()

    @property
    def snapshot(self) -> MetricsSnapshot:
        return self._snapshot

    def record_request(self, latency_ms: float, is_error: bool = False) -> None:
        """由中间件调用，记录每次请求。"""
        self._request_count += 1
        self._latencies.append(latency_ms)
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
            except Exception:
                logger.exception("Failed to collect metrics")
            await asyncio.sleep(self._interval)

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
        snap.system.disk_used_gb = disk.used / (1024 ** 3)
        snap.system.disk_total_gb = disk.total / (1024 ** 3)
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
        except Exception:
            pass

        try:
            from src.database.meilisearch import meili_client

            if meili_client.AVAILABLE and meili_client.client is not None:
                stats = meili_client.get_stats()
                biz.search_count = stats.get("numberOfSearches", 0)
        except Exception:
            pass

        return biz
