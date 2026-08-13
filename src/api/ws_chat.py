"""WebSocket endpoint for real-time chat communication."""

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from starlette.websockets import WebSocketState

from src.api.user import decode_token
from src.acp.proxy import get_acp_process


logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time chat.
    
    Protocol:
    - Client sends: {"type": "message", "text": "...", "mode": "hermes|acp|a2a", "session_id": "..."}
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

                if not text:
                    await websocket.send_json({"type": "error", "message": "Empty message"})
                    continue

                # Signal thinking
                await websocket.send_json({"type": "thinking"})

                try:
                    acp = get_acp_process()

                    if mode == "hermes":
                        # Use hermes -z (reliable)
                        result = await acp.send_message(text, session_id)
                        response_text = result.get("response_text", "")
                        source = result.get("source", "hermes")
                    elif mode == "acp":
                        # Try ACP first
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
                            chunk = response_text[i:i+chunk_size]
                            await websocket.send_json({"type": "chunk", "text": chunk})
                            await asyncio.sleep(0.05)  # Small delay for streaming feel

                        await websocket.send_json({
                            "type": "done",
                            "text": response_text,
                            "source": source,
                        })
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
