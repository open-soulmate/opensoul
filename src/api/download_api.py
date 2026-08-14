"""Download plugin management API.

Endpoints for managing download plugins, triggering downloads with resume/P2P,
and auto-updating plugins.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.user import get_current_user
from src.api.download_plugins import get_download_manager, DownloadProgress

router = APIRouter()
logger = logging.getLogger(__name__)

# Cache dir for downloaded packages
CACHE_DIR = Path.home() / ".openmate" / "download-cache"


# ─── Plugin Management ───────────────────────────────────────────

@router.get("/plugins")
async def list_plugins():
    """List all download plugins and their status"""
    dm = get_download_manager()
    plugins = dm.list_plugins()
    return {
        "plugins": [
            {
                "id": p.id, "name": p.name, "description": p.description,
                "version": p.version, "status": p.status.value,
                "supports_resume": p.supports_resume, "supports_p2p": p.supports_p2p,
                "priority": p.priority,
            }
            for p in plugins
        ]
    }


@router.post("/plugins/{plugin_id}/install")
async def install_plugin(plugin_id: str):
    """Install a download plugin"""
    dm = get_download_manager()
    success = await dm.install_plugin(plugin_id)
    if success:
        return {"success": True, "message": f"Plugin {plugin_id} installed"}
    raise HTTPException(status_code=400, detail=f"Failed to install plugin {plugin_id}")


@router.post("/plugins/{plugin_id}/update")
async def update_plugin(plugin_id: str):
    """Update a specific plugin"""
    dm = get_download_manager()
    plugin = dm.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    if not plugin.is_available():
        raise HTTPException(status_code=400, detail="Plugin not installed")
    success = await plugin.update()
    return {"success": success}


@router.post("/plugins/update-all")
async def update_all_plugins():
    """Auto-update all installed plugins"""
    dm = get_download_manager()
    results = await dm.auto_update_plugins()
    return {"results": results}


class BatchPluginRequest(BaseModel):
    action: str  # 'install' | 'uninstall' | 'update'
    plugin_ids: list[str]


@router.post("/plugins/batch")
async def batch_plugin_action(req: BatchPluginRequest):
    """Batch install/uninstall/update plugins"""
    dm = get_download_manager()
    results = {}

    for plugin_id in req.plugin_ids:
        plugin = dm.get_plugin(plugin_id)
        if not plugin:
            results[plugin_id] = {"success": False, "error": "Plugin not found"}
            continue

        try:
            if req.action == "install":
                if plugin.is_available():
                    results[plugin_id] = {"success": True, "message": "Already installed"}
                else:
                    success = await plugin.install()
                    results[plugin_id] = {"success": success}

            elif req.action == "update":
                if not plugin.is_available():
                    results[plugin_id] = {"success": False, "error": "Not installed"}
                else:
                    success = await plugin.update()
                    results[plugin_id] = {"success": success}

            elif req.action == "uninstall":
                if not plugin.is_available():
                    results[plugin_id] = {"success": True, "message": "Not installed"}
                else:
                    info = plugin.get_info()
                    import platform
                    os_name = "darwin" if platform.system() == "Darwin" else "linux"
                    # Construct uninstall command from binary name
                    if os_name == "darwin":
                        cmd = f"brew uninstall {info.binary} 2>/dev/null || true"
                    else:
                        cmd = f"sudo pacman -R --noconfirm {info.binary} 2>/dev/null || sudo apt remove -y {info.binary} 2>/dev/null || true"
                    proc = await asyncio.create_subprocess_shell(cmd)
                    await proc.wait()
                    results[plugin_id] = {"success": True, "message": "Uninstalled"}
            else:
                results[plugin_id] = {"success": False, "error": f"Unknown action: {req.action}"}
        except Exception as e:
            logger.error(f"Batch {req.action} failed for {plugin_id}: {e}")
            results[plugin_id] = {"success": False, "error": str(e)}

    return {"results": results}


@router.get("/plugins/{plugin_id}/version")
async def get_plugin_version(plugin_id: str):
    """Get installed version of a plugin"""
    dm = get_download_manager()
    plugin = dm.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    version = await plugin.get_version()
    return {"id": plugin_id, "version": version, "available": plugin.is_available()}


# ─── Download with Resume/P2P ────────────────────────────────────

class DownloadRequest(BaseModel):
    url: str
    dest: Optional[str] = None  # defaults to cache dir
    resume: bool = True
    plugin_id: Optional[str] = None  # auto-select if None


@router.post("/download")
async def start_download(req: DownloadRequest):
    """Start a download with resume support"""
    dm = get_download_manager()

    # Determine destination
    if req.dest:
        dest = req.dest
    else:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        filename = req.url.split("/")[-1].split("?")[0] or "download"
        dest = str(CACHE_DIR / filename)

    # Start download in background
    task_id = f"dl-{hash(req.url) % 100000:05d}"

    async def do_download():
        return await dm.download(req.url, dest, resume=req.resume, plugin_id=req.plugin_id)

    asyncio.create_task(do_download())
    return {"task_id": task_id, "dest": dest, "url": req.url, "status": "started"}


@router.post("/download/sync")
async def download_sync(req: DownloadRequest):
    """Synchronous download (waits for completion)"""
    dm = get_download_manager()

    if req.dest:
        dest = req.dest
    else:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        filename = req.url.split("/")[-1].split("?")[0] or "download"
        dest = str(CACHE_DIR / filename)

    result = await dm.download(req.url, dest, resume=req.resume, plugin_id=req.plugin_id)
    return {
        "status": result.status, "dest": result.dest, "url": result.url,
        "total_bytes": result.total_bytes, "downloaded_bytes": result.downloaded_bytes,
        "plugin": result.plugin, "supports_resume": result.supports_resume,
        "error": result.error,
    }


@router.get("/download/progress")
async def download_progress_stream():
    """SSE stream for download progress (all active downloads)"""
    dm = get_download_manager()

    async def stream():
        while True:
            # In a real impl, this would track active downloads
            await asyncio.sleep(1)
            yield f"data: {json.dumps({'status': 'idle'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ─── Cache Management ────────────────────────────────────────────

@router.get("/cache")
async def list_cache():
    """List cached downloads"""
    if not CACHE_DIR.exists():
        return {"files": [], "total_size": 0}

    files = []
    total_size = 0
    for f in CACHE_DIR.iterdir():
        if f.is_file() and not f.name.endswith(".aria2"):
            size = f.stat().st_size
            total_size += size
            files.append({
                "name": f.name, "path": str(f), "size": size,
                "modified": f.stat().st_mtime,
            })

    return {"files": sorted(files, key=lambda x: x["modified"], reverse=True), "total_size": total_size}


@router.delete("/cache")
async def clear_cache():
    """Clear download cache"""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return {"success": True}


@router.delete("/cache/{filename}")
async def delete_cache_file(filename: str):
    """Delete a specific cached file"""
    filepath = CACHE_DIR / filename
    if filepath.exists():
        filepath.unlink()
        return {"success": True}
    raise HTTPException(status_code=404, detail="File not found")
