"""Persistent timeline event store using SQLite.

Stores all organ events persistently with rich querying capabilities.
Events are automatically captured from the event_bridge and stored here.
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TimelineEvent:
    event_id: str
    organ: str
    emoji: str
    event_type: str
    summary: str
    detail: str  # JSON string
    timestamp: float
    collected_at: float

    @property
    def detail_dict(self) -> dict:
        try:
            return json.loads(self.detail)
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "organ": self.organ,
            "emoji": self.emoji,
            "event_type": self.event_type,
            "summary": self.summary,
            "detail": self.detail_dict,
            "timestamp": self.timestamp,
            "collected_at": self.collected_at,
            "time_ago": _time_ago(self.timestamp),
        }


def _time_ago(ts: float) -> str:
    """Human-readable time ago string."""
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)}s ago"
    elif diff < 3600:
        return f"{int(diff / 60)}m ago"
    elif diff < 86400:
        return f"{int(diff / 3600)}h ago"
    else:
        return f"{int(diff / 86400)}d ago"


class TimelineStore:
    """Persistent event timeline with SQLite backend."""

    def __init__(self, db_path: str | None = None):
        db = db_path or os.path.expanduser("~/opensoul/data/timeline.db")
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._init_db()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id    TEXT PRIMARY KEY,
                organ       TEXT NOT NULL,
                emoji       TEXT DEFAULT '',
                event_type  TEXT NOT NULL,
                summary     TEXT NOT NULL,
                detail      TEXT DEFAULT '{}',
                timestamp   REAL NOT NULL,
                collected_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_organ ON events(organ);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_events_organ_ts ON events(organ, timestamp DESC);
        """)
        self._db.commit()

    def record(self, event: dict) -> bool:
        """Record an event to the timeline. Returns True if inserted (deduped by event_id)."""
        event_id = event.get("id", "")
        if not event_id:
            return False

        try:
            self._db.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, organ, emoji, event_type, summary, detail, timestamp, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    event.get("organ", "unknown"),
                    event.get("emoji", ""),
                    event.get("type", "unknown"),
                    event.get("summary", ""),
                    json.dumps(event.get("detail", {}), ensure_ascii=False),
                    event.get("timestamp", event.get("collected_at", time.time())),
                    event.get("collected_at", time.time()),
                ),
            )
            self._db.commit()
            return True
        except Exception:
            return False

    def query(
        self,
        organ: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TimelineEvent]:
        """Query events with filters."""
        clauses = ["1=1"]
        params: list = []

        if organ:
            clauses.append("organ = ?")
            params.append(organ)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if search:
            clauses.append("(summary LIKE ? OR detail LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)

        where = " AND ".join(clauses)
        query = f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.execute(query, params).fetchall()
        return [
            TimelineEvent(
                event_id=r["event_id"],
                organ=r["organ"],
                emoji=r["emoji"],
                event_type=r["event_type"],
                summary=r["summary"],
                detail=r["detail"],
                timestamp=r["timestamp"],
                collected_at=r["collected_at"],
            )
            for r in rows
        ]

    def get_event(self, event_id: str) -> TimelineEvent | None:
        """Get a single event by ID."""
        row = self._db.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if not row:
            return None
        return TimelineEvent(
            event_id=row["event_id"],
            organ=row["organ"],
            emoji=row["emoji"],
            event_type=row["event_type"],
            summary=row["summary"],
            detail=row["detail"],
            timestamp=row["timestamp"],
            collected_at=row["collected_at"],
        )

    def delete_event(self, event_id: str) -> bool:
        """Delete a single event."""
        cursor = self._db.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def clear(self, older_than_days: int | None = None) -> int:
        """Clear events. If older_than_days is set, only clear old events."""
        if older_than_days:
            cutoff = time.time() - (older_than_days * 86400)
            cursor = self._db.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        else:
            cursor = self._db.execute("DELETE FROM events")
        self._db.commit()
        return cursor.rowcount

    def stats(self) -> dict:
        """Get timeline statistics."""
        total = self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        # Events per organ
        organ_counts = {}
        for row in self._db.execute(
            "SELECT organ, COUNT(*) as cnt FROM events GROUP BY organ ORDER BY cnt DESC"
        ).fetchall():
            organ_counts[row["organ"]] = row["cnt"]

        # Events per type
        type_counts = {}
        for row in self._db.execute(
            "SELECT event_type, COUNT(*) as cnt FROM events GROUP BY event_type ORDER BY cnt DESC LIMIT 20"
        ).fetchall():
            type_counts[row["event_type"]] = row["cnt"]

        # Time range
        time_range = self._db.execute(
            "SELECT MIN(timestamp) as earliest, MAX(timestamp) as latest FROM events"
        ).fetchone()

        # Recent activity (last 24h)
        day_ago = time.time() - 86400
        recent = self._db.execute(
            "SELECT COUNT(*) FROM events WHERE timestamp > ?", (day_ago,)
        ).fetchone()[0]

        # Hourly distribution (last 24h)
        hourly = []
        for i in range(24):
            hour_start = time.time() - (i + 1) * 3600
            hour_end = time.time() - i * 3600
            count = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE timestamp > ? AND timestamp <= ?",
                (hour_start, hour_end),
            ).fetchone()[0]
            hourly.append({"hours_ago": i + 1, "count": count})

        return {
            "total_events": total,
            "recent_24h": recent,
            "by_organ": organ_counts,
            "by_type": type_counts,
            "time_range": {
                "earliest": time_range["earliest"] if time_range else None,
                "latest": time_range["latest"] if time_range else None,
            },
            "hourly_distribution": hourly,
        }

    def organs(self) -> list[dict]:
        """List all organs that have events, with counts."""
        rows = self._db.execute(
            """SELECT organ, emoji, COUNT(*) as count,
                      MAX(timestamp) as last_event
               FROM events GROUP BY organ ORDER BY last_event DESC"""
        ).fetchall()
        return [
            {
                "organ": r["organ"],
                "emoji": r["emoji"],
                "count": r["count"],
                "last_event": r["last_event"],
                "last_event_ago": _time_ago(r["last_event"]),
            }
            for r in rows
        ]

    def event_types(self) -> list[dict]:
        """List all event types with counts."""
        rows = self._db.execute(
            """SELECT event_type, COUNT(*) as count,
                      MAX(timestamp) as last_seen
               FROM events GROUP BY event_type ORDER BY count DESC"""
        ).fetchall()
        return [
            {
                "event_type": r["event_type"],
                "count": r["count"],
                "last_seen": r["last_seen"],
            }
            for r in rows
        ]
