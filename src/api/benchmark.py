"""OpenBenchmark API — organ performance benchmarking with historical tracking."""

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.benchmark.engine import BENCHMARK_TARGETS, benchmark_engine

router = APIRouter()


class BenchmarkRunRequest(BaseModel):
    organs: list[str] | None = None  # None = all
    iterations: int = 20
    concurrency: int = 5


# ── Health ──────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenBenchmark"}


# ── Stats ───────────────────────────────────────────────────


@router.get("/stats")
async def stats():
    return benchmark_engine.stats()


# ── Available Targets ───────────────────────────────────────


@router.get("/targets")
async def list_targets():
    """List all benchmarkable organs."""
    return {
        "targets": [
            {"organ": k, "label": v["label"], "endpoint": v["endpoint"]}
            for k, v in BENCHMARK_TARGETS.items()
        ],
        "total": len(BENCHMARK_TARGETS),
    }


# ── Run Benchmark ───────────────────────────────────────────


@router.post("/run")
async def run_benchmark(req: BenchmarkRunRequest):
    """Run a performance benchmark against specified organs."""
    if req.iterations < 1 or req.iterations > 200:
        raise HTTPException(status_code=400, detail="iterations must be 1-200")
    if req.concurrency < 1 or req.concurrency > 50:
        raise HTTPException(status_code=400, detail="concurrency must be 1-50")

    result = await benchmark_engine.run_benchmark(
        organs=req.organs,
        iterations=req.iterations,
        concurrency=req.concurrency,
    )
    return result


# ── Quick Benchmark (single organ) ──────────────────────────


@router.post("/quick/{organ}")
async def quick_benchmark(organ: str, iterations: int = Query(default=10, ge=1, le=100)):
    """Quick benchmark a single organ with default settings."""
    if organ not in BENCHMARK_TARGETS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown organ: {organ}. Valid: {list(BENCHMARK_TARGETS.keys())}",
        )

    result = await benchmark_engine.run_benchmark(
        organs=[organ],
        iterations=iterations,
        concurrency=3,
    )
    return result


# ── Cancel Running Benchmark ────────────────────────────────


@router.post("/cancel/{run_id}")
async def cancel_benchmark(run_id: str):
    """Cancel a running benchmark."""
    if benchmark_engine.cancel_run(run_id):
        return {"message": f"Benchmark {run_id} cancelled"}
    raise HTTPException(status_code=404, detail=f"No running benchmark with id: {run_id}")


# ── History ─────────────────────────────────────────────────


@router.get("/history")
async def get_history(
    organ: str = Query(default="", description="Filter by organ"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Get benchmark history."""
    return {
        "history": benchmark_engine.get_history(organ=organ, limit=limit),
    }


@router.get("/history/runs")
async def get_runs(limit: int = Query(default=20, ge=1, le=100)):
    """Get benchmark run history."""
    return {
        "runs": benchmark_engine.get_runs(limit=limit),
    }


# ── Latest / Comparison ─────────────────────────────────────


@router.get("/latest")
async def get_latest():
    """Get latest benchmark results for all tested organs."""
    return {
        "latest": benchmark_engine.get_latest(),
        "timestamp": time.time(),
    }


@router.get("/comparison")
async def get_comparison():
    """Get comparison data — all organs sorted by performance."""
    return {
        "comparison": benchmark_engine.get_comparison(),
        "timestamp": time.time(),
    }


# ── Delete History ──────────────────────────────────────────


@router.delete("/history")
async def delete_history(organ: str = Query(default="", description="Delete by organ, or all")):
    """Delete benchmark history."""
    deleted = benchmark_engine.delete_history(organ=organ)
    return {"deleted": deleted, "organ": organ or "all"}
