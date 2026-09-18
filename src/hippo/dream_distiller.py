"""P0-6: Dream记忆蒸馏 — CowAgent Deep Dream五步蒸馏 + nanobot archive-as-tool-call

功能：
- 从对话历史 + 现有记忆蒸馏结构化记忆操作
- CowAgent五步法：合并提炼/新增萃取/冲突更新/清理无效/删除冗余
- 防幻觉条款："只能基于提供的材料整理，严禁编造推测"
- nanobot archive模式：LLM显式确认每个记忆操作（非后台启发式）
- kilocode记忆回声阻断：recall命中过的回合跳过digest防自我污染

参照：
- CowAgent agent/memory/summarizer.py 34KB（五步蒸馏prompt+防编造条款）
- nanobot memory.py（archive-as-tool-call: LLM显式确认记忆检查点）
- kilocode recalledMemory()（15行防记忆回声）
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("opensoul.hippo.dream")


@dataclass
class DreamAction:
    """A single memory operation produced by Dream distillation.

    Mirrors nanobot's archive-as-tool-call: the LLM explicitly confirms
    each memory action rather than a background heuristic deciding.
    """
    action: str  # ADD / UPDATE / DELETE / SKIP
    content: str = ""  # new memory content (for ADD)
    memory_id: str = ""  # target memory (for UPDATE/DELETE)
    new_content: str = ""  # updated content (for UPDATE)
    memory_type: str = "semantic"
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    reason: str = ""  # why this action was chosen


@dataclass
class DreamResult:
    """Result of a Dream distillation run."""
    dream_id: str
    actions: list[DreamAction] = field(default_factory=list)
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    echo_blocked: bool = False  # True if digest was skipped due to memory echo
    raw_response: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for a in self.actions:
            c[a.action] = c.get(a.action, 0) + 1
        return c

    def to_dict(self) -> dict:
        return {
            "dream_id": self.dream_id,
            "total_actions": len(self.actions),
            "counts": self.counts,
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "echo_blocked": self.echo_blocked,
            "error": self.error,
            "created_at": self.created_at,
        }


# CowAgent Deep Dream 5-step distillation prompt
# Source: CowAgent agent/memory/summarizer.py (34KB, supplement #1)
# Anti-hallucination clause: "只能基于提供的材料整理，严禁编造推测"
DREAM_SYSTEM_PROMPT = """你是一个记忆蒸馏专家。你的任务是从对话历史和现有记忆中，提炼出结构化的记忆操作。

## 五步蒸馏法

请按以下五个步骤分析，输出JSON格式的记忆操作列表：

1. **合并提炼**：如果现有记忆中有与对话内容相关的条目，考虑是否需要合并提炼（UPDATE）
2. **新增萃取**：从对话历史中提取新的值得记住的信息（ADD）
3. **冲突更新**：如果对话中出现了与现有记忆矛盾的信息，更新旧记忆（UPDATE）
4. **清理无效**：识别已过时或不再准确的记忆（DELETE）
5. **删除冗余**：识别重复或已被其他记忆涵盖的条目（DELETE）

## 输出格式

输出一个JSON数组，每个元素是一个记忆操作：
```json
[
  {"action": "ADD", "content": "记忆内容", "memory_type": "semantic", "importance": 0.7, "tags": ["tag1"], "reason": "为什么添加"},
  {"action": "UPDATE", "memory_id": "ltm_xxx", "new_content": "更新后的内容", "reason": "为什么更新"},
  {"action": "DELETE", "memory_id": "ltm_xxx", "reason": "为什么删除"},
  {"action": "SKIP", "memory_id": "ltm_xxx", "reason": "为什么保留不动"}
]
```

## 铁律

- **只能基于提供的材料整理，严禁编造推测**——不得添加对话和现有记忆中没有的信息
- 每个操作必须给出reason（为什么做这个决定）
- importance评分范围0.0-1.0（0.3以下=低价值，0.5=中等，0.7+=高价值）
- memory_type只能选：episodic（事件）/ semantic（知识）/ procedural（技能）/ working（工作记忆）
- 如果对话中没有值得记住的新信息，返回空数组[]
- 不确定的记忆操作用SKIP而非DELETE（保守原则）
"""


def _parse_dream_actions(response: str) -> list[DreamAction]:
    """Parse LLM response into DreamAction list.

    Implements nanobot's tolerant parsing: look for JSON array in response,
    handle fenced code blocks, fall back to raw text extraction.
    """
    actions = []
    text = response.strip()

    # Try to find JSON array (fenced or bare)
    json_str = ""
    # Look for ```json ... ``` fence
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        json_str = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        json_str = text[start:end].strip()
    elif "[" in text and "]" in text:
        start = text.index("[")
        end = text.rindex("]") + 1
        json_str = text[start:end]
    else:
        return actions

    try:
        items = json.loads(json_str)
        if not isinstance(items, list):
            return actions
    except json.JSONDecodeError:
        return actions

    valid_actions = {"ADD", "UPDATE", "DELETE", "SKIP"}
    valid_types = {"episodic", "semantic", "procedural", "working"}

    for item in items:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).upper().strip()
        if action not in valid_actions:
            continue

        da = DreamAction(
            action=action,
            content=str(item.get("content", "")),
            memory_id=str(item.get("memory_id", "")),
            new_content=str(item.get("new_content", "")),
            memory_type=str(item.get("memory_type", "semantic")),
            importance=max(0.0, min(1.0, float(item.get("importance", 0.5)))),
            tags=[str(t) for t in item.get("tags", [])],
            reason=str(item.get("reason", "")),
        )
        # Validate memory_type
        if da.memory_type not in valid_types:
            da.memory_type = "semantic"
        # ADD requires content
        if action == "ADD" and not da.content:
            continue
        # UPDATE/DELETE require memory_id
        if action in ("UPDATE", "DELETE") and not da.memory_id:
            continue
        actions.append(da)

    return actions


class DreamDistiller:
    """Dream memory distillation pipeline.

    Combines:
    - CowAgent 5-step distillation prompt (merge/extract/conflict/cleanup/dedupe)
    - nanobot archive-as-tool-call (LLM explicitly confirms each action)
    - kilocode memory echo blocker (skip digest if recall was used this turn)
    """

    def __init__(self, ltm_store, llm_call: Optional[Callable] = None):
        """Initialize with a LongTermMemoryStore and optional LLM caller.

        Args:
            ltm_store: LongTermMemoryStore instance for CRUD operations
            llm_call: async callable(system_prompt, user_prompt) -> str
                      If None, uses the gland router.
        """
        self._store = ltm_store
        self._llm_call = llm_call
        # Memory echo tracking (kilocode pattern)
        self._recalled_memory_ids: set[str] = set()
        self._recall_count_this_turn: int = 0
        # Dream history
        self._dream_history: list[dict] = []

    # ── Memory echo blocker (kilocode recalledMemory, 15-line pattern) ──

    def mark_recall(self, memory_ids: list[str]):
        """Record that memories were recalled this turn.

        kilocode pattern: "本轮若跑过kilo_memory_recall且count>0→跳过digest"
        — "答案来自记忆的回合不能再蒸馏回记忆"（记忆自我污染闭环的阻断器）
        """
        self._recalled_memory_ids.update(memory_ids)
        self._recall_count_this_turn += len(memory_ids)

    def should_skip_digest(self) -> bool:
        """Check if digest should be skipped due to memory echo.

        Returns True if any memories were recalled this turn —
        the answer came from memory, so distilling it back would
        create a self-pollution loop.
        """
        return self._recall_count_this_turn > 0

    def reset_turn(self):
        """Reset per-turn tracking (call at turn boundary)."""
        self._recalled_memory_ids.clear()
        self._recall_count_this_turn = 0

    @property
    def echo_stats(self) -> dict:
        return {
            "recalled_this_turn": self._recall_count_this_turn,
            "unique_recalled": len(self._recalled_memory_ids),
            "digest_blocked": self.should_skip_digest(),
        }

    # ── Dream distillation pipeline ──

    async def dream(
        self,
        messages: list[dict],
        force: bool = False,
        max_existing: int = 50,
    ) -> DreamResult:
        """Run Dream distillation from conversation history + existing memories.

        Args:
            messages: Conversation messages [{"role": "user"/"assistant", "content": "..."}]
            force: If True, bypass the memory echo blocker (for manual triggers)
            max_existing: Max existing memories to include in the prompt

        Returns:
            DreamResult with parsed actions and execution stats.
        """
        dream_id = f"dream_{hashlib.sha256(f'{time.time()}:{len(messages)}'.encode()).hexdigest()[:12]}"
        result = DreamResult(dream_id=dream_id)

        # Memory echo blocker (kilocode)
        if not force and self.should_skip_digest():
            result.echo_blocked = True
            result.error = (
                f"Digest skipped: {self._recall_count_this_turn} memories were recalled "
                "this turn — answers from memory must not be distilled back (echo blocker)"
            )
            logger.info(f"Dream {dream_id}: echo blocked ({self.echo_stats})")
            self._dream_history.append(result.to_dict())
            return result

        if not messages:
            result.error = "No messages to distill"
            self._dream_history.append(result.to_dict())
            return result

        # Gather existing memories
        existing = self._store.list_memories(limit=max_existing)
        existing_text = self._format_memories(existing)

        # Format conversation history
        conv_text = self._format_messages(messages)

        # Build user prompt
        user_prompt = f"""## 现有记忆（{len(existing)}条）
{existing_text if existing else "（无现有记忆）"}

## 对话历史
{conv_text}

请按五步蒸馏法分析以上材料，输出JSON格式的记忆操作列表。"""

        # Call LLM
        try:
            if self._llm_call:
                response = await self._llm_call(DREAM_SYSTEM_PROMPT, user_prompt)
            else:
                response = await self._call_gland_llm(DREAM_SYSTEM_PROMPT, user_prompt)
        except Exception as e:
            result.error = f"LLM call failed: {e}"
            logger.error(f"Dream {dream_id}: LLM error: {e}")
            self._dream_history.append(result.to_dict())
            return result

        result.raw_response = response[:2000]

        # Parse actions
        actions = _parse_dream_actions(response)
        result.actions = actions

        if not actions:
            logger.info(f"Dream {dream_id}: no actions parsed from response")
            self._dream_history.append(result.to_dict())
            return result

        # Execute actions against the store
        for action in actions:
            try:
                ok = self._execute_action(action)
                if ok:
                    result.applied += 1
                else:
                    result.skipped += 1
            except Exception as e:
                result.failed += 1
                logger.warning(f"Dream {dream_id}: action {action.action} failed: {e}")

        logger.info(
            f"Dream {dream_id}: {result.applied} applied, "
            f"{result.skipped} skipped, {result.failed} failed "
            f"(actions: {result.counts})"
        )
        self._dream_history.append(result.to_dict())
        return result

    def _execute_action(self, action: DreamAction) -> bool:
        """Execute a single DreamAction against the LTM store.

        nanobot pattern: each action is an explicit confirmed operation.
        """
        if action.action == "ADD":
            self._store.store(
                content=action.content,
                memory_type=action.memory_type,
                importance=action.importance,
                tags=action.tags,
                metadata={"source": "dream_distillation", "reason": action.reason},
                source_session="dream",
            )
            return True

        elif action.action == "UPDATE":
            result = self._store.update_memory(
                memory_id=action.memory_id,
                content=action.new_content if action.new_content else None,
                reason=f"dream_update: {action.reason}",
            )
            return result is not None

        elif action.action == "DELETE":
            return self._store.delete_memory(
                memory_id=action.memory_id,
                reason=f"dream_delete: {action.reason}",
                hard_delete=False,
            )

        elif action.action == "SKIP":
            return True  # Explicit no-op, counted as applied

        return False

    async def _call_gland_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call LLM via gland router (default when no llm_call provided)."""
        try:
            # 复用api/gland.py的gateway单例(providers已bootstrap)——
            # 此前新建ModelRouter()导致providers为空→NoProviderError
            from src.api.gland import _ensure_bootstrapped, gateway
            _ensure_bootstrapped()
            result = await gateway.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,  # Low temperature for consistent distillation
                max_tokens=4096,
            )
            if isinstance(result, dict):
                return result.get("content", result.get("text", str(result)))
            return str(result)
        except Exception as e:
            raise RuntimeError(f"Gland router call failed: {e}") from e

    @staticmethod
    def _format_memories(memories: list[dict]) -> str:
        """Format existing memories for the prompt."""
        lines = []
        for m in memories:
            mid = m.get("memory_id", "")
            content = m.get("content", "")
            mtype = m.get("memory_type", "semantic")
            importance = m.get("importance", 0.5)
            lines.append(f"[{mid}] ({mtype}, imp={importance:.1f}) {content}")
        return "\n".join(lines)

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """Format conversation messages for the prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        """Get Dream distillation statistics."""
        total_dreams = len(self._dream_history)
        total_applied = sum(h.get("applied", 0) for h in self._dream_history)
        total_echo_blocked = sum(1 for h in self._dream_history if h.get("echo_blocked"))
        return {
            "total_dreams": total_dreams,
            "total_actions_applied": total_applied,
            "echo_blocked_count": total_echo_blocked,
            "current_turn_echo": self.echo_stats,
            "recent_dreams": self._dream_history[-5:],
        }
