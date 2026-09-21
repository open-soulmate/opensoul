"""跨agent会话导入 — goose session/import_formats 移植（SUMMARY.md §三 P0-10 会话资产化）

调研来源（源码级，本地clone ~/agent-research-src/goose）：
- crates/goose/src/session/import_formats/mod.rs（223行全文）：detect_format嗅探分层——
  首行JSON探测 session_meta=Codex / type:session+version(or cwd+id)=Pi /
  sessionId+(type|uuid)=ClaudeCode；全文working_dir=goose原生；fallback扫前5行sessionId
- claude_code.rs（410行）：tool_use/tool_result/thinking(带signature)/image全映射、
  cache token归并（input+=cache_read+cache_write）、ai-title或首行80字符摘要做会话名
- codex.rs（382行）：session_meta头/cwd+id、event_msg收割usage（input已含cache）、
  developer/system跳过、is_context_blob启发式（环境上下文blob不配当会话名）
- pi.rs（448行）：header必须type=session、toolResult/bashExecution角色、
  usage.input+=cacheRead+cacheWrite、cost.total记账
- utils.rs sanitize_unicode_tags：NFC归一 + 剥除Unicode Tags Block（U+E0000–U+E007F）——
  "导入路径也是攻击面"（goose测试原文：visible U+E0041 世界→visible世界）

与goose的差异（OpenSoul canonical schema适配）：
- goose转换目标是自家Session JSON；本模块目标是opensoul agent_sessions/agent_messages
  生产表（acp-proxy ws_chat同款schema，前端 /api/sessions 现有读路径直接可见）
- canonical消息表只有role/content/timestamp/attachments四列——工具请求随assistant消息、
  工具响应随user消息（goose同款语义），以文本标记序列化进content：
  [tool_call {name} id={id}] / [tool_result id={id} error=1] / [thinking]...[/thinking]
- 会话去重：确定性主键 import:{format}:{source_id}，重复导入status=duplicate零写入
- 失败可见（evolution-engine-patterns §1.1 mem0"禁止静默降级"）：
  无法识别的格式抛ImportFormatError；畸形行/跳过行全部计数返回，不静默吞
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field

from src.trajectory.message_tree import ensure_parent_column

logger = logging.getLogger("opensoul.trajectory.import_formats")

OPENSOUL_DB = os.environ.get(
    "OPENSOUL_DB",
    os.path.join(os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul"), "data", "opensoul.db"),
)
MAX_IMPORT_BYTES = 100 * 1024 * 1024

# goose utils.rs sanitize_unicode_tags：剥除Unicode Tags Block（不可见注入字符）
TAG_BLOCK_RE = re.compile("[\U000e0000-\U000e007f]")

# codex.rs is_context_blob：harness注入的环境上下文不配当会话名
CONTEXT_BLOB_PREFIXES = (
    "<environment_context>",
    "<app-context>",
    "<permissions instructions>",
    "# AGENTS.md",
)


class ImportFormatError(ValueError):
    """导入格式错误 — 必须显式可见，禁止静默吞掉（mem0 §1.1 失败必须可见）"""


def sanitize_unicode_tags(text: str) -> str:
    """NFC归一 + 剥除Unicode Tags Block（goose utils.rs同款两步）"""
    normalized = unicodedata.normalize("NFC", text)
    return TAG_BLOCK_RE.sub("", normalized)


def summarize_first_line(s: str) -> str:
    """首个非空行，字符级截断80（CJK安全，goose mod.rs summarize_first_line同款）"""
    line = next((l for l in s.splitlines() if l.strip()), s).strip()
    if len(line) <= 80:
        return line
    return line[:77] + "..."


def _parse_ts(s) -> float | None:
    """RFC3339/ISO8601 → unix float；解析失败返回None（调用方fallback）"""
    if not s or not isinstance(s, str):
        return None
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


def detect_format(content: str) -> str:
    """嗅探外部会话格式（goose mod.rs detect_format分层同款）。

    返回 "codex" / "pi" / "claude_code" / "unknown"。
    分层顺序：特定标记(session_meta)优先于通用标记(sessionId)，最后fallback扫5行。
    """
    first_line = next((l for l in content.splitlines() if l.strip()), "")
    try:
        v = json.loads(first_line)
    except (json.JSONDecodeError, ValueError):
        v = None
    if isinstance(v, dict):
        vtype = v.get("type")
        # Codex rollout首行恒为 {"type":"session_meta",...}
        if vtype == "session_meta":
            return "codex"
        # Pi首行 {"type":"session",...}；旧fixture无version但必有cwd+id
        if vtype == "session" and (
            v.get("version") is not None or (v.get("cwd") is not None and v.get("id") is not None)
        ):
            return "pi"
        # Claude Code行恒有sessionId；goose原生JSON是单个多行对象
        if v.get("sessionId") is not None and (
            v.get("type") is not None or v.get("uuid") is not None
        ):
            return "claude_code"
    # fallback：前5行内任一JSON行含sessionId → Claude Code
    for line in [l for l in content.splitlines() if l.strip()][:5]:
        try:
            lv = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(lv, dict) and lv.get("sessionId") is not None:
            return "claude_code"
    return "unknown"


@dataclass
class ConvertedSession:
    """统一转换产物 — 外部transcript → OpenSoul canonical会话"""

    source_format: str
    source_id: str
    title: str
    working_dir: str
    created_at: float
    updated_at: float
    messages: list = field(default_factory=list)  # [{role, content, timestamp}]
    usage: dict = field(default_factory=dict)
    malformed_lines: int = 0
    skipped_lines: int = 0
    images_skipped: int = 0


def _msg(role: str, content: str, ts: float | None) -> dict:
    return {
        "role": role,
        "content": sanitize_unicode_tags(content),
        "timestamp": ts if ts is not None else time.time(),
    }


def _tool_result_text(content, is_error: bool) -> str:
    """claude_code.rs build_tool_result同款：string直取/array拼text+tool_reference"""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                parts.append(json.dumps(b, ensure_ascii=False))
                continue
            bt = b.get("type")
            if bt == "text":
                parts.append(b.get("text") or "")
            elif bt == "tool_reference":
                parts.append(f"[tool_reference: {b.get('tool_name', '')}]")
            else:
                parts.append(json.dumps(b, ensure_ascii=False))
        text = "\n".join(parts)
    elif content is None:
        text = ""
    else:
        text = json.dumps(content, ensure_ascii=False)
    return f"[error]\n{text}" if is_error else text


def _serialize_args(args) -> str:
    if isinstance(args, dict):
        return json.dumps(args, ensure_ascii=False)
    if isinstance(args, str):
        # codex.rs同款：arguments是JSON字符串时先解析再结构化序列化
        try:
            return json.dumps(json.loads(args), ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            return args
    return json.dumps(args or {}, ensure_ascii=False)


def convert_claude_code(content: str) -> ConvertedSession:
    """Claude Code ~/.claude/projects/**/*.jsonl → canonical会话（claude_code.rs移植）"""
    parsed = []
    malformed = 0
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            malformed += 1
    if not parsed:
        raise ImportFormatError("Claude Code import: no parseable JSON lines")

    cwd = ""
    session_id = "imported"
    ai_title = None
    messages = []
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
    }
    first_ts: float | None = None
    last_ts: float | None = None
    first_user_text = None
    skipped = 0
    images = 0

    for line in parsed:
        ltype = line.get("type") or ""
        ts = _parse_ts(line.get("timestamp"))
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        if not cwd and line.get("cwd"):
            cwd = line["cwd"]
        if line.get("sessionId") and session_id == "imported":
            session_id = str(line["sessionId"])
        if ltype == "ai-title" and line.get("aiTitle"):
            ai_title = str(line["aiTitle"])
            continue
        inner = line.get("message") or {}
        ic = inner.get("content")
        if ltype == "user":
            parts = []
            if isinstance(ic, str):
                parts.append(ic)
            elif isinstance(ic, list):
                for block in ic:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "text":
                        parts.append(block.get("text") or "")
                    elif bt == "tool_result":
                        rid = block.get("tool_use_id") or ""
                        is_err = bool(block.get("is_error"))
                        err_flag = " error=1" if is_err else ""
                        body = _tool_result_text(block.get("content"), is_err)
                        parts.append(f"[tool_result id={rid}{err_flag}]\n{body}")
                    elif bt == "image":
                        src = block.get("source") or {}
                        images += 1
                        parts.append(f"[image: {src.get('media_type', '?')}]")
            text = "\n".join(p for p in parts if p).strip()
            if not text:
                skipped += 1
                continue
            if first_user_text is None:
                first_user_text = text
            messages.append(_msg("user", text, ts))
        elif ltype == "assistant":
            if not isinstance(ic, list):
                skipped += 1
                continue
            parts = []
            for block in ic:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text":
                    t = block.get("text") or ""
                    if t:
                        parts.append(t)
                elif bt == "thinking":
                    t = block.get("thinking") or ""
                    if t:
                        parts.append(f"[thinking]\n{t}\n[/thinking]")
                elif bt == "tool_use":
                    name = block.get("name") or "unknown_tool"
                    tid = block.get("id") or ""
                    parts.append(
                        f"[tool_call {name} id={tid}]\n{_serialize_args(block.get('input'))}"
                    )
            u = inner.get("usage") or {}
            if isinstance(u, dict) and u:
                cw = int(u.get("cache_creation_input_tokens") or 0)
                cr = int(u.get("cache_read_input_tokens") or 0)
                # goose同款：input_tokens += cache_write + cache_read（归并计费口径）
                usage["input_tokens"] += int(u.get("input_tokens") or 0) + cw + cr
                usage["output_tokens"] += int(u.get("output_tokens") or 0)
                usage["cache_read_input_tokens"] += cr
                usage["cache_write_input_tokens"] += cw
            text = "\n".join(parts).strip()
            if not text:
                skipped += 1
                continue
            messages.append(_msg("assistant", text, ts))
        else:
            skipped += 1  # attachment/queue-operation等噪声行

    title = ai_title or (summarize_first_line(first_user_text) if first_user_text else "")
    if not title:
        title = f"Imported Claude Code session {session_id}"
    now = time.time()
    return ConvertedSession(
        source_format="claude_code",
        source_id=session_id,
        title=title,
        working_dir=cwd,
        created_at=first_ts if first_ts is not None else now,
        updated_at=last_ts if last_ts is not None else (first_ts if first_ts is not None else now),
        messages=messages,
        usage=usage,
        malformed_lines=malformed,
        skipped_lines=skipped,
        images_skipped=images,
    )


def _collect_user_text(content) -> str:
    """codex.rs collect_user_text：input_text/text/output_text块拼接"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in ("input_text", "text", "output_text"):
            parts.append(block.get("text") or "")
    return "\n".join(parts)


def _is_context_blob(text: str) -> bool:
    t = text.lstrip()
    return any(t.startswith(p) for p in CONTEXT_BLOB_PREFIXES)


def convert_codex(content: str) -> ConvertedSession:
    """Codex ~/.codex/sessions/**/rollout-*.jsonl → canonical会话（codex.rs移植）"""
    parsed = []
    malformed = 0
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            malformed += 1
    if not parsed:
        raise ImportFormatError("Codex import: no parseable JSON lines")

    meta = next((l.get("payload") for l in parsed if l.get("type") == "session_meta"), None) or {}
    cwd = str(meta.get("cwd") or "")
    session_id = str(meta.get("id") or "imported")

    messages = []
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
    }
    first_ts: float | None = None
    last_ts: float | None = None
    first_user_text = None
    skipped = 0

    for line_idx, line in enumerate(parsed):
        ltype = line.get("type") or ""
        ts = _parse_ts(line.get("timestamp"))
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        if ltype == "event_msg":
            u = (line.get("payload") or {}).get("usage") or {}
            if isinstance(u, dict) and u:
                # Codex input_tokens已含cached_input_tokens
                usage["input_tokens"] += int(u.get("input_tokens") or 0)
                usage["cache_read_input_tokens"] += int(u.get("cached_input_tokens") or 0)
                usage["output_tokens"] += int(u.get("output_tokens") or 0)
            continue
        if ltype == "session_meta":
            continue  # 头部元数据已收割（cwd/id），非跳过
        if ltype != "response_item":
            skipped += 1
            continue
        payload = line.get("payload")
        if not isinstance(payload, dict):
            skipped += 1
            continue
        pt = payload.get("type") or ""
        role = payload.get("role")
        if role in ("developer", "system"):
            skipped += 1  # harness注入prompt，不进会话（codex.rs同款）
            continue
        if role == "user":
            text = _collect_user_text(payload.get("content")).strip()
            if not text:
                skipped += 1
                continue
            if first_user_text is None and not _is_context_blob(text):
                first_user_text = text
            messages.append(_msg("user", text, ts))
            continue
        if pt == "message" and role == "assistant":
            parts = []
            ic = payload.get("content")
            if isinstance(ic, list):
                for block in ic:
                    if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                        t = block.get("text") or ""
                        if t:
                            parts.append(t)
            text = "\n".join(parts).strip()
            if text:
                messages.append(_msg("assistant", text, ts))
            else:
                skipped += 1
            continue
        if pt == "reasoning":
            parts = []
            for item in payload.get("summary") or []:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(item["text"])
            text = "\n".join(parts).strip()
            if text:
                messages.append(_msg("assistant", f"[reasoning]\n{text}", ts))
            else:
                skipped += 1
            continue
        if pt == "function_call":
            name = payload.get("name") or "unknown_tool"
            cid = payload.get("call_id") or ""
            messages.append(
                _msg(
                    "assistant",
                    f"[tool_call {name} id={cid}]\n{_serialize_args(payload.get('arguments'))}",
                    ts,
                )
            )
            continue
        if pt == "function_call_output":
            cid = payload.get("call_id") or ""
            out = payload.get("output")
            out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            messages.append(_msg("user", f"[tool_result id={cid}]\n{out}", ts))
            continue
        if pt == "web_search_call":
            action = payload.get("action") or {}
            args = {}
            if action.get("query"):
                args["query"] = action["query"]
            if action.get("url"):
                args["url"] = action["url"]
            wid = f"codex_websearch_{line_idx}"
            messages.append(
                _msg("assistant", f"[tool_call web_search id={wid}]\n{_serialize_args(args)}", ts)
            )
            status = payload.get("status") or "completed"
            messages.append(_msg("user", f"[tool_result id={wid}]\n[web_search {status}]", ts))
            continue
        skipped += 1  # 未知response_item类型，显式计数不静默

    title = summarize_first_line(first_user_text) if first_user_text else ""
    if not title:
        title = f"Imported Codex session {session_id}"
    now = time.time()
    return ConvertedSession(
        source_format="codex",
        source_id=session_id,
        title=title,
        working_dir=cwd,
        created_at=first_ts if first_ts is not None else now,
        updated_at=last_ts if last_ts is not None else (first_ts if first_ts is not None else now),
        messages=messages,
        usage=usage,
        malformed_lines=malformed,
        skipped_lines=skipped,
    )


def convert_pi(content: str) -> ConvertedSession:
    """Pi ~/.pi/agent/sessions/**/*.jsonl → canonical会话（pi.rs移植）"""
    lines = [l for l in content.splitlines() if l.strip()]
    if not lines:
        raise ImportFormatError("Pi import: empty file")
    try:
        header = json.loads(lines[0])
    except (json.JSONDecodeError, ValueError) as e:
        raise ImportFormatError(f"Pi import: header is not valid JSON: {e}") from e
    if not isinstance(header, dict) or header.get("type") != "session":
        raise ImportFormatError("Pi import: missing session header")

    cwd = str(header.get("cwd") or "")
    session_id = str(header.get("id") or "imported")
    header_ts = _parse_ts(header.get("timestamp"))

    messages = []
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "cost": 0.0,
    }
    first_ts = header_ts
    last_ts = header_ts
    first_user_text = None
    malformed = 0
    skipped = 0
    images = 0

    for entry_idx, line in enumerate(lines[1:]):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            malformed += 1
            continue
        if not isinstance(entry, dict) or entry.get("type") != "message":
            skipped += 1
            continue
        inner = entry.get("message")
        if not isinstance(inner, dict):
            skipped += 1
            continue
        ts = _parse_ts(entry.get("timestamp"))
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        u = inner.get("usage")
        if isinstance(u, dict) and u:
            cr = int(u.get("cacheRead") or 0)
            cw = int(u.get("cacheWrite") or 0)
            usage["input_tokens"] += int(u.get("input") or 0) + cr + cw
            usage["output_tokens"] += int(u.get("output") or 0)
            usage["cache_read_input_tokens"] += cr
            usage["cache_write_input_tokens"] += cw
            cost = (u.get("cost") or {}).get("total")
            if isinstance(cost, (int, float)):
                usage["cost"] += float(cost)
        role = inner.get("role") or ""
        ic = inner.get("content")
        if role == "user":
            parts = []
            if isinstance(ic, str):
                parts.append(ic)
            elif isinstance(ic, list):
                for block in ic:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        parts.append(block.get("text") or "")
                    elif block.get("type") == "image":
                        images += 1
                        parts.append(f"[image: {block.get('mimeType', '?')}]")
            text = "\n".join(p for p in parts if p).strip()
            if not text:
                skipped += 1
                continue
            if first_user_text is None:
                first_user_text = text
            messages.append(_msg("user", text, ts))
        elif role == "assistant":
            if isinstance(ic, str):
                text = ic.strip()
                if text:
                    messages.append(_msg("assistant", text, ts))
                else:
                    skipped += 1
                continue
            parts = []
            if isinstance(ic, list):
                for block in ic:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "text":
                        t = block.get("text") or ""
                        if t:
                            parts.append(t)
                    elif bt == "thinking":
                        t = block.get("thinking") or ""
                        if t:
                            parts.append(f"[thinking]\n{t}\n[/thinking]")
                    elif bt == "toolCall":
                        name = block.get("name") or "unknown_tool"
                        tid = block.get("id") or ""
                        parts.append(
                            f"[tool_call {name} id={tid}]\n{_serialize_args(block.get('arguments'))}"
                        )
                    elif bt == "summary":
                        t = block.get("text") or ""
                        if t:
                            parts.append(t)
            text = "\n".join(parts).strip()
            if text:
                messages.append(_msg("assistant", text, ts))
            else:
                skipped += 1
        elif role == "toolResult":
            tid = inner.get("toolCallId") or ""
            is_err = bool(inner.get("isError"))
            err_flag = " error=1" if is_err else ""
            body = _tool_result_text(ic, is_err)
            messages.append(_msg("user", f"[tool_result id={tid}{err_flag}]\n{body}", ts))
        elif role == "bashExecution":
            # pi.rs同款：合成bash工具往返，会话读起来自然
            command = inner.get("command") or ""
            output = inner.get("output") or ""
            exit_code = inner.get("exitCode")
            bid = f"pi_bash_{entry_idx}"
            messages.append(
                _msg(
                    "assistant",
                    f"[tool_call bash id={bid}]\n{_serialize_args({'command': command})}",
                    ts,
                )
            )
            result_text = f"exit {exit_code}\n{output}" if exit_code not in (None, 0) else output
            messages.append(_msg("user", f"[tool_result id={bid}]\n{result_text}", ts))
        else:
            summary = inner.get("summary")
            if isinstance(summary, str) and summary.strip():
                # custom/branchSummary/compactionSummary — 保留为assistant文本注记
                messages.append(_msg("assistant", f"[{role}] {summary}", ts))
            else:
                skipped += 1

    title = summarize_first_line(first_user_text) if first_user_text else ""
    if not title:
        title = f"Imported pi session {session_id}"
    now = time.time()
    fallback_ts = first_ts if first_ts is not None else now
    return ConvertedSession(
        source_format="pi",
        source_id=session_id,
        title=title,
        working_dir=cwd,
        created_at=fallback_ts,
        updated_at=last_ts if last_ts is not None else fallback_ts,
        messages=messages,
        usage=usage,
        malformed_lines=malformed,
        skipped_lines=skipped,
        images_skipped=images,
    )


def convert(content: str) -> ConvertedSession:
    """嗅探+分发：外部transcript → canonical会话。未知格式显式报错（fail-visible）。"""
    fmt = detect_format(content)
    if fmt == "claude_code":
        return convert_claude_code(content)
    if fmt == "codex":
        return convert_codex(content)
    if fmt == "pi":
        return convert_pi(content)
    raise ImportFormatError(
        f"unsupported session format (sniffed='{fmt}'): expected Claude Code / Codex / Pi jsonl"
    )


_SCHEMA_DDL = (
    """CREATE TABLE IF NOT EXISTS agent_sessions (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        title TEXT DEFAULT 'New Chat',
        created_at REAL NOT NULL,
        last_activity_at REAL,
        message_count INTEGER DEFAULT 0,
        archived INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS agent_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp REAL NOT NULL,
        attachments TEXT,
        parent_message_id INTEGER,
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
    )""",
)


def import_to_db(
    conv: ConvertedSession, db_path: str = OPENSOUL_DB, agent_id: str = "imported"
) -> dict:
    """canonical会话写入agent_sessions/agent_messages（生产schema，acp-proxy ws_chat同库）。

    去重契约：确定性主键 import:{format}:{source_id} — 同一transcript重复导入零写入。
    """
    session_id = f"import:{conv.source_format}:{conv.source_id}"
    stats = {
        "session_id": session_id,
        "source_format": conv.source_format,
        "source_session_id": conv.source_id,
        "title": conv.title,
        "status": "imported",
        "messages_written": 0,
        "message_count": len(conv.messages),
        "usage": dict(conv.usage),
        "malformed_lines": conv.malformed_lines,
        "skipped_lines": conv.skipped_lines,
        "images_skipped": conv.images_skipped,
        "working_dir": conv.working_dir,
    }
    if not conv.messages:
        # 空会话不写库，但必须显式报告（禁止静默）
        stats["status"] = "empty"
        return stats
    conn = sqlite3.connect(db_path)
    try:
        for ddl in _SCHEMA_DDL:
            conn.execute(ddl)
        # P0-10消息树：老库缺parent_message_id列时probe+ALTER+backfill（幂等）
        ensure_parent_column(conn)
        existing = conn.execute(
            "SELECT 1 FROM agent_sessions WHERE id = ? LIMIT 1", (session_id,)
        ).fetchone()
        if existing:
            stats["status"] = "duplicate"
            return stats
        conn.execute(
            "INSERT INTO agent_sessions (id, agent_id, title, created_at, last_activity_at, message_count, archived)"
            " VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                session_id,
                agent_id,
                conv.title,
                conv.created_at,
                conv.updated_at,
                len(conv.messages),
            ),
        )
        # P0-10消息树parentId（pi追加树）：导入transcript是顺序追加的，
        # 每条消息parent=本会话内前一条新行id（线性主干链）
        prev_id = None
        for m in conv.messages:
            cur = conn.execute(
                "INSERT INTO agent_messages "
                "(session_id, role, content, timestamp, parent_message_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, m["role"], m["content"], m["timestamp"], prev_id),
            )
            prev_id = cur.lastrowid
        conn.commit()
        stats["messages_written"] = len(conv.messages)
        return stats
    finally:
        conn.close()
