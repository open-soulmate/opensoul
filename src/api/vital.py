"""OpenVital API — 健康检查 / 指标 / 告警接口。"""

from fastapi import APIRouter, Request

router = APIRouter()


def _get_collector(request: Request):
    return request.app.state.vital_collector


def _get_checker(request: Request):
    return request.app.state.vital_checker


def _get_alert_mgr(request: Request):
    return request.app.state.vital_alert_mgr


# ── Health ────────────────────────────────────────────────


@router.get("/health")
async def health(request: Request):
    checker = _get_checker(request)
    report = await checker.check()
    return {
        "status": report.status.value,
        "components": [
            {
                "name": c.name,
                "status": c.status.value,
                "latency_ms": round(c.latency_ms, 2),
                "message": c.message,
            }
            for c in report.components
        ],
        "ts": report.ts,
    }


# ── Metrics (Prometheus text format) ──────────────────────


@router.get("/metrics")
async def metrics(request: Request):
    collector = _get_collector(request)
    snap = collector.snapshot

    lines = [
        _gauge("vital_cpu_percent", snap.system.cpu_percent),
        _gauge("vital_memory_percent", snap.system.memory_percent),
        _gauge("vital_memory_used_mb", snap.system.memory_used_mb),
        _gauge("vital_memory_total_mb", snap.system.memory_total_mb),
        _gauge("vital_disk_percent", snap.system.disk_percent),
        _gauge("vital_disk_used_gb", snap.system.disk_used_gb),
        _gauge("vital_disk_total_gb", snap.system.disk_total_gb),
        _counter("vital_net_sent_bytes", snap.system.net_sent_bytes),
        _counter("vital_net_recv_bytes", snap.system.net_recv_bytes),
        _gauge("vital_request_qps", snap.app.request_qps),
        _gauge("vital_latency_p99_ms", snap.app.latency_p99_ms),
        _gauge("vital_error_rate", snap.app.error_rate),
        _counter("vital_requests_total", snap.app.total_requests),
        _counter("vital_errors_total", snap.app.total_errors),
        _gauge("vital_knowledge_entries", snap.biz.knowledge_entries),
        _gauge("vital_agents_online", snap.biz.agents_online),
        _counter("vital_search_count", snap.biz.search_count),
    ]

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


def _gauge(name: str, value: float) -> str:
    return f"{name} {value:.4f}"


def _counter(name: str, value: int | float) -> str:
    return f"{name} {value}"


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def vital_stats(request: Request):
    """OpenVital aggregated statistics."""
    collector = _get_collector(request)
    checker = _get_checker(request)
    alert_mgr = _get_alert_mgr(request)
    snap = collector.snapshot
    report = await checker.check()

    return {
        "status": "ok",
        "component": "OpenVital",
        "system": {
            "cpu_percent": snap.system.cpu_percent,
            "memory_percent": snap.system.memory_percent,
            "memory_used_mb": snap.system.memory_used_mb,
            "memory_total_mb": snap.system.memory_total_mb,
            "disk_percent": snap.system.disk_percent,
            "disk_used_gb": snap.system.disk_used_gb,
            "disk_total_gb": snap.system.disk_total_gb,
        },
        "app": {
            "request_qps": snap.app.request_qps,
            "latency_p99_ms": snap.app.latency_p99_ms,
            "error_rate": snap.app.error_rate,
            "total_requests": snap.app.total_requests,
            "total_errors": snap.app.total_errors,
        },
        "health": {
            "status": report.status.value,
            "component_count": len(report.components),
            "healthy": sum(1 for c in report.components if c.status.value == "ok"),
        },
        "alerts": {
            "total": len(alert_mgr.history),
            "active": sum(1 for a in alert_mgr.history if not a.resolved),
        },
    }


# ── Alerts ────────────────────────────────────────────────


@router.get("/alerts")
async def alerts(request: Request):
    alert_mgr = _get_alert_mgr(request)
    return {
        "alerts": [
            {
                "rule": a.rule_name,
                "severity": a.severity,
                "message": a.message,
                "value": a.value,
                "threshold": a.threshold,
                "resolved": a.resolved,
                "ts": a.ts,
            }
            for a in alert_mgr.history
        ]
    }
