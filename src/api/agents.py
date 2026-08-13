"""Agent detection & install API with progress tracking."""

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.user import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

# Known agents with platform-specific install commands
AGENT_REGISTRY = {
    "hermes": {
        "name": "Hermes Agent",
        "binary": "hermes",
        "description": "Nous Research Hermes Agent",
        "icon": "🏛️",
        "install": {
            "linux": "pip install hermes-agent",
            "darwin": "pip install hermes-agent",
            "win32": "pip install hermes-agent",
        },
    },
    "mimo": {
        "name": "MiMo Code",
        "binary": "mimo",
        "description": "Xiaomi MiMo Code CLI",
        "icon": "📱",
        "install": {
            "linux": "npm install -g @anthropic-ai/claude-code",
            "darwin": "npm install -g @anthropic-ai/claude-code",
            "win32": "npm install -g @anthropic-ai/claude-code",
        },
    },
    "opencode": {
        "name": "OpenCode",
        "binary": "opencode",
        "description": "Open source coding agent",
        "icon": "⚡",
        "install": {
            "linux": "go install github.com/opencode-ai/opencode@latest",
            "darwin": "go install github.com/opencode-ai/opencode@latest",
            "win32": "go install github.com/opencode-ai/opencode@latest",
        },
    },
    "claude": {
        "name": "Claude Code",
        "binary": "claude",
        "description": "Anthropic Claude Code CLI",
        "icon": "🟣",
        "install": {
            "linux": "npm install -g @anthropic-ai/claude-code",
            "darwin": "npm install -g @anthropic-ai/claude-code",
            "win32": "npm install -g @anthropic-ai/claude-code",
        },
    },
    "aider": {
        "name": "Aider",
        "binary": "aider",
        "description": "AI pair programming in your terminal",
        "icon": "🤝",
        "install": {
            "linux": "pip install aider-chat",
            "darwin": "pip install aider-chat",
            "win32": "pip install aider-chat",
        },
    },
    "deepseek": {
        "name": "DeepSeek CLI",
        "binary": "deepseek",
        "description": "DeepSeek AI CLI",
        "icon": "🐋",
        "install": {
            "linux": "pip install deepseek-cli",
            "darwin": "pip install deepseek-cli",
            "win32": "pip install deepseek-cli",
        },
    },
}

# Track running installations
_install_tasks: Dict[str, dict] = {}


def _get_os() -> str:
    """Detect current OS."""
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    elif system == "windows":
        return "win32"
    return "linux"


@router.get("/detect")
async def detect_agents(user_id: UUID = Depends(get_current_user)):
    """检测本机安装的AI Agent，返回OS和安装状态"""
    os_name = _get_os()
    result = []
    for agent_id, info in AGENT_REGISTRY.items():
        path = shutil.which(info["binary"])
        # Get version if installed
        version = None
        if path:
            try:
                r = subprocess.run([info["binary"], "--version"], capture_output=True, text=True, timeout=5)
                version = r.stdout.strip()[:50] or None
            except Exception:
                pass
        result.append({
            "id": agent_id,
            "name": info["name"],
            "binary": info["binary"],
            "description": info["description"],
            "icon": info["icon"],
            "available": path is not None,
            "version": version,
            "path": path,
            "installCommand": info["install"].get(os_name),
            "os": os_name,
        })
    return {"os": os_name, "agents": result}


class InstallRequest(BaseModel):
    agent_id: str


@router.post("/install")
async def start_install(req: InstallRequest, user_id: UUID = Depends(get_current_user)):
    """Start installing an agent in background. Returns task_id for progress tracking."""
    agent_id = req.agent_id
    if agent_id not in AGENT_REGISTRY:
        return {"success": False, "error": f"Unknown agent: {agent_id}"}

    agent = AGENT_REGISTRY[agent_id]
    os_name = _get_os()
    cmd = agent["install"].get(os_name)
    if not cmd:
        return {"success": False, "error": f"No install command for {os_name}"}

    # Check if already installing
    if agent_id in _install_tasks and _install_tasks[agent_id]["status"] == "running":
        return {"success": True, "task_id": agent_id, "status": "already_running"}

    # Start background task
    _install_tasks[agent_id] = {
        "status": "running",
        "progress": 0,
        "output": [],
        "error": None,
    }

    asyncio.create_task(_run_install(agent_id, cmd))
    return {"success": True, "task_id": agent_id, "status": "started"}


@router.get("/install/{agent_id}/progress")
async def get_install_progress(agent_id: str, user_id: UUID = Depends(get_current_user)):
    """SSE endpoint for real-time install progress."""
    if agent_id not in AGENT_REGISTRY:
        return {"error": "Unknown agent"}

    async def event_stream():
        last_idx = 0
        while True:
            task = _install_tasks.get(agent_id)
            if not task:
                yield f"data: {json.dumps({'status': 'unknown', 'error': 'No install task found'})}\n\n"
                break

            # Send new output lines
            output_lines = task["output"]
            if len(output_lines) > last_idx:
                for line in output_lines[last_idx:]:
                    yield f"data: {json.dumps({'status': 'running', 'line': line, 'progress': task['progress']})}\n\n"
                last_idx = len(output_lines)

            if task["status"] in ("done", "error"):
                yield f"data: {json.dumps({'status': task['status'], 'error': task.get('error'), 'progress': 100 if task['status'] == 'done' else task['progress']})}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/install/status")
async def get_all_install_status(user_id: UUID = Depends(get_current_user)):
    """Get status of all running/recent installations."""
    result = {}
    for agent_id, task in _install_tasks.items():
        result[agent_id] = {
            "status": task["status"],
            "progress": task["progress"],
            "line_count": len(task["output"]),
            "error": task.get("error"),
        }
    return result


async def _run_install(agent_id: str, cmd: str):
    """Run install command in background with progress tracking."""
    task = _install_tasks[agent_id]
    try:
        # Parse npm/pip progress patterns
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        while True:
            line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                task["output"].append(text)
                # Estimate progress from npm/pip output
                if "added" in text.lower() or "installed" in text.lower() or "successfully" in text.lower():
                    task["progress"] = 90
                elif "downloading" in text.lower() or "fetching" in text.lower():
                    task["progress"] = min(task["progress"] + 10, 80)
                elif "building" in text.lower() or "compiling" in text.lower():
                    task["progress"] = min(task["progress"] + 5, 85)
                else:
                    task["progress"] = min(task["progress"] + 2, 75)

        await proc.wait()
        if proc.returncode == 0:
            task["status"] = "done"
            task["progress"] = 100
        else:
            task["status"] = "error"
            task["error"] = f"Exit code {proc.returncode}"
    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
