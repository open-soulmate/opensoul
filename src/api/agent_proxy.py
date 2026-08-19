"""Agent proxy API - routes messages to local CLI agents (Hermes, MiMo, etc.)."""

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()
@router.get("/health")
async def agent_proxy_health():
    """AgentProxy health check."""
    return {"status": "ok", "component": "AgentProxy"}

# Registry of CLI agents and their commands
AGENT_REGISTRY = {
    "hermes": {
        "name": "Hermes Agent",
        "binary": "hermes",
        "args": ["-z"],  # hermes -z "prompt"
        "description": "Nous Research Hermes Agent",
    },
    "mimo": {
        "name": "MiMo Code",
        "binary": "mimo",
        "args": ["run", "--prompt"],  # mimo run --prompt "prompt"
        "description": "Xiaomi MiMo Code CLI",
    },
    "claude": {
        "name": "Claude Code",
        "binary": "claude",
        "args": ["-p"],  # claude -p "prompt"
        "description": "Anthropic Claude Code CLI",
    },
    "codex": {
        "name": "Codex CLI",
        "binary": "codex",
        "args": ["-q"],  # codex -q "prompt"
        "description": "OpenAI Codex CLI",
    },
    "aider": {
        "name": "Aider",
        "binary": "aider",
        "args": ["--message"],  # aider --message "prompt"
        "description": "AI pair programming",
    },
}


class AgentMessage(BaseModel):
    agent_id: str
    message: str


class AgentResponse(BaseModel):
    agent_id: str
    agent_name: str
    response: str
    success: bool
    error: str | None = None


@router.get("/agents")
async def list_agents():
    """列出所有可用的CLI Agent"""
    import shutil

    agents = []
    for agent_id, config in AGENT_REGISTRY.items():
        binary_path = shutil.which(config["binary"])
        agents.append(
            {
                "id": agent_id,
                "name": config["name"],
                "description": config["description"],
                "available": binary_path is not None,
                "binary": config["binary"],
            }
        )
    return agents


@router.post("/send")
async def send_to_agent(data: AgentMessage, user_id: UUID = Depends(get_current_user)):
    """发送消息给指定的CLI Agent"""
    agent_config = AGENT_REGISTRY.get(data.agent_id)
    if not agent_config:
        raise HTTPException(status_code=404, detail=f"Agent not found: {data.agent_id}")

    import shutil

    if not shutil.which(agent_config["binary"]):
        raise HTTPException(
            status_code=400, detail=f"Agent binary not found: {agent_config['binary']}"
        )

    try:
        cmd = [agent_config["binary"]] + agent_config["args"] + [data.message]
        logger.info(f"Running: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        response = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip() if proc.returncode != 0 else None

        return AgentResponse(
            agent_id=data.agent_id,
            agent_name=agent_config["name"],
            response=response,
            success=proc.returncode == 0,
            error=error,
        )

    except TimeoutError:
        raise HTTPException(status_code=408, detail="Agent response timeout (120s)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
