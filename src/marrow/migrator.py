"""Data export/import — cross-environment data migration."""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExportJob:
    job_id: str
    format: str  # "json", "csv", "jsonl"
    created_at: float
    record_count: int
    size_bytes: int
    status: str = "complete"
    file_path: str = ""


class DataMigrator:
    """Export/import data in multiple formats for cross-environment migration."""

    def __init__(self, export_dir: str | Path | None = None):
        import os

        self.export_dir = Path(export_dir or os.path.expanduser("~/.opensoul/exports"))
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, ExportJob] = {}

    def export_json(self, data: list[dict], name: str = "export") -> ExportJob:
        """Export data as JSON."""
        job_id = f"export_{int(time.time())}"
        path = self.export_dir / f"{name}_{job_id}.json"

        content = json.dumps(data, ensure_ascii=False, indent=2)
        path.write_text(content, encoding="utf-8")

        job = ExportJob(
            job_id=job_id,
            format="json",
            created_at=time.time(),
            record_count=len(data),
            size_bytes=len(content.encode("utf-8")),
            file_path=str(path),
        )
        self._jobs[job_id] = job
        return job

    def export_jsonl(self, data: list[dict], name: str = "export") -> ExportJob:
        """Export data as JSON Lines."""
        job_id = f"export_{int(time.time())}"
        path = self.export_dir / f"{name}_{job_id}.jsonl"

        lines = []
        for item in data:
            lines.append(json.dumps(item, ensure_ascii=False))
        content = "\n".join(lines) + "\n"
        path.write_text(content, encoding="utf-8")

        job = ExportJob(
            job_id=job_id,
            format="jsonl",
            created_at=time.time(),
            record_count=len(data),
            size_bytes=len(content.encode("utf-8")),
            file_path=str(path),
        )
        self._jobs[job_id] = job
        return job

    def export_csv(self, data: list[dict], name: str = "export") -> ExportJob:
        """Export data as CSV."""
        job_id = f"export_{int(time.time())}"
        path = self.export_dir / f"{name}_{job_id}.csv"

        if not data:
            content = ""
        else:
            output = io.StringIO()
            fieldnames = list(data[0].keys())
            writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data)
            content = output.getvalue()

        path.write_text(content, encoding="utf-8")

        job = ExportJob(
            job_id=job_id,
            format="csv",
            created_at=time.time(),
            record_count=len(data),
            size_bytes=len(content.encode("utf-8")),
            file_path=str(path),
        )
        self._jobs[job_id] = job
        return job

    def import_json(self, file_path: str | Path) -> list[dict]:
        """Import data from JSON file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def import_jsonl(self, file_path: str | Path) -> list[dict]:
        """Import data from JSONL file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        results = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                results.append(json.loads(line))
        return results

    def import_csv(self, file_path: str | Path) -> list[dict]:
        """Import data from CSV file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def list_exports(self) -> list[dict]:
        return [
            {
                "job_id": j.job_id,
                "format": j.format,
                "created_at": j.created_at,
                "record_count": j.record_count,
                "size_bytes": j.size_bytes,
                "status": j.status,
            }
            for j in sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)
        ]
