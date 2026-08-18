"""ACP proxy with hermes -z fallback."""

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class ACPProcess:
    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._msg_id: int = 0
        self._initialized: bool = False
        self._default_session_id: str | None = None
        self._read_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self):
        if self.is_running:
            return self._get_agent_info()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self._proc = await asyncio.create_subprocess_exec(
            "hermes",
            "acp",
            "--accept-hooks",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        print(f"ACP: started pid={self._proc.pid}", flush=True)

        await self._rpc(
            "initialize",
            {"protocolVersion": 1, "clientInfo": {"name": "OpenMate", "version": "1.0.0"}},
        )
        self._initialized = True
        print("ACP: initialized", flush=True)
        return self._get_agent_info()

    async def stop(self):
        if self._proc:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except:
                self._proc.kill()
            self._proc = None
        self._initialized = False
        self._default_session_id = None

    async def send_message(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        if not self.is_running or not self._initialized:
            await self.start()
        sid = session_id or self._default_session_id or "default"
        try:
            result = await self._prompt(text, sid)
            if result.get("response_text"):
                return result
            print("ACP: events not captured (buffering), using hermes -z", flush=True)
        except Exception as e:
            print(f"ACP: error {e}, falling back to hermes -z", flush=True)
        return await self._cli(text)

    async def send_message_with_image(
        self,
        text: str,
        image_data: str,
        mime_type: str = "image/png",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_running or not self._initialized:
            await self.start()
        sid = session_id or self._default_session_id or "default"
        try:
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            b64 = image_data.split(",")[-1] if "," in image_data else image_data
            parts.append(
                {"type": "image", "source": {"type": "base64", "mediaType": mime_type, "data": b64}}
            )
            result = await self._prompt_parts(parts, sid)
            if result.get("response_text"):
                return result
        except Exception as e:
            print(f"ACP: image error {e}", flush=True)
        return await self._cli(text or "用户发送了一张图片")

    async def list_sessions(self) -> list[dict]:
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._rpc("session/list", {})
        return resp.get("sessions", [])

    async def new_session(self, cwd: str = "/home/climbing") -> dict:
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._rpc("session/new", {"cwd": cwd, "mcpServers": []})
        sid = resp.get("sessionId") or resp.get("session_id")
        if sid:
            self._default_session_id = sid
        return resp

    # ---- Internal ----

    async def _rpc(self, method: str, params: dict) -> dict:
        """Simple RPC: send request, read lines until matching response."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        async with self._read_lock:
            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()
            start = time.time()
            while time.time() - start < 30:
                raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=5)
                if not raw:
                    break
                msg = self._parse(raw)
                if msg and str(msg.get("id", "")) == msg_id:
                    if "error" in msg:
                        raise Exception(msg["error"])
                    return msg.get("result", {})
        raise TimeoutError(f"ACP timeout: {method}")

    async def _prompt(self, text: str, session_id: str) -> dict:
        return await self._prompt_parts([{"type": "text", "text": text}], session_id)

    async def _prompt_parts(self, parts: list[dict], session_id: str) -> dict:
        """Send prompt and collect response + events in one read pass."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "session/prompt",
            "params": {"prompt": parts, "sessionId": session_id},
        }

        async with self._read_lock:
            # Drain stale events from previous RPC calls
            while True:
                try:
                    stale = await asyncio.wait_for(self._proc.stdout.readline(), timeout=0.3)
                    if stale:
                        m = self._parse(stale)
                        if m:
                            print(f"ACP drain: {m.get('method', '')}", flush=True)
                    else:
                        break
                except TimeoutError:
                    break

            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()

            collected = []
            response = {}
            start = time.time()

            while time.time() - start < 60:
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=2)
                except TimeoutError:
                    if response and collected:
                        break
                    if response:
                        break
                    continue
                if not raw:
                    break

                msg = self._parse(raw)
                if not msg:
                    continue

                # Our response?
                if str(msg.get("id", "")) == msg_id:
                    response = msg.get("result", {})
                    if "error" in msg:
                        raise Exception(msg["error"])
                    # Keep reading for more chunks
                    continue

                # Event?
                method = msg.get("method", "")
                if method == "session/update":
                    update = msg.get("params", {}).get("update", {})
                    su = update.get("sessionUpdate", "")
                    if su == "agent_message_chunk":
                        content = update.get("content", "")
                        if (
                            isinstance(content, dict)
                            and content.get("type") == "text"
                            and content.get("text")
                        ):
                            collected.append(content["text"])
                            print(f"ACP chunk: {content['text'][:40]}", flush=True)
                    elif su == "usage_update" and response and collected:
                        break

            print(f"ACP: {len(collected)} chunks, response={bool(response)}", flush=True)
            response["response_text"] = "".join(collected)
            response["source"] = "acp"
            return response

    async def _cli(self, text: str) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes", "-z", text, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            return {
                "stopReason": "end_turn",
                "response_text": stdout.decode("utf-8", errors="replace").strip(),
                "source": "hermes-cli",
            }
        except TimeoutError:
            return {"stopReason": "timeout", "response_text": "请求超时", "source": "hermes-cli"}
        except Exception as e:
            return {"stopReason": "error", "response_text": f"错误: {e}", "source": "hermes-cli"}

    @staticmethod
    def _parse(raw: bytes) -> dict | None:
        try:
            return json.loads(raw.decode().strip())
        except:
            return None

    def _get_agent_info(self) -> dict:
        return {"agentInfo": {"name": "hermes-agent", "version": "0.20.0"}, "protocolVersion": 1}


_acp: ACPProcess | None = None


def get_acp_process() -> ACPProcess:
    global _acp
    if _acp is None:
        _acp = ACPProcess()
    return _acp
