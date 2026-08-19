"""WebSocket terminal endpoint for OpenMate."""

import asyncio
import fcntl
import json
import os
import pty
import struct
import termios

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
@router.get("/health")
async def terminal_ws_health():
    """TerminalWS health check."""
    return {"status": "ok", "component": "TerminalWS"}


@router.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    """WebSocket terminal - spawns a PTY shell."""
    await websocket.accept()

    # Spawn a PTY with bash
    child_pid, fd = pty.openpty()
    if child_pid == 0:
        # Child process
        os.execvp("/bin/bash", ["/bin/bash", "-l"])
        return

    # Parent process
    asyncio.get_event_loop()

    async def read_pty():
        """Read from PTY and send to WebSocket."""
        try:
            while True:
                await asyncio.sleep(0.02)
                try:
                    data = os.read(fd, 4096)
                    if data:
                        await websocket.send_text(data.decode("utf-8", errors="replace"))
                except OSError:
                    break
        except Exception:
            pass

    async def read_ws():
        """Read from WebSocket and write to PTY."""
        try:
            while True:
                msg = await websocket.receive_text()
                data = json.loads(msg)
                if data.get("type") == "input":
                    os.write(fd, data["data"].encode("utf-8"))
                elif data.get("type") == "resize":
                    cols = data.get("cols", 80)
                    rows = data.get("rows", 24)
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        await asyncio.gather(read_pty(), read_ws())
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
