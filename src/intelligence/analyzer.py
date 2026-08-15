"""System Intelligence — cross-component analytics, anomaly detection, and optimization insights."""

from __future__ import annotations

import time
import threading
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum


class InsightType(str, Enum):
    ANOMALY = "anomaly"
    OPTIMIZATION = "optimization"
    TREND = "trend"
    WARNING = "warning"
    INFO = "info"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Insight:
    insight_id: str
    insight_type: InsightType
    severity: Severity
    component: str
    title: str
    description: str
    recommendation: str
    timestamp: float
    metadata: dict = field(default_factory=dict)


@dataclass
class ComponentMetrics:
    name: str
    health: str = "unknown"
    response_time_ms: float = 0.0
    request_count: int = 0
    error_count: int = 0
    last_check: float = 0.0
    custom: dict = field(default_factory=dict)


@dataclass
class TrendPoint:
    timestamp: float
    value: float


class SystemIntelligence:
    """Aggregates metrics from all components and generates intelligent insights."""

    def __init__(self, history_size: int = 1000):
        self._history_size = history_size
        self._metrics_history: dict[str, deque[TrendPoint]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )
        self._insights: deque[Insight] = deque(maxlen=500)
        self._component_metrics: dict[str, ComponentMetrics] = {}
        self._anomaly_baselines: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._insight_counter = 0

    def record_metrics(self, component: str, metrics: dict) -> None:
        """Record metrics from a component."""
        with self._lock:
            now = time.time()
            cm = self._component_metrics.get(component, ComponentMetrics(name=component))
            cm.health = metrics.get("health", cm.health)
            cm.response_time_ms = metrics.get("response_time_ms", cm.response_time_ms)
            cm.request_count = metrics.get("request_count", cm.request_count)
            cm.error_count = metrics.get("error_count", cm.error_count)
            cm.last_check = now
            cm.custom = metrics.get("custom", cm.custom)
            self._component_metrics[component] = cm

            # Record response time trend
            if cm.response_time_ms > 0:
                self._metrics_history[f"{component}:response_time"].append(
                    TrendPoint(timestamp=now, value=cm.response_time_ms)
                )

            # Run anomaly detection
            self._detect_anomalies(component, cm)

    def _detect_anomalies(self, component: str, metrics: ComponentMetrics) -> None:
        """Detect anomalies in component metrics."""
        # Check response time anomalies
        key = f"{component}:response_time"
        history = self._metrics_history[key]
        if len(history) >= 10:
            values = [p.value for p in list(history)[-20:]]
            mean = statistics.mean(values)
            stdev = statistics.stdev(values) if len(values) > 1 else 0

            # Z-score anomaly detection (threshold: 3)
            if stdev > 0 and metrics.response_time_ms > mean + 3 * stdev:
                self._add_insight(
                    insight_type=InsightType.ANOMALY,
                    severity=Severity.HIGH,
                    component=component,
                    title=f"Response time anomaly in {component}",
                    description=f"Response time {metrics.response_time_ms:.0f}ms is {((metrics.response_time_ms - mean) / stdev):.1f} standard deviations above mean ({mean:.0f}ms)",
                    recommendation=f"Investigate {component} for performance issues. Consider scaling or optimizing.",
                )

        # Check error rate
        if metrics.request_count > 0:
            error_rate = metrics.error_count / metrics.request_count
            if error_rate > 0.1:
                self._add_insight(
                    insight_type=InsightType.WARNING,
                    severity=Severity.HIGH if error_rate > 0.3 else Severity.MEDIUM,
                    component=component,
                    title=f"High error rate in {component}",
                    description=f"Error rate: {error_rate:.1%} ({metrics.error_count}/{metrics.request_count} requests)",
                    recommendation=f"Check {component} logs for recurring errors. Consider circuit breaker pattern.",
                )

        # Check health degradation
        if metrics.health == "error":
            self._add_insight(
                insight_type=InsightType.ANOMALY,
                severity=Severity.CRITICAL,
                component=component,
                title=f"Component {component} is unhealthy",
                description=f"Health check returned error status",
                recommendation=f"Restart {component} or check dependencies.",
            )

    def _add_insight(self, **kwargs) -> None:
        """Add a new insight."""
        self._insight_counter += 1
        insight = Insight(
            insight_id=f"insight-{self._insight_counter}",
            timestamp=time.time(),
            **kwargs,
        )
        self._insights.append(insight)

    def get_insights(
        self,
        component: str | None = None,
        insight_type: InsightType | None = None,
        severity: Severity | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get insights with optional filters."""
        results = []
        for insight in reversed(self._insights):
            if component and insight.component != component:
                continue
            if insight_type and insight.insight_type != insight_type:
                continue
            if severity and insight.severity != severity:
                continue
            results.append({
                "id": insight.insight_id,
                "type": insight.insight_type.value,
                "severity": insight.severity.value,
                "component": insight.component,
                "title": insight.title,
                "description": insight.description,
                "recommendation": insight.recommendation,
                "timestamp": insight.timestamp,
                "metadata": insight.metadata,
            })
            if len(results) >= limit:
                break
        return results

    def get_trends(self, component: str, metric: str, duration_seconds: int = 3600) -> list[dict]:
        """Get trend data for a component metric."""
        key = f"{component}:{metric}"
        history = self._metrics_history.get(key, deque())
        cutoff = time.time() - duration_seconds
        return [
            {"timestamp": p.timestamp, "value": p.value}
            for p in history
            if p.timestamp >= cutoff
        ]

    def get_system_summary(self) -> dict:
        """Get overall system intelligence summary."""
        with self._lock:
            total = len(self._component_metrics)
            healthy = sum(1 for m in self._component_metrics.values() if m.health == "ok")
            unhealthy = sum(1 for m in self._component_metrics.values() if m.health == "error")
            unknown = total - healthy - unhealthy

            # Calculate average response time
            response_times = [m.response_time_ms for m in self._component_metrics.values() if m.response_time_ms > 0]
            avg_response = statistics.mean(response_times) if response_times else 0

            # Count recent insights by severity
            recent_cutoff = time.time() - 3600
            recent_insights = [i for i in self._insights if i.timestamp >= recent_cutoff]
            by_severity = defaultdict(int)
            for i in recent_insights:
                by_severity[i.severity.value] += 1

            # System health score (0-100)
            health_score = (healthy / total * 100) if total > 0 else 0
            # Penalize for critical/high insights
            health_score -= by_severity.get("critical", 0) * 10
            health_score -= by_severity.get("high", 0) * 5
            health_score = max(0, min(100, health_score))

            return {
                "health_score": round(health_score, 1),
                "components": {
                    "total": total,
                    "healthy": healthy,
                    "unhealthy": unhealthy,
                    "unknown": unknown,
                },
                "performance": {
                    "avg_response_ms": round(avg_response, 1),
                    "tracked_metrics": len(self._metrics_history),
                },
                "insights": {
                    "total": len(self._insights),
                    "recent_hour": len(recent_insights),
                    "by_severity": dict(by_severity),
                },
                "timestamp": time.time(),
            }

    def get_component_details(self) -> list[dict]:
        """Get detailed metrics for all components."""
        with self._lock:
            return [
                {
                    "name": m.name,
                    "health": m.health,
                    "response_time_ms": round(m.response_time_ms, 1),
                    "request_count": m.request_count,
                    "error_count": m.error_count,
                    "last_check": m.last_check,
                    "custom": m.custom,
                }
                for m in self._component_metrics.values()
            ]

    def generate_recommendations(self) -> list[dict]:
        """Generate optimization recommendations based on collected data."""
        recommendations = []

        with self._lock:
            for name, m in self._component_metrics.items():
                # Slow component recommendation
                if m.response_time_ms > 1000:
                    recommendations.append({
                        "component": name,
                        "type": "performance",
                        "priority": "high",
                        "title": f"{name} is responding slowly",
                        "description": f"Average response time: {m.response_time_ms:.0f}ms",
                        "suggestion": "Consider adding caching, optimizing queries, or scaling horizontally.",
                    })

                # High error rate recommendation
                if m.request_count > 10 and m.error_count / max(m.request_count, 1) > 0.05:
                    error_rate = m.error_count / m.request_count
                    recommendations.append({
                        "component": name,
                        "type": "reliability",
                        "priority": "medium",
                        "title": f"{name} has elevated error rate",
                        "description": f"Error rate: {error_rate:.1%}",
                        "suggestion": "Review error logs, add retry logic, or implement circuit breaker.",
                    })

                # Stale data recommendation
                if m.last_check > 0 and time.time() - m.last_check > 300:
                    recommendations.append({
                        "component": name,
                        "type": "monitoring",
                        "priority": "low",
                        "title": f"{name} metrics are stale",
                        "description": f"Last update: {int(time.time() - m.last_check)}s ago",
                        "suggestion": "Check if the component's metrics endpoint is responding.",
                    })

        return recommendations
