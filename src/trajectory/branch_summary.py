"""切分支自动摘要 — pi branch-summarization.ts移植（P0-10会话树升级，上轮遗留#1销账）。

调研来源（源码级，本地clone ~/agent-research-src/pi逐文件精读）：
- packages/coding-agent/src/core/compaction/branch-summarization.ts（382行全文）：
  collectEntriesForBranchSummary（oldLeaf→公共祖先收集，时间序）+
  prepareBranchEntries（token预算 newest-first 选择 + summary类条目90%内挤入 +
  嵌套branch_summary的readFiles/modifiedFiles累积透传）+
  generateBranchSummary（结构化prompt：Goal/Constraints&Preferences/
  Progress(Done/In Progress/Blocked)/Key Decisions/Next Steps + BRANCH_SUMMARY_PREAMBLE
  "The user explored a different conversation branch before returning here." +
  read/modified文件清单<read-files> XML附尾 + 空内容显式"No content to summarize"）
- packages/coding-agent/src/core/compaction/utils.ts（全文）：
  serializeConversation（[User]/[Assistant]/[Assistant thinking]/[Assistant tool calls]/
  [Tool result]序列化，tool result 2000字符截断显式标记）+ computeFileLists
  （readFiles=只读未改，modifiedFiles=edited∪written排序）+ formatFileOperations（XML标签）
- packages/coding-agent/src/core/agent-session.ts:3167 navigateTree（触发点）：
  "generates a summary of the branch being left so context isn't lost"——离开分支时
  摘要被离开的分支，BranchSummaryEntry挂在导航目标位置，参与LLM上下文
  （createBranchSummaryMessage，messages.ts:100）
- 15-pi-source.md #1：会话=追加树(id/parentId)，分支导航+离开分支自动摘要（P0）

OpenSoul语义映射：
- 树导航（pi navigateTree同会话内）→ POST /{session_id}/branch-summaries
  {from_message_id=oldLeaf, target_message_id=navigate target}
- fork（open-webui语义已在message_tree.py落地）→ 用户从源会话叶节点"导航"到fork点，
  被离开的分支=源会话fork点之后的尾部消息——fork成功后自动生成branch summary，
  挂在fork产物会话（pi"summary attached at navigation target position"）
- 存储独立表agent_branch_summaries（canonical agent_messages不加行，避免污染
  role/content契约），确定性主键 branchsum:{session_id}:{from}:{target}
  （agno idempotency_key：重复摘要零重写status=duplicate）
- LLM上下文注入消费方：acp-proxy soulmate_agent._load_messages_from_db
  （真实聊天路径stale-session恢复时读agent_messages——同库同表）前置注入pi语义note

失败可见（evolution-engine-patterns §1.1 mem0"禁止静默降级"）：
- old leaf/target不存在→MessageNotFoundError；parent链环→CycleError（复用
  message_tree异常族，调用方与fork端点同款HTTP映射）
- 空分支→status=no_content显式返回，不写库不伪造摘要
- 自定义summarizer异常→降级extractive并显式标记summarizer=extractive-fallback
  + stats携带error（绝不静默吞掉LLM失败装作成功）

LLM summarizer（15:27轮遗留#1销账，本轮接线）：pi用LLM生成结构化摘要（provider依赖），
本模块交付完整管线 + 可插拔SummarizerFn契约 + llm_branch_summarizer默认LLM实现
（gland ModelRouter + BRANCH_SUMMARY_PROMPT原文 + pi EXACT格式门禁 + 失败可见降级
extractive-fallback）。API层经resolve_summarizer(mode)解析：auto=provider已配置→LLM，
env BRANCH_SUMMARY_SUMMARIZER可强制extractive/llm。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from src.cortex.token_attribution import estimate_tokens
from src.trajectory.message_tree import (
    CycleError,
    MessageNotFoundError,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)

# pi branch-summarization.ts原文（BRANCH_SUMMARY_PREAMBLE / BRANCH_SUMMARY_PROMPT）
BRANCH_SUMMARY_PREAMBLE = (
    "The user explored a different conversation branch before returning here.\n"
    "Summary of that exploration:\n\n"
)

BRANCH_SUMMARY_PROMPT = """Create a structured summary of this conversation branch for context when returning later.

Use this EXACT format:

## Goal
[What was the user trying to accomplish in this branch?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Work that was started but not finished]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next to continue this work]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

# pi utils.ts：tool result截断上限 + 显式截断标记
TOOL_RESULT_MAX_CHARS = 2000

# canonical内容中的工具标记（import_formats.py序列化协议）：
# [tool_call {name} id={id}] / [tool_result id=.. error=1]
_TOOL_CALL_RE = re.compile(r"\[tool_call (\S+) id=")
_TOOL_RESULT_RE = re.compile(r"\[tool_result id=(\S+?)( error=1)?\]")

# extractive Key Decisions启发式（决定/选择/采用/改用/decided/switched/chose…）
_DECISION_RE = re.compile(
    r"决定|选择|采用|改用|改成|调整为|决定用|decided|switched to|chose|opted for", re.IGNORECASE
)
_CONSTRAINT_RE = re.compile(
    r"必须|要求|不得|不能|注意|不要|must|require|should not|do not", re.IGNORECASE
)
_INTERROGATIVE_RE = re.compile(
    r"[?？]$|吗$|呢$|请|如何|怎么|为什么|^how|^what|^why|^please", re.IGNORECASE
)

# extractive摘要中工具调用/错误/约束/决策条目数量上限（防超长分支摘要膨胀）
_MAX_ITEMS = 8

SummarizerFn = Callable[[str, list[dict]], str]


# ── Schema ─────────────────────────────────────────────────────────


def ensure_branch_summary_table(conn: sqlite3.Connection) -> bool:
    """幂等建表agent_branch_summaries。返回True=本轮创建。

    from/target存TEXT：agent_messages.id是INTEGER rowid，但源会话与fork产物
    会话的消息id分属不同会话命名空间，统一TEXT避免跨会话id混淆。
    """
    existed = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_branch_summaries'"
    ).fetchone()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_branch_summaries (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            source_session_id TEXT,
            from_message_id TEXT NOT NULL,
            target_message_id TEXT NOT NULL,
            common_ancestor_id TEXT,
            summary TEXT NOT NULL,
            read_files TEXT,
            modified_files TEXT,
            tool_ops TEXT,
            entries_count INTEGER DEFAULT 0,
            summarizer TEXT,
            source TEXT,
            created_at REAL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_branch_summaries_session "
        "ON agent_branch_summaries(session_id)"
    )
    conn.commit()
    return existed is None


# ── Entry Collection（pi collectEntriesForBranchSummary移植）───────


def _walk_path(mmap: dict[str, dict], start_id: str) -> list[str]:
    """从start沿parent回溯到根，返回根→start的id列表。

    seen set环检测（message_tree.build_branch同款fail-visible）：
    - start不在表中 → MessageNotFoundError
    - parent链成环 → CycleError（损坏数据不挂死调用方）
    """
    if start_id not in mmap:
        raise MessageNotFoundError(f"message not found: {start_id}")
    path: list[str] = []
    seen: set[str] = set()
    cur = start_id
    while cur:
        if cur in seen:
            raise CycleError(f"message branch contains a cycle (at id={cur})")
        seen.add(cur)
        node = mmap[cur]
        path.append(cur)
        parent = node.get("parent_id")
        cur = str(parent) if parent is not None else ""
    path.reverse()
    return path


def collect_entries_for_branch(rows: list[dict], old_leaf_id, target_id) -> dict[str, Any]:
    """pi collectEntriesForBranchSummary语义：收集"被离开的分支"条目。

    rows：会话消息dict列表（键id/role/content/timestamp/parent_id，parent兼容
    parent_message_id别名）。old_leaf=离开前所在叶，target=导航目标。
    - 公共祖先=old路径与target路径的最深公共节点（pi：targetPath从尾部逆序找）
    - entries=old leaf→公共祖先（不含祖先）的时间序条目（pi reverse同款）
    - old leaf == target → 空entries（pi no-op语义）
    返回{entries, common_ancestor_id, old_leaf_id, target_id}。
    异常：old/target不在表中→MessageNotFoundError；环→CycleError；
    rows为空→MessageNotFoundError（显式，不静默空分支）。
    """
    old_leaf_id = str(old_leaf_id)
    target_id = str(target_id)
    mmap: dict[str, dict] = {}
    for m in rows:
        mid = m.get("id")
        if mid is None:
            continue
        entry = dict(m)
        parent = entry.get("parent_id")
        if parent is None:
            parent = entry.get("parent_message_id")
        entry["parent_id"] = str(parent) if parent is not None else None
        entry["id"] = str(mid)
        mmap[entry["id"]] = entry
    if not mmap:
        raise MessageNotFoundError("session has no messages")

    old_path_ids = _walk_path(mmap, old_leaf_id)  # 根→old leaf
    old_path_set = set(old_path_ids)
    target_path_ids = _walk_path(mmap, target_id)  # 根→target

    common_ancestor_id = None
    for tid in reversed(target_path_ids):  # 最深公共节点优先（pi同款）
        if tid in old_path_set:
            common_ancestor_id = tid
            break

    entries: list[dict] = []
    if old_leaf_id != target_id and common_ancestor_id is not None:
        cur = old_leaf_id
        seen: set[str] = set()
        while cur and cur != common_ancestor_id:
            if cur in seen:
                raise CycleError(f"message branch contains a cycle (at id={cur})")
            seen.add(cur)
            node = mmap[cur]
            entries.append(
                {
                    "id": node["id"],
                    "role": node.get("role") or "",
                    "content": node.get("content") or "",
                    "timestamp": node.get("timestamp"),
                    "kind": "message",
                }
            )
            parent = node.get("parent_id")
            cur = str(parent) if parent is not None else ""
        entries.reverse()  # 时间序（pi同款）

    return {
        "entries": entries,
        "common_ancestor_id": common_ancestor_id,
        "old_leaf_id": old_leaf_id,
        "target_id": target_id,
    }


# ── 序列化（pi utils.ts serializeConversation移植）─────────────────


def _truncate_for_summary(text: str, max_chars: int = TOOL_RESULT_MAX_CHARS) -> str:
    """pi truncateForSummary：保留头部+显式截断标记（禁止静默截断）。"""
    if len(text) <= max_chars:
        return text
    truncated_chars = len(text) - max_chars
    return f"{text[:max_chars]}\n\n[... {truncated_chars} more characters truncated]"


def serialize_conversation(entries: list[dict]) -> str:
    """pi serializeConversation：canonical条目→摘要用序列化文本。

    防LLM把摘要输入当对话续写（pi注释语义）。canonical内容标记映射：
    [thinking]..[/thinking]→[Assistant thinking]；[tool_call name id=..]→
    [Assistant tool calls]；[tool_result id=.. error=1]→[Tool result]（2000截断）。
    纯文本user/assistant→[User]/[Assistant]。
    """
    parts: list[str] = []
    for msg in entries:
        role = (msg.get("role") or "").lower()
        content = msg.get("content") or ""
        if role == "user":
            # user侧tool_result标记（import_formats把工具响应随user消息）单独展开
            result_parts = []
            plain = content
            for m in _TOOL_RESULT_RE.finditer(content):
                marker = m.group(0)
                plain = plain.replace(marker, "")
                err = " [ERROR]" if m.group(2) else ""
                result_parts.append(f"[Tool result {m.group(1)}{err}]")
            plain = plain.strip()
            if plain:
                parts.append(f"[User]: {_truncate_for_summary(plain)}")
            if result_parts:
                parts.append(" ".join(result_parts))
            continue
        if role != "assistant":
            if content.strip():
                parts.append(f"[{role or 'unknown'}]: {_truncate_for_summary(content.strip())}")
            continue
        # assistant：thinking/tool_call标记展开 + 纯文本
        thinking_parts = []
        for tm in re.finditer(r"\[thinking\](.*?)\[/thinking\]", content, re.DOTALL):
            thinking_parts.append(tm.group(1).strip())
        tool_calls = _TOOL_CALL_RE.findall(content)
        text = re.sub(r"\[thinking\].*?\[/thinking\]", "", content, flags=re.DOTALL)
        text = _TOOL_CALL_RE.sub("", text)
        text = re.sub(r"\[tool_call[^\]]*\]", "", text)
        text = text.strip()
        if thinking_parts:
            parts.append(f"[Assistant thinking]: {' '.join(thinking_parts)}")
        if text:
            parts.append(f"[Assistant]: {_truncate_for_summary(text)}")
        if tool_calls:
            parts.append(f"[Assistant tool calls]: {'; '.join(tool_calls)}")
    return "\n\n".join(parts)


# ── 工具操作提取（pi utils.ts extractFileOpsFromMessage语义）────────


def extract_tool_ops(entries: list[dict]) -> dict[str, list[str]]:
    """从canonical内容标记提取工具级操作分类（pi fileOps的诚实降级版）。

    pi从结构化toolCall args提取read/write/edit文件路径；canonical agent_messages
    的工具标记不含args（真实生产库0条tool_call标记，导入transcript标记无args），
    文件路径级归因不可得——如实返回工具名级分类，绝不编造路径：
    - read：read_file/read_file_segment/search_files等只读工具名
    - write：write_file/patch/execute_code等有副作用工具名
    - error：[tool_result id=.. error=1]对应工具名（失败必须可见）
    嵌套branch summary的文件清单累积由调用方经existing_summaries传入
    （pi first pass cumulative tracking语义）。
    """
    read_tools: set[str] = set()
    write_tools: set[str] = set()
    error_ids: set[str] = set()
    id_to_name: dict[str, str] = {}
    for msg in entries:
        content = msg.get("content") or ""
        for name in _TOOL_CALL_RE.findall(content):
            if name in (
                "read_file",
                "read_file_segment",
                "search_files",
                "web_search",
                "web_extract",
            ):
                read_tools.add(name)
            elif name in ("question", "suggest", "todo", "notice", "clarify"):
                continue  # 不计分工具（goal_runner none_tools同款语义）
            else:
                write_tools.add(name)
            # 记录最近一次同名调用的id（error结果按id回溯）
            for m in re.finditer(r"\[tool_call " + re.escape(name) + r" id=(\S+?)\]", content):
                id_to_name[m.group(1)] = name
        for m in _TOOL_RESULT_RE.finditer(content):
            if m.group(2):
                error_ids.add(m.group(1))
    errors = sorted({id_to_name.get(i, i) for i in error_ids})
    return {
        "read": sorted(read_tools),
        "write": sorted(write_tools - set(errors)),
        "error": errors,
    }


def compute_file_lists(file_ops: dict[str, set]) -> tuple[list[str], list[str]]:
    """pi computeFileLists：readFiles=只读未改；modifiedFiles=edited∪written排序。"""
    modified = set(file_ops.get("edited", set())) | set(file_ops.get("written", set()))
    read_only = sorted(set(file_ops.get("read", set())) - modified)
    return read_only, sorted(modified)


def format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    """pi formatFileOperations：<read-files>/<modified-files> XML标签附尾，空则空串。"""
    sections: list[str] = []
    if read_files:
        sections.append(f"<read-files>\n{chr(10).join(read_files)}\n</read-files>")
    if modified_files:
        sections.append(f"<modified-files>\n{chr(10).join(modified_files)}\n</modified-files>")
    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections)


# ── 预算化选择（pi prepareBranchEntries移植）───────────────────────


def prepare_branch_entries(
    entries: list[dict], token_budget: int = 0, existing_summaries: list[dict] | None = None
) -> dict[str, Any]:
    """pi prepareBranchEntries：newest→oldest预算化选择 + 嵌套摘要文件清单累积。

    - existing_summaries（pi first pass）：收集分支路径上既有branch summary的
      read_files/modified_files/tool_ops累积（即使对应条目不在预算内）
    - token_budget=0表示不限；超预算时summary类条目（kind!=message）在总token
      <90%预算时仍挤入（pi同款：摘要条目是重要上下文）
    返回{entries(时间序), tool_ops, total_tokens, read_files, modified_files}。
    """
    file_ops: dict[str, set] = {"read": set(), "written": set(), "edited": set()}
    tool_ops_acc: dict[str, set] = {"read": set(), "write": set(), "error": set()}
    # pi first pass：既有嵌套摘要的累积文件清单（全部收集，不受预算影响）
    for s in existing_summaries or []:
        for f in _json_list(s.get("read_files")):
            file_ops["read"].add(f)
        for f in _json_list(s.get("modified_files")):
            file_ops["edited"].add(f)
        ops = s.get("tool_ops") or {}
        if isinstance(ops, dict):
            for k in ("read", "write", "error"):
                for v in ops.get(k) or []:
                    tool_ops_acc[k].add(v)
        elif isinstance(ops, str):
            try:
                parsed = json.loads(ops)
                for k in ("read", "write", "error"):
                    for v in parsed.get(k) or []:
                        tool_ops_acc[k].add(v)
            except (json.JSONDecodeError, AttributeError):
                pass

    # pi first pass（工具标记）：跨全部entries一次性提取——error结果标记与
    # tool_call标记常分属不同消息（工具响应随user消息），逐条提取会丢失
    # id→工具名映射导致error归因退化为裸id。不受token预算影响（pi同款）。
    all_ops = extract_tool_ops(entries)
    for k in ("read", "write", "error"):
        tool_ops_acc[k].update(all_ops[k])

    selected: list[dict] = []
    total_tokens = 0
    for i in range(len(entries) - 1, -1, -1):  # newest→oldest
        entry = entries[i]
        content = entry.get("content") or ""
        tokens = estimate_tokens(content)
        if token_budget > 0 and total_tokens + tokens > token_budget:
            # pi：summary/compaction类条目在总token<90%预算时仍挤入
            if (
                entry.get("kind") in ("branch_summary", "compaction")
                and total_tokens < token_budget * 0.9
            ):
                selected.insert(0, entry)
                total_tokens += tokens
            break
        selected.insert(0, entry)
        total_tokens += tokens

    return {
        "entries": selected,
        "tool_ops": {k: sorted(v) for k, v in tool_ops_acc.items()},
        "total_tokens": total_tokens,
    }


def _json_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return [str(v) for v in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


# ── 摘要生成（pi generateBranchSummary移植）────────────────────────


def _first_line(text: str, limit: int = 160) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def extractive_summary(entries: list[dict], tool_ops: dict[str, list[str]]) -> str:
    """确定性extractive摘要：按pi BRANCH_SUMMARY_PROMPT的EXACT格式逐段填充。

    全部段落从entries内容如实提取（无内容→"(none)"），绝不编造。
    LLM summarizer未接入前的默认实现；BRANCH_SUMMARY_PROMPT保留pi原文。
    """
    user_entries = [e for e in entries if (e.get("role") or "").lower() == "user"]
    assistant_entries = [e for e in entries if (e.get("role") or "").lower() == "assistant"]

    # Goal：首个用户消息首行
    goal = _first_line(user_entries[0]["content"]) if user_entries else "(unknown)"

    # Constraints & Preferences：用户消息中的约束关键词行
    constraints: list[str] = []
    for e in user_entries:
        for line in (e.get("content") or "").splitlines():
            line = line.strip()
            if line and _CONSTRAINT_RE.search(line) and len(constraints) < _MAX_ITEMS:
                constraints.append(f"- {_first_line(line, 120)}")
    constraints_text = "\n".join(constraints) if constraints else "- (none)"

    # Done：无错误工具调用聚合 + assistant文本中显式"- [x]"行
    done_items: list[str] = []
    call_counts: dict[str, int] = {}
    for e in entries:
        for name in _TOOL_CALL_RE.findall(e.get("content") or ""):
            if name not in tool_ops.get("error", []):
                call_counts[name] = call_counts.get(name, 0) + 1
    for name in sorted(call_counts)[:_MAX_ITEMS]:
        suffix = f" ×{call_counts[name]}" if call_counts[name] > 1 else ""
        done_items.append(f"- [x] {name}{suffix}")
    for e in assistant_entries:
        for line in (e.get("content") or "").splitlines():
            s = line.strip()
            if s.startswith("- [x]") and len(done_items) < _MAX_ITEMS:
                done_items.append(s)
    done_text = "\n".join(done_items) if done_items else "- (none)"

    # In Progress：最后一条是用户消息=尚未回应的请求（pi "started but not finished"）
    in_progress = "- (none)"
    if entries and (entries[-1].get("role") or "").lower() == "user":
        in_progress = f"- [ ] pending request: {_first_line(entries[-1].get('content') or '')}"

    # Blocked：error标记工具 + 显式错误关键词行（mem0失败必须可见）
    blocked_items = [f"- [!] {name} failed" for name in tool_ops.get("error", [])[:_MAX_ITEMS]]
    for e in entries:
        for line in (e.get("content") or "").splitlines():
            s = line.strip()
            if (
                s
                and re.search(r"推理错误|Traceback|BLOCKED|被拦截", s)
                and len(blocked_items) < _MAX_ITEMS
            ):
                blocked_items.append(f"- {_first_line(s, 120)}")
    blocked_text = "\n".join(blocked_items) if blocked_items else "- (none)"

    # Key Decisions：assistant文本中的决策关键词行
    decisions: list[str] = []
    for e in assistant_entries:
        for line in (e.get("content") or "").splitlines():
            s = line.strip()
            if s and _DECISION_RE.search(s) and len(decisions) < _MAX_ITEMS:
                decisions.append(f"- {_first_line(s, 120)}")
    decisions_text = "\n".join(decisions) if decisions else "- (none)"

    # Next Steps：末条用户消息若为疑问/指令
    next_steps = "(none)"
    if user_entries:
        last_user = _first_line(user_entries[-1].get("content") or "")
        if last_user and _INTERROGATIVE_RE.search(last_user):
            next_steps = f"1. {last_user}"

    return (
        f"## Goal\n{goal}\n\n"
        f"## Constraints & Preferences\n{constraints_text}\n\n"
        f"## Progress\n### Done\n{done_text}\n\n"
        f"### In Progress\n{in_progress}\n\n"
        f"### Blocked\n{blocked_text}\n\n"
        f"## Key Decisions\n{decisions_text}\n\n"
        f"## Next Steps\n{next_steps}"
    )


# ── LLM summarizer（pi generateBranchSummary的LLM路径，15:27轮遗留#1销账）──

# pi BRANCH_SUMMARY_PROMPT要求EXACT格式——LLM输出设格式门禁（deepagents
# RubricMiddleware"完成=裁判通过"哲学）：空输出/缺必备段落→显式失败→
# extractive降级，消费方永远拿到pi契约内的结构化摘要，绝不接收格式漂移输出。
_LLM_REQUIRED_SECTIONS = ("## Goal", "## Progress")
# LLM调用超时（秒）：摘要是非关键路径，超时=降级extractive，不阻塞fork/导航。
LLM_SUMMARIZER_TIMEOUT = 60.0


async def _call_llm_router(system_prompt: str, user_prompt: str, router_factory=None) -> str:
    """gland ModelRouter调用 — dream_distiller._call_gland_llm同款已验证模式。

    fresh ModelRouter实例绑定当前event loop + 显式provider注册（api/gland.py
    gateway单例跨loop复用会400——dream_distiller live实证教训）+
    extract_chat_text权威解包（result.get("content")猜测在真实provider上永远
    落空——dream live实证bug）。provider链=settings主provider（priority=0）
    + 变体备胎（priority=5，register_variant_backup单一真源——此前本块漏注册
    变体备胎，primary 402时branch摘要饿死降级extractive-fallback，与dream/
    Phase1修复前同款形态）+ ollama本地兜底（priority=10，CowAgent有序降级链语义）。

    router_factory：测试seam（call_memory_llm.router_factory同款先例）——返回
    预配置好transport的ModelRouter（如httpx.MockTransport），注册逻辑照常执行。
    """
    from src.config import settings
    from src.gland.router import ModelRouter, extract_chat_text

    router = router_factory() if router_factory is not None else ModelRouter()
    if settings.llm_base_url:
        router.add_provider(
            name="openai",
            base_url=settings.llm_base_url,
            models={"chat": settings.llm_model},
            priority=0,
        )
        if settings.llm_api_key:
            router.key_manager.add_key("openai", settings.llm_api_key)
    # 变体备胎入链（priority=5，单一真源register_variant_backup）。
    try:
        from src.api.llm import register_variant_backup

        register_variant_backup(router, settings.llm_model)
    except Exception:  # noqa: BLE001 — 备胎注册绝不反噬摘要调用
        pass
    ollama_url = getattr(settings, "ollama_base_url", "http://localhost:11434/v1")
    router.add_provider(
        name="ollama",
        base_url=ollama_url,
        models={"chat": "deepseek-r1:latest"},
        priority=10,
    )
    result = await router.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,  # 摘要要稳定复现（dream_distiller 0.3同源理由）
        max_tokens=2048,
        role="summarize",  # Harness Profile: summarize role → own model/budget
    )
    return extract_chat_text(result)


async def _summarize_via_llm(conversation_text: str) -> str:
    """带超时的LLM摘要调用：超时/失败上抛，由generate_branch_summary统一降级。"""
    return await asyncio.wait_for(
        _call_llm_router(BRANCH_SUMMARY_PROMPT, conversation_text),
        timeout=LLM_SUMMARIZER_TIMEOUT,
    )


def _run_coro_sync(coro):
    """sync SummarizerFn契约下执行async LLM调用：独立线程+独立event loop。

    为什么线程：SummarizerFn=Callable[[str,list],str]是同步契约（generate_
    branch_summary同步编排），而调用方是FastAPI async端点——在running loop里
    asyncio.run会直接抛错。线程内新建loop，router在该loop内创建（loop亲和，
    dream_distiller教训），从sync/async任何调用方进入都不死锁。
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _validate_llm_summary(body: str) -> str:
    """pi EXACT格式门禁：空输出/缺必备段落→显式失败（绝不静默接收漂移格式）。"""
    text = (body or "").strip()
    if not text:
        raise ValueError("LLM summarizer returned empty body")
    missing = [s for s in _LLM_REQUIRED_SECTIONS if s not in text]
    if missing:
        raise ValueError(f"LLM summary missing required sections: {', '.join(missing)}")
    return text


def llm_branch_summarizer(conversation_text: str, entries: list[dict]) -> str:
    """pi generateBranchSummary的LLM summarizer（BRANCH_SUMMARY_PROMPT原文驱动）。

    entries参数为契约兼容（extractive需要条目做启发式，LLM只吃序列化文本）。
    失败语义：任何异常（provider全链失败/超时/格式门禁不过）上抛，由
    generate_branch_summary降级extractive + summarizer="extractive-fallback"
    + error可见——失败必须可见（evolution-engine-patterns §1.1 mem0）。
    """
    body = _run_coro_sync(_summarize_via_llm(conversation_text))
    return _validate_llm_summary(body)


llm_branch_summarizer.summarizer_kind = "llm"


def resolve_summarizer(mode: str | None = None) -> SummarizerFn | None:
    """API层summarizer模式解析（LLM summarizer运行时接线）。

    解析优先级：显式mode参数 > 环境变量BRANCH_SUMMARY_SUMMARIZER > "auto"。
    - "llm"：强制LLM（不做provider预判——失败由降级路径显式可见）
    - "extractive"：强制确定性启发式（离线/测试环境，无网络依赖）
    - "auto"：provider已配置（settings.llm_api_key非空 / ollama_base_url显式
      配置 / llm_base_url非OpenAI默认值，dream_distiller同款判定家族）→
      LLM summarizer；否则确定性extractive
    - 未知模式→ValueError（fail-closed，API层映射400）
    环境变量每次调用live读取（非settings实例缓存）：测试setenv即时生效。
    """
    resolved = mode or os.environ.get("BRANCH_SUMMARY_SUMMARIZER", "") or ""
    resolved = resolved.strip().lower() or None
    if resolved is None:
        resolved = "auto"
    if resolved == "extractive":
        return None
    if resolved == "llm":
        return llm_branch_summarizer
    if resolved == "auto":
        from src.config import settings

        provider_ready = (
            bool(settings.llm_api_key)
            or bool(getattr(settings, "ollama_base_url", ""))
            or (settings.llm_base_url not in ("", "https://api.openai.com/v1"))
        )
        return llm_branch_summarizer if provider_ready else None
    raise ValueError(f"unknown summarizer mode: {mode!r}")


def generate_branch_summary(
    entries: list[dict],
    summarizer: SummarizerFn | None = None,
    token_budget: int = 0,
    existing_summaries: list[dict] | None = None,
) -> dict[str, Any]:
    """pi generateBranchSummary语义：生成"被离开分支"的结构化摘要。

    - entries为空 → {status: "no_content", summary: None}（pi "No content to
      summarize"，显式不写库不伪造）
    - summarizer=None → 确定性extractive（summarizer_used="extractive"）
    - summarizer callable(conversation_text, entries) -> str；异常时降级extractive，
      summarizer_used="extractive-fallback" + error字段（失败可见，绝不静默）；
      callable带summarizer_kind属性时summarizer_used取该值（llm路径标注"llm"，
      无标注的自定义callable保持"custom"）
    - 最终summary = BRANCH_SUMMARY_PREAMBLE + 正文 + <read-files>/<modified-files>
      附尾（pi同款三段结构）
    """
    if not entries:
        return {"status": "no_content", "summary": None, "entries_count": 0}

    prep = prepare_branch_entries(entries, token_budget, existing_summaries)
    tool_ops = prep["tool_ops"]
    read_files, modified_files = compute_file_lists(
        {
            "read": set(tool_ops.get("read", []))
            | _summary_file_set(existing_summaries, "read_files"),
            "written": set(tool_ops.get("write", [])),
            "edited": _summary_file_set(existing_summaries, "modified_files"),
        }
    )

    conversation_text = serialize_conversation(prep["entries"])
    error: str | None = None
    if summarizer is not None:
        try:
            body = summarizer(conversation_text, prep["entries"])
            # summarizer_kind属性标注（llm_branch_summarizer.summarizer_kind="llm"）；
            # 无标注的自定义callable保持"custom"（既有契约test_custom_summarizer_used）
            summarizer_used = getattr(summarizer, "summarizer_kind", "custom")
        except Exception as exc:  # noqa: BLE001 — 降级必须显式可见
            error = f"summarizer failed, fell back to extractive: {exc}"
            logger.warning("branch summary summarizer failed: %s", exc)
            body = extractive_summary(prep["entries"], tool_ops)
            summarizer_used = "extractive-fallback"
    else:
        body = extractive_summary(prep["entries"], tool_ops)
        summarizer_used = "extractive"

    summary = BRANCH_SUMMARY_PREAMBLE + body + format_file_operations(read_files, modified_files)
    result = {
        "status": "created",
        "summary": summary,
        "entries_count": len(prep["entries"]),
        "total_tokens": prep["total_tokens"],
        "read_files": read_files,
        "modified_files": modified_files,
        "tool_ops": tool_ops,
        "summarizer": summarizer_used,
    }
    if error:
        result["error"] = error
    return result


def _summary_file_set(existing_summaries: list[dict] | None, key: str) -> set[str]:
    out: set[str] = set()
    for s in existing_summaries or []:
        out.update(_json_list(s.get(key)))
    return out


# ── 存储（agno idempotency_key：确定性主键幂等）────────────────────


def record_branch_summary(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    from_message_id,
    target_message_id,
    generated: dict[str, Any],
    source_session_id: str | None = None,
    common_ancestor_id=None,
    source: str = "navigate",
) -> dict[str, Any]:
    """落库branch summary。确定性主键branchsum:{session_id}:{from}:{target}：

    重复记录返回status=duplicate零重写（agno idempotency_key语义，与
    fork:{source}:{message}同款）。返回{status, id}。
    """
    ensure_branch_summary_table(conn)
    summary_id = f"branchsum:{session_id}:{from_message_id}:{target_message_id}"
    existing = conn.execute(
        "SELECT id FROM agent_branch_summaries WHERE id = ?", (summary_id,)
    ).fetchone()
    if existing:
        return {"status": "duplicate", "id": summary_id}
    conn.execute(
        "INSERT INTO agent_branch_summaries "
        "(id, session_id, source_session_id, from_message_id, target_message_id, "
        " common_ancestor_id, summary, read_files, modified_files, tool_ops, "
        " entries_count, summarizer, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            summary_id,
            str(session_id),
            str(source_session_id) if source_session_id is not None else None,
            str(from_message_id),
            str(target_message_id),
            str(common_ancestor_id) if common_ancestor_id is not None else None,
            generated["summary"],
            json.dumps(generated.get("read_files") or [], ensure_ascii=False),
            json.dumps(generated.get("modified_files") or [], ensure_ascii=False),
            json.dumps(generated.get("tool_ops") or {}, ensure_ascii=False),
            generated.get("entries_count") or 0,
            generated.get("summarizer"),
            source,
            time.time(),
        ),
    )
    conn.commit()
    return {"status": "created", "id": summary_id}


def get_branch_summaries(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """读取会话的branch summaries（pi BranchSummaryEntry读侧，按时间序）。

    表不存在=无摘要，返回[]（幂等自愈：顺带建表）。created_at转ISO文本。
    """
    ensure_branch_summary_table(conn)
    rows = conn.execute(
        "SELECT * FROM agent_branch_summaries WHERE session_id = ? ORDER BY created_at, id",
        (str(session_id),),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        ts = d.get("created_at")
        d["created_at"] = datetime.fromtimestamp(ts, tz=UTC).isoformat() if ts else None
        d["read_files"] = _json_list(d.get("read_files"))
        d["modified_files"] = _json_list(d.get("modified_files"))
        ops = d.get("tool_ops")
        try:
            d["tool_ops"] = json.loads(ops) if isinstance(ops, str) and ops else (ops or {})
        except json.JSONDecodeError:
            d["tool_ops"] = {}
        out.append(d)
    return out


def delete_branch_summaries(conn: sqlite3.Connection, session_id: str) -> int:
    """删除会话全部branch summaries（session DELETE端点配套清理，防孤儿行）。"""
    ensure_branch_summary_table(conn)
    cur = conn.execute(
        "DELETE FROM agent_branch_summaries WHERE session_id = ?", (str(session_id),)
    )
    conn.commit()
    return cur.rowcount or 0


def list_branch_points(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """会话内分支点列表：parent_message_id有≥2个子消息的节点（可观测性读侧）。

    pi树导航/branch summary的可视化数据源：分支点+各子分支首条消息id。
    """
    rows = conn.execute(
        "SELECT id, parent_message_id FROM agent_messages WHERE session_id = ? ORDER BY id",
        (str(session_id),),
    ).fetchall()
    children: dict[str, list] = {}
    for r in rows:
        parent = r["parent_message_id"]
        if parent is not None:
            children.setdefault(str(parent), []).append(r["id"])
    points = []
    for parent_id, kids in sorted(
        children.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0
    ):
        if len(kids) >= 2:
            points.append(
                {"branch_point_message_id": parent_id, "children": [str(k) for k in kids]}
            )
    return points


# ── 编排器（API层消费）─────────────────────────────────────────────


def summarize_branch(
    db_path: str,
    session_id: str,
    old_leaf_id,
    target_id,
    summarizer: SummarizerFn | None = None,
    source: str = "navigate",
    token_budget: int = 0,
) -> dict[str, Any]:
    """同会话树导航摘要（pi navigateTree {summarize:true}语义）。

    收集old_leaf→公共祖先的被离开分支 → 生成摘要 → 记录到session_id名下。
    返回stats：status(created|duplicate|no_content)/summary_id/entries_count/
    common_ancestor_id/summarizer/read_files/modified_files。
    异常上抛：MessageNotFoundError/CycleError由API层映射HTTP状态码。
    """
    session_id = str(session_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_branch_summary_table(conn)
        rows = conn.execute(
            "SELECT id, role, content, timestamp, parent_message_id "
            "FROM agent_messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        collected = collect_entries_for_branch([dict(r) for r in rows], old_leaf_id, target_id)
        entries = collected["entries"]
        # 嵌套摘要累积（pi first pass）：同会话既有摘要中from在本分支路径上的
        path_ids = {e["id"] for e in entries} | {str(collected["common_ancestor_id"])}
        existing = [
            s
            for s in get_branch_summaries(conn, session_id)
            if str(s.get("from_message_id")) in path_ids
            or str(s.get("target_message_id")) in path_ids
        ]
        generated = generate_branch_summary(entries, summarizer, token_budget, existing)
        if generated["status"] == "no_content":
            return {
                "status": "no_content",
                "session_id": session_id,
                "from_message_id": str(old_leaf_id),
                "target_message_id": str(target_id),
                "common_ancestor_id": collected["common_ancestor_id"],
                "entries_count": 0,
            }
        rec = record_branch_summary(
            conn,
            session_id=session_id,
            source_session_id=session_id,
            from_message_id=old_leaf_id,
            target_message_id=target_id,
            generated=generated,
            common_ancestor_id=collected["common_ancestor_id"],
            source=source,
        )
        stats = {
            "status": rec["status"],
            "summary_id": rec["id"],
            "session_id": session_id,
            "from_message_id": str(old_leaf_id),
            "target_message_id": str(target_id),
            "common_ancestor_id": collected["common_ancestor_id"],
            "entries_count": generated["entries_count"],
            "summarizer": generated["summarizer"],
            "read_files": generated.get("read_files") or [],
            "modified_files": generated.get("modified_files") or [],
        }
        if generated.get("error"):
            stats["error"] = generated["error"]
        return stats
    finally:
        conn.close()


def summarize_fork_context(
    db_path: str,
    source_session_id: str,
    fork_point_message_id,
    fork_session_id: str,
    summarizer: SummarizerFn | None = None,
    token_budget: int = 0,
) -> dict[str, Any]:
    """fork触发的分支摘要（pi navigateTree映射到fork语义）。

    用户fork=从源会话当前叶"导航"到fork点（新会话）——被离开的分支=源会话
    fork点之后的尾部消息（collect: old leaf=源会话最后一条，target=fork点，
    公共祖先=fork点）。摘要挂fork产物会话名下（pi"summary attached at the
    navigation target position"——新上下文里保留被离开分支的context）。

    - fork点=源会话叶 → entries为空 → status=no_content（显式不写库）
    - 幂等：同fork点重复触发→branchsum:{fork_session}:{from}:{target} duplicate
    - 源会话不存在→SessionNotFoundError；消息树异常上抛
    """
    source_session_id = str(source_session_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_branch_summary_table(conn)
        src = conn.execute(
            "SELECT id FROM agent_sessions WHERE id = ?", (source_session_id,)
        ).fetchone()
        if src is None:
            raise SessionNotFoundError(f"session not found: {source_session_id}")
        rows = conn.execute(
            "SELECT id, role, content, timestamp, parent_message_id "
            "FROM agent_messages WHERE session_id = ? ORDER BY id",
            (source_session_id,),
        ).fetchall()
        row_dicts = [dict(r) for r in rows]
        if not row_dicts:
            raise MessageNotFoundError(f"session has no messages: {source_session_id}")
        old_leaf_id = str(row_dicts[-1]["id"])  # 源会话当前叶
        collected = collect_entries_for_branch(row_dicts, old_leaf_id, fork_point_message_id)
        entries = collected["entries"]
        generated = generate_branch_summary(entries, summarizer, token_budget)
        if generated["status"] == "no_content":
            return {
                "status": "no_content",
                "source_session_id": source_session_id,
                "fork_session_id": str(fork_session_id),
                "from_message_id": old_leaf_id,
                "target_message_id": str(fork_point_message_id),
                "entries_count": 0,
            }
        rec = record_branch_summary(
            conn,
            session_id=fork_session_id,
            source_session_id=source_session_id,
            from_message_id=old_leaf_id,
            target_message_id=fork_point_message_id,
            generated=generated,
            common_ancestor_id=collected["common_ancestor_id"],
            source="fork",
        )
        stats = {
            "status": rec["status"],
            "summary_id": rec["id"],
            "source_session_id": source_session_id,
            "fork_session_id": str(fork_session_id),
            "from_message_id": old_leaf_id,
            "target_message_id": str(fork_point_message_id),
            "common_ancestor_id": collected["common_ancestor_id"],
            "entries_count": generated["entries_count"],
            "summarizer": generated["summarizer"],
            "read_files": generated.get("read_files") or [],
            "modified_files": generated.get("modified_files") or [],
            "summary_preview": (generated["summary"] or "")[:300],
        }
        if generated.get("error"):
            stats["error"] = generated["error"]
        return stats
    finally:
        conn.close()
