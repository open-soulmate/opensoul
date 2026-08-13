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
        self._msg_id: int = 0
        self._initialized: bool = False
        self._default_session_id: str | None = None

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
        )
        print(f"ACP: process started (pid={self._proc.pid})", flush=True)

        # Initialize
        resp = await self._send_rpc("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "OpenMate", "version": "1.0.0"},
        })
        self._initialized = True
        print(f"ACP: initialized", flush=True)
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
        self._initialized = False
        self._default_session_id = None

    async def send_message(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        """Send a message using ACP protocol with event collection."""
        if not self.is_running or not self._initialized:
            await self.start()

        sid = session_id or self._default_session_id or "default"

        # Try ACP
        try:
            result = await self._send_via_acp(text, sid)
            if result.get("response_text"):
                return result
            print("ACP: empty response, falling back to hermes -z", flush=True)
        except Exception as e:
            print(f"ACP: send failed: {e}, falling back to hermes -z", flush=True)

        # hermes -z fallback
        return await self._send_via_cli(text)

    async def send_message_with_image(self, text: str, image_data: str, mime_type: str = "image/png", session_id: str | None = None) -> dict[str, Any]:
        """Send message with image. Falls back to CLI."""
        if not self.is_running or not self._initialized:
            await self.start()

        sid = session_id or self._default_session_id or "default"

        # Try ACP with image
        try:
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            b64 = image_data.split(",")[-1] if "," in image_data else image_data
            parts.append({"type": "image", "source": {"type": "base64", "mediaType": mime_type, "data": b64}})

            result = await self._send_via_acp_parts(parts, sid)
            if result.get("response_text"):
                return result
        except Exception as e:
            print(f"ACP: image send failed: {e}", flush=True)

        # Fallback: just send text
        return await self._send_via_cli(text or "用户发送了一张图片")

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

    async def _send_rpc(self, method: str, params: dict) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response (for init/list/etc)."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {'jsonrpc': '2.0', 'id': msg_id, 'method': method, 'params': params}
        line = json.dumps(request) + chr(10)
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

        start = time.time()
        while time.time() - start < 30:
            try:
                raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=5)
                if not raw: break
                decoded = raw.decode().strip()
                if not decoded: continue
                msg = json.loads(decoded)
                if str(msg.get('id', '')) == msg_id:
                    if 'error' in msg:
                        raise Exception(msg['error'])
                    return msg.get('result', {})
            except asyncio.TimeoutError:
                continue
        raise TimeoutError(f'ACP timeout for {method}')

    async def _send_via_cli(self, text: str) -> dict[str, Any]:
        """Send message using hermes -z CLI (reliable fallback)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes", "-z", text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            response = stdout.decode("utf-8", errors="replace").strip()
            return {"stopReason": "end_turn", "response_text": response, "source": "hermes-cli"}
        except asyncio.TimeoutError:
            return {"stopReason": "timeout", "response_text": "请求超时", "source": "hermes-cli"}
        except Exception as e:
            return {"stopReason": "error", "response_text": f"错误: {e}", "source": "hermes-cli"}

    async def _send_via_acp(self, text: str, session_id: str) -> dict[str, Any]:
        """Send text via ACP with integrated event collection."""
        prompt_parts = [{"type": "text", "text": text}]
        return await self._send_via_acp_parts(prompt_parts, session_id)

    async def _send_via_acp_parts(self, prompt_parts: list[dict], session_id: str) -> dict[str, Any]:
        """Send prompt parts via ACP, collecting events inline."""
        self._msg_id += 1
        msg_id = str(self._msg_id)

        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "session/prompt",
            "params": {
                "prompt": prompt_parts,
                "sessionId": session_id,
            }
        }

        line = json.dumps(request) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

        # Read all messages until we get our response
        collected_text = []
        response_data = {}
        start = time.time()

        while time.time() - start < 60:
            try:
                raw_line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=2.0)
                if not raw_line:
                    break
                decoded = raw_line.decode().strip()
                if not decoded:
                    continue

                try:
                    msg = json.loads(decoded)
                except json.JSONDecodeError:
                    continue

                # Check if this is our response
                if str(msg.get("id", "")) == msg_id:
                    response_data = msg.get("result", {})
                    if "error" in msg:
                        raise Exception(msg["error"])
                    # Don't break yet - there might be more events after response
                    # Wait a bit more for any remaining chunks
                    continue

                # Process event
                method = msg.get("method", "")
                if method == "session/update":
                    update = msg.get("params", {}).get("update", {})
                    su = update.get("sessionUpdate", "")
                    if su == "agent_message_chunk":
                        content = update.get("content", "")
                        if isinstance(content, dict) and content.get("type") == "text" and content.get("text"):
                            collected_text.append(content["text"])
                        elif isinstance(content, str) and content:
                            collected_text.append(content)

                    # Stream end signals
                    if su in ("usage_update",) and response_data and collected_text:
                        break

                # If we already have the response and some text, check if done
                if response_data and collected_text:
                    # Quick non-blocking check for more events
                    try:
                        more = await asyncio.wait_for(self._proc.stdout.readline(), timeout=0.5)
                        if more:
                            decoded = more.decode().strip()
                            if decoded:
                                try:
                                    extra = json.loads(decoded)
                                    if str(extra.get("id", "")) == msg_id:
                                        response_data = extra.get("result", {})
                                    elif extra.get("method") == "session/update":
                                        update = extra.get("params", {}).get("update", {})
                                        su = update.get("sessionUpdate", "")
                                        if su == "agent_message_chunk":
                                            content = update.get("content", "")
                                            if isinstance(content, dict) and content.get("type") == "text":
                                                collected_text.append(content.get("text", ""))
                                except json.JSONDecodeError:
                                    pass
                    except asyncio.TimeoutError:
                        break

            except asyncio.TimeoutError:
                if response_data and collected_text:
                    break
                continue

        response_data["response_text"] = "".join(collected_text)
        response_data["source"] = "acp"
        return response_data

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
