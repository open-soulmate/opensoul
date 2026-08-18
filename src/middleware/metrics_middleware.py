"""Metrics middleware — records HTTP request count, latency, and status codes
for the Prometheus /metrics endpoint.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.api.metrics_api import record_request


class MetricsMiddleware(BaseHTTPMiddleware):
    """Track every HTTP request and feed metrics to the collector."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip metrics endpoint itself to avoid recursion
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        record_request(
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_seconds=duration,
        )

        # Inject standard headers
        response.headers["X-Response-Time"] = f"{duration * 1000:.1f}ms"

        return response
