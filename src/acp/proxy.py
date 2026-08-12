"""ACP (Agent Client Protocol) proxy for OpenSoul.

Launches `hermes acp` as a subprocess and communicates via stdin/stdout JSON-RPC.
Web clients connect via WebSocket to OpenSoul, which relays to the ACP process.
"""

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class ACPProcess:
    """Manages a single hermes acp subprocess."""

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._msg_id: int = 0
        self._event_queue: asyncio.Queue = asyncio.Queue()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self):
        """Start the hermes acp process."""
        if self.is_running:
            return

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")

        self._proc = await asyncio.create_subprocess_exec(
            "hermes", "acp", "--accept-hooks",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(f"ACP process started (pid={self._proc.pid})")

        # Send initialize
        resp = await self._send_rpc("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "OpenMate", "version": "1.0.0"},
        })
        logger.info(f"ACP initialized: {resp}")
        return resp

    async def stop(self):
        """Stop the ACP process."""
        if self._proc:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

    async def send_message(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        """Send a user message and get the agent response."""
        if not self.is_running:
            await self.start()

        # ACP PromptRequest format: {prompt: [ContentBlock], sessionId: str}
        params: dict[str, Any] = {
            "prompt": [{"type": "text", "text": text}],
            "sessionId": session_id or "default",
        }

        resp = await self._send_rpc("session/prompt", params)
        return resp

    async def send_message_with_image(self, text: str, image_data: str, mime_type: str = "image/png", session_id: str | None = None) -> dict[str, Any]:
        """Send a message with an image attachment."""
        if not self.is_running:
            await self.start()

        parts: list[dict] = []
        if text:
            parts.append({"type": "text", "text": text})
        # ACP ImageContentBlock format
        b64 = image_data.split(",")[-1] if "," in image_data else image_data
        parts.append({"type": "image", "source": {"type": "base64", "mediaType": mime_type, "data": b64}})

        params: dict[str, Any] = {
            "prompt": parts,
            "sessionId": session_id or "default",
        }

        resp = await self._send_rpc("session/prompt", params)
        return resp

    async def list_sessions(self) -> list[dict]:
        """List available sessions."""
        if not self.is_running:
            await self.start()
        resp = await self._send_rpc("session/list", {})
        return resp.get("sessions", [])

    async def new_session(self) -> dict:
        """Create a new session."""
        if not self.is_running:
            await self.start()
        resp = await self._send_rpc("session/new", {})
        return resp

    async def _send_rpc(self, method: str, params: dict) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        self._msg_id += 1
        msg_id = str(self._msg_id)

        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        line = json.dumps(request) + "\n"
        logger.debug(f"ACP SEND: {line.strip()}")
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=120)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"ACP timeout for method={method}")

    async def _read_loop(self):
        """Read JSON-RPC messages from stdout."""
        try:
            while self._proc and self._proc.returncode is None:
                line = await self._proc.stdout.readline()
                if not line:
                    break

                line = line.decode().strip()
                if not line:
                    continue

                logger.debug(f"ACP RECV: {line[:200]}")

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Handle response
                msg_id = str(msg.get("id", ""))
                if msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if "error" in msg:
                        future.set_exception(Exception(msg["error"]))
                    else:
                        future.set_result(msg.get("result", {}))
                else:
                    # Handle notification/event
                    await self._event_queue.put(msg)

        except Exception as e:
            logger.error(f"ACP read loop error: {e}")


# Global ACP process instance
_acp_process: ACPProcess | None = None


def get_acp_process() -> ACPProcess:
    global _acp_process
    if _acp_process is None:
        _acp_process = ACPProcess()
    return _acp_process
