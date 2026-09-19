"""记忆准入 Gatekeeper — LobeChat memory-user-memory/gatekeeper 模式移植。

调研来源：26-lobe-chat-source.md（LobeChat #26 60k★）
- LobeChat 6维记忆抽取器中的 gatekeeper："守门员：判断这条该不该进长期记忆"。
- "gatekeeper 是关键差异——不加过滤的记忆=垃圾堆积"。

组合参照：
- DeerMem近重复判定：token-Jaccard + CJK bigram（feature-matrix P0-6 DeerMem条目）
- CAMEL verifiers哲学（78-camel-source.md）："能程序化验证的绝不靠LLM"——
  准入判定是确定性规则，便宜、可靠、可重复，LLM裁判留给规则覆盖不了的维度（后续扩展点）。
- mem0 §1.1（evolution-engine-patterns.md）："失败必须可见，禁止静默降级"——
  每次reject必须落审计（GATE_REJECT事件），不允许静默丢弃。

设计：
- verdict两态：admit / reject；reject带rule+reason（可审计、可统计）。
- 规则（确定性）：
  1. empty_content        空/纯空白
  2. too_short            去空白后 < min_length（默认4）
  3. no_meaningful_content 无拉丁字母且无CJK汉字（纯数字/标点/符号）
  4. control_char_noise   控制字符/不可打印字符占比 > 0.3（乱码/二进制噪声）
  5. secret_detected      疑似凭证（API key/密码/token）——记忆库不是密钥库（对齐immune输出侧护栏）
  6. duplicate_exact      与既有活跃记忆完全相同（归一化后）
  7. duplicate_near       与既有活跃记忆近重复（token-Jaccard/CJK-bigram ≥ threshold，默认0.85，
                          anything-llm循环检测Jaccard 0.85为调研背书值）
- 重复判定由调用方（LongTermMemoryStore）提供候选集 recent_contents，
  gatekeeper本身不碰数据库，可独立单测。
- force=True / enabled=False 时全部规则旁路（bypass），判定记为admit(rule="bypass")。
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("opensoul.hippo.extractors.gatekeeper")

# 凭证特征（与immune输出侧护栏同源思路：记忆库不得落密钥）
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd)\b\s*[=:：]\s*\S{8,}"
    ),
]

# 拉丁字母或CJK统一表意文字（有实质内容）
_MEANINGFUL_RE = re.compile(r"[A-Za-z\u4e00-\u9fff]")

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

VERDICT_ADMIT = "admit"
VERDICT_REJECT = "reject"


@dataclass
class GateDecision:
    """一次准入判定的结果（mem0 §1.1：判定必须显式可见）。"""

    verdict: str  # admit / reject
    rule: str = "ok"  # 命中的规则名；admit时为"ok"/"bypass"
    reason: str = ""  # 人类可读原因
    duplicate_of: str = ""  # reject且为重复时，指向已有memory_id
    similarity: float = 0.0  # reject且为重复时的相似度
    checked_at: float = field(default_factory=time.time)

    @property
    def admitted(self) -> bool:
        return self.verdict == VERDICT_ADMIT

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "rule": self.rule,
            "reason": self.reason,
            "duplicate_of": self.duplicate_of,
            "similarity": round(self.similarity, 3),
            "checked_at": self.checked_at,
        }


def _tokenize(text: str) -> set:
    """拉丁词 + CJK bigram 混合token集（DeerMem近重复判定特征）。"""
    lower = text.lower()
    tokens = set(_WORD_RE.findall(lower))
    cjk = _CJK_RE.findall(lower)
    if len(cjk) == 1:
        tokens.add(cjk[0])
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    return tokens


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


class MemoryGatekeeper:
    """记忆准入判定器（确定性规则，无LLM依赖）。

    用法：
        gk = MemoryGatekeeper()
        decision = gk.evaluate("用户偏好Python",
                               recent_contents=[("ltm_x", "用户偏好Rust")])
        if decision.verdict == "reject":
            ...  # 不入库，落GATE_REJECT审计
    """

    def __init__(
        self,
        enabled: bool = True,
        min_length: int = 4,
        near_dup_threshold: float = 0.85,
        max_control_char_ratio: float = 0.3,
    ):
        self.enabled = enabled
        self.min_length = min_length
        self.near_dup_threshold = near_dup_threshold
        self.max_control_char_ratio = max_control_char_ratio
        # 进程内计数（跨进程历史以memory_audit的GATE_REJECT事件为准）
        self.admitted = 0
        self.rejected = 0
        self.rejected_by_rule: dict[str, int] = {}
        self.last_decision: Optional[GateDecision] = None

    # ── 规则 ──────────────────────────────────────────────

    def _check_secret(self, content: str) -> Optional[tuple[str, str]]:
        for pat in _SECRET_PATTERNS:
            m = pat.search(content)
            if m:
                # 不把命中的密钥原文写进reason（防二次泄露），只报类型
                return (
                    "secret_detected",
                    f"疑似凭证特征: pattern={pat.pattern[:30]}...",
                )
        return None

    def _check_control_noise(self, content: str) -> Optional[tuple[str, str]]:
        if not content:
            return None
        bad = sum(1 for ch in content if ord(ch) < 32 and ch not in "\t\n\r")
        ratio = bad / len(content)
        if ratio > self.max_control_char_ratio:
            return (
                "control_char_noise",
                f"控制字符占比{ratio:.2f} > {self.max_control_char_ratio}",
            )
        return None

    def _check_duplicate(
        self,
        content: str,
        recent_contents: list[tuple[str, str]],
    ) -> Optional[tuple[str, str, str, float]]:
        """返回 (rule, reason, duplicate_of, similarity) 或 None。

        recent_contents: [(memory_id, content), ...] 既有活跃记忆候选集，
        由调用方提供（store路径给最近N条），gatekeeper不查库。
        """
        norm = " ".join(content.lower().split())
        cand_tokens = _tokenize(content)
        for mid, existing in recent_contents:
            if " ".join(existing.lower().split()) == norm:
                return ("duplicate_exact", f"与既有记忆{mid}完全相同", mid, 1.0)
        for mid, existing in recent_contents:
            sim = _jaccard(cand_tokens, _tokenize(existing))
            if sim >= self.near_dup_threshold:
                return (
                    "duplicate_near",
                    f"与既有记忆{mid}近重复(similarity={sim:.2f}>= {self.near_dup_threshold})",
                    mid,
                    sim,
                )
        return None

    # ── 判定入口 ──────────────────────────────────────────

    def evaluate(
        self,
        content: str,
        memory_type: str = "",
        recent_contents: Optional[list[tuple[str, str]]] = None,
        force: bool = False,
    ) -> GateDecision:
        """判定一条候选记忆是否准入长期记忆库。

        Args:
            content: 候选记忆内容
            memory_type: 记忆类型（当前规则未按类型分流，预留）
            recent_contents: 既有活跃记忆[(memory_id, content)]，None=跳过重复判定
            force: True时旁路全部规则（显式人工覆盖）
        """
        if not self.enabled or force:
            decision = GateDecision(
                verdict=VERDICT_ADMIT,
                rule="bypass",
                reason="force" if force else "gatekeeper_disabled",
            )
            self._record(decision)
            return decision

        stripped = (content or "").strip()

        # 1. 空内容
        if not stripped:
            decision = GateDecision(VERDICT_REJECT, "empty_content", "内容为空/纯空白")
            self._record(decision)
            return decision

        # 2. 过短
        if len(stripped) < self.min_length:
            decision = GateDecision(
                VERDICT_REJECT,
                "too_short",
                f"内容过短(len={len(stripped)} < {self.min_length})",
            )
            self._record(decision)
            return decision

        # 3. 无实质内容（纯数字/标点/符号）
        if not _MEANINGFUL_RE.search(stripped):
            decision = GateDecision(
                VERDICT_REJECT,
                "no_meaningful_content",
                "无拉丁字母/汉字（纯数字或符号）",
            )
            self._record(decision)
            return decision

        # 4. 控制字符噪声
        hit = self._check_control_noise(stripped)
        if hit:
            decision = GateDecision(VERDICT_REJECT, hit[0], hit[1])
            self._record(decision)
            return decision

        # 5. 凭证
        hit = self._check_secret(stripped)
        if hit:
            decision = GateDecision(VERDICT_REJECT, hit[0], hit[1])
            self._record(decision)
            return decision

        # 6/7. 重复（需要调用方给候选集）
        if recent_contents is not None:
            dup = self._check_duplicate(stripped, recent_contents)
            if dup:
                rule, reason, dup_of, sim = dup
                decision = GateDecision(
                    VERDICT_REJECT,
                    rule,
                    reason,
                    duplicate_of=dup_of,
                    similarity=sim,
                )
                self._record(decision)
                return decision

        decision = GateDecision(VERDICT_ADMIT, "ok", "通过全部准入规则")
        self._record(decision)
        return decision

    def _record(self, decision: GateDecision):
        self.last_decision = decision
        if decision.admitted:
            self.admitted += 1
        else:
            self.rejected += 1
            self.rejected_by_rule[decision.rule] = (
                self.rejected_by_rule.get(decision.rule, 0) + 1
            )
            logger.info(
                "gatekeeper reject rule=%s reason=%s", decision.rule, decision.reason
            )

    def stats(self) -> dict:
        """进程内判定统计。跨进程历史见LongTermMemoryStore.get_gatekeeper_stats()。"""
        return {
            "enabled": self.enabled,
            "admitted": self.admitted,
            "rejected": self.rejected,
            "rejected_by_rule": dict(self.rejected_by_rule),
            "last_decision": self.last_decision.to_dict() if self.last_decision else None,
            "config": {
                "min_length": self.min_length,
                "near_dup_threshold": self.near_dup_threshold,
                "max_control_char_ratio": self.max_control_char_ratio,
            },
        }
