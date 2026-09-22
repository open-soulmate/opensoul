"""P0-6: codex两阶段记忆管线 — Phase1结构化提取 + Phase2 consolidation agent
+ MemoryVersion版本化 + memory workspace diff + 旧资源修剪

调研来源（SUMMARY.md P0-6最后一项，连续两轮dev-report遗留#1）：
- 11-openai-codex-source.md #1：codex memories/write/src/{phase1,phase2,workspace,storage}.rs
  "Phase1每会话提取结构化输出 → Phase2 consolidation agent合并，含memory workspace diff、
  MemoryVersion版本化、旧extension资源修剪"；实现建议"Phase1可直接用LLM结构化输出跑；
  Phase2用现有delegate_task做consolidation agent；版本号字段加进hippo表"
- mem0 §1.1（evolution-engine-patterns.md）：失败必须可见，禁止静默降级——
  Phase2 LLM失败→确定性降级但fallback_reason必须透出，绝不假装LLM判定成功
- CAMEL verifiers §2.4："能程序化验证的绝不靠LLM"——降级路径=程序化合并
 （gatekeeper近重复判定+fact_dedup并入门已存在于store层），LLM只做语义级整合
- CowAgent防幻觉条款：提取prompt"只能基于提供的材料整理，严禁编造推测"
- agent-zero §4.4稀疏编辑/快照回滚：版本化让"整合改坏"可回滚（storage.rs MemoryVersion本地化）

管线语义：
  messages ──Phase1(LLM结构化提取)──> candidates ──Phase2(consolidation agent)──> decisions
  decisions ──apply──> store（ADD_NEW→store dup_policy=merge / MERGE_INTO·UPDATE→update_memory
  写新版本 / REJECT→不落库）──> workspace diff（每条决策的落地结果+版本前后号）
  全程run持久化到pipeline_runs表（可观测：管线跑过什么、改了什么）。

降级与旁路：
- candidates直供（mode=import）：外部提取器/调用方已有候选时跳过Phase1，
  同时是不依赖LLM的live验证路径
- use_llm_phase2=False或LLM失败：确定性整合（全部ADD_NEW，靠store既有
  gatekeeper+fact_dedup近重复并入兜底），meta.phase2_fallback_reason可见
"""

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from src.gland.router import extract_chat_text

logger = logging.getLogger("opensoul.hippo.memory_pipeline")

VALID_OPS = {"ADD_NEW", "MERGE_INTO", "UPDATE", "REJECT"}
VALID_TYPES = {"episodic", "semantic", "procedural", "working"}


# ── Phase1：每会话结构化提取（codex phase1.rs本地化） ──────────────

PHASE1_EXTRACT_PROMPT = """你是一个长期记忆提取专家（codex两阶段记忆管线 Phase1：每会话结构化提取）。

任务：从会话历史中提取值得进入长期记忆的候选事实。

## 铁律
- **只能基于提供的会话材料整理，严禁编造推测**——每条候选必须给出evidence（会话原文片段，100字以内）
- 只提取有长期价值的事实：用户偏好/项目事实/规则约定/关键决策/经验教训
- 临时性内容（寒暄/过程性输出/一次性任务细节）不提取
- importance评分0.0-1.0：0.7+=用户明确强调或会影响未来行为；0.5=一般事实；低于0.3的不提取
- memory_type只能选：episodic（事件）/ semantic（知识）/ procedural（技能）/ working（工作记忆）
- DeerMem三标签：scope=user/task/project/session、durability=durable/transient、
  authority=descriptive/prescriptive/contradiction；长期记忆优先user+durable+descriptive
- 会话中没有值得记住的新信息时，输出空数组[]

## 输出格式
输出一个JSON数组：
```json
[
  {"content": "事实内容", "memory_type": "semantic", "importance": 0.7,
   "tags": ["tag1"], "scope": "user", "durability": "durable",
   "authority": "descriptive", "evidence": "会话原文片段", "reason": "为什么值得记住"}
]
```
"""

# ── Phase2：consolidation agent（codex phase2.rs本地化） ────────────
# 注意：prompt用[[EXISTING]]/[[CANDIDATES]]占位符而非str.format——
# prompt体内含JSON大括号，.format会炸（OpenMate教训："Python.format()遇JSON大括号"）

PHASE2_CONSOLIDATE_PROMPT = """你是一个记忆整合agent（codex两阶段记忆管线 Phase2：consolidation agent）。

任务：把Phase1提取的候选事实与现有长期记忆对照，为每条候选决定唯一去向：
- ADD_NEW：现有记忆没有覆盖，全新入库
- MERGE_INTO：与某条现有记忆语义重复或高度相关→并入该记忆（给出merged_content，
  保留双方有效信息，保留原memory_id）
- UPDATE：候选信息比某条现有记忆更新/更准确→取代该记忆内容（target_memory_id=被取代者）
- REJECT：低价值/已被现有记忆完整覆盖/不适合长期记忆→拒绝入库（给出reason）

## 铁律
- **merged_content只能基于候选与目标记忆的原文整合，严禁引入材料外信息**
- 不确定时选ADD_NEW（入库侧有近重复并入门兜底）而非REJECT
- target_memory_id必须从现有记忆列表中选取，不得编造

## 现有记忆（[memory_id] 开头）
[[EXISTING]]

## Phase1候选（candidate_index 开头）
[[CANDIDATES]]

## 输出格式
输出一个JSON数组，每条候选恰好一个决策：
```json
[
  {"candidate_index": 0, "op": "ADD_NEW", "reason": "..."},
  {"candidate_index": 1, "op": "MERGE_INTO", "target_memory_id": "ltm_xxx", "merged_content": "合并后内容", "reason": "..."},
  {"candidate_index": 2, "op": "UPDATE", "target_memory_id": "ltm_yyy", "merged_content": "更新后内容", "reason": "..."},
  {"candidate_index": 3, "op": "REJECT", "reason": "..."}
]
```
"""


def _extract_json_array(text: str) -> str:
    """宽容JSON数组提取（nanobot/dream同款三层：fenced json→fenced→bare array）。"""
    t = (text or "").strip()
    if "```json" in t:
        start = t.index("```json") + 7
        end = t.index("```", start)
        return t[start:end].strip()
    if "```" in t:
        start = t.index("```") + 3
        end = t.index("```", start)
        return t[start:end].strip()
    if "[" in t and "]" in t:
        return t[t.index("[") : t.rindex("]") + 1]
    return ""


@dataclass
class Phase1Candidate:
    """一条Phase1提取的候选事实。"""

    content: str = ""
    memory_type: str = "semantic"
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    scope: str = ""
    durability: str = ""
    authority: str = ""
    evidence: str = ""
    reason: str = ""

    @classmethod
    def from_item(cls, item: Any) -> Optional["Phase1Candidate"]:
        """宽容解析单条候选（缺失字段安全默认；无content=None）。"""
        if not isinstance(item, dict):
            return None
        content = str(item.get("content", "") or "").strip()
        if not content:
            return None
        mtype = str(item.get("memory_type", "semantic") or "semantic").strip().lower()
        if mtype not in VALID_TYPES:
            mtype = "semantic"
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        importance = max(0.0, min(1.0, importance))
        tags_raw = item.get("tags") or []
        tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
        return cls(
            content=content,
            memory_type=mtype,
            importance=importance,
            tags=tags,
            scope=str(item.get("scope", "") or "").strip().lower(),
            durability=str(item.get("durability", "") or "").strip().lower(),
            authority=str(item.get("authority", "") or "").strip().lower(),
            evidence=str(item.get("evidence", "") or "")[:200],
            reason=str(item.get("reason", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "memory_type": self.memory_type,
            "importance": self.importance,
            "tags": self.tags,
            "scope": self.scope,
            "durability": self.durability,
            "authority": self.authority,
            "evidence": self.evidence,
            "reason": self.reason,
        }


def parse_phase1(response: str) -> list[Phase1Candidate]:
    """解析Phase1 LLM输出为候选列表（宽容：坏条目跳过，不炸整批）。"""
    json_str = _extract_json_array(response)
    if not json_str:
        return []
    try:
        items = json.loads(json_str)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        c = Phase1Candidate.from_item(item)
        if c is not None:
            out.append(c)
    return out


@dataclass
class ConsolidationDecision:
    """Phase2 consolidation agent对单条候选的去向决策。"""

    op: str  # ADD_NEW / MERGE_INTO / UPDATE / REJECT
    candidate: Phase1Candidate
    target_memory_id: str = ""
    merged_content: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "candidate": self.candidate.to_dict(),
            "target_memory_id": self.target_memory_id,
            "merged_content": self.merged_content,
            "reason": self.reason,
        }


def parse_phase2(
    response: str, candidates: list[Phase1Candidate]
) -> tuple[list[ConsolidationDecision], dict]:
    """解析Phase2 LLM输出为决策列表。

    容错语义（mem0失败可见+CAMEL程序化兜底）：
    - candidate_index越界→跳过并计数（meta["skipped_out_of_range"]）
    - MERGE_INTO/UPDATE缺target_memory_id→降级ADD_NEW并标注
      "[fallback:missing_target]"（数据保守侧：入库由近重复并入门去重，
      而不是丢弃一条可能有价值的候选）
    - 非法op→跳过并计数（meta["skipped_invalid_op"]）
    """
    meta: dict = {"skipped_out_of_range": 0, "skipped_invalid_op": 0, "missing_target_fallback": 0}
    json_str = _extract_json_array(response)
    if not json_str:
        return [], meta
    try:
        items = json.loads(json_str)
    except json.JSONDecodeError:
        return [], meta
    if not isinstance(items, list):
        return [], meta
    decisions = []
    for item in items:
        if not isinstance(item, dict):
            meta["skipped_invalid_op"] += 1
            continue
        op = str(item.get("op", "") or "").upper().strip()
        if op not in VALID_OPS:
            meta["skipped_invalid_op"] += 1
            continue
        try:
            idx = int(item.get("candidate_index", -1))
        except (TypeError, ValueError):
            idx = -1
        if idx < 0 or idx >= len(candidates):
            meta["skipped_out_of_range"] += 1
            continue
        target = str(item.get("target_memory_id", "") or "").strip()
        merged = str(item.get("merged_content", "") or "").strip()
        reason = str(item.get("reason", "") or "")
        if op in ("MERGE_INTO", "UPDATE") and not target:
            meta["missing_target_fallback"] += 1
            op = "ADD_NEW"
            reason = f"[fallback:missing_target] {reason}".strip()
            target, merged = "", ""
        decisions.append(
            ConsolidationDecision(
                op=op,
                candidate=candidates[idx],
                target_memory_id=target,
                merged_content=merged,
                reason=reason,
            )
        )
    return decisions, meta


@dataclass
class Phase1Result:
    run_id: str = ""
    session_id: str = ""
    candidates: list[Phase1Candidate] = field(default_factory=list)
    raw_response: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "candidates": [c.to_dict() for c in self.candidates],
            "count": len(self.candidates),
            "error": self.error,
            # 失败可见：count=0时调用方可审计LLM原始返回（截断500字）
            "raw_response": self.raw_response[:500],
            "created_at": self.created_at,
        }


@dataclass
class PipelineResult:
    """一次管线运行的完整结果（含codex memory workspace diff）。"""

    run_id: str = ""
    session_id: str = ""
    mode: str = ""  # llm / import（candidates直供跳过Phase1）
    phase1_count: int = 0
    decision_counts: dict = field(default_factory=dict)
    phase2_meta: dict = field(default_factory=dict)
    diff: list = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "phase1_count": self.phase1_count,
            "decision_counts": self.decision_counts,
            "phase2": self.phase2_meta,
            "diff": self.diff,
            "error": self.error,
            "created_at": self.created_at,
        }


class MemoryPipeline:
    """codex两阶段记忆管线（Phase1提取→Phase2整合→版本化落库→workspace diff）。"""

    def __init__(self, ltm_store, llm_call: Callable | None = None):
        """
        Args:
            ltm_store: LongTermMemoryStore实例（写入/版本化的真源）
            llm_call: async callable(system_prompt, user_prompt) -> str；None时走gland router
        """
        self._store = ltm_store
        self._llm_call = llm_call
        self._init_runs_db()

    def _init_runs_db(self):
        """pipeline_runs表：每次管线运行的可观测记录（codex workspace diff持久化）。"""
        with sqlite3.connect(self._store.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT DEFAULT '',
                    mode TEXT DEFAULT '',
                    phase1_count INTEGER DEFAULT 0,
                    decision_counts TEXT DEFAULT '{}',
                    diff TEXT DEFAULT '[]',
                    phase2_meta TEXT DEFAULT '{}',
                    error TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    # ── Phase1：结构化提取 ─────────────────────────────────────

    async def extract(self, messages: list[dict], session_id: str = "") -> Phase1Result:
        """Phase1：会话历史→候选事实（LLM结构化输出）。"""
        run_id = f"p1_{hashlib.sha256(f'{time.time()}:{session_id}:{len(messages)}'.encode()).hexdigest()[:12]}"
        res = Phase1Result(run_id=run_id, session_id=session_id)
        if not messages:
            res.error = "no messages to extract"
            return res
        conv_text = self._format_messages(messages)
        user_prompt = (
            f"## 会话历史\n{conv_text}\n\n请按铁律提取值得进入长期记忆的候选事实，输出JSON数组。"
        )
        try:
            response = await self._call_llm(PHASE1_EXTRACT_PROMPT, user_prompt)
        except Exception as e:  # noqa: BLE001 — LLM故障必须可见（result.error），不静默
            res.error = f"Phase1 LLM call failed: {e}"
            logger.error("Pipeline %s: %s", run_id, res.error)
            return res
        # 非str响应（OpenAI风格dict等）统一经权威解包点（router.extract_chat_text）
        if not isinstance(response, str):
            response = extract_chat_text(response)
        res.raw_response = (response or "")[:2000]
        res.candidates = parse_phase1(response)
        return res

    # ── Phase2：consolidation agent ────────────────────────────

    async def consolidate(
        self, candidates: list[Phase1Candidate], use_llm: bool = True
    ) -> tuple[list[ConsolidationDecision], dict]:
        """Phase2：候选×现有记忆对照→去向决策。

        LLM不可用/不可解析/use_llm=False→确定性降级（全部ADD_NEW，
        入库侧gatekeeper+fact_dedup近重复并入兜底）；降级原因在meta透出
        （mem0 §1.1：区分"LLM判定REJECT"与"降级路径没跑LLM"，绝不混淆）。
        """
        meta: dict = {"phase2": "deterministic"}
        if not candidates:
            return [], meta
        if use_llm:
            existing = self._store.list_memories(include_deleted=False, limit=30)
            try:
                prompt = PHASE2_CONSOLIDATE_PROMPT.replace(
                    "[[EXISTING]]", self._format_memories(existing) or "（无现有记忆）"
                ).replace("[[CANDIDATES]]", self._format_candidates(candidates))
                response = await self._call_llm(prompt, "请为每条候选输出整合决策JSON数组。")
                if not isinstance(response, str):
                    response = extract_chat_text(response)
                decisions, pmeta = parse_phase2(response or "", candidates)
                if decisions:
                    meta = {"phase2": "llm", **pmeta}
                    return decisions, meta
                meta = {
                    "phase2": "deterministic",
                    "phase2_fallback_reason": "llm_response_empty_or_unparseable",
                    **pmeta,
                }
            except Exception as e:  # noqa: BLE001
                meta = {"phase2": "deterministic", "phase2_fallback_reason": f"llm_error:{e}"}
                logger.warning("Pipeline Phase2 LLM failed, deterministic fallback: %s", e)
        else:
            meta = {"phase2": "deterministic", "phase2_fallback_reason": "use_llm_phase2=False"}
        return self._deterministic_decisions(candidates), meta

    @staticmethod
    def _deterministic_decisions(
        candidates: list[Phase1Candidate],
    ) -> list[ConsolidationDecision]:
        """确定性降级：全部ADD_NEW（近重复由store的fact_dedup并入门处理）。"""
        return [
            ConsolidationDecision(op="ADD_NEW", candidate=c, reason="deterministic_fallback")
            for c in candidates
        ]

    # ── Apply：决策落库 + workspace diff ───────────────────────

    def apply(
        self,
        decisions: list[ConsolidationDecision],
        run_id: str = "",
        source_session: str = "",
        dry_run: bool = False,
    ) -> list[dict]:
        """执行决策，返回codex memory workspace diff（每条决策的落地结果）。

        diff条目：op(added/merged/updated/rejected/planned_*) + memory_id +
        version_before/version_after + reason——"这条记忆是被哪次管线、因为什么改的"。
        dry_run=True：只产diff不落库（planned_*）。
        """
        diff: list[dict] = []
        existing_ids: set[str] = set()
        if any(d.op in ("MERGE_INTO", "UPDATE") for d in decisions):
            existing_ids = {
                m.get("memory_id", "")
                for m in self._store.list_memories(include_deleted=False, limit=10000)
            }
        for d in decisions:
            entry = {
                "op": "",
                "candidate": (d.candidate.content or "")[:120],
                "memory_id": "",
                "reason": d.reason,
                "version_before": 0,
                "version_after": 0,
            }
            if dry_run:
                entry["op"] = f"planned_{d.op.lower()}"
                entry["memory_id"] = d.target_memory_id
                diff.append(entry)
                continue
            if d.op == "REJECT":
                entry["op"] = "rejected"
                diff.append(entry)
                continue
            if d.op == "ADD_NEW":
                c = d.candidate
                safety_tags = None
                if c.scope or c.durability or c.authority:
                    safety_tags = {
                        "scope": c.scope,
                        "durability": c.durability,
                        "authority": c.authority,
                    }
                mem = self._store.store(
                    content=c.content,
                    memory_type=c.memory_type,
                    importance=c.importance,
                    tags=c.tags,
                    metadata={
                        "source": "memory_pipeline",
                        "run_id": run_id,
                        "reason": d.reason,
                        "evidence": c.evidence,
                    },
                    source_session=source_session,
                    safety_tags=safety_tags,
                    write_mode="auto",
                    # codex管线是自动抽取路径：近重复→并入既有fact而非追加
                    dup_policy="merge",
                )
                if mem is None:
                    # gatekeeper/标签门拒绝：diff可见（mem0 §1.1失败必须可见）
                    outcome = getattr(self._store, "last_write_outcome", "") or "rejected"
                    entry["op"] = "rejected"
                    entry["reason"] = f"{d.reason} (store_outcome={outcome})".strip()
                else:
                    outcome = getattr(self._store, "last_write_outcome", "added")
                    entry["memory_id"] = mem.memory_id
                    entry["op"] = "merged" if outcome == "merged" else "added"
                    entry["version_after"] = self._store.get_current_version(mem.memory_id)
                diff.append(entry)
                continue
            # MERGE_INTO / UPDATE：目标必须是活记忆（不存在→fail-safe拒绝，可见）
            target = d.target_memory_id
            if target not in existing_ids:
                entry["op"] = "rejected"
                entry["reason"] = f"{d.reason} (target_not_found:{target})".strip()
                diff.append(entry)
                continue
            new_content = d.merged_content or d.candidate.content
            entry["version_before"] = self._store.get_current_version(target)
            updated = self._store.update_memory(
                memory_id=target,
                content=new_content,
                reason=f"pipeline_{d.op.lower()}: {d.reason}"[:200],
            )
            if updated is None:
                entry["op"] = "rejected"
                entry["reason"] = f"{d.reason} (update_failed)".strip()
            else:
                entry["op"] = "merged" if d.op == "MERGE_INTO" else "updated"
                entry["memory_id"] = target
                entry["version_after"] = self._store.get_current_version(target)
            diff.append(entry)
        return diff

    # ── run：端到端管线 ────────────────────────────────────────

    async def run(
        self,
        messages: list[dict] | None = None,
        session_id: str = "",
        candidates: list[dict] | None = None,
        apply: bool = True,
        use_llm_phase2: bool = True,
    ) -> PipelineResult:
        """端到端：Phase1→Phase2→apply→workspace diff→持久化run记录。

        - candidates直供（mode="import"）：跳过Phase1，供外部提取器/确定性live验证
        - messages：走Phase1 LLM提取（mode="llm"）
        - 两者都无→error可见返回（不静默假成功）
        """
        run_id = f"pipe_{hashlib.sha256(f'{time.time()}:{session_id}:{len(messages or [])}:{len(candidates or [])}'.encode()).hexdigest()[:12]}"
        result = PipelineResult(run_id=run_id, session_id=session_id)
        cands: list[Phase1Candidate] = []
        if candidates:
            result.mode = "import"
            for item in candidates:
                c = Phase1Candidate.from_item(item)
                if c is not None:
                    cands.append(c)
        elif messages:
            result.mode = "llm"
            p1 = await self.extract(messages, session_id=session_id)
            if p1.error:
                result.error = p1.error
                self._persist(result, decisions=[])
                return result
            cands = p1.candidates
        else:
            result.error = "no messages and no candidates"
            self._persist(result, decisions=[])
            return result
        result.phase1_count = len(cands)
        decisions, pmeta = await self.consolidate(cands, use_llm=use_llm_phase2)
        result.phase2_meta = pmeta
        counts: dict[str, int] = {}
        for d in decisions:
            counts[d.op] = counts.get(d.op, 0) + 1
        result.decision_counts = counts
        result.diff = self.apply(
            decisions, run_id=run_id, source_session=session_id, dry_run=not apply
        )
        self._persist(result, decisions=decisions)
        logger.info(
            "Pipeline %s: mode=%s phase1=%d decisions=%s fallback=%s",
            run_id,
            result.mode,
            result.phase1_count,
            counts,
            pmeta.get("phase2_fallback_reason", "-"),
        )
        return result

    # ── 旧资源修剪（codex"旧extension资源修剪"本地化对应） ──────

    def prune(self, max_age_hours: float = 72.0, memory_type: str = "working") -> dict:
        """修剪过期transient工作记忆（codex workspace旧资源修剪的本地化语义）。

        只修剪：memory_type匹配 ∧ deermem_tags.durability=transient ∧ 超过TTL。
        删除走delete_memory(delete_mode="auto")——task/project域被删除门拦截时
        计blocked可见（fail-closed不绕过既有安全门）。
        """
        cutoff = time.time() - max_age_hours * 3600
        stats = {
            "pruned": 0,
            "blocked": 0,
            "skipped": 0,
            "candidates": 0,
            "max_age_hours": max_age_hours,
            "memory_type": memory_type,
        }
        memories = self._store.list_memories(
            memory_type=memory_type, include_deleted=False, limit=10000
        )
        for m in memories:
            meta = m.get("metadata") or {}
            tags = meta.get("deermem_tags") or {}
            if tags.get("durability") != "transient":
                stats["skipped"] += 1
                continue
            if float(m.get("created_at") or 0) > cutoff:
                stats["skipped"] += 1
                continue
            stats["candidates"] += 1
            ok = self._store.delete_memory(
                memory_id=m.get("memory_id", ""),
                reason=f"pipeline_prune:older_than_{max_age_hours}h",
                hard_delete=False,
                delete_mode="auto",
            )
            if ok:
                stats["pruned"] += 1
            else:
                stats["blocked"] += 1
        if stats["candidates"]:
            logger.info("Pipeline prune: %s", stats)
        return stats

    # ── 可观测：runs持久化与统计 ───────────────────────────────

    def _persist(self, result: PipelineResult, decisions: list[ConsolidationDecision]):
        """run记录落pipeline_runs表（diff即codex workspace diff）。"""
        try:
            with sqlite3.connect(self._store.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO pipeline_runs
                       (run_id, session_id, mode, phase1_count, decision_counts,
                        diff, phase2_meta, error, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        result.run_id,
                        result.session_id,
                        result.mode,
                        result.phase1_count,
                        json.dumps(result.decision_counts, ensure_ascii=False),
                        json.dumps(result.diff, ensure_ascii=False),
                        json.dumps(result.phase2_meta, ensure_ascii=False),
                        result.error,
                        result.created_at,
                    ),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001 — 记账失败不阻断管线结果返回，但必须可见
            logger.error("Pipeline run persist failed %s: %s", result.run_id, e)

    def get_runs(self, limit: int = 20) -> list[dict]:
        """最近的管线运行记录（monitoring/调试用：管线跑过什么、改了什么）。"""
        with sqlite3.connect(self._store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for key in ("decision_counts", "phase2_meta"):
                try:
                    d[key] = json.loads(d.get(key) or "{}")
                except json.JSONDecodeError:
                    d[key] = {}
            try:
                d["diff"] = json.loads(d.get("diff") or "[]")
            except json.JSONDecodeError:
                d["diff"] = []
            out.append(d)
        return out

    def get_stats(self) -> dict:
        """管线统计（/api/hippo/health并入——既有monitoring探测即可见）。"""
        runs = self.get_runs(limit=1000)
        total_added = total_merged = total_updated = total_rejected = 0
        for r in runs:
            for entry in r.get("diff", []):
                op = entry.get("op", "")
                if op == "added":
                    total_added += 1
                elif op == "merged":
                    total_merged += 1
                elif op == "updated":
                    total_updated += 1
                elif op == "rejected":
                    total_rejected += 1
        return {
            "total_runs": len(runs),
            "total_candidates": sum(r.get("phase1_count", 0) for r in runs),
            "diff_added": total_added,
            "diff_merged": total_merged,
            "diff_updated": total_updated,
            "diff_rejected": total_rejected,
            "llm_runs": sum(1 for r in runs if r.get("mode") == "llm"),
            "import_runs": sum(1 for r in runs if r.get("mode") == "import"),
        }

    # ── LLM调用与prompt格式化 ──────────────────────────────────

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """LLM调用：显式llm_call优先；缺省走gland router（dream_distiller同款
        fresh ModelRouter模式——不复用api/gland单例，避免跨event loop的http_client问题）。"""
        if self._llm_call:
            return await self._llm_call(system_prompt, user_prompt)
        try:
            from src.config import settings
            from src.gland.router import ModelRouter

            router = ModelRouter()
            if settings.llm_base_url:
                router.add_provider(
                    name="openai",
                    base_url=settings.llm_base_url,
                    models={"chat": settings.llm_model},
                    priority=0,
                )
                if settings.llm_api_key:
                    router.key_manager.add_key("openai", settings.llm_api_key)
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
                temperature=0.2,
                max_tokens=4096,
                role="summarize",  # Harness Profile: summarize role → own model/budget
            )
            # 权威解包：chat()返回provider原始响应体（choices[0].message.content），
            # 此前的result.get("content")猜测在真实provider上永远落空（live实证bug）
            return extract_chat_text(result)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Gland router call failed: {e}") from e

    @staticmethod
    def _format_memories(memories: list[dict]) -> str:
        """现有记忆→prompt文本（token预算：每条100字×20条×总预算1200字，dream同款）。"""
        lines, total = [], 0
        for m in memories[:20]:
            mid = m.get("memory_id", "")
            content = str(m.get("content", ""))[:100]
            line = f"[{mid}] ({m.get('memory_type', 'semantic')}, imp={float(m.get('importance', 0.5)):.1f}) {content}"
            if total + len(line) > 1200:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    @staticmethod
    def _format_candidates(candidates: list[Phase1Candidate]) -> str:
        """候选→prompt文本（带candidate_index前缀，Phase2决策的引用键）。"""
        lines, total = [], 0
        for i, c in enumerate(candidates[:30]):
            line = (
                f"candidate_index={i} ({c.memory_type}, imp={c.importance:.1f}) {c.content[:150]}"
            )
            if total + len(line) > 1800:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """会话→prompt文本（token预算：每条300字×20条×总预算1800字，dream同款）。"""
        lines, total = [], 0
        for msg in messages[-20:]:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))[:300]
            if not content:
                continue
            line = f"[{role}] {content}"
            if total + len(line) > 1800:
                lines.append(f"[{role}] (消息截断——超出token预算)")
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)
