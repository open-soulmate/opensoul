"""OpenMarrow API — 骨髓系统：备份恢复、数据迁移。"""

import os
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel

from src.marrow.backup import BackupManager
from src.marrow.migrator import DataMigrator
from src.nerve.event_bridge import push_event

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
backup_manager = BackupManager()
migrator = DataMigrator()


# ── Request Schemas ────────────────────────────────────────

class BackupCreateRequest(BaseModel):
    source_dirs: list[str]
    name: str = ""
    description: str = ""
    tags: list[str] = []


class RestoreRequest(BaseModel):
    backup_id: str
    target_dir: str


class ExportRequest(BaseModel):
    data: list[dict]
    format: str = "json"  # "json", "jsonl", "csv"
    name: str = "export"


# ── Backup Endpoints ───────────────────────────────────────

@router.post("/backup")
async def create_backup(req: BackupCreateRequest):
    """Create a backup snapshot of specified directories."""
    # Validate paths exist
    valid_dirs = []
    for d in req.source_dirs:
        expanded = os.path.expanduser(d)
        if os.path.exists(expanded):
            valid_dirs.append(expanded)
    if not valid_dirs:
        raise HTTPException(400, "No valid source directories found")

    manifest = backup_manager.create_backup(
        source_dirs=valid_dirs,
        name=req.name,
        description=req.description,
        tags=req.tags,
    )

    push_event({
        "organ": "marrow", "emoji": "🦴", "type": "backup_created",
        "summary": f"💾 Backup created: {manifest.name} ({manifest.file_count} files)",
        "detail": {"backup_id": manifest.backup_id, "name": manifest.name, "file_count": manifest.file_count},
    })

    return {
        "backup_id": manifest.backup_id,
        "name": manifest.name,
        "size_bytes": manifest.size_bytes,
        "file_count": manifest.file_count,
        "checksum": manifest.checksum,
    }


@router.get("/backups")
async def list_backups():
    """List all backups."""
    return {"backups": backup_manager.list_backups()}


@router.get("/backups/{backup_id}")
async def get_backup(backup_id: str):
    """Get backup details."""
    result = backup_manager.get_backup(backup_id)
    if not result:
        raise HTTPException(404, "Backup not found")
    return result


@router.delete("/backups/{backup_id}")
async def delete_backup(backup_id: str):
    """Delete a backup."""
    result = backup_manager.delete_backup(backup_id)
    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result


@router.post("/restore/{backup_id}")
async def restore_backup(backup_id: str, req: RestoreRequest | None = None):
    """Restore a backup to target directory."""
    target = (req.target_dir if req else None) or os.path.expanduser("~/.opensoul/restore")
    result = backup_manager.restore_backup(backup_id, target)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    push_event({
        "organ": "marrow", "emoji": "🦴", "type": "backup_restored",
        "summary": f"♻️ Backup restored: {backup_id}",
        "detail": {"backup_id": backup_id, "target_dir": target},
    })
    return result


# ── Data Migration Endpoints ───────────────────────────────

@router.post("/export")
async def export_data(req: ExportRequest):
    """Export data in specified format (json/jsonl/csv)."""
    if not req.data:
        raise HTTPException(400, "No data to export")

    if req.format == "json":
        job = migrator.export_json(req.data, req.name)
    elif req.format == "jsonl":
        job = migrator.export_jsonl(req.data, req.name)
    elif req.format == "csv":
        job = migrator.export_csv(req.data, req.name)
    else:
        raise HTTPException(400, f"Unsupported format: {req.format}")

    return {
        "job_id": job.job_id,
        "format": job.format,
        "record_count": job.record_count,
        "size_bytes": job.size_bytes,
        "file_path": job.file_path,
    }


@router.post("/import")
async def import_data(
    file: UploadFile = File(...),
    format: str = Form(default="json"),
):
    """Import data from uploaded file."""
    import tempfile
    import os

    suffix = {"json": ".json", "jsonl": ".jsonl", "csv": ".csv"}.get(format, ".txt")
    data = await file.read()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        if format == "json":
            records = migrator.import_json(tmp_path)
        elif format == "jsonl":
            records = migrator.import_jsonl(tmp_path)
        elif format == "csv":
            records = migrator.import_csv(tmp_path)
        else:
            raise HTTPException(400, f"Unsupported format: {format}")
    finally:
        os.unlink(tmp_path)

    return {"records": len(records), "data": records}


@router.get("/exports")
async def list_exports():
    """List export jobs."""
    return {"exports": migrator.list_exports()}


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def marrow_health():
    """OpenMarrow health check."""
    return {
        "status": "ok",
        "component": "OpenMarrow",
        "backup": backup_manager.stats(),
        "export_dir": str(migrator.export_dir),
    }
