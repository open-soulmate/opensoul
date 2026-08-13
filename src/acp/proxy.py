"""ACP proxy with hermes -z fallback for reliable message delivery."""

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class ACPProcess:
    """Manages ACP process with hermes -z fallback."""

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._msg_id: int = 0
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._initialized: bool = False
        self._default_session_id: str | None = None
        self._event_count: int = 0
        self._chunk_count: int = 0

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self):
        """Start the hermes acp process."""
        if self.is_running:
            return self._get_agent_info()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

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
        self._initialized = True
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
        self._initialized = False
        self._default_session_id = None

    async def send_message(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        """Send a message using hermes -z (reliable fallback)."""
        sid = session_id or self._default_session_id or "default"

        # Try ACP first if running
        if self.is_running and self._initialized:
            try:
                result = await self._send_via_acp(text, sid)
                if result.get("response_text"):
                    return result
                # ACP returned empty, fall through to hermes -z
                logger.warning("ACP returned empty response, falling back to hermes -z")
            except Exception as e:
                logger.warning(f"ACP send failed: {e}, falling back to hermes -z")

        # hermes -z fallback
        return await self._send_via_cli(text)

    async def send_message_with_image(self, text: str, image_data: str, mime_type: str = "image/png", session_id: str | None = None) -> dict[str, Any]:
        """Send a message with image. Falls back to CLI."""
        # For images, always try ACP first (CLI doesn't support images well)
        if self.is_running and self._initialized:
            try:
                return await self._send_image_via_acp(text, image_data, mime_type, session_id or "default")
            except Exception as e:
                logger.warning(f"ACP image send failed: {e}")

        # Fallback: just send text
        text_msg = text or "用户发送了一张图片"
        return await self._send_via_cli(text_msg)

    async def list_sessions(self) -> list[dict]:
        """List ACP sessions."""
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._send_rpc("session/list", {})
        return resp.get("sessions", [])

    async def new_session(self, cwd: str = "/home/climbing") -> dict:
        """Create a new ACP session."""
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._send_rpc("session/new", {"cwd": cwd, "mcpServers": []})
        sid = resp.get("sessionId") or resp.get("session_id")
        if sid:
            self._default_session_id = sid
        return resp

    async def _send_via_cli(self, text: str) -> dict[str, Any]:
        """Send message using hermes -z CLI (reliable)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes", "-z", text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            response = stdout.decode("utf-8", errors="replace").strip()

            return {
                "stopReason": "end_turn",
                "response_text": response,
                "source": "hermes-cli",
            }
        except asyncio.TimeoutError:
            return {"stopReason": "timeout", "response_text": "请求超时", "source": "hermes-cli"}
        except Exception as e:
            return {"stopReason": "error", "response_text": f"错误: {e}", "source": "hermes-cli"}

    async def _send_via_acp(self, text: str, session_id: str) -> dict[str, Any]:
        """Send via ACP protocol with event collection."""
        # Drain stale events
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except:
                break

        self._chunk_count = 0
        self._event_count = 0
        params = {
            "prompt": [{"type": "text", "text": text}],
            "sessionId": session_id,
        }

        resp = await self._send_rpc("session/prompt", params)

        # Collect streamed response
        collected = []
        start = time.time()
        while time.time() - start < 30:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                chunks = self._extract_text_chunks(event)
                collected.extend(chunks)
                if self._is_stream_end(event) and collected:
                    break
            except asyncio.TimeoutError:
                if collected:
                    break
                continue

        logger.info(f"ACP response: {self._chunk_count} chunks, {self._event_count} events, {len(collected)} text parts")
        resp["response_text"] = "".join(collected)
        return resp

    async def _send_image_via_acp(self, text: str, image_data: str, mime_type: str, session_id: str) -> dict[str, Any]:
        """Send image via ACP."""
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except:
                break

        self._chunk_count = 0
        self._event_count = 0
        parts = []
        if text:
            parts.append({"type": "text", "text": text})
        b64 = image_data.split(",")[-1] if "," in image_data else image_data
        parts.append({"type": "image", "source": {"type": "base64", "mediaType": mime_type, "data": b64}})

        params = {"prompt": parts, "sessionId": session_id}
        resp = await self._send_rpc("session/prompt", params)

        collected = []
        start = time.time()
        while time.time() - start < 60:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                chunks = self._extract_text_chunks(event)
                collected.extend(chunks)
                if self._is_stream_end(event) and collected:
                    break
            except asyncio.TimeoutError:
                if collected:
                    break
                continue

        logger.info(f"ACP image response: {self._chunk_count} chunks, {self._event_count} events, {len(collected)} text parts")
        resp["response_text"] = "".join(collected)
        return resp

    def _extract_text_chunks(self, event: dict) -> list[str]:
        """Extract text from ACP events, trying multiple known formats."""
        self._event_count += 1
        method = event.get("method", "")
        params = event.get("params", {})
        update = params.get("update", {})

        # Format 1: {method: "session/update", params: {update: {sessionUpdate: "agent_message_chunk", parts: [...]}}}
        su = update.get("sessionUpdate") or update.get("session_update", "")
        if su == "agent_message_chunk":
            self._chunk_count += 1
            texts = []
            for p in update.get("parts", []):
                if p.get("type") == "text" and p.get("text"):
                    texts.append(p["text"])
            if texts:
                return texts
            # Check for content field - actual Hermes ACP format: {content: {text: "...", type: "text"}}
            content = update.get("content", "")
            if isinstance(content, dict) and content.get("type") == "text" and content.get("text"):
                return [content["text"]]
            elif isinstance(content, str) and content:
                return [content]

        # Format 2: {method: "session/update", params: {update: {type: "content_block_delta", delta: {text: "..."}}}}  (Anthropic-style)
        if update.get("type") == "content_block_delta":
            delta = update.get("delta", {})
            if delta.get("type") == "text_delta" and delta.get("text"):
                self._chunk_count += 1
                return [delta["text"]]

        # Format 3: {method: "session/update", params: {update: {type: "message_delta", content: [...]}}}
        if update.get("type") in ("message", "message_delta"):
            for p in update.get("content", []):
                if isinstance(p, dict) and p.get("type") == "text" and p.get("text"):
                    self._chunk_count += 1
                    return [p["text"]]

        # Format 4: Direct text in params
        if params.get("text") and method == "session/update":
            self._chunk_count += 1
            return [params["text"]]

        # Log unhandled event for debugging
        if method == "session/update" and self._event_count <= 20:
            logger.debug(f"ACP event #{self._event_count}: method={method}, update_keys={list(update.keys())}, su={su}")
        elif self._event_count == 21:
            logger.debug("ACP event logging capped at 20")

        return []

    def _is_stream_end(self, event: dict) -> bool:
        """Check if event signals end of agent response stream."""
        params = event.get("params", {})
        update = params.get("update", {})
        su = update.get("sessionUpdate") or update.get("session_update", "")
        ev_type = update.get("type", "")
        return su in ("usage_update", "turn_end", "message_complete") or ev_type in ("message_stop",)

    async def _send_rpc(self, method: str, params: dict) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        self._msg_id += 1
        msg_id = str(self._msg_id)

        request = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        line = json.dumps(request) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

        try:
            return await asyncio.wait_for(future, timeout=120)
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
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"ACP non-JSON line: {line[:200]}")
                    continue

                msg_id = str(msg.get("id", ""))
                if msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if "error" in msg:
                        future.set_exception(Exception(msg["error"]))
                    else:
                        future.set_result(msg.get("result", {}))
                else:
                    method = msg.get("method", "unknown")
                    logger.debug(f"ACP event: method={method}, id={msg.get('id')}")
                    await self._event_queue.put(msg)
        except Exception as e:
            logger.error(f"ACP read loop error: {e}")

    def _get_agent_info(self) -> dict:
        return {
            "agentInfo": {"name": "hermes-agent", "version": "0.20.0"},
            "protocolVersion": 1,
        }


# Global instance
_acp_process: ACPProcess | None = None


def get_acp_process() -> ACPProcess:
    global _acp_process
    if _acp_process is None:
        _acp_process = ACPProcess()
    return _acp_process
