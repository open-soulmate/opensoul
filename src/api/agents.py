"""Agent detection & install API with progress tracking."""

import asyncio
import json
import logging
import os
import platform
import shutil
from pathlib import Path
import subprocess
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.user import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

# Shared skills directory
SHARED_SKILLS_DIR = Path.home() / ".openmate" / "shared-skills"

# Agent skill directory mapping
AGENT_SKILL_DIRS = {
    "hermes": Path.home() / ".hermes" / "skills",
    "mimo": Path.home() / ".config" / "mimo" / "skills",
    "opencode": Path.home() / ".config" / "opencode" / "skills",
    "claude": Path.home() / ".claude" / "skills",
    "aider": Path.home() / ".aider" / "skills",
}

def _get_os() -> str:
    system = platform.system().lower()
    if system == "darwin": return "darwin"
    elif system == "windows": return "win32"
    return "linux"

# Full agent registry - 20+ agents
AGENT_REGISTRY = {
    "hermes": {"name": "Hermes Agent", "binary": "hermes", "description": "Nous Research Hermes Agent", "icon": "🏛️",
               "install": {"linux": "pip install hermes-agent", "darwin": "pip install hermes-agent", "win32": "pip install hermes-agent"}},
    "mimo": {"name": "MiMo Code", "binary": "mimo", "description": "Xiaomi MiMo Code CLI", "icon": "📱",
             "install": {"linux": "npm install -g @anthropic-ai/claude-code", "darwin": "npm install -g @anthropic-ai/claude-code", "win32": "npm install -g @anthropic-ai/claude-code"}},
    "opencode": {"name": "OpenCode", "binary": "opencode", "description": "Open source coding agent", "icon": "⚡",
                 "install": {"linux": "go install github.com/opencode-ai/opencode@latest", "darwin": "go install github.com/opencode-ai/opencode@latest", "win32": "go install github.com/opencode-ai/opencode@latest"}},
    "claude": {"name": "Claude Code", "binary": "claude", "description": "Anthropic Claude Code CLI", "icon": "🟣",
               "install": {"linux": "npm install -g @anthropic-ai/claude-code", "darwin": "npm install -g @anthropic-ai/claude-code", "win32": "npm install -g @anthropic-ai/claude-code"}},
    "codex": {"name": "Codex CLI", "binary": "codex", "description": "OpenAI Codex CLI", "icon": "🟢",
              "install": {"linux": "npm install -g @openai/codex", "darwin": "npm install -g @openai/codex", "win32": "npm install -g @openai/codex"}},
    "gemini": {"name": "Gemini CLI", "binary": "gemini", "description": "Google Gemini CLI", "icon": "🔵",
               "install": {"linux": "npm install -g @google/gemini-cli", "darwin": "npm install -g @google/gemini-cli", "win32": "npm install -g @google/gemini-cli"}},
    "qwen": {"name": "Qwen CLI", "binary": "qwen", "description": "Alibaba Qwen CLI", "icon": "🟠",
             "install": {"linux": "pip install qwen-cli", "darwin": "pip install qwen-cli", "win32": "pip install qwen-cli"}},
    "cursor": {"name": "Cursor Agent", "binary": "cursor", "description": "Cursor AI coding agent", "icon": "▶️",
               "install": {"linux": "https://cursor.sh", "darwin": "https://cursor.sh", "win32": "https://cursor.sh"}},
    "copilot": {"name": "GitHub Copilot", "binary": "gh", "description": "GitHub Copilot CLI", "icon": "🐙",
                "install": {"linux": "gh extension install github/gh-copilot", "darwin": "gh extension install github/gh-copilot", "win32": "gh extension install github/gh-copilot"}},
    "deepseek": {"name": "DeepSeek CLI", "binary": "deepseek", "description": "DeepSeek AI CLI", "icon": "🐋",
                 "install": {"linux": "pip install deepseek-cli", "darwin": "pip install deepseek-cli", "win32": "pip install deepseek-cli"}},
    "aider": {"name": "Aider", "binary": "aider", "description": "AI pair programming in your terminal", "icon": "🤝",
              "install": {"linux": "pip install aider-chat", "darwin": "pip install aider-chat", "win32": "pip install aider-chat"}},
    "continue": {"name": "Continue", "binary": "continue", "description": "Continue dev - open source AI code assistant", "icon": "🔄",
                 "install": {"linux": "https://continue.dev", "darwin": "https://continue.dev", "win32": "https://continue.dev"}},
    "windsurf": {"name": "Windsurf", "binary": "windsurf", "description": "Windsurf AI coding agent", "icon": "🏄",
                 "install": {"linux": "https://windsurf.ai", "darwin": "https://windsurf.ai", "win32": "https://windsurf.ai"}},
    "cline": {"name": "Cline", "binary": "cline", "description": "Cline AI coding assistant", "icon": "🔧",
              "install": {"linux": "VS Code extension", "darwin": "VS Code extension", "win32": "VS Code extension"}},
    "roo": {"name": "Roo Code", "binary": "roo", "description": "Roo Code AI assistant", "icon": "🦘",
            "install": {"linux": "VS Code extension", "darwin": "VS Code extension", "win32": "VS Code extension"}},
    "kilo": {"name": "Kilo Code", "binary": "kilo", "description": "Kilo Code AI assistant", "icon": "⚡",
             "install": {"linux": "VS Code extension", "darwin": "VS Code extension", "win32": "VS Code extension"}},
    "kiro": {"name": "Kiro", "binary": "kiro", "description": "AWS Kiro AI IDE", "icon": "🎯",
             "install": {"linux": "https://kiro.dev", "darwin": "https://kiro.dev", "win32": "https://kiro.dev"}},
    "amazon-q": {"name": "Amazon Q", "binary": "q", "description": "Amazon Q Developer CLI", "icon": "☁️",
                 "install": {"linux": "https://aws.amazon.com/q/developer/", "darwin": "https://aws.amazon.com/q/developer/", "win32": "https://aws.amazon.com/q/developer/"}},
    "tabby": {"name": "Tabby", "binary": "tabby", "description": "Tabby AI coding assistant", "icon": "📋",
              "install": {"linux": "https://tabby.tabbyml.com", "darwin": "https://tabby.tabbyml.com", "win32": "https://tabby.tabbyml.com"}},
    "devin": {"name": "Devin", "binary": "devin", "description": "Devin AI software engineer", "icon": "🤖",
              "install": {"linux": "https://devin.ai", "darwin": "https://devin.ai", "win32": "https://devin.ai"}},
}

_install_tasks: Dict[str, dict] = {}


@router.get("/detect")
async def detect_agents(user_id: UUID = Depends(get_current_user)):
    """检测本机安装的AI Agent"""
    os_name = _get_os()
    result = []
    for agent_id, info in AGENT_REGISTRY.items():
        path = shutil.which(info["binary"])
        version = None
        if path:
            try:
                r = subprocess.run([info["binary"], "--version"], capture_output=True, text=True, timeout=2, env={**os.environ, "NO_COLOR": "1"})
                version = r.stdout.strip()[:50] or None
            except Exception:
                pass
        # Auto-takeover: symlink agent skills dir to shared dir
        skills_managed = False
        if path and agent_id in AGENT_SKILL_DIRS:
            agent_skills = AGENT_SKILL_DIRS[agent_id]
            SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            if agent_skills.exists() and agent_skills.is_symlink():
                # Already managed
                skills_managed = agent_skills.resolve() == SHARED_SKILLS_DIR
            elif agent_skills.exists() and not agent_skills.is_symlink():
                # Migrate existing skills to shared, then symlink
                for item in agent_skills.iterdir():
                    dest = SHARED_SKILLS_DIR / item.name
                    if not dest.exists():
                        shutil.copytree(item, dest) if item.is_dir() else shutil.copy2(item, dest)
                shutil.rmtree(agent_skills)
                agent_skills.symlink_to(SHARED_SKILLS_DIR)
                skills_managed = True
                logger.info(f"Migrated {agent_id} skills to shared dir")
            elif not agent_skills.exists():
                # Create symlink to shared dir
                agent_skills.parent.mkdir(parents=True, exist_ok=True)
                agent_skills.symlink_to(SHARED_SKILLS_DIR)
                skills_managed = True
                logger.info(f"Linked {agent_id} skills to shared dir")

        result.append({
            "id": agent_id, "name": info["name"], "binary": info["binary"],
            "description": info["description"], "icon": info["icon"],
            "available": path is not None, "version": version, "path": path,
            "installCommand": info["install"].get(os_name), "os": os_name,
            "skillsManaged": skills_managed,
        })
    return {"os": os_name, "agents": result}


class InstallRequest(BaseModel):
    agent_id: str


@router.post("/install")
async def start_install(req: InstallRequest, user_id: UUID = Depends(get_current_user)):
    """Start installing an agent in background."""
    agent_id = req.agent_id
    if agent_id not in AGENT_REGISTRY:
        return {"success": False, "error": f"Unknown agent: {agent_id}"}

    agent = AGENT_REGISTRY[agent_id]
    os_name = _get_os()
    cmd = agent["install"].get(os_name)
    if not cmd:
        return {"success": False, "error": f"No install command for {os_name}"}

    if agent_id in _install_tasks and _install_tasks[agent_id]["status"] == "running":
        return {"success": True, "task_id": agent_id, "status": "already_running"}

    _install_tasks[agent_id] = {"status": "running", "progress": 0, "output": [], "error": None}
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
        result[agent_id] = {"status": task["status"], "progress": task["progress"], "line_count": len(task["output"]), "error": task.get("error")}
    return result


async def _run_install(agent_id: str, cmd: str):
    """Run install command in background with progress tracking."""
    task = _install_tasks[agent_id]
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        while True:
            line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                task["output"].append(text)
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


class UninstallRequest(BaseModel):
    agent_id: str


@router.post("/uninstall")
async def uninstall_agent(req: UninstallRequest, user_id: UUID = Depends(get_current_user)):
    """Uninstall an agent using package manager"""
    if req.agent_id not in AGENT_REGISTRY:
        return {"success": False, "error": f"Unknown agent: {req.agent_id}"}
    agent = AGENT_REGISTRY[req.agent_id]
    binary = agent["binary"]
    path = shutil.which(binary)
    if not path:
        return {"success": False, "error": "Agent not installed"}
    
    # Try common uninstall commands
    cmds = []
    if shutil.which("npm"):
        cmds.append(f"npm uninstall -g {binary}")
    if shutil.which("pip"):
        cmds.append(f"pip uninstall -y {binary}")
    if shutil.which("pip3"):
        cmds.append(f"pip3 uninstall -y {binary}")
    
    for cmd in cmds:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0:
                return {"success": True, "output": stdout.decode()[-300:]}
        except Exception:
            continue
    
    return {"success": False, "error": "Could not uninstall. Try manually: " + path}


class UpdateRequest(BaseModel):
    agent_id: str


@router.post("/update")
async def update_agent(req: UpdateRequest, user_id: UUID = Depends(get_current_user)):
    """Update an agent - uses install command which typically upgrades"""
    if req.agent_id not in AGENT_REGISTRY:
        return {"success": False, "error": f"Unknown agent: {req.agent_id}"}
    
    agent = AGENT_REGISTRY[req.agent_id]
    os_name = _get_os()
    cmd = agent["install"].get(os_name)
    if not cmd:
        return {"success": False, "error": f"No update command for {os_name}"}
    
    # Check if already updating
    if req.agent_id in _install_tasks and _install_tasks[req.agent_id]["status"] == "running":
        return {"success": True, "task_id": req.agent_id, "status": "already_running"}
    
    _install_tasks[req.agent_id] = {"status": "running", "progress": 0, "output": [], "error": None}
    asyncio.create_task(_run_install(req.agent_id, cmd))
    return {"success": True, "task_id": req.agent_id, "status": "started"}
