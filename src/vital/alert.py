"""Alert manager — 告警规则、通知与历史记录。"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from src.config import settings

from .collector import MetricsCollector, MetricsSnapshot

logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    name: str
    metric: str  # dot-path, e.g. "system.cpu_percent"
    threshold: float
    op: str = ">"  # ">" or "<"
    severity: str = "warning"
    message_tpl: str = ""


@dataclass
class AlertRecord:
    rule_name: str
    severity: str
    message: str
    value: float
    threshold: float
    ts: float = field(default_factory=time.time)
    resolved: bool = False


DEFAULT_RULES: list[AlertRule] = [
    AlertRule(
        name="high_cpu",
        metric="system.cpu_percent",
        threshold=80.0,
        message_tpl="CPU 使用率 {value:.1f}% 超过阈值 {threshold}%",
    ),
    AlertRule(
        name="high_memory",
        metric="system.memory_percent",
        threshold=90.0,
        message_tpl="内存使用率 {value:.1f}% 超过阈值 {threshold}%",
    ),
    AlertRule(
        name="high_disk",
        metric="system.disk_percent",
        threshold=95.0,
        message_tpl="磁盘使用率 {value:.1f}% 超过阈值 {threshold}%",
    ),
    AlertRule(
        name="high_error_rate",
        metric="app.error_rate",
        threshold=0.05,
        message_tpl="错误率 {value:.2%} 超过阈值 {threshold:.0%}",
    ),
]


def _resolve_path(snap: MetricsSnapshot, dotpath: str) -> float:
    obj = snap
    for part in dotpath.split("."):
        obj = getattr(obj, part)
    return float(obj)


class AlertManager:
    """评估告警规则并发送通知。"""

    def __init__(self, collector: MetricsCollector, rules: list[AlertRule] | None = None) -> None:
        self._collector = collector
        self._rules = rules or list(DEFAULT_RULES)
        self._history: list[AlertRecord] = []
        self._firing: dict[str, AlertRecord] = {}  # rule_name -> active alert
        self._task: asyncio.Task | None = None

    @property
    def history(self) -> list[AlertRecord]:
        return list(self._history)

    async def start(self, interval: float = 30.0) -> None:
        self._task = asyncio.create_task(self._loop(interval))
        logger.info("AlertManager started (interval=%.1fs)", interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    async def _loop(self, interval: float) -> None:
        while True:
            try:
                await self.evaluate()
            except Exception:
                logger.exception("Alert evaluation failed")
            await asyncio.sleep(interval)

    async def evaluate(self) -> list[AlertRecord]:
        snap = self._collector.snapshot
        new_alerts: list[AlertRecord] = []

        for rule in self._rules:
            try:
                value = _resolve_path(snap, rule.metric)
            except (AttributeError, TypeError):
                continue

            triggered = value > rule.threshold if rule.op == ">" else value < rule.threshold

            if triggered and rule.name not in self._firing:
                record = AlertRecord(
                    rule_name=rule.name,
                    severity=rule.severity,
                    message=rule.message_tpl.format(value=value, threshold=rule.threshold),
                    value=value,
                    threshold=rule.threshold,
                )
                self._firing[rule.name] = record
                self._history.append(record)
                new_alerts.append(record)
                logger.warning("ALERT [%s] %s", rule.severity, record.message)
                await self._notify(record)

            elif not triggered and rule.name in self._firing:
                old = self._firing.pop(rule.name)
                old.resolved = True
                logger.info("RESOLVED %s", rule.name)

        return new_alerts

    async def _notify(self, record: AlertRecord) -> None:
        # Push to Notification Center
        try:
            from src.api.notifications import push_notification

            emoji = (
                "🔴"
                if record.severity == "critical"
                else "🟡"
                if record.severity == "warning"
                else "🔵"
            )
            push_notification(
                source="vital",
                title=f"{emoji} 系统告警: {record.rule_name}",
                body=record.message,
                level="error" if record.severity == "critical" else "warning",
                organ="vital",
                emoji=emoji,
                action_url="/vital",
                metadata={
                    "rule": record.rule_name,
                    "value": record.value,
                    "threshold": record.threshold,
                },
            )
        except (ImportError, AttributeError):
            pass

        if settings.alert_webhook_url:
            await self._send_webhook(record)

    async def _send_webhook(self, record: AlertRecord) -> None:
        payload = {
            "rule": record.rule_name,
            "severity": record.severity,
            "message": record.message,
            "value": record.value,
            "threshold": record.threshold,
            "ts": record.ts,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(settings.alert_webhook_url, json=payload)
                resp.raise_for_status()
        except Exception:
            logger.exception("Webhook notification failed for %s", record.rule_name)
