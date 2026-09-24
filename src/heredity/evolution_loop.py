"""Evolution Loop — 自进化闭环（声明式意图 + 审批落盘 + 护栏 + 回滚 + 记账）。

调研来源（SUMMARY.md P0-7 + evolution-engine-patterns.md）：
- LobeChat declareSelfFeedbackIntent：agent只"声明改进意图"+confidence+evidenceRefs
  稳定id，绝不动手；reviewer负责去重/审批/落盘——"高召回声明+严格审批"
  解掉不敢改/乱改两个死法。
- claude-code ProposeSkills：提议必须给evidence（观察到该流程的稳定引用）
  +完整内容，用户审阅后才保存——无证据的提议直接拒绝。
- CowAgent idle进化触发器+内置skills保护+_WorkspaceWriteGuard硬护栏+
  无效结果回滚+预算控制。
- agno 错误风暴熔断：连续同类型错误即停，防系统性故障被记成N条独立失败。
- agno run_continuation_blocked："审批人≠发起人"。
- mem0 审计表：每次变更写old/new/event账本，全程可追溯。
- Letta READ_ONLY_BLOCK_LABELS / pre-commit保护区：进化产出分「可改区」
  与「保护区」，保护fail-closed不可被绕过。
- kilocode 防记忆回声：同样的证据不能重复产生同一条进化。
- agent-zero _agent_editor/_time_travel：确定性稀疏编辑（只替换锚点片段，
  绝不整文件覆盖）+改动前快照，随时revert。

铁律：本模块只提供"声明→审批→落盘→可回滚"的管线，声明本身不改任何文件；
apply必须经过approved状态，且目标文件在改动前先做快照。
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── CowAgent _WorkspaceWriteGuard / Letta保护区：进化提案永不许碰的目标 ──
# 命中即拒绝（fail-closed）：安全策略、审计/进化引擎自身、密钥类文件。
# 铁律2"不改evo自身bootstrap代码"同样由此程序化强制。
PROTECTED_TARGET_MARKERS = (
    "src/immune/",
    "src/heredity/self_evolution.py",
    "src/heredity/evolution_loop.py",
    "src/api/immune.py",
    "data/",
    ".env",
    "~/.ssh",
)
PROTECTED_FILE_SUFFIXES = (".pem", ".key")

PROPOSAL_KINDS = (
    "skill_new",
    "skill_improvement",
    "policy_adjustment",
    "prompt_strategy",
    "failure_avoidance",
)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_APPLIED = "applied"
STATUS_APPLY_FAILED = "apply_failed"
STATUS_ROLLED_BACK = "rolled_back"

LEDGER_DECLARED = "DECLARED"
LEDGER_APPROVED = "APPROVED"
LEDGER_REJECTED = "REJECTED"
LEDGER_APPLIED = "APPLIED"
LEDGER_APPLY_FAILED = "APPLY_FAILED"
LEDGER_ROLLED_BACK = "ROLLED_BACK"
LEDGER_TRIGGERED = "TRIGGERED"

# ── 预算自愈常量（实盘教训：pending占死预算→budget_exceeded永久拒绝一切新声明） ──
PENDING_TTL_S = 48 * 3600  # pending超龄退役时限（agno stale回收）
REDECLARE_COOLDOWN_S = 7 * 24 * 3600  # 已裁决提案重复声明冷却（kilocode防回声推广）


def is_protected_target(target: str) -> bool:
    """目标路径是否落在保护区内（子串/后缀双向匹配，fail-closed）。"""
    if not target:
        return True  # 空目标=不可落盘，按保护处理
    t = target.replace("\\", "/")
    for marker in PROTECTED_TARGET_MARKERS:
        if marker in t:
            return True
    name = t.rsplit("/", 1)[-1]
    if name == ".env":
        return True
    return name.endswith(PROTECTED_FILE_SUFFIXES)


@dataclass
class EvolutionProposal:
    """一条进化提案：声明≠执行，只有approved后才允许apply。"""

    proposal_id: str
    kind: str
    title: str
    rationale: str = ""
    confidence: float = 0.0
    evidence_refs: list = field(default_factory=list)
    proposed_change: dict = field(default_factory=dict)
    proposer: str = "agent"
    status: str = STATUS_PENDING
    dedup_digest: str = ""
    duplicate_of: str = ""
    reject_reason: str = ""
    reviewed_by: str = ""
    review_comment: str = ""
    snapshot_path: str = ""
    created_at: float = 0.0
    reviewed_at: float = 0.0
    applied_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class EvolutionStore:
    """SQLite持久化：提案表 + mem0式审计账本 + 触发器事件 + 熔断状态。"""

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "evolution"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "evolution.db")
        self.db_path = db_path
        parent = Path(db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir = str(parent / "snapshots")
        Path(self.snapshot_dir).mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS evolution_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    rationale TEXT,
                    confidence REAL,
                    evidence_refs TEXT,
                    proposed_change TEXT,
                    proposer TEXT,
                    status TEXT NOT NULL,
                    dedup_digest TEXT,
                    duplicate_of TEXT,
                    reject_reason TEXT,
                    reviewed_by TEXT,
                    review_comment TEXT,
                    snapshot_path TEXT,
                    created_at REAL,
                    reviewed_at REAL,
                    applied_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_prop_status
                    ON evolution_proposals(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_prop_digest
                    ON evolution_proposals(dedup_digest, status);

                CREATE TABLE IF NOT EXISTS evolution_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT,
                    event TEXT NOT NULL,
                    actor TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    reason TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ledger_prop
                    ON evolution_ledger(proposal_id, created_at);

                CREATE TABLE IF NOT EXISTS evolution_triggers (
                    trigger_id TEXT PRIMARY KEY,
                    trigger_type TEXT NOT NULL,
                    details TEXT,
                    proposal_id TEXT,
                    suppressed INTEGER DEFAULT 0,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS evolution_breaker (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    error_type TEXT,
                    consecutive INTEGER DEFAULT 0,
                    open INTEGER DEFAULT 0,
                    updated_at REAL
                );
                """
            )

    # ── proposals ──────────────────────────────────────────────
    def insert_proposal(self, p: EvolutionProposal):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO evolution_proposals
                   (proposal_id, kind, title, rationale, confidence, evidence_refs,
                    proposed_change, proposer, status, dedup_digest, duplicate_of,
                    reject_reason, reviewed_by, review_comment, snapshot_path,
                    created_at, reviewed_at, applied_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    p.proposal_id,
                    p.kind,
                    p.title,
                    p.rationale,
                    p.confidence,
                    json.dumps(p.evidence_refs, ensure_ascii=False),
                    json.dumps(p.proposed_change, ensure_ascii=False),
                    p.proposer,
                    p.status,
                    p.dedup_digest,
                    p.duplicate_of,
                    p.reject_reason,
                    p.reviewed_by,
                    p.review_comment,
                    p.snapshot_path,
                    p.created_at,
                    p.reviewed_at,
                    p.applied_at,
                ),
            )

    def _row_to_proposal(self, row: sqlite3.Row) -> EvolutionProposal:
        return EvolutionProposal(
            proposal_id=row["proposal_id"],
            kind=row["kind"],
            title=row["title"],
            rationale=row["rationale"] or "",
            confidence=row["confidence"] or 0.0,
            evidence_refs=json.loads(row["evidence_refs"] or "[]"),
            proposed_change=json.loads(row["proposed_change"] or "{}"),
            proposer=row["proposer"] or "",
            status=row["status"],
            dedup_digest=row["dedup_digest"] or "",
            duplicate_of=row["duplicate_of"] or "",
            reject_reason=row["reject_reason"] or "",
            reviewed_by=row["reviewed_by"] or "",
            review_comment=row["review_comment"] or "",
            snapshot_path=row["snapshot_path"] or "",
            created_at=row["created_at"] or 0.0,
            reviewed_at=row["reviewed_at"] or 0.0,
            applied_at=row["applied_at"] or 0.0,
        )

    def get_proposal(self, proposal_id: str) -> EvolutionProposal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return self._row_to_proposal(row) if row else None

    def find_by_digest(self, digest: str, statuses: tuple) -> EvolutionProposal | None:
        if not statuses:
            return None
        placeholders = ",".join("?" * len(statuses))
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT * FROM evolution_proposals
                    WHERE dedup_digest = ? AND status IN ({placeholders})
                    ORDER BY created_at DESC LIMIT 1""",
                (digest, *statuses),
            ).fetchone()
        return self._row_to_proposal(row) if row else None

    def count_by_status(self, status: str) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM evolution_proposals WHERE status = ?",
                (status,),
            ).fetchone()[0]

    def count_stale_pending(self, ttl_s: float, now: float | None = None) -> int:
        cutoff = (now if now is not None else time.time()) - ttl_s
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM evolution_proposals WHERE status = ? AND created_at < ?",
                (STATUS_PENDING, cutoff),
            ).fetchone()[0]

    def retire_stale_pending(self, ttl_s: float, now: float | None = None) -> list:
        """agno stale回收：超龄pending转rejected（stale_pending_expired）+账本可见。

        没有审批人的自主生产者（self_evolution触发器）会让pending永远没人处理，
        max_pending预算一经烧光就永久拒绝一切新声明=进化闭环瘫痪（实盘教训）。
        退役是账本可见的终态（REJECTED actor=retention），不是静默丢弃（mem0失败
        必须可见）；声明方可重提，冷却期只对"人类裁决/试过并回滚"生效。
        """
        cutoff = (now if now is not None else time.time()) - ttl_s
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT proposal_id FROM evolution_proposals WHERE status = ? AND created_at < ?",
                (STATUS_PENDING, cutoff),
            ).fetchall()
        retired = [r["proposal_id"] for r in rows]
        for pid in retired:
            self.update_proposal(
                pid,
                status=STATUS_REJECTED,
                reject_reason="stale_pending_expired",
            )
            self.write_ledger(
                pid,
                LEDGER_REJECTED,
                actor="retention",
                old_value=STATUS_PENDING,
                new_value=STATUS_REJECTED,
                reason=f"stale_pending_expired:ttl={int(ttl_s)}s",
            )
        return retired

    def find_decided_echo(self, digest: str, cutoff: float) -> EvolutionProposal | None:
        """kilocode防记忆回声推广：同digest已有「人类裁决/试过并回滚」且在冷却期内
        → 不重复建单（自主触发器对同一问题反复声明的解药）。

        只认终态裁决：rejected且reviewed_by非空（guard瞬态拒绝如budget_exceeded/
        stale_pending_expired无reviewer，不算裁决，允许重提）或rolled_back
        （试过并回退=最强的"别再提"信号）。
        """
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM evolution_proposals
                    WHERE dedup_digest = ?
                      AND ((status = ? AND reviewed_by != '' AND reviewed_at >= ?)
                        OR (status = ? AND applied_at >= ?))
                    ORDER BY created_at DESC LIMIT 1""",
                (digest, STATUS_REJECTED, cutoff, STATUS_ROLLED_BACK, cutoff),
            ).fetchone()
        return self._row_to_proposal(row) if row else None

    def list_proposals(self, status: str = "", limit: int = 50) -> list:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM evolution_proposals WHERE status = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM evolution_proposals ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_proposal(r).to_dict() for r in rows]

    def update_proposal(self, proposal_id: str, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE evolution_proposals SET {sets} WHERE proposal_id = ?",
                (*fields.values(), proposal_id),
            )

    # ── ledger（mem0审计账本：old/new/event全程可追溯） ─────────
    def write_ledger(
        self,
        proposal_id: str,
        event: str,
        actor: str = "",
        old_value: str = "",
        new_value: str = "",
        reason: str = "",
    ):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO evolution_ledger
                   (proposal_id, event, actor, old_value, new_value, reason, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    proposal_id,
                    event,
                    actor,
                    (old_value or "")[:500],
                    (new_value or "")[:500],
                    (reason or "")[:500],
                    time.time(),
                ),
            )

    def get_ledger(self, proposal_id: str = "", limit: int = 100) -> list:
        with self._connect() as conn:
            if proposal_id:
                rows = conn.execute(
                    """SELECT * FROM evolution_ledger WHERE proposal_id = ?
                       ORDER BY created_at ASC, id ASC LIMIT ?""",
                    (proposal_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM evolution_ledger ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def ledger_event_counts(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event, COUNT(*) AS cnt FROM evolution_ledger GROUP BY event"
            ).fetchall()
        return {r["event"]: r["cnt"] for r in rows}

    # ── triggers + breaker ─────────────────────────────────────
    def insert_trigger(
        self,
        trigger_type: str,
        details: str = "",
        proposal_id: str = "",
        suppressed: int = 0,
    ) -> str:
        trigger_id = f"trg_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO evolution_triggers
                   (trigger_id, trigger_type, details, proposal_id, suppressed, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (trigger_id, trigger_type, details[:500], proposal_id, suppressed, time.time()),
            )
        return trigger_id

    def count_triggers(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM evolution_triggers").fetchone()[0]

    def get_breaker(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evolution_breaker WHERE id = 1").fetchone()
        if not row:
            return {"open": False, "error_type": "", "consecutive": 0}
        return {
            "open": bool(row["open"]),
            "error_type": row["error_type"] or "",
            "consecutive": row["consecutive"] or 0,
        }

    def set_breaker(self, error_type: str, consecutive: int, open_: bool):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO evolution_breaker (id, error_type, consecutive, open, updated_at)
                   VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     error_type = excluded.error_type,
                     consecutive = excluded.consecutive,
                     open = excluded.open,
                     updated_at = excluded.updated_at""",
                (error_type, consecutive, 1 if open_ else 0, time.time()),
            )


class EvolutionEngine:
    """自进化闭环引擎：声明 → 去重/护栏 → 审批 → 稀疏落盘 → 可回滚 → 记账。

    声明方（agent/自动触发器）永远不能直接改文件；reviewer审批人≠发起人；
    apply前先快照；保护区fail-closed。
    """

    def __init__(
        self,
        db_path: str = "",
        max_pending: int = 20,
        idle_threshold_s: float = 60.0,
        context_pressure_threshold: float = 0.8,
        storm_window: int = 3,
        pending_ttl_s: float = PENDING_TTL_S,
        redeclare_cooldown_s: float = REDECLARE_COOLDOWN_S,
    ):
        self.store = EvolutionStore(db_path)
        self.max_pending = max_pending
        self.idle_threshold_s = idle_threshold_s
        self.context_pressure_threshold = context_pressure_threshold
        self.storm_window = storm_window
        self.pending_ttl_s = pending_ttl_s
        self.redeclare_cooldown_s = redeclare_cooldown_s

    # ── digest / 去重（kilocode防回声 + LobeChat reviewer侧去重的前置拦截） ──
    @staticmethod
    def _digest(kind: str, title: str) -> str:
        normalized = " ".join((title or "").lower().split())
        # 波动量归一化：失败计数"(310次)"与成功率趋势"32% vs 90%"是同一问题的时变
        # 读数——不归一会让同一问题每轮生成新digest，去重全面失效，pending被同一组
        # 问题的不同读数占死，budget_exceeded永久拒绝一切新声明=进化闭环瘫痪（实盘
        # 教训：20/20 pending全是同一组问题的不同读数）。只折叠两类读数形态
        # （"(N次)"计数 + "N% vs N%"趋势对），普通数字/百分比改动（如"阈值从32%
        # 调到40%"）保持区分度，防止误合并真正不同的提案。
        normalized = re.sub(r"\(\s*\d+\s*次\s*\)", "(n次)", normalized)
        normalized = re.sub(r"\d+(?:\.\d+)?%\s+vs\s+\d+(?:\.\d+)?%", "n% vs n%", normalized)
        return hashlib.sha256(f"{kind}|{normalized}".encode()).hexdigest()[:16]

    @staticmethod
    def _new_id() -> str:
        return f"evo_{uuid.uuid4().hex[:12]}"

    def _reject(
        self,
        kind: str,
        title: str,
        rationale: str,
        confidence: float,
        evidence_refs: list,
        proposed_change: dict,
        proposer: str,
        reason: str,
        digest: str = "",
        duplicate_of: str = "",
    ) -> dict:
        """记录一条被拒绝的声明——mem0"失败必须可见"，绝不静默丢弃。"""
        p = EvolutionProposal(
            proposal_id=self._new_id(),
            kind=kind if kind in PROPOSAL_KINDS else "policy_adjustment",
            title=(title or "")[:200],
            rationale=rationale or "",
            confidence=confidence,
            evidence_refs=evidence_refs or [],
            proposed_change=proposed_change or {},
            proposer=proposer or "agent",
            status=STATUS_REJECTED,
            dedup_digest=digest,
            duplicate_of=duplicate_of,
            reject_reason=reason,
            created_at=time.time(),
        )
        self.store.insert_proposal(p)
        self.store.write_ledger(
            p.proposal_id,
            LEDGER_REJECTED,
            actor="guard",
            old_value=STATUS_PENDING,
            new_value=STATUS_REJECTED,
            reason=reason,
        )
        return {**p.to_dict(), "duplicate": False}

    # ── ① 声明（LobeChat declareSelfFeedbackIntent：只声明，绝不动手） ──
    def declare_intent(
        self,
        kind: str,
        title: str,
        rationale: str = "",
        confidence: float = 0.5,
        evidence_refs: list | None = None,
        proposed_change: dict | None = None,
        proposer: str = "agent",
    ) -> dict:
        evidence_refs = list(evidence_refs or [])
        proposed_change = dict(proposed_change or {})

        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"invalid kind: {kind!r}, must be one of {PROPOSAL_KINDS}")
        if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
            raise ValueError(f"confidence must be in [0,1], got {confidence!r}")
        if not title or not title.strip():
            raise ValueError("title is required")

        digest = self._digest(kind, title)

        # claude-code ProposeSkills：无evidence的提议直接拒绝
        if not evidence_refs:
            return self._reject(
                kind,
                title,
                rationale,
                confidence,
                evidence_refs,
                proposed_change,
                proposer,
                "evidence_required",
                digest=digest,
            )

        # CowAgent硬护栏：目标落在保护区的提案自动拒绝（审批也不可放行）
        target = str(proposed_change.get("target", ""))
        if proposed_change and target and is_protected_target(target):
            return self._reject(
                kind,
                title,
                rationale,
                confidence,
                evidence_refs,
                proposed_change,
                proposer,
                f"protected_target:{target}",
                digest=digest,
            )

        # kilocode防记忆回声：同digest已applied → 拒绝并指向原提案
        applied_dup = self.store.find_by_digest(digest, (STATUS_APPLIED,))
        if applied_dup:
            return self._reject(
                kind,
                title,
                rationale,
                confidence,
                evidence_refs,
                proposed_change,
                proposer,
                "memory_echo",
                digest=digest,
                duplicate_of=applied_dup.proposal_id,
            )

        # kilocode防记忆回声推广：同digest已有「人类裁决/试过并回滚」且在冷却期内
        # → 返回既有提案，不重复建单（自主触发器对同一问题反复声明的解药；
        # guard瞬态拒绝如budget_exceeded无reviewer，不算裁决，允许重提）
        decided_echo = self.store.find_decided_echo(digest, time.time() - self.redeclare_cooldown_s)
        if decided_echo:
            return {**decided_echo.to_dict(), "duplicate": True}

        # agno stale回收（声明路径惰性清扫）：超龄pending先退役再查重/查预算——
        # 否则无审批人的自主提案会永久占满max_pending预算，一切新声明被
        # budget_exceeded拒绝=进化闭环瘫痪（实盘教训：20/20 pending全卡死）
        try:
            self.store.retire_stale_pending(self.pending_ttl_s)
        except Exception:  # 清扫失败绝不反噬声明路径
            logger.warning("retire_stale_pending failed", exc_info=True)

        # 去重：同digest仍在pending → 返回既有提案，不重复建单
        pending_dup = self.store.find_by_digest(digest, (STATUS_PENDING,))
        if pending_dup:
            return {**pending_dup.to_dict(), "duplicate": True}

        # MetaGPT预算控制 / CowAgent budget：pending积压上限
        if self.store.count_by_status(STATUS_PENDING) >= self.max_pending:
            return self._reject(
                kind,
                title,
                rationale,
                confidence,
                evidence_refs,
                proposed_change,
                proposer,
                f"budget_exceeded:max_pending={self.max_pending}",
                digest=digest,
            )

        p = EvolutionProposal(
            proposal_id=self._new_id(),
            kind=kind,
            title=title.strip()[:200],
            rationale=rationale or "",
            confidence=float(confidence),
            evidence_refs=evidence_refs,
            proposed_change=proposed_change,
            proposer=proposer or "agent",
            status=STATUS_PENDING,
            dedup_digest=digest,
            created_at=time.time(),
        )
        self.store.insert_proposal(p)
        self.store.write_ledger(
            p.proposal_id,
            LEDGER_DECLARED,
            actor=proposer,
            new_value=f"{kind}:{p.title}",
            reason=rationale[:500] if rationale else "",
        )
        return {**p.to_dict(), "duplicate": False}

    # ── ② 审批（agno：审批人≠发起人） ─────────────────────────
    def review(
        self,
        proposal_id: str,
        decision: str,
        reviewer: str,
        comment: str = "",
    ) -> dict:
        if decision not in ("approve", "reject"):
            raise ValueError(f"invalid decision: {decision!r}, must be approve/reject")
        p = self.store.get_proposal(proposal_id)
        if not p:
            raise ValueError(f"proposal not found: {proposal_id}")
        if p.status != STATUS_PENDING:
            raise ValueError(f"proposal {proposal_id} is {p.status}, only pending can be reviewed")
        if reviewer and p.proposer and reviewer == p.proposer:
            raise ValueError("reviewer_is_proposer: 审批人≠发起人（agno规则）")

        new_status = STATUS_APPROVED if decision == "approve" else STATUS_REJECTED
        reject_reason = "" if decision == "approve" else (comment or "rejected_by_reviewer")
        self.store.update_proposal(
            proposal_id,
            status=new_status,
            reviewed_by=reviewer,
            review_comment=comment or "",
            reject_reason=reject_reason,
            reviewed_at=time.time(),
        )
        self.store.write_ledger(
            proposal_id,
            LEDGER_APPROVED if decision == "approve" else LEDGER_REJECTED,
            actor=reviewer,
            old_value=STATUS_PENDING,
            new_value=new_status,
            reason=comment or "",
        )
        updated = self.store.get_proposal(proposal_id)
        return updated.to_dict() if updated else {}

    # ── ③ 落盘（agent-zero确定性稀疏编辑：只替换锚点，绝不整文件重写） ──
    @staticmethod
    def _resolve_target(target: str, base_dir: str) -> Path:
        p = Path(target)
        if not p.is_absolute():
            base = Path(base_dir) if base_dir else Path.cwd()
            p = base / p
        return p

    def apply(self, proposal_id: str, actor: str = "", base_dir: str = "") -> dict:
        p = self.store.get_proposal(proposal_id)
        if not p:
            raise ValueError(f"proposal not found: {proposal_id}")
        if p.status != STATUS_APPROVED:
            raise ValueError(f"proposal {proposal_id} is {p.status}, only approved can be applied")

        change = p.proposed_change or {}
        target = str(change.get("target", ""))
        anchor_old = str(change.get("anchor_old", ""))
        anchor_new = str(change.get("anchor_new", ""))
        if not target or not anchor_old:
            return self._apply_failed(
                p, actor, "invalid_change:target和anchor_old必填（稀疏编辑不支持整文件重写）"
            )

        # 保护区二次校验（fail-closed：即使DB被篡改为approved也拦得住）
        if is_protected_target(target):
            return self._apply_failed(p, actor, f"protected_target:{target}")

        path = self._resolve_target(target, base_dir)
        if not path.exists():
            return self._apply_failed(p, actor, f"target_missing:{path}")
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:  # 读不出就不改（fail-closed）
            return self._apply_failed(p, actor, f"target_unreadable:{e}")

        if anchor_old not in content:
            return self._apply_failed(p, actor, "anchor_not_found:文件未被修改")

        # agent-zero _time_travel：改动前快照，随时可revert
        snapshot_path = Path(self.store.snapshot_dir) / f"{proposal_id}.snap"
        snapshot_path.write_text(content, encoding="utf-8")

        new_content = content.replace(anchor_old, anchor_new, 1)
        path.write_text(new_content, encoding="utf-8")

        self.store.update_proposal(
            proposal_id,
            status=STATUS_APPLIED,
            snapshot_path=str(snapshot_path),
            applied_at=time.time(),
        )
        self.store.write_ledger(
            proposal_id,
            LEDGER_APPLIED,
            actor=actor or "system",
            old_value=anchor_old,
            new_value=anchor_new,
            reason=f"target={path}",
        )
        updated = self.store.get_proposal(proposal_id)
        return updated.to_dict() if updated else {}

    def _apply_failed(self, p: EvolutionProposal, actor: str, reason: str) -> dict:
        self.store.update_proposal(
            p.proposal_id,
            status=STATUS_APPLY_FAILED,
            reject_reason=reason,
        )
        self.store.write_ledger(
            p.proposal_id,
            LEDGER_APPLY_FAILED,
            actor=actor or "system",
            old_value=p.status,
            new_value=STATUS_APPLY_FAILED,
            reason=reason,
        )
        updated = self.store.get_proposal(p.proposal_id)
        return updated.to_dict() if updated else {}

    # ── ④ 回滚（CowAgent无效结果回滚 + agent-zero revert） ──────
    def rollback(self, proposal_id: str, actor: str = "", reason: str = "") -> dict:
        p = self.store.get_proposal(proposal_id)
        if not p:
            raise ValueError(f"proposal not found: {proposal_id}")
        if p.status != STATUS_APPLIED:
            raise ValueError(
                f"proposal {proposal_id} is {p.status}, only applied can be rolled back"
            )
        if not p.snapshot_path or not Path(p.snapshot_path).exists():
            raise ValueError(f"snapshot missing for {proposal_id}, cannot rollback")

        change = p.proposed_change or {}
        target = str(change.get("target", ""))
        path = self._resolve_target(target, "")
        old_content = Path(p.snapshot_path).read_text(encoding="utf-8")
        path.write_text(old_content, encoding="utf-8")

        self.store.update_proposal(proposal_id, status=STATUS_ROLLED_BACK)
        self.store.write_ledger(
            proposal_id,
            LEDGER_ROLLED_BACK,
            actor=actor or "system",
            old_value=str(change.get("anchor_new", ""))[:500],
            new_value=str(change.get("anchor_old", ""))[:500],
            reason=reason or "manual rollback",
        )
        updated = self.store.get_proposal(proposal_id)
        return updated.to_dict() if updated else {}

    # ── ⑤ 触发器（CowAgent idle触发 + agno错误风暴熔断） ────────
    def evaluate_triggers(
        self,
        idle_seconds: float = 0.0,
        context_pressure: float = 0.0,
        budget_remaining: float = 1.0,
        recent_errors: list | None = None,
        auto_propose: bool = False,
    ) -> dict:
        recent_errors = list(recent_errors or [])
        breaker = self.store.get_breaker()

        # 连续同类型错误计数（只看尾部连续段）
        consecutive, last_error = 0, ""
        if recent_errors:
            last_error = recent_errors[-1]
            for e in reversed(recent_errors):
                if e == last_error:
                    consecutive += 1
                else:
                    break

        # agno错误风暴熔断：同类型连续错≥storm_window → 打开熔断，
        # 只记一条breaker事件，绝不把系统性故障记成N条独立触发
        if consecutive >= self.storm_window:
            if breaker["open"] and breaker["error_type"] == last_error:
                return {
                    "fired": [],
                    "breaker_open": True,
                    "breaker_error_type": last_error,
                    "suppressed": True,
                }
            self.store.set_breaker(last_error, consecutive, True)
            trigger_id = self.store.insert_trigger(
                "error_storm_breaker",
                details=f"consecutive={consecutive};error={last_error}",
            )
            self.store.write_ledger(
                trigger_id,
                LEDGER_TRIGGERED,
                actor="trigger",
                new_value="error_storm_breaker",
                reason=last_error,
            )
            return {
                "fired": ["error_storm_breaker"],
                "breaker_open": True,
                "breaker_error_type": last_error,
                "suppressed": False,
            }

        # 错误类型变了/错误消失 → 解除旧熔断（响应反映解除后的状态）
        if breaker["open"] and last_error != breaker["error_type"]:
            self.store.set_breaker(breaker["error_type"], 0, False)
            breaker = {**breaker, "open": False, "consecutive": 0}

        fired = []

        # CowAgent idle进化触发器：idle≥N ∧ context压力≥0.8 ∧ 预算可用
        if (
            idle_seconds >= self.idle_threshold_s
            and context_pressure >= self.context_pressure_threshold
            and budget_remaining > 0
        ):
            trigger_id = self.store.insert_trigger(
                "idle_evolution",
                details=f"idle={idle_seconds}s;pressure={context_pressure};budget={budget_remaining}",
            )
            self.store.write_ledger(
                trigger_id,
                LEDGER_TRIGGERED,
                actor="trigger",
                new_value="idle_evolution",
                reason=f"idle={idle_seconds}s,pressure={context_pressure}",
            )
            fired.append(("idle_evolution", trigger_id))

        # 非风暴级错误模式：有错但未达熔断线 → 记一条error_pattern触发
        if recent_errors and consecutive < self.storm_window:
            trigger_id = self.store.insert_trigger(
                "error_pattern",
                details=f"last_error={last_error};tail_consecutive={consecutive}",
            )
            self.store.write_ledger(
                trigger_id,
                LEDGER_TRIGGERED,
                actor="trigger",
                new_value="error_pattern",
                reason=last_error,
            )
            fired.append(("error_pattern", trigger_id))

        # auto_propose：触发器可以自动"声明"提案（仍是pending，等人审批）
        proposals = []
        if auto_propose:
            kind_map = {"idle_evolution": "policy_adjustment", "error_pattern": "failure_avoidance"}
            for trigger_type, trigger_id in fired:
                result = self.declare_intent(
                    kind=kind_map.get(trigger_type, "policy_adjustment"),
                    title=f"auto:{trigger_type}:{trigger_id}",
                    rationale=f"自动触发器{trigger_type}产生的进化声明",
                    confidence=0.4,
                    evidence_refs=[f"trigger:{trigger_id}"],
                    proposer="auto_trigger",
                )
                if result.get("proposal_id") and not result.get("reject_reason"):
                    with sqlite3.connect(self.store.db_path) as conn:
                        conn.execute(
                            "UPDATE evolution_triggers SET proposal_id = ? WHERE trigger_id = ?",
                            (result["proposal_id"], trigger_id),
                        )
                proposals.append(result)

        return {
            "fired": [t for t, _ in fired],
            "trigger_ids": [tid for _, tid in fired],
            "breaker_open": breaker["open"],
            "auto_proposals": proposals,
        }

    # ── ⑥ 观测（用户极度重视可观测性：一条SQL看清进化管线全貌） ──
    def get_stats(self) -> dict:
        statuses = (
            STATUS_PENDING,
            STATUS_APPROVED,
            STATUS_REJECTED,
            STATUS_APPLIED,
            STATUS_APPLY_FAILED,
            STATUS_ROLLED_BACK,
        )
        proposals = {s: self.store.count_by_status(s) for s in statuses}
        pending = proposals[STATUS_PENDING]
        return {
            "proposals": proposals,
            "pending": pending,
            "max_pending": self.max_pending,
            "pending_budget_remaining": max(0, self.max_pending - pending),
            "pending_stale": self.store.count_stale_pending(self.pending_ttl_s),
            "pending_ttl_s": self.pending_ttl_s,
            "redeclare_cooldown_s": self.redeclare_cooldown_s,
            "ledger_events": self.store.ledger_event_counts(),
            "triggers": self.store.count_triggers(),
            "breaker": self.store.get_breaker(),
        }
