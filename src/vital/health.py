"""Health checker — 各依赖服务的连通性检查。"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from src.config import settings

logger = logging.getLogger(__name__)


class Status(str, Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


@dataclass
class ComponentHealth:
    name: str
    status: Status
    latency_ms: float = 0.0
    message: str = ""


@dataclass
class HealthReport:
    status: Status = Status.UP
    components: list[ComponentHealth] = field(default_factory=list)
    ts: float = field(default_factory=time.time)


class HealthChecker:
    """检查 PostgreSQL / Qdrant / Meilisearch / NATS 的连通性。"""

    async def check(self) -> HealthReport:
        results = await asyncio.gather(
            self._check_postgres(),
            self._check_qdrant(),
            self._check_meilisearch(),
            self._check_nats(),
            return_exceptions=False,
        )

        report = HealthReport(components=list(results))

        statuses = [c.status for c in report.components if c.status != Status.SKIPPED]
        if any(s == Status.DOWN for s in statuses):
            report.status = Status.DOWN
        elif any(s == Status.DEGRADED for s in statuses):
            report.status = Status.DEGRADED
        else:
            report.status = Status.UP

        return report

    async def _check_postgres(self) -> ComponentHealth:
        try:
            from src.database.postgres import db_pool

            t0 = time.monotonic()
            await db_pool.fetchval("SELECT 1")
            latency = (time.monotonic() - t0) * 1000
            return ComponentHealth("postgres", Status.UP, latency_ms=latency)
        except Exception as e:
            return ComponentHealth("postgres", Status.DOWN, message=str(e))

    async def _check_qdrant(self) -> ComponentHealth:
        try:
            from src.database.qdrant import qdrant_client

            if not qdrant_client.AVAILABLE:
                return ComponentHealth("qdrant", Status.SKIPPED, message="qdrant_client not installed")
            if qdrant_client.client is None:
                return ComponentHealth("qdrant", Status.SKIPPED, message="qdrant client unavailable")

            t0 = time.monotonic()
            qdrant_client.client.get_collections()
            latency = (time.monotonic() - t0) * 1000
            return ComponentHealth("qdrant", Status.UP, latency_ms=latency)
        except Exception as e:
            return ComponentHealth("qdrant", Status.DOWN, message=str(e))

    async def _check_meilisearch(self) -> ComponentHealth:
        try:
            from src.database.meilisearch import meili_client

            if not meili_client.AVAILABLE:
                return ComponentHealth("meilisearch", Status.SKIPPED, message="meilisearch not installed")
            if meili_client.client is None:
                return ComponentHealth("meilisearch", Status.SKIPPED, message="meilisearch client unavailable")

            t0 = time.monotonic()
            meili_client.client.health()
            latency = (time.monotonic() - t0) * 1000
            return ComponentHealth("meilisearch", Status.UP, latency_ms=latency)
        except Exception as e:
            return ComponentHealth("meilisearch", Status.DOWN, message=str(e))

    async def _check_nats(self) -> ComponentHealth:
        if not settings.nats_url:
            return ComponentHealth("nats", Status.SKIPPED, message="NATS not configured")
        try:
            import nats

            t0 = time.monotonic()
            nc = await nats.connect(settings.nats_url, connect_timeout=3)
            await nc.drain()
            latency = (time.monotonic() - t0) * 1000
            return ComponentHealth("nats", Status.UP, latency_ms=latency)
        except Exception as e:
            return ComponentHealth("nats", Status.DOWN, message=str(e))
