"""Daily Digest Plugin — 跨器官数据聚合、系统健康趋势、每日洞察报告。"""

import json
import time
import sqlite3
import asyncio
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()

DB_PATH = Path.home() / ".openmate" / "plugins" / "daily-digest" / "digest.db"

BASE_URL = "http://127.0.0.1:8090"

# ── Database ───────────────────────────────────────────────

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS digests (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL UNIQUE,
            generated_at REAL NOT NULL,
            health_snapshot TEXT DEFAULT '{}',
            organ_summary TEXT DEFAULT '{}',
            timeline_summary TEXT DEFAULT '{}',
            metrics_summary TEXT DEFAULT '{}',
            highlights TEXT DEFAULT '[]',
            warnings TEXT DEFAULT '[]',
            score REAL DEFAULT 0,
            total_events INTEGER DEFAULT 0,
            active_organs INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS digest_trends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            organ TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_digests_date ON digests(date);
        CREATE INDEX IF NOT EXISTS idx_trends_date ON digest_trends(date);
    """)
    conn.close()


init_db()


# ── Data Collection ────────────────────────────────────────

ORGAN_ENDPOINTS = {
    "soul": "/api/health",
    "cortex": "/api/cortex/health",
    "nerve": "/api/nerve/health",
    "vein": "/api/vein/stats",
    "sense": "/api/sense/health",
    "will": "/api/will/health",
    "immune": "/api/immune/health",
    "vital": "/api/vital/health",
    "marrow": "/api/marrow/health",
    "gland": "/api/gland/health",
    "gene": "/api/gene/health",
    "echo": "/api/echo/health",
    "mirror": "/api/mirror/health",
    "link": "/api/link/health",
    "hippo": "/api/hippo/health",
    "reflex": "/api/reflex/health",
    "heredity": "/api/heredity/health",
    "pulse": "/api/pulse/health",
    "nest": "/api/nest/health",
    "limb": "/api/limb/health",
    "voice": "/api/voice/health",
    "vision": "/api/vision/health",
    "mind": "/api/mind/health",
    "trajectory": "/api/trajectory/health",
    "intelligence": "/api/intelligence/health",
}

ORGAN_EMOJI = {
    "soul": "🧠", "cortex": "🧩", "nerve": "⚡", "vein": "🩸",
    "sense": "👁", "will": "✨", "immune": "🛡", "vital": "📊",
    "marrow": "🦴", "gland": "🧪", "gene": "🧬", "echo": "🔊",
    "mirror": "🪞", "link": "🔗", "hippo": "🧠", "reflex": "⚡",
    "heredity": "🔗", "pulse": "💓", "nest": "🏠", "limb": "💪",
    "voice": "🎤", "vision": "🎨", "mind": "💭", "trajectory": "📊",
    "intelligence": "🧠",
}


async def collect_health_data() -> dict:
    """Collect health data from all organs."""
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        tasks = []
        for organ, endpoint in ORGAN_ENDPOINTS.items():
            tasks.append(_check_organ(client, organ, endpoint))
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for resp in responses:
            if isinstance(resp, dict):
                results[resp["organ"]] = resp
    return results


async def _check_organ(client: httpx.AsyncClient, organ: str, endpoint: str) -> dict:
    """Check a single organ's health."""
    start = time.time()
    try:
        r = await client.get(f"{BASE_URL}{endpoint}")
        elapsed_ms = (time.time() - start) * 1000
        data = r.json() if r.status_code == 200 else {}
        return {
            "organ": organ,
            "emoji": ORGAN_EMOJI.get(organ, "⚙️"),
            "status": "ok" if r.status_code == 200 else "error",
            "status_code": r.status_code,
            "response_time_ms": round(elapsed_ms, 1),
            "data": data,
        }
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        return {
            "organ": organ,
            "emoji": ORGAN_EMOJI.get(organ, "⚙️"),
            "status": "error",
            "status_code": 0,
            "response_time_ms": round(elapsed_ms, 1),
            "error": str(e),
            "data": {},
        }


async def collect_timeline_events(hours: int = 24) -> dict:
    """Collect recent timeline events."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{BASE_URL}/api/timeline/events", params={"limit": 200})
            if r.status_code == 200:
                data = r.json()
                events = data.get("events", [])
                cutoff = time.time() - (hours * 3600)
                recent = [e for e in events if e.get("timestamp", 0) > cutoff]
                # Group by organ
                by_organ = {}
                for e in recent:
                    organ = e.get("organ", "unknown")
                    if organ not in by_organ:
                        by_organ[organ] = 0
                    by_organ[organ] += 1
                # Group by type
                by_type = {}
                for e in recent:
                    etype = e.get("type", "unknown")
                    if etype not in by_type:
                        by_type[etype] = 0
                    by_type[etype] += 1
                return {
                    "total_events": len(recent),
                    "by_organ": by_organ,
                    "by_type": by_type,
                    "recent": recent[:20],  # Last 20 events
                }
    except Exception:
        pass
    return {"total_events": 0, "by_organ": {}, "by_type": {}, "recent": []}


async def collect_vital_metrics() -> dict:
    """Collect system vital metrics."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{BASE_URL}/api/vital/stats")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


async def collect_knowledge_stats() -> dict:
    """Collect knowledge base stats."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{BASE_URL}/api/knowledge/", params={"limit": 1})
            if r.status_code == 200:
                data = r.json()
                return {
                    "total_entries": data.get("total", 0),
                }
    except Exception:
        pass
    return {"total_entries": 0}


async def collect_trajectory_stats() -> dict:
    """Collect trajectory/agent stats."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{BASE_URL}/api/trajectory/stats")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


def compute_health_score(health_data: dict) -> float:
    """Compute a 0-100 health score based on organ status and response times."""
    if not health_data:
        return 0.0
    total = len(health_data)
    healthy = sum(1 for o in health_data.values() if o.get("status") == "ok")
    health_pct = (healthy / total * 100) if total > 0 else 0
    # Response time penalty
    avg_rt = sum(o.get("response_time_ms", 0) for o in health_data.values()) / max(total, 1)
    rt_penalty = min(avg_rt / 100, 10)  # Up to 10 points penalty for slow responses
    return round(max(0, min(100, health_pct - rt_penalty)), 1)


def generate_highlights(health_data: dict, timeline: dict, vitals: dict) -> list[str]:
    """Generate highlight messages."""
    highlights = []
    total = len(health_data)
    healthy = sum(1 for o in health_data.values() if o.get("status") == "ok")
    if healthy == total:
        highlights.append(f"✅ All {total} organs are healthy")
    else:
        highlights.append(f"⚠️ {total - healthy}/{total} organs have issues")

    events = timeline.get("total_events", 0)
    if events > 0:
        highlights.append(f"📈 {events} events recorded in the last 24h")

    # Most active organ
    by_organ = timeline.get("by_organ", {})
    if by_organ:
        top = max(by_organ, key=by_organ.get)
        highlights.append(f"🏆 Most active organ: {ORGAN_EMOJI.get(top, '⚙️')} {top} ({by_organ[top]} events)")

    # Slowest organ
    slowest = max(health_data.values(), key=lambda x: x.get("response_time_ms", 0), default=None)
    if slowest and slowest.get("response_time_ms", 0) > 200:
        highlights.append(f"🐌 Slowest response: {slowest['emoji']} {slowest['organ']} ({slowest['response_time_ms']}ms)")

    return highlights


def generate_warnings(health_data: dict, vitals: dict) -> list[str]:
    """Generate warning messages."""
    warnings = []
    for organ, data in health_data.items():
        if data.get("status") != "ok":
            warnings.append(f"❌ {data.get('emoji', '⚙️')} {organ} is {data.get('status')}")
        elif data.get("response_time_ms", 0) > 500:
            warnings.append(f"⏱️ {data.get('emoji', '⚙️')} {organ} response time is high ({data['response_time_ms']}ms)")
    return warnings


def generate_digest_id(date_str: str) -> str:
    """Generate a unique digest ID."""
    return f"digest_{hashlib.md5(date_str.encode()).hexdigest()[:12]}"


# ── API Endpoints ──────────────────────────────────────────

@router.get("/health")
async def health():
    """Daily Digest health check."""
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM digests").fetchone()
        count = row["cnt"] if row else 0
    finally:
        conn.close()
    return {"status": "ok", "component": "DailyDigest", "digests_stored": count}


@router.get("/stats")
async def stats():
    """Get digest statistics."""
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt, AVG(score) as avg_score, AVG(total_events) as avg_events FROM digests").fetchone()
        trend_count = conn.execute("SELECT COUNT(*) as cnt FROM digest_trends").fetchone()
        return {
            "total_digests": row["cnt"] or 0,
            "avg_score": round(row["avg_score"] or 0, 1),
            "avg_daily_events": round(row["avg_events"] or 0, 1),
            "trend_points": trend_count["cnt"] or 0,
        }
    finally:
        conn.close()


@router.post("/generate")
async def generate_digest(force: bool = Query(False)):
    """Generate a daily digest for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    digest_id = generate_digest_id(today)

    conn = get_db()
    try:
        # Check if already exists
        if not force:
            existing = conn.execute("SELECT id FROM digests WHERE date = ?", (today,)).fetchone()
            if existing:
                return {"message": "Digest already exists for today", "date": today, "id": existing["id"], "cached": True}
    finally:
        conn.close()

    # Collect data
    health_data, timeline, vitals, knowledge, trajectory = await asyncio.gather(
        collect_health_data(),
        collect_timeline_events(24),
        collect_vital_metrics(),
        collect_knowledge_stats(),
        collect_trajectory_stats(),
    )

    score = compute_health_score(health_data)
    highlights = generate_highlights(health_data, timeline, vitals)
    warnings = generate_warnings(health_data, vitals)
    active_organs = sum(1 for o in health_data.values() if o.get("status") == "ok")

    # Organ summary (without raw data, just key metrics)
    organ_summary = {}
    for name, data in health_data.items():
        organ_summary[name] = {
            "emoji": data.get("emoji", "⚙️"),
            "status": data.get("status"),
            "response_time_ms": data.get("response_time_ms", 0),
        }

    # Metrics summary
    metrics_summary = {
        "knowledge_entries": knowledge.get("total_entries", 0),
        "trajectory_sessions": trajectory.get("total_sessions", 0),
        "trajectory_events": trajectory.get("total_events", 0),
        "trajectory_tokens": trajectory.get("total_tokens", 0),
    }

    now = time.time()
    conn = get_db()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO digests (id, date, generated_at, health_snapshot, organ_summary,
                timeline_summary, metrics_summary, highlights, warnings, score, total_events, active_organs, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            digest_id, today, now,
            json.dumps({k: {"status": v.get("status"), "rt": v.get("response_time_ms", 0)} for k, v in health_data.items()}),
            json.dumps(organ_summary),
            json.dumps(timeline),
            json.dumps(metrics_summary),
            json.dumps(highlights),
            json.dumps(warnings),
            score,
            timeline.get("total_events", 0),
            active_organs,
            json.dumps({"knowledge": knowledge, "trajectory": trajectory}),
        ))

        # Store trend data
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "health_score", score))
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "total_events", timeline.get("total_events", 0)))
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "active_organs", active_organs))
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "avg_response_ms", sum(o.get("response_time_ms", 0) for o in health_data.values()) / max(len(health_data), 1)))
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "knowledge_entries", knowledge.get("total_entries", 0)))

        # Per-organ response times
        for name, data in health_data.items():
            conn.execute("INSERT INTO digest_trends (date, metric, value, organ) VALUES (?, ?, ?, ?)",
                         (today, "organ_response_ms", data.get("response_time_ms", 0), name))

        conn.commit()
    finally:
        conn.close()

    return {
        "id": digest_id,
        "date": today,
        "score": score,
        "active_organs": active_organs,
        "total_organs": len(health_data),
        "total_events": timeline.get("total_events", 0),
        "highlights": highlights,
        "warnings": warnings,
        "organ_summary": organ_summary,
        "metrics_summary": metrics_summary,
        "timeline_summary": {
            "total_events": timeline.get("total_events", 0),
            "by_organ": timeline.get("by_organ", {}),
            "by_type": timeline.get("by_type", {}),
        },
        "generated_at": now,
    }


@router.get("/today")
async def get_today_digest():
    """Get today's digest (generates if not exists)."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM digests WHERE date = ?", (today,)).fetchone()
        if row:
            return _row_to_digest(row)
    finally:
        conn.close()

    # Auto-generate
    return await generate_digest()


@router.get("/digests")
async def list_digests(
    limit: int = Query(30, ge=1, le=365),
    offset: int = Query(0, ge=0),
):
    """List historical digests."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, date, score, total_events, active_organs, generated_at, highlights, warnings FROM digests ORDER BY date DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as cnt FROM digests").fetchone()["cnt"]
        return {
            "digests": [
                {
                    "id": r["id"],
                    "date": r["date"],
                    "score": r["score"],
                    "total_events": r["total_events"],
                    "active_organs": r["active_organs"],
                    "generated_at": r["generated_at"],
                    "highlights": json.loads(r["highlights"]) if r["highlights"] else [],
                    "warnings": json.loads(r["warnings"]) if r["warnings"] else [],
                }
                for r in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


@router.get("/digests/{date}")
async def get_digest(date: str):
    """Get a specific digest by date (YYYY-MM-DD)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM digests WHERE date = ?", (date,)).fetchone()
        if not row:
            return {"error": f"No digest found for {date}", "date": date}
        return _row_to_digest(row)
    finally:
        conn.close()


@router.get("/trends")
async def get_trends(
    metric: str = Query("health_score"),
    days: int = Query(30, ge=1, le=365),
):
    """Get trend data for a specific metric."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_db()
    try:
        if metric == "organ_response_ms":
            rows = conn.execute(
                "SELECT date, organ, value FROM digest_trends WHERE metric = ? AND date >= ? ORDER BY date",
                (metric, cutoff)
            ).fetchall()
            # Group by organ
            by_organ = {}
            for r in rows:
                organ = r["organ"]
                if organ not in by_organ:
                    by_organ[organ] = []
                by_organ[organ].append({"date": r["date"], "value": r["value"]})
            return {"metric": metric, "days": days, "by_organ": by_organ}
        else:
            rows = conn.execute(
                "SELECT date, value FROM digest_trends WHERE metric = ? AND date >= ? ORDER BY date",
                (metric, cutoff)
            ).fetchall()
            return {"metric": metric, "days": days, "data": [{"date": r["date"], "value": r["value"]} for r in rows]}
    finally:
        conn.close()


@router.delete("/digests/{date}")
async def delete_digest(date: str):
    """Delete a digest by date."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM digests WHERE date = ?", (date,))
        conn.execute("DELETE FROM digest_trends WHERE date = ?", (date,))
        conn.commit()
        return {"message": f"Digest for {date} deleted", "date": date}
    finally:
        conn.close()


@router.post("/collect-metrics")
async def collect_and_store_metrics():
    """Collect current metrics and store as trend data (without generating full digest)."""
    today = datetime.now().strftime("%Y-%m-%d")
    health_data = await collect_health_data()

    score = compute_health_score(health_data)
    active = sum(1 for o in health_data.values() if o.get("status") == "ok")
    avg_rt = sum(o.get("response_time_ms", 0) for o in health_data.values()) / max(len(health_data), 1)

    conn = get_db()
    try:
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "health_score", score))
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "active_organs", active))
        conn.execute("INSERT INTO digest_trends (date, metric, value) VALUES (?, ?, ?)", (today, "avg_response_ms", avg_rt))
        for name, data in health_data.items():
            conn.execute("INSERT INTO digest_trends (date, metric, value, organ) VALUES (?, ?, ?, ?)",
                         (today, "organ_response_ms", data.get("response_time_ms", 0), name))
        conn.commit()
    finally:
        conn.close()

    return {
        "date": today,
        "score": score,
        "active_organs": active,
        "avg_response_ms": round(avg_rt, 1),
        "organs_collected": len(health_data),
    }


def _row_to_digest(row) -> dict:
    """Convert a database row to a digest dict."""
    return {
        "id": row["id"],
        "date": row["date"],
        "generated_at": row["generated_at"],
        "score": row["score"],
        "total_events": row["total_events"],
        "active_organs": row["active_organs"],
        "health_snapshot": json.loads(row["health_snapshot"]) if row["health_snapshot"] else {},
        "organ_summary": json.loads(row["organ_summary"]) if row["organ_summary"] else {},
        "timeline_summary": json.loads(row["timeline_summary"]) if row["timeline_summary"] else {},
        "metrics_summary": json.loads(row["metrics_summary"]) if row["metrics_summary"] else {},
        "highlights": json.loads(row["highlights"]) if row["highlights"] else [],
        "warnings": json.loads(row["warnings"]) if row["warnings"] else [],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
    }
