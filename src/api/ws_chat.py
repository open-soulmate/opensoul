"""WebSocket endpoint for real-time chat communication."""

import asyncio
import logging
import shutil

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.acp.proxy import get_acp_process
from src.api.user import decode_token

logger = logging.getLogger(__name__)
router = APIRouter()
@router.get("/health")
async def ws_chat_health():
    """WSChat health check."""
    return {"status": "ok", "component": "WSChat"}

# Agent proxy registry (same as agent_proxy.py)
AGENT_REGISTRY = {
    "hermes": {
        "name": "Hermes Agent",
        "binary": "hermes",
        "args": ["-z"],
        "description": "Nous Research Hermes Agent",
    },
    "mimo": {
        "name": "MiMo Code",
        "binary": "mimo",
        "args": ["run", "--prompt"],
        "description": "Xiaomi MiMo Code CLI",
    },
    "claude": {
        "name": "Claude Code",
        "binary": "claude",
        "args": ["-p"],
        "description": "Anthropic Claude Code CLI",
    },
    "codex": {
        "name": "Codex CLI",
        "binary": "codex",
        "args": ["-q"],
        "description": "OpenAI Codex CLI",
    },
    "aider": {
        "name": "Aider",
        "binary": "aider",
        "args": ["--message"],
        "description": "AI pair programming",
    },
}


async def run_agent_proxy(agent_id: str, text: str) -> tuple[str, str, bool]:
    """Run a message through agent proxy. Returns (response_text, source, success)."""
    agent_config = AGENT_REGISTRY.get(agent_id)
    if not agent_config:
        return f"未知Agent: {agent_id}", "error", False

    binary = agent_config["binary"]
    if not shutil.which(binary):
        return f"Agent未安装: {binary}", "error", False

    cmd = [binary] + agent_config["args"] + [text]
    logger.info(f"Agent proxy running: {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        response = stdout.decode("utf-8", errors="replace").strip()
        if not response and proc.returncode != 0:
            response = stderr.decode("utf-8", errors="replace").strip()
        return response or "（无响应）", agent_id, proc.returncode == 0
    except TimeoutError:
        return "Agent响应超时 (120s)", "error", False
    except Exception as e:
        return str(e), "error", False


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time chat.

    Protocol:
    - Client sends: {"type": "message", "text": "...", "mode": "hermes|acp|a2a|agent_proxy", "session_id": "...", "agent_id": "..."}
    - Server sends: {"type": "thinking"} (when processing starts)
    - Server sends: {"type": "chunk", "text": "..."} (streaming chunks)
    - Server sends: {"type": "done", "text": "...", "source": "..."} (final response)
    - Server sends: {"type": "error", "message": "..."} (on error)
    """
    await websocket.accept()

    # Authenticate via first message or query param
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.send_json({"type": "error", "message": "Missing token"})
        await websocket.close()
        return

    user_id = decode_token(token)
    if not user_id:
        await websocket.send_json({"type": "error", "message": "Invalid token"})
        await websocket.close()
        return

    await websocket.send_json({"type": "connected", "user_id": str(user_id)})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "message":
                text = data.get("text", "").strip()
                mode = data.get("mode", "hermes")
                session_id = data.get("session_id")
                agent_id = data.get("agent_id")
                attachments = data.get("attachments", [])  # [{type, data, name}]

                # Build text from attachments if no text provided
                if not text and attachments:
                    image_parts = [a for a in attachments if a.get("type") == "image"]
                    file_parts = [a for a in attachments if a.get("type") == "file"]
                    if image_parts:
                        text = "用户发送了一张图片"
                    elif file_parts:
                        text = f"用户发送了文件: {', '.join(a.get('name', 'file') for a in file_parts)}"

                if not text:
                    await websocket.send_json({"type": "error", "message": "Empty message"})
                    continue

                # Signal thinking
                await websocket.send_json({"type": "thinking"})

                try:
                    # Check for image attachments
                    image_attachments = [a for a in attachments if a.get("type") == "image"]

                    if mode == "agent_proxy" and agent_id:
                        # Route to specific CLI agent
                        response_text, source, success = await run_agent_proxy(agent_id, text)
                    else:
                        acp = get_acp_process()

                        if image_attachments and mode in ("hermes", "acp"):
                            # Send with image via ACP
                            img = image_attachments[0]
                            result = await acp.send_message_with_image(
                                text,
                                img.get("data", ""),
                                img.get("mime_type", "image/png"),
                                session_id,
                            )
                            response_text = result.get("response_text", "")
                            source = result.get("source", "acp")
                        elif mode == "hermes":
                            result = await acp.send_message(text, session_id)
                            response_text = result.get("response_text", "")
                            source = result.get("source", "hermes")
                        elif mode == "acp":
                            result = await acp.send_message(text, session_id)
                            response_text = result.get("response_text", "")
                            source = result.get("source", "acp")
                        else:
                            response_text = "不支持的模式"
                            source = "error"

                    # Send response
                    if response_text:
                        # Simulate streaming by sending chunks
                        chunk_size = 20
                        for i in range(0, len(response_text), chunk_size):
                            chunk = response_text[i : i + chunk_size]
                            await websocket.send_json({"type": "chunk", "text": chunk})
                            await asyncio.sleep(0.05)

                        await websocket.send_json(
                            {
                                "type": "done",
                                "text": response_text,
                                "source": source,
                            }
                        )
                    else:
                        await websocket.send_json({"type": "error", "message": "无响应"})

                except Exception as e:
                    logger.error(f"WS chat error: {e}")
                    await websocket.send_json({"type": "error", "message": str(e)})

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
