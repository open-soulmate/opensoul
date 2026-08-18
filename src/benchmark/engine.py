"""OpenBenchmark — organ performance benchmarking with historical tracking."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

DB_PATH = Path.home() / ".opensoul" / "benchmark.db"

# All benchmarkable organs
BENCHMARK_TARGETS = {
    "soul": {"endpoint": "/api/health", "label": "🧠 Soul"},
    "cortex": {"endpoint": "/api/cortex/health", "label": "🧩 Cortex"},
    "nerve": {"endpoint": "/api/nerve/health", "label": "⚡ Nerve"},
    "vein": {"endpoint": "/api/vein/health", "label": "🩸 Vein"},
    "sense": {"endpoint": "/api/sense/health", "label": "👁 Sense"},
    "will": {"endpoint": "/api/will/health", "label": "✨ Will"},
    "immune": {"endpoint": "/api/immune/health", "label": "🛡 Immune"},
    "vital": {"endpoint": "/api/vital/health", "label": "📊 Vital"},
    "marrow": {"endpoint": "/api/marrow/health", "label": "🦴 Marrow"},
    "gland": {"endpoint": "/api/gland/health", "label": "🧪 Gland"},
    "gene": {"endpoint": "/api/gene/health", "label": "🧬 Gene"},
    "echo": {"endpoint": "/api/echo/health", "label": "🔊 Echo"},
    "mirror": {"endpoint": "/api/mirror/health", "label": "🪞 Mirror"},
    "link": {"endpoint": "/api/link/health", "label": "🔗 Link"},
    "hippo": {"endpoint": "/api/hippo/health", "label": "🧠 Hippo"},
    "reflex": {"endpoint": "/api/reflex/health", "label": "⚡ Reflex"},
    "heredity": {"endpoint": "/api/heredity/health", "label": "🔗 Heredity"},
    "pulse": {"endpoint": "/api/pulse/health", "label": "💓 Pulse"},
    "nest": {"endpoint": "/api/nest/health", "label": "🏠 Nest"},
    "limb": {"endpoint": "/api/limb/health", "label": "💪 Limb"},
    "voice": {"endpoint": "/api/voice/health", "label": "🎤 Voice"},
    "vision": {"endpoint": "/api/vision/health", "label": "🎨 Vision"},
    "mind": {"endpoint": "/api/mind/health", "label": "💭 Mind"},
    "intelligence": {"endpoint": "/api/intelligence/health", "label": "🧠 Intelligence"},
    "trajectory": {"endpoint": "/api/trajectory/health", "label": "📊 Trajectory"},
    "healer": {"endpoint": "/api/healer/health", "label": "💊 Healer"},
    "timeline": {"endpoint": "/api/timeline/health", "label": "📜 Timeline"},
    "topology": {"endpoint": "/api/topology/health", "label": "🗺 Topology"},
    "pipeline": {"endpoint": "/api/pipeline/health", "label": "🔄 Pipeline"},
    "capture": {"endpoint": "/api/capture/health", "label": "📸 Capture"},
}


@dataclass
class BenchmarkResult:
    organ: str
    label: str
    iterations: int
    success_count: int
    error_count: int
    min_ms: float
    max_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    throughput_rps: float
    total_duration_ms: float
    timestamp: float


class BenchmarkEngine:
    """Run performance benchmarks against organ health endpoints."""

    def __init__(self, base_url: str = "http://127.0.0.1:8090"):
        self._base_url = base_url
        self._ensure_db()
        self._running: dict[str, bool] = {}

    def _ensure_db(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmarks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                organ       TEXT NOT NULL,
                label       TEXT NOT NULL,
                iterations  INTEGER NOT NULL,
                success     INTEGER NOT NULL,
                errors      INTEGER NOT NULL,
                min_ms      REAL NOT NULL,
                max_ms      REAL NOT NULL,
                avg_ms      REAL NOT NULL,
                p50_ms      REAL NOT NULL,
                p95_ms      REAL NOT NULL,
                p99_ms      REAL NOT NULL,
                rps         REAL NOT NULL,
                total_ms    REAL NOT NULL,
                timestamp   REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                run_id      TEXT PRIMARY KEY,
                organs      TEXT NOT NULL,
                iterations  INTEGER NOT NULL,
                concurrency INTEGER NOT NULL,
                started_at  REAL NOT NULL,
                completed_at REAL,
                status      TEXT NOT NULL DEFAULT 'running'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bench_organ ON benchmarks(organ)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bench_ts ON benchmarks(timestamp)")
        conn.commit()
        conn.close()

    async def run_benchmark(
        self,
        organs: list[str] | None = None,
        iterations: int = 20,
        concurrency: int = 5,
        run_id: str = "",
    ) -> dict:
        """Run benchmark against specified organs (or all)."""
        import uuid

        if not run_id:
            run_id = f"bench_{int(time.time())}_{uuid.uuid4().hex[:8]}"

        targets = organs or list(BENCHMARK_TARGETS.keys())
        # Filter valid targets
        targets = [t for t in targets if t in BENCHMARK_TARGETS]

        if not targets:
            return {"error": "No valid organs specified", "valid": list(BENCHMARK_TARGETS.keys())}

        # Record run start
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO benchmark_runs (run_id, organs, iterations, concurrency, started_at, status) VALUES (?,?,?,?,?,?)",
            (run_id, json.dumps(targets), iterations, concurrency, time.time(), "running"),
        )
        conn.commit()
        conn.close()

        self._running[run_id] = True
        results = []

        for organ in targets:
            if not self._running.get(run_id):
                break
            result = await self._benchmark_organ(organ, iterations, concurrency, run_id)
            results.append(result)

        self._running.pop(run_id, None)

        # Mark run complete
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE benchmark_runs SET completed_at=?, status=? WHERE run_id=?",
            (time.time(), "completed", run_id),
        )
        conn.commit()
        conn.close()

        return {
            "run_id": run_id,
            "organs_benchmarked": len(results),
            "iterations": iterations,
            "concurrency": concurrency,
            "results": [self._result_to_dict(r) for r in results],
        }

    async def _benchmark_organ(
        self, organ: str, iterations: int, concurrency: int, run_id: str
    ) -> BenchmarkResult:
        """Benchmark a single organ."""
        target = BENCHMARK_TARGETS[organ]
        url = f"{self._base_url}{target['endpoint']}"
        label = target["label"]

        latencies: list[float] = []
        errors = 0
        start_all = time.time()

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Run with controlled concurrency
            semaphore = asyncio.Semaphore(concurrency)

            async def single_request():
                nonlocal errors
                async with semaphore:
                    req_start = time.time()
                    try:
                        r = await client.get(url)
                        elapsed = (time.time() - req_start) * 1000
                        latencies.append(elapsed)
                        if r.status_code != 200:
                            errors += 1
                    except Exception:
                        elapsed = (time.time() - req_start) * 1000
                        latencies.append(elapsed)
                        errors += 1

            await asyncio.gather(*[single_request() for _ in range(iterations)])

        total_ms = (time.time() - start_all) * 1000
        success = iterations - errors

        # Calculate stats
        if latencies:
            sorted_lat = sorted(latencies)
            min_ms = sorted_lat[0]
            max_ms = sorted_lat[-1]
            avg_ms = statistics.mean(sorted_lat)
            p50_ms = sorted_lat[int(len(sorted_lat) * 0.5)]
            p95_ms = sorted_lat[min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)]
            p99_ms = sorted_lat[min(int(len(sorted_lat) * 0.99), len(sorted_lat) - 1)]
        else:
            min_ms = max_ms = avg_ms = p50_ms = p95_ms = p99_ms = 0.0

        throughput = (iterations / (total_ms / 1000)) if total_ms > 0 else 0.0

        result = BenchmarkResult(
            organ=organ,
            label=label,
            iterations=iterations,
            success_count=success,
            error_count=errors,
            min_ms=round(min_ms, 2),
            max_ms=round(max_ms, 2),
            avg_ms=round(avg_ms, 2),
            p50_ms=round(p50_ms, 2),
            p95_ms=round(p95_ms, 2),
            p99_ms=round(p99_ms, 2),
            throughput_rps=round(throughput, 1),
            total_duration_ms=round(total_ms, 2),
            timestamp=time.time(),
        )

        # Save to DB
        self._save_result(result, run_id)
        return result

    def _save_result(self, result: BenchmarkResult, run_id: str):
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO benchmarks (run_id, organ, label, iterations, success, errors,
               min_ms, max_ms, avg_ms, p50_ms, p95_ms, p99_ms, rps, total_ms, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                result.organ,
                result.label,
                result.iterations,
                result.success_count,
                result.error_count,
                result.min_ms,
                result.max_ms,
                result.avg_ms,
                result.p50_ms,
                result.p95_ms,
                result.p99_ms,
                result.throughput_rps,
                result.total_duration_ms,
                result.timestamp,
            ),
        )
        conn.commit()
        conn.close()

    def get_history(self, organ: str = "", limit: int = 50) -> list[dict]:
        """Get benchmark history, optionally filtered by organ."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        if organ:
            rows = conn.execute(
                "SELECT * FROM benchmarks WHERE organ=? ORDER BY timestamp DESC LIMIT ?",
                (organ, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM benchmarks ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_runs(self, limit: int = 20) -> list[dict]:
        """Get benchmark run history."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM benchmark_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_latest(self) -> dict:
        """Get latest benchmark results for all organs."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # Get latest result per organ
        rows = conn.execute("""
            SELECT b.* FROM benchmarks b
            INNER JOIN (
                SELECT organ, MAX(timestamp) as max_ts
                FROM benchmarks GROUP BY organ
            ) latest ON b.organ = latest.organ AND b.timestamp = latest.max_ts
            ORDER BY b.organ
        """).fetchall()
        conn.close()
        return {r["organ"]: dict(r) for r in rows}

    def get_comparison(self) -> list[dict]:
        """Get comparison data — latest benchmark for each organ, sorted by avg_ms."""
        latest = self.get_latest()
        results = []
        for organ, data in sorted(latest.items(), key=lambda x: x[1].get("avg_ms", 999)):
            results.append(data)
        return results

    def delete_history(self, organ: str = "") -> int:
        """Delete benchmark history."""
        conn = sqlite3.connect(str(DB_PATH))
        if organ:
            cursor = conn.execute("DELETE FROM benchmarks WHERE organ=?", (organ,))
        else:
            cursor = conn.execute("DELETE FROM benchmarks")
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted

    def cancel_run(self, run_id: str) -> bool:
        if run_id in self._running:
            self._running[run_id] = False
            return True
        return False

    def stats(self) -> dict:
        conn = sqlite3.connect(str(DB_PATH))
        total = conn.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0]
        organs_tested = conn.execute("SELECT COUNT(DISTINCT organ) FROM benchmarks").fetchone()[0]
        conn.close()
        return {
            "total_benchmarks": total,
            "total_runs": runs,
            "organs_tested": organs_tested,
            "available_targets": len(BENCHMARK_TARGETS),
        }

    @staticmethod
    def _result_to_dict(r: BenchmarkResult) -> dict:
        return {
            "organ": r.organ,
            "label": r.label,
            "iterations": r.iterations,
            "success": r.success_count,
            "errors": r.error_count,
            "latency": {
                "min_ms": r.min_ms,
                "max_ms": r.max_ms,
                "avg_ms": r.avg_ms,
                "p50_ms": r.p50_ms,
                "p95_ms": r.p95_ms,
                "p99_ms": r.p99_ms,
            },
            "throughput_rps": r.throughput_rps,
            "total_duration_ms": r.total_duration_ms,
            "timestamp": r.timestamp,
        }


# Singleton
benchmark_engine = BenchmarkEngine()
