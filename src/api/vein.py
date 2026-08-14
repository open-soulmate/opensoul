"""OpenVein API — 血管系统：大文件分片上传、缓存管理、资源同步。"""

import hashlib
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.vein.file_store import FileStore
from src.vein.cache import CacheManager
from src.vein.chunked import ChunkedUploader

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
store = FileStore()
cache = CacheManager(max_size_mb=256, default_ttl=3600)
uploader = ChunkedUploader()


# ── Request Schemas ────────────────────────────────────────

class CachePutRequest(BaseModel):
    key: str
    data: str  # base64 encoded
    ttl: int | None = None


class UploadSessionCreate(BaseModel):
    filename: str
    total_size: int
    chunk_size: int | None = None
    mime_type: str = "application/octet-stream"
    tags: list[str] | None = None


class ChunkUpload(BaseModel):
    chunk_index: int
    data: str  # base64 encoded


# ── File Store Endpoints ───────────────────────────────────

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    tags: str = Form(default=""),
):
    """Upload a single file (small-medium size). Content-addressable dedup."""
    data = await file.read()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    meta = store.store(
        data=data,
        name=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        tags=tag_list,
    )

    # Auto-cache for hot access
    cache.put(f"file:{meta.file_id}", data)

    return {
        "file_id": meta.file_id,
        "name": meta.name,
        "content_hash": meta.content_hash,
        "size": meta.size,
        "mime_type": meta.mime_type,
        "tags": tag_list,
    }


@router.get("/files")
async def list_files(
    name: str | None = Query(None, description="Filter by name (substring)"),
    tag: str | None = Query(None, description="Filter by tag"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List stored files with optional filters."""
    files = store.list_files(name_filter=name, tag=tag, limit=limit, offset=offset)
    return {
        "files": [
            {
                "file_id": f.file_id,
                "name": f.name,
                "size": f.size,
                "mime_type": f.mime_type,
                "tags": f.tags.split(",") if f.tags else [],
                "created_at": f.created_at,
                "content_hash": f.content_hash,
            }
            for f in files
        ],
        "count": len(files),
    }


@router.get("/files/{file_id}")
async def get_file_meta(file_id: str):
    """Get file metadata by ID."""
    meta = store.get_meta(file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "file_id": meta.file_id,
        "name": meta.name,
        "size": meta.size,
        "mime_type": meta.mime_type,
        "tags": meta.tags.split(",") if meta.tags else [],
        "content_hash": meta.content_hash,
        "created_at": meta.created_at,
    }


@router.get("/files/{file_id}/download")
async def download_file(file_id: str):
    """Download a file. Streams from cache if available."""
    # Check cache first
    cached = cache.get(f"file:{file_id}")
    if cached:
        meta = store.get_meta(file_id)
        return StreamingResponse(
            iter([cached]),
            media_type=meta.mime_type if meta else "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{meta.name if meta else file_id}"'},
        )

    # Stream from disk
    blob_path, meta = store.stream_retrieve(file_id)
    if not blob_path or not meta:
        raise HTTPException(status_code=404, detail="File not found")

    def iter_file():
        with open(blob_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=meta.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{meta.name}"'},
    )


@router.delete("/files/{file_id}")
async def delete_file(file_id: str):
    """Delete a file and its cache entry."""
    if not store.delete(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    cache.invalidate(f"file:{file_id}")
    return {"status": "ok", "file_id": file_id}


# ── Chunked Upload Endpoints ───────────────────────────────

@router.post("/upload/chunked/init")
async def init_chunked_upload(body: UploadSessionCreate):
    """Initialize a chunked upload session."""
    session = uploader.create_session(
        filename=body.filename,
        total_size=body.total_size,
        chunk_size=body.chunk_size,
        mime_type=body.mime_type,
        tags=body.tags,
    )
    return session.to_dict()


@router.post("/upload/chunked/{upload_id}/chunk/{chunk_index}")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    file: UploadFile = File(...),
):
    """Upload a single chunk."""
    session = uploader.get_session(upload_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    data = await file.read()
    updated = uploader.upload_chunk(upload_id, chunk_index, data)
    if not updated:
        raise HTTPException(status_code=400, detail="Failed to store chunk")

    return updated.to_dict()


@router.post("/upload/chunked/{upload_id}/complete")
async def complete_chunked_upload(upload_id: str):
    """Complete a chunked upload — reassemble and store."""
    session = uploader.get_session(upload_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")
    if not session.is_complete:
        raise HTTPException(
            status_code=400,
            detail=f"Upload incomplete: {session.progress}% ({len(session.received_chunks)}/{session.total_chunks} chunks)",
        )

    result = uploader.assemble(upload_id)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to assemble chunks")

    data, sess = result
    meta = store.store(
        data=data,
        name=sess.filename,
        mime_type=sess.mime_type,
        tags=sess.tags,
    )

    # Auto-cache
    cache.put(f"file:{meta.file_id}", data)

    # Cleanup temp chunks
    uploader.cleanup(upload_id)

    return {
        "status": "ok",
        "file_id": meta.file_id,
        "name": meta.name,
        "content_hash": meta.content_hash,
        "size": meta.size,
        "elapsed_seconds": sess.elapsed_seconds,
    }


@router.get("/upload/chunked/{upload_id}")
async def get_upload_status(upload_id: str):
    """Get the status of a chunked upload session."""
    session = uploader.get_session(upload_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return session.to_dict()


@router.get("/upload/chunked")
async def list_uploads():
    """List all active upload sessions."""
    return {"sessions": uploader.list_sessions()}


@router.delete("/upload/chunked/{upload_id}")
async def cancel_upload(upload_id: str):
    """Cancel and cleanup an upload session."""
    if not uploader.cleanup(upload_id):
        raise HTTPException(status_code=404, detail="Upload session not found")
    return {"status": "ok", "upload_id": upload_id}


# ── Cache Endpoints ────────────────────────────────────────

@router.get("/cache/stats")
async def get_cache_stats():
    """Get cache statistics."""
    return cache.stats


@router.get("/cache/{key}")
async def get_cached(key: str):
    """Get a cached value by key."""
    data = cache.get(key)
    if data is None:
        raise HTTPException(status_code=404, detail="Cache miss or expired")
    return {"key": key, "size": len(data), "data": data.hex()[:100] + "..."}


@router.put("/cache")
async def put_cache(body: CachePutRequest):
    """Put a value into the cache."""
    import base64
    try:
        data = base64.b64decode(body.data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 data")
    cache.put(body.key, data, ttl=body.ttl)
    return {"status": "ok", "key": body.key, "size": len(data)}


@router.delete("/cache/{key}")
async def invalidate_cache(key: str):
    """Invalidate a specific cache entry."""
    if not cache.invalidate(key):
        raise HTTPException(status_code=404, detail="Key not in cache")
    return {"status": "ok", "key": key}


@router.post("/cache/clear")
async def clear_cache():
    """Clear all cache entries."""
    count = cache.clear()
    return {"status": "ok", "cleared": count}


@router.post("/cache/cleanup")
async def cleanup_cache():
    """Remove expired cache entries."""
    count = cache.cleanup_expired()
    return {"status": "ok", "removed": count}


# ── Stats ──────────────────────────────────────────────────

@router.get("/stats")
async def get_stats():
    """Get overall OpenVein statistics."""
    return {
        "store": store.stats(),
        "cache": cache.stats,
        "uploads": {
            "active_sessions": len(uploader.list_sessions()),
        },
    }
