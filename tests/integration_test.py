#!/usr/bin/env python3
"""Open-Soulmate Integration Test Suite.

Tests all 25 component APIs end-to-end, verifies responses,
and generates a comprehensive health report.

Usage:
    python tests/integration_test.py [--base-url http://localhost:8090] [--json] [--verbose]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class TestResult:
    component: str
    endpoint: str
    method: str = "GET"
    status_code: int = 0
    passed: bool = False
    message: str = ""
    elapsed_ms: float = 0.0
    response_size: int = 0


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total else 0

    @property
    def elapsed_seconds(self) -> float:
        return self.end_time - self.start_time


def make_request(
    base_url: str, method: str, path: str, body: dict | None = None, timeout: float = 10.0
) -> tuple[int, dict | str, float]:
    """Make an HTTP request and return (status_code, response_body, elapsed_ms)."""
    url = f"{base_url}{path}"
    start = time.time()
    try:
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.time() - start) * 1000
            body_bytes = resp.read()
            try:
                return resp.status, json.loads(body_bytes), elapsed
            except json.JSONDecodeError:
                return resp.status, body_bytes.decode("utf-8", errors="replace"), elapsed
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - start) * 1000
        try:
            body: dict | str = json.loads(e.read())
        except Exception:
            body = str(e)
        return e.code, body, elapsed
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return 0, str(e), elapsed


def check_endpoint(
    suite: TestSuite,
    base_url: str,
    component: str,
    method: str,
    path: str,
    expected_status: int = 200,
    body: dict | None = None,
    required_keys: list[str] | None = None,
    verbose: bool = False,
):
    """Test a single endpoint and record the result."""
    status, resp, elapsed = make_request(base_url, method, path, body)

    result = TestResult(
        component=component,
        endpoint=path,
        method=method,
        status_code=status,
        elapsed_ms=round(elapsed, 2),
    )

    if isinstance(resp, dict):
        result.response_size = len(json.dumps(resp))
    else:
        result.response_size = len(str(resp))

    # Check status code
    if status != expected_status:
        result.message = f"Expected {expected_status}, got {status}"
        suite.results.append(result)
        return

    # Check required keys
    if required_keys and isinstance(resp, dict):
        missing = [k for k in required_keys if k not in resp]
        if missing:
            result.message = f"Missing keys: {missing}"
            suite.results.append(result)
            return

    result.passed = True
    result.message = "ok"
    suite.results.append(result)

    if verbose:
        icon = "✓" if result.passed else "✗"
        print(f"  {icon} {method} {path} → {status} ({elapsed:.0f}ms)")


def run_tests(base_url: str, verbose: bool = False) -> TestSuite:
    """Run all integration tests."""
    suite = TestSuite()
    suite.start_time = time.time()

    if verbose:
        print(f"\n{'=' * 60}")
        print("  Open-Soulmate Integration Test Suite")
        print(f"  Base URL: {base_url}")
        print(f"{'=' * 60}\n")

    # ── Core System ──────────────────────────────────────────
    if verbose:
        print("🧠 Core System")
    check_endpoint(
        suite, base_url, "soul", "GET", "/api/health", required_keys=["component", "status"]
    )
    check_endpoint(
        suite, base_url, "soul", "GET", "/api/version", required_keys=["version", "name"]
    )
    check_endpoint(
        suite,
        base_url,
        "soul",
        "GET",
        "/api/health/all",
        required_keys=["organs", "healthy", "total"],
    )
    check_endpoint(
        suite,
        base_url,
        "soul",
        "GET",
        "/api/system/overview",
        required_keys=["organs", "metrics", "system_status"],
    )

    # ── Knowledge ────────────────────────────────────────────
    if verbose:
        print("📚 Knowledge")
    check_endpoint(suite, base_url, "knowledge", "GET", "/api/knowledge/health")
    check_endpoint(suite, base_url, "knowledge", "GET", "/api/knowledge/stats")
    check_endpoint(suite, base_url, "knowledge", "GET", "/api/knowledge/stats")
    check_endpoint(suite, base_url, "search", "GET", "/api/search/health")
    check_endpoint(suite, base_url, "search", "GET", "/api/search/stats")
    check_endpoint(suite, base_url, "graph", "GET", "/api/graph/health")
    check_endpoint(suite, base_url, "graph", "GET", "/api/graph/stats")
    check_endpoint(suite, base_url, "entity", "GET", "/api/entity/health")
    check_endpoint(suite, base_url, "tag", "GET", "/api/tags/health")

    # ── Cortex (AI Engine) ───────────────────────────────────
    if verbose:
        print("🧩 Cortex")
    check_endpoint(suite, base_url, "cortex", "GET", "/api/cortex/health")
    check_endpoint(suite, base_url, "cortex", "GET", "/api/cortex/stats")
    check_endpoint(suite, base_url, "cortex-enhanced", "GET", "/api/cortex/enhanced/health")

    # ── Nerve (Event Bus) ────────────────────────────────────
    if verbose:
        print("⚡ Nerve")
    check_endpoint(suite, base_url, "nerve", "GET", "/api/nerve/health")
    check_endpoint(suite, base_url, "nerve", "GET", "/api/nerve/stats")
    check_endpoint(suite, base_url, "nerve", "GET", "/api/nerve/nodes")
    check_endpoint(suite, base_url, "nerve", "GET", "/api/nerve/events?limit=5")

    # ── Vein (File System) ───────────────────────────────────
    if verbose:
        print("🩸 Vein")
    check_endpoint(suite, base_url, "vein", "GET", "/api/vein/health")
    check_endpoint(suite, base_url, "vein", "GET", "/api/vein/stats")
    check_endpoint(suite, base_url, "vein", "GET", "/api/vein/files?limit=5")
    check_endpoint(suite, base_url, "vein", "GET", "/api/vein/cache/stats")

    # ── Sense (Multimodal) ───────────────────────────────────
    if verbose:
        print("👁 Sense")
    check_endpoint(suite, base_url, "sense", "GET", "/api/sense/health")
    check_endpoint(suite, base_url, "sense", "GET", "/api/sense/stats")
    check_endpoint(suite, base_url, "sense", "GET", "/api/sense/stats")

    # ── Immune (Security) ────────────────────────────────────
    if verbose:
        print("🛡 Immune")
    check_endpoint(suite, base_url, "immune", "GET", "/api/immune/health")
    check_endpoint(suite, base_url, "immune", "GET", "/api/immune/stats")
    check_endpoint(suite, base_url, "immune", "GET", "/api/immune/audit/log?limit=5")

    # ── Vital (Monitoring) ───────────────────────────────────
    if verbose:
        print("📊 Vital")
    check_endpoint(suite, base_url, "vital", "GET", "/api/vital/health")
    check_endpoint(suite, base_url, "vital", "GET", "/api/vital/stats")
    check_endpoint(suite, base_url, "vital", "GET", "/api/vital/metrics")

    # ── Gland (LLM Gateway) ──────────────────────────────────
    if verbose:
        print("🧪 Gland")
    check_endpoint(suite, base_url, "gland", "GET", "/api/gland/health")
    check_endpoint(suite, base_url, "gland", "GET", "/api/gland/stats")
    check_endpoint(suite, base_url, "gland", "GET", "/api/gland/models")

    # ── Marrow (Backup) ──────────────────────────────────────
    if verbose:
        print("🦴 Marrow")
    check_endpoint(suite, base_url, "marrow", "GET", "/api/marrow/health")
    check_endpoint(suite, base_url, "marrow", "GET", "/api/marrow/stats")
    check_endpoint(suite, base_url, "marrow", "GET", "/api/marrow/backups")

    # ── Gene (Templates) ─────────────────────────────────────
    if verbose:
        print("🧬 Gene")
    check_endpoint(suite, base_url, "gene", "GET", "/api/gene/health")
    check_endpoint(suite, base_url, "gene", "GET", "/api/gene/stats")
    check_endpoint(suite, base_url, "gene", "GET", "/api/gene/templates")

    # ── Echo (Notifications) ─────────────────────────────────
    if verbose:
        print("🔊 Echo")
    check_endpoint(suite, base_url, "echo", "GET", "/api/echo/health")
    check_endpoint(suite, base_url, "echo", "GET", "/api/echo/stats")
    check_endpoint(suite, base_url, "echo", "GET", "/api/echo/channels")
    check_endpoint(suite, base_url, "echo", "GET", "/api/echo/history?limit=5")

    # ── Mirror (Sandbox) ─────────────────────────────────────
    if verbose:
        print("🪞 Mirror")
    check_endpoint(suite, base_url, "mirror", "GET", "/api/mirror/health")
    check_endpoint(suite, base_url, "mirror", "GET", "/api/mirror/stats")
    check_endpoint(suite, base_url, "mirror", "GET", "/api/mirror/sandboxes")

    # ── Link (Integration) ───────────────────────────────────
    if verbose:
        print("🔗 Link")
    check_endpoint(suite, base_url, "link", "GET", "/api/link/health")
    check_endpoint(suite, base_url, "link", "GET", "/api/link/stats")
    check_endpoint(suite, base_url, "link", "GET", "/api/link/connectors")
    check_endpoint(suite, base_url, "link", "GET", "/api/link/events?limit=5")

    # ── Hippo (Memory Lifecycle) ─────────────────────────────
    if verbose:
        print("🧠 Hippo")
    check_endpoint(suite, base_url, "hippo", "GET", "/api/hippo/health")
    check_endpoint(suite, base_url, "hippo", "GET", "/api/hippo/stats")

    # ── Reflex (Fast Response) ───────────────────────────────
    if verbose:
        print("⚡ Reflex")
    check_endpoint(suite, base_url, "reflex", "GET", "/api/reflex/health")
    check_endpoint(suite, base_url, "reflex", "GET", "/api/reflex/stats")

    # ── Heredity (Versioning) ────────────────────────────────
    if verbose:
        print("🔗 Heredity")
    check_endpoint(suite, base_url, "heredity", "GET", "/api/heredity/health")
    check_endpoint(suite, base_url, "heredity", "GET", "/api/heredity/stats")

    # ── Pulse (Timer) ────────────────────────────────────────
    if verbose:
        print("💓 Pulse")
    check_endpoint(suite, base_url, "pulse", "GET", "/api/pulse/health")
    check_endpoint(suite, base_url, "pulse", "GET", "/api/pulse/stats")

    # ── Nest (Multi-tenant) ──────────────────────────────────
    if verbose:
        print("🏠 Nest")
    check_endpoint(suite, base_url, "nest", "GET", "/api/nest/health")
    check_endpoint(suite, base_url, "nest", "GET", "/api/nest/stats")

    # ── Limb (RPA) ───────────────────────────────────────────
    if verbose:
        print("💪 Limb")
    check_endpoint(suite, base_url, "limb", "GET", "/api/limb/health")
    check_endpoint(suite, base_url, "limb", "GET", "/api/limb/stats")

    # ── Voice (TTS) ──────────────────────────────────────────
    if verbose:
        print("🎤 Voice")
    check_endpoint(suite, base_url, "voice", "GET", "/api/voice/health")
    check_endpoint(suite, base_url, "voice", "GET", "/api/voice/stats")

    # ── Vision (Charts) ──────────────────────────────────────
    if verbose:
        print("🎨 Vision")
    check_endpoint(suite, base_url, "vision", "GET", "/api/vision/health")
    check_endpoint(suite, base_url, "vision", "GET", "/api/vision/stats")

    # ── Mind (Personality) ───────────────────────────────────
    if verbose:
        print("💭 Mind")
    check_endpoint(suite, base_url, "mind", "GET", "/api/mind/health")
    check_endpoint(suite, base_url, "mind", "GET", "/api/mind/stats")

    # ── Will (Workflow) ──────────────────────────────────────
    if verbose:
        print("✨ Will")
    check_endpoint(suite, base_url, "will", "GET", "/api/will/health")
    check_endpoint(suite, base_url, "will", "GET", "/api/will/stats")

    # ── Workflow ─────────────────────────────────────────────
    if verbose:
        print("⚙️ Workflow")
    check_endpoint(suite, base_url, "workflow", "GET", "/api/workflow/health")
    check_endpoint(suite, base_url, "workflow", "GET", "/api/workflow/stats")

    # ── Trajectory ───────────────────────────────────────────
    if verbose:
        print("📈 Trajectory")
    check_endpoint(suite, base_url, "trajectory", "GET", "/api/trajectory/health")
    check_endpoint(suite, base_url, "trajectory", "GET", "/api/trajectory/stats")
    check_endpoint(suite, base_url, "trajectory", "GET", "/api/trajectory/sessions?limit=5")
    check_endpoint(suite, base_url, "trajectory", "GET", "/api/trajectory/event-types")

    # ── Intelligence ─────────────────────────────────────────
    if verbose:
        print("🔍 Intelligence")
    check_endpoint(suite, base_url, "intelligence", "GET", "/api/intelligence/health")
    check_endpoint(suite, base_url, "intelligence", "GET", "/api/intelligence/health")

    # ── Healer ───────────────────────────────────────────────
    if verbose:
        print("💊 Healer")
    check_endpoint(suite, base_url, "healer", "GET", "/api/healer/health")
    check_endpoint(suite, base_url, "healer", "GET", "/api/healer/stats")

    # ── Timeline ─────────────────────────────────────────────
    if verbose:
        print("📅 Timeline")
    check_endpoint(suite, base_url, "timeline", "GET", "/api/timeline/health")
    check_endpoint(suite, base_url, "timeline", "GET", "/api/timeline/stats")

    # ── Benchmark ────────────────────────────────────────────
    if verbose:
        print("📊 Benchmark")
    check_endpoint(suite, base_url, "benchmark", "GET", "/api/benchmark/health")
    check_endpoint(suite, base_url, "benchmark", "GET", "/api/benchmark/stats")

    # ── Diagnostics ──────────────────────────────────────────
    if verbose:
        print("🩺 Diagnostics")
    check_endpoint(suite, base_url, "diagnostics", "GET", "/api/diagnostics/health")
    check_endpoint(suite, base_url, "diagnostics", "GET", "/api/diagnostics/health")

    # ── Soma Connector ───────────────────────────────────────
    if verbose:
        print("🤖 Soma Connector")
    check_endpoint(suite, base_url, "soma", "GET", "/api/soma/health")
    check_endpoint(suite, base_url, "soma", "GET", "/api/soma/health")

    # ── Pipeline ─────────────────────────────────────────────
    if verbose:
        print("🔄 Pipeline")
    check_endpoint(suite, base_url, "pipeline", "GET", "/api/pipeline/health")
    check_endpoint(suite, base_url, "pipeline", "GET", "/api/pipeline/stats")

    # ── Topology ─────────────────────────────────────────────
    if verbose:
        print("🌐 Topology")
    check_endpoint(suite, base_url, "topology", "GET", "/api/topology/health")
    check_endpoint(suite, base_url, "topology", "GET", "/api/topology/health")

    # ── Notifications ────────────────────────────────────────
    if verbose:
        print("🔔 Notifications")
    check_endpoint(suite, base_url, "notifications", "GET", "/api/notifications/health")
    check_endpoint(suite, base_url, "notifications", "GET", "/api/notifications/health")

    # ── Config ───────────────────────────────────────────────
    if verbose:
        print("⚙️ Config")
    check_endpoint(suite, base_url, "config", "GET", "/api/config")

    # ── Registry ─────────────────────────────────────────────
    if verbose:
        print("📋 Registry")
    check_endpoint(suite, base_url, "registry", "GET", "/api/registry/components")

    # ── Plugins ──────────────────────────────────────────────
    if verbose:
        print("🔌 Plugins")
    check_endpoint(suite, base_url, "plugins", "GET", "/api/plugins/health")

    # ── MCP ──────────────────────────────────────────────────
    if verbose:
        print("🔧 MCP")
    check_endpoint(suite, base_url, "mcp", "GET", "/api/mcp/health")

    # ── Learn ────────────────────────────────────────────────
    if verbose:
        print("📖 Learn")
    check_endpoint(suite, base_url, "learn", "GET", "/api/learn/health")

    # ── Event Stream ─────────────────────────────────────────
    if verbose:
        print("📡 Event Stream")
    check_endpoint(suite, base_url, "event-stream", "GET", "/api/events/health")

    # ── Admin ────────────────────────────────────────────────
    if verbose:
        print("🔐 Admin")
    check_endpoint(suite, base_url, "admin", "GET", "/api/admin/report")

    # ── System Bootstrap ─────────────────────────────────────
    if verbose:
        print("🚀 System Bootstrap")
    check_endpoint(suite, base_url, "bootstrap", "GET", "/api/system/bootstrap/status")
    check_endpoint(suite, base_url, "bootstrap", "POST", "/api/system/bootstrap/run")

    suite.end_time = time.time()
    return suite


def print_report(suite: TestSuite, as_json: bool = False):
    """Print the test report."""
    if as_json:
        report = {
            "timestamp": time.time(),
            "elapsed_seconds": round(suite.elapsed_seconds, 2),
            "total": suite.total,
            "passed": suite.passed,
            "failed": suite.failed,
            "pass_rate": round(suite.pass_rate, 1),
            "failures": [
                {
                    "component": r.component,
                    "endpoint": r.endpoint,
                    "method": r.method,
                    "status_code": r.status_code,
                    "message": r.message,
                }
                for r in suite.results
                if not r.passed
            ],
            "by_component": {},
        }
        # Group by component
        for r in suite.results:
            comp = r.component
            if comp not in report["by_component"]:
                report["by_component"][comp] = {"total": 0, "passed": 0, "failed": 0}
            report["by_component"][comp]["total"] += 1
            if r.passed:
                report["by_component"][comp]["passed"] += 1
            else:
                report["by_component"][comp]["failed"] += 1
        print(json.dumps(report, indent=2))
        return

    # Text report
    print(f"\n{'=' * 60}")
    print("  Open-Soulmate Integration Test Report")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")

    # Summary
    status_icon = "✅" if suite.failed == 0 else "⚠️"
    print(f"  {status_icon} Results: {suite.passed}/{suite.total} passed ({suite.pass_rate:.1f}%)")
    print(f"  ⏱  Duration: {suite.elapsed_seconds:.2f}s")
    print()

    # Failures
    failures = [r for r in suite.results if not r.passed]
    if failures:
        print(f"  ❌ Failures ({len(failures)}):")
        for r in failures:
            print(f"     {r.component}: {r.method} {r.endpoint} → {r.status_code} ({r.message})")
        print()

    # Component summary
    components: dict[str, dict] = {}
    for r in suite.results:
        comp = r.component
        if comp not in components:
            components[comp] = {"total": 0, "passed": 0}
        components[comp]["total"] += 1
        if r.passed:
            components[comp]["passed"] += 1

    print(f"  📊 Components ({len(components)}):")
    for comp, stats in components.items():
        icon = "✅" if stats["passed"] == stats["total"] else "⚠️"
        print(f"     {icon} {comp}: {stats['passed']}/{stats['total']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Open-Soulmate Integration Test Suite")
    parser.add_argument("--base-url", default="http://localhost:8090", help="API base URL")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    suite = run_tests(args.base_url, verbose=args.verbose)
    print_report(suite, as_json=args.json)

    sys.exit(0 if suite.failed == 0 else 1)


if __name__ == "__main__":
    main()
