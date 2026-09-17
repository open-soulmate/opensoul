"""Context compression engine — goose 9-section summary + kilocode chunking.

P0 gap (SUMMARY.md: "cortex | 上下文压缩引擎（9段式摘要+预算化切分+失败降级）
| goose+kilocode合读 | P0"). Before this module, long conversations overflowed
context window without graceful degradation, causing complete failures instead
of progressive summarization.

Core design (consolidated from goose structured.rs + kilocode compaction-chunks.ts):

1. **9-section structured summary** (goose): user_intent, technical_concepts,
   files, errors_and_fixes, problem_solving, user_messages, pending_tasks,
   current_work, next_step — each list ordered most-important-first so consumers
   can cut from tail without losing critical context.

2. **Budget-aware chunking** (kilocode): split oversized message sequences into
   chunks that fit summarizer's context window (60% of usable, min 1000 tokens).
   Process chunks with bounded concurrency (default 3), then reduce partial
   summaries via binary merge with depth limit (default 3).

3. **Multi-candidate JSON extraction** (goose): try fenced JSON after </analysis>
   markers (last first), then leading object. Brace-balanced extraction handles
   string values containing ```. Lenient deserialization stringifies objects/
   numbers rather than failing. Empty/blank summaries trigger raw-text fallback.

4. **Three-tier failure degradation** (kilocode): LLM error during chunk →
   retry with next chunk; all chunks fail → return "compact" signal (caller
   decides whether to truncate or abort); fatal error (non-overflow) → return
   "stop" with error. Never silently swallow failures.

5. **Preserved user message** (goose): most recent user text-only message is
   extracted before compaction and re-appended after summary, so agent can
   still "hear" the user's last request even after aggressive summarization.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Budget fraction (kilocode: RATIO=0.6)
DEFAULT_BUDGET_RATIO = 0.6
DEFAULT_MIN_BUDGET_TOKENS = 1_000
# Concurrency for parallel chunk summarization (kilocode: CONCURRENCY=3)
DEFAULT_CONCURRENCY = 3
# Binary merge depth limit (kilocode: DEPTH=3)
DEFAULT_REDUCE_DEPTH = 3
# Tool output clip threshold (kilocode: TOOL_OUTPUT_MAX_CHARS=2_000)
TOOL_OUTPUT_MAX_CHARS = 2_000
# Per-message transcript clip (kilocode: TRANSCRIPT_MAX_CHARS=16_000)
TRANSCRIPT_MAX_CHARS = 16_000
# Max summary output tokens (kilocode: SUMMARY_OUTPUT_TOKENS=4_096)
SUMMARY_OUTPUT_TOKENS = 4_096


@dataclass
class FileActivity:
    """File activity record from goose structured summary."""
    path: str
    summary: str = ""
    key_code: str | None = None


@dataclass
class StructuredSummary:
    """9-section structured summary — goose format, ordered most-important-first.

    Every list is ordered so consumers (render template, truncation experiments)
    can cut from tail without losing critical context. Fields deserialize leniently:
    omitted fields default to empty, objects/numbers are stringified rather than
    failing, blank entries are dropped.
    """
    user_intent: list[str] = field(default_factory=list)
    technical_concepts: list[str] = field(default_factory=list)
    files: list[FileActivity] = field(default_factory=list)
    errors_and_fixes: list[str] = field(default_factory=list)
    problem_solving: list[str] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    pending_tasks: list[str] = field(default_factory=list)
    current_work: str | None = None
    next_step: str | None = None
    # Unknown top-level fields kept for customized prompts
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, response_text: str) -> StructuredSummary | None:
        """Extract structured summary from LLM response.

        Tries multiple JSON candidates (goose json_candidates logic):
        1. Fenced ```json blocks after </analysis> markers (last first)
        2. Leading JSON objects (brace-balanced extraction)
        3. Whole-text leading object

        Returns None when no usable JSON found → caller uses raw text fallback.
        """
        candidates = _json_candidates(response_text)
        for candidate in candidates:
            try:
                value = json.loads(candidate)
                summary = cls._from_dict(value)
                summary._normalize()
                if not summary._is_empty():
                    return summary
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
        return None

    @classmethod
    def _from_dict(cls, data: dict) -> StructuredSummary:
        """Lenient deserialization — stringify objects/numbers, wrap scalars."""
        def lenient_list(val) -> list[str]:
            if val is None:
                return []
            if isinstance(val, list):
                return [_stringify(item) for item in val if item is not None]
            return [_stringify(val)]

        def lenient_file_list(val) -> list[FileActivity]:
            if val is None:
                return []
            if not isinstance(val, list):
                val = [val]
            out = []
            for item in val:
                if isinstance(item, dict):
                    path = _stringify(item.get("path", ""))
                    summary = _stringify(item.get("summary", ""))
                    key_code = item.get("key_code")
                    if isinstance(key_code, str) and not key_code.strip():
                        key_code = None
                    elif key_code is not None:
                        key_code = _stringify(key_code)
                    if path.strip() or summary.strip() or key_code:
                        out.append(FileActivity(path=path, summary=summary, key_code=key_code))
                else:
                    path = _stringify(item)
                    if path.strip():
                        out.append(FileActivity(path=path))
            return out

        def lenient_opt(val) -> str | None:
            if val is None:
                return None
            s = _stringify(val)
            return s if s.strip() else None

        # Extract known fields
        known_keys = {
            "user_intent", "technical_concepts", "files", "errors_and_fixes",
            "problem_solving", "user_messages", "pending_tasks",
            "current_work", "next_step",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}

        return cls(
            user_intent=lenient_list(data.get("user_intent")),
            technical_concepts=lenient_list(data.get("technical_concepts")),
            files=lenient_file_list(data.get("files")),
            errors_and_fixes=lenient_list(data.get("errors_and_fixes")),
            problem_solving=lenient_list(data.get("problem_solving")),
            user_messages=lenient_list(data.get("user_messages")),
            pending_tasks=lenient_list(data.get("pending_tasks")),
            current_work=lenient_opt(data.get("current_work")),
            next_step=lenient_opt(data.get("next_step")),
            extra=extra,
        )

    def _normalize(self) -> None:
        """Drop blank entries so response of blanks counts as empty."""
        def blank(s: str) -> bool:
            return not s.strip()

        self.user_intent = [s for s in self.user_intent if not blank(s)]
        self.technical_concepts = [s for s in self.technical_concepts if not blank(s)]
        self.errors_and_fixes = [s for s in self.errors_and_fixes if not blank(s)]
        self.problem_solving = [s for s in self.problem_solving if not blank(s)]
        self.user_messages = [s for s in self.user_messages if not blank(s)]
        self.pending_tasks = [s for s in self.pending_tasks if not blank(s)]
        self.files = [
            f for f in self.files
            if not blank(f.path) or not blank(f.summary) or f.key_code is not None
        ]
        if self.current_work is not None and blank(self.current_work):
            self.current_work = None
        if self.next_step is not None and blank(self.next_step):
            self.next_step = None

    def _is_empty(self) -> bool:
        return (
            not self.user_intent
            and not self.technical_concepts
            and not self.files
            and not self.errors_and_fixes
            and not self.problem_solving
            and not self.user_messages
            and not self.pending_tasks
            and self.current_work is None
            and self.next_step is None
        )

    def render(self) -> str:
        """Render as markdown with section headers (goose template)."""
        lines = ["# Conversation Summary"]

        if self.user_intent:
            lines.append("## User Intent")
            lines.extend(f"- {item}" for item in self.user_intent)

        if self.technical_concepts:
            lines.append("## Technical Concepts")
            lines.extend(f"- {item}" for item in self.technical_concepts)

        if self.files:
            lines.append("## Files")
            for f in self.files:
                lines.append(f"### {f.path}")
                if f.summary:
                    lines.append(f.summary)
                if f.key_code:
                    lines.append(_fence_code(f.key_code))

        if self.errors_and_fixes:
            lines.append("## Errors + Fixes")
            lines.extend(f"- {item}" for item in self.errors_and_fixes)

        if self.problem_solving:
            lines.append("## Problem Solving")
            lines.extend(f"- {item}" for item in self.problem_solving)

        if self.user_messages:
            lines.append("## User Messages")
            lines.extend(f"- {item}" for item in self.user_messages)

        if self.pending_tasks:
            lines.append("## Pending Tasks")
            lines.extend(f"- {item}" for item in self.pending_tasks)

        if self.current_work:
            lines.append("## Current Work")
            lines.append(self.current_work)

        if self.next_step:
            lines.append("## Next Step")
            lines.append(self.next_step)

        return "\n\n".join(lines)


def _stringify(value: Any) -> str:
    """Lenient string conversion — goose stringify_lenient logic."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_stringify(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "; ".join(_stringify(item) for item in value)
    return str(value)


def _fence_code(code: str) -> str:
    """Wrap code in fence that exceeds longest backtick run (goose logic)."""
    longest = max((len(run) for run in _backtick_runs(code)), default=0)
    fence = "`" * (longest + 1) if longest >= 3 else "```"
    return f"{fence}\n{code}\n{fence}"


def _backtick_runs(text: str) -> list[str]:
    """Extract consecutive backtick runs."""
    import re
    return re.findall(r"`+", text)


def _json_candidates(text: str) -> list[str]:
    """Candidate JSON documents, tried in order (goose json_candidates logic).

    Order: after each </analysis> terminator (last first) → post-terminator
    ```json fences (last first) → leading object. Finally leading object of
    whole text.
    """
    terminator = "</analysis>"
    cuts = []
    idx = 0
    while True:
        idx = text.find(terminator, idx)
        if idx == -1:
            break
        cuts.append(idx + len(terminator))
        idx += 1
    if not cuts:
        cuts = [0]

    candidates = []
    for cut in reversed(cuts):
        tail = text[cut:]
        later_terminators = tail.count(terminator)
        for candidate in _fenced_json_blocks(tail) + [_leading_object(tail)]:
            if candidate and candidate.count(terminator) == later_terminators:
                candidates.append(candidate)

    leading = _leading_object(text)
    if leading:
        candidates.append(leading)

    # Dedupe
    seen = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def _fenced_json_blocks(text: str) -> list[str]:
    """Extract fenced ```json blocks (last first)."""
    marker = "```json"
    blocks = []
    idx = 0
    while True:
        idx = text.find(marker, idx)
        if idx == -1:
            break
        candidate = _leading_object(text[idx + len(marker):])
        if candidate:
            blocks.append(candidate)
        idx += 1
    return list(reversed(blocks))


def _leading_object(text: str) -> str | None:
    """Brace-balanced extraction of leading JSON object."""
    text = text.lstrip()
    if not text.startswith("{"):
        return None

    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[:i + 1]
    return None


@dataclass
class MessageChunk:
    """Chunk of messages for parallel summarization."""
    index: int
    messages: list[dict]


@dataclass
class CompactionResult:
    """Result of compaction operation."""
    status: str  # "continue" | "compact" | "stop"
    summary: str | None = None
    error: str | None = None


class ContextCompressor:
    """Context compression engine — budget-aware chunking + structured summary.

    Usage:
        compressor = ContextCompressor(llm_fn=my_llm_call)
        result = await compressor.compress(messages, context_limit=128000)
        if result.status == "continue":
            new_context = result.summary
    """

    def __init__(
        self,
        llm_fn,
        budget_ratio: float = DEFAULT_BUDGET_RATIO,
        min_budget_tokens: int = DEFAULT_MIN_BUDGET_TOKENS,
        concurrency: int = DEFAULT_CONCURRENCY,
        reduce_depth: int = DEFAULT_REDUCE_DEPTH,
    ):
        """
        Args:
            llm_fn: async callable(prompt: str) -> str, returns LLM response text.
            budget_ratio: fraction of context window for chunk budget (0.6).
            min_budget_tokens: minimum chunk budget (1000 tokens).
            concurrency: parallel chunk summarization limit (3).
            reduce_depth: binary merge depth limit (3).
        """
        self.llm_fn = llm_fn
        self.budget_ratio = budget_ratio
        self.min_budget_tokens = min_budget_tokens
        self.concurrency = concurrency
        self.reduce_depth = reduce_depth

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate (chars / 4, kilocode formula)."""
        return len(text) // 4

    def _budget(self, context_limit: int) -> int:
        """Chunk budget — 60% of context, min 1000 tokens (kilocode)."""
        return max(self.min_budget_tokens, int(context_limit * self.budget_ratio))

    def _split(
        self, messages: list[dict], budget: int
    ) -> list[MessageChunk]:
        """Split messages into chunks that fit budget (kilocode split logic)."""
        chunks = []
        buf = []
        for msg in messages:
            next_buf = buf + [msg]
            size = self._estimate_tokens(self._serialize_messages(next_buf))
            if buf and size > budget:
                chunks.append(MessageChunk(index=len(chunks), messages=buf))
                buf = [msg]
            else:
                buf = next_buf
        if buf:
            chunks.append(MessageChunk(index=len(chunks), messages=buf))
        return chunks

    def _serialize_messages(self, messages: list[dict]) -> str:
        """Serialize messages to text for token estimation and summarization."""
        parts = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Multi-part message (tool calls, attachments)
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif part.get("type") == "tool_call":
                            name = part.get("name", "?")
                            args = json.dumps(part.get("arguments", {}))[:TOOL_OUTPUT_MAX_CHARS]
                            text_parts.append(f"[Tool call]: {name}({args})")
                        elif part.get("type") == "tool_result":
                            output = str(part.get("output", ""))[:TOOL_OUTPUT_MAX_CHARS]
                            text_parts.append(f"[Tool result]: {output}")
                content = "\n".join(text_parts)
            elif not isinstance(content, str):
                content = str(content)
            # Clip long content (kilocode TRANSCRIPT_MAX_CHARS)
            if len(content) > TRANSCRIPT_MAX_CHARS:
                content = content[:TRANSCRIPT_MAX_CHARS] + "\n[truncated for compaction]"
            parts.append(f'<message index="{i + 1}" role="{role}">\n{content}\n</message>')
        return "\n\n".join(parts)

    def _chunk_prompt(self, chunk: MessageChunk, total: int) -> str:
        """Summarization prompt for individual chunk (kilocode prompt)."""
        return "\n".join([
            f"Summarize conversation chunk {chunk.index + 1} of {total}.",
            "Only summarize facts present in this chunk.",
            "Preserve concrete file paths, commands, errors, decisions, and unresolved tasks.",
            "Use terse Markdown bullets. Do not mention chunking or compaction.",
            "Output a JSON object with these fields (ordered most-important-first):",
            '```json',
            '{',
            '  "user_intent": ["..."],',
            '  "technical_concepts": ["..."],',
            '  "files": [{"path": "...", "summary": "...", "key_code": "..."}],',
            '  "errors_and_fixes": ["..."],',
            '  "problem_solving": ["..."],',
            '  "user_messages": ["..."],',
            '  "pending_tasks": ["..."],',
            '  "current_work": "...",',
            '  "next_step": "..."',
            '}',
            '```',
        ])

    def _reduce_prompt(self, summaries: list[str]) -> str:
        """Merge prompt for reducing partial summaries (kilocode messages())."""
        parts = []
        for i, summary in enumerate(summaries):
            parts.append(f'<partial-summary index="{i + 1}">\n{summary}\n</partial-summary>')
        return "\n\n".join([
            "Merge the following partial summaries into one coherent summary.",
            "Preserve all critical information: file paths, commands, errors, decisions, pending tasks.",
            "Order items by importance (most important first in each list).",
            "Output a JSON object with the same schema as the partial summaries.",
            "\n".join(parts),
        ])

    async def _summarize_chunk(
        self, chunk: MessageChunk, total: int
    ) -> tuple[str | None, str | None]:
        """Summarize single chunk — returns (summary_text, error)."""
        try:
            transcript = self._serialize_messages(chunk.messages)
            prompt = self._chunk_prompt(chunk, total)
            full_prompt = f"<conversation>\n{transcript}\n</conversation>\n\n{prompt}"
            response = await self.llm_fn(full_prompt)

            # Try structured summary first (goose)
            structured = StructuredSummary.parse(response)
            if structured:
                return structured.render(), None

            # Raw text fallback
            if response.strip():
                return response.strip(), None

            return None, "empty_response"
        except Exception as e:
            logger.warning("Chunk %d summarization failed: %s", chunk.index, e)
            return None, str(e)

    async def _reduce_summaries(
        self, summaries: list[str], depth: int = 0
    ) -> tuple[str | None, str | None]:
        """Binary merge partial summaries with depth limit (kilocode reduce)."""
        if len(summaries) == 1:
            return summaries[0], None

        try:
            prompt = self._reduce_prompt(summaries)
            response = await self.llm_fn(prompt)

            structured = StructuredSummary.parse(response)
            if structured:
                return structured.render(), None

            if response.strip():
                return response.strip(), None

            # Empty response — retry with binary split if depth allows
            if depth < self.reduce_depth and len(summaries) > 1:
                mid = len(summaries) // 2
                left_sum, left_err = await self._reduce_summaries(summaries[:mid], depth + 1)
                right_sum, right_err = await self._reduce_summaries(summaries[mid:], depth + 1)
                if left_err or right_err:
                    return None, left_err or right_err
                # Merge left + right
                merged = f"{left_sum}\n\n{right_sum}"
                return merged, None

            return None, "empty_response"
        except Exception as e:
            logger.warning("Summary reduction failed at depth %d: %s", depth, e)
            return None, str(e)

    async def compress(
        self,
        messages: list[dict],
        context_limit: int,
        preserve_last_user: bool = True,
    ) -> CompactionResult:
        """Compress message history to fit context window.

        Args:
            messages: list of {"role": ..., "content": ...} dicts.
            context_limit: total context window size in tokens.
            preserve_last_user: extract & re-append most recent user message.

        Returns:
            CompactionResult with status ("continue"/"compact"/"stop") and
            summary text if successful.
        """
        if not messages:
            return CompactionResult(status="continue", summary="")

        budget = self._budget(context_limit)

        # Preserve most recent user text-only message (goose logic)
        preserved_user = None
        messages_to_compact = messages
        if preserve_last_user:
            for i in range(len(messages) - 1, -1, -1):
                msg = messages[i]
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    # Only preserve text-only messages
                    if isinstance(content, str) and content.strip():
                        preserved_user = content
                        messages_to_compact = messages[:i] + messages[i + 1:]
                        break

        # Split into chunks
        chunks = self._split(messages_to_compact, budget)
        logger.info("Compaction: %d messages → %d chunks (budget=%d tokens)",
                    len(messages_to_compact), len(chunks), budget)

        # Summarize chunks (sequential for now; async concurrency needs asyncio.gather)
        partial_summaries = []
        for chunk in chunks:
            summary, error = await self._summarize_chunk(chunk, len(chunks))
            if error:
                # Non-fatal: continue with other chunks
                logger.warning("Chunk %d failed, skipping: %s", chunk.index, error)
                continue
            if summary:
                partial_summaries.append(summary)

        if not partial_summaries:
            return CompactionResult(
                status="compact",
                error="all_chunks_failed",
            )

        # Reduce partial summaries
        if len(partial_summaries) == 1:
            final_summary = partial_summaries[0]
        else:
            final_summary, error = await self._reduce_summaries(partial_summaries)
            if error:
                return CompactionResult(status="compact", error=error)

        # Re-append preserved user message
        if preserved_user:
            final_summary = f"{final_summary}\n\n## Most Recent User Message\n{preserved_user}"

        return CompactionResult(status="continue", summary=final_summary)
