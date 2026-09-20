"""DeerMem 记忆抽取安全标签（scope/durability/authority）+ 写侧近重复并入门。

调研来源：18-deer-flow-source.md #10/#11 + PROGRESS.md DeerMem条目 + SUMMARY.md P0-6
- #11 记忆抽取安全标签："抽取提议必须带scope/durability/authority，自动写只接受
  user-scoped+durable+descriptive；矛盾删除带reason+replacement，task/project域
  删除fail-closed"——防agent乱写记忆，fail-closed设计。
- #10 写侧近重复fact门（fact_dedup）："新fact与同类别现有fact释义重复→并入
  （保留原id/confidence取max）而非追加；token-Jaccard确定性无网络，CJK bigram参与"。
  （近重复判定特征复用 gatekeeper._tokenize：拉丁词+CJK bigram）

组合参照：
- CAMEL verifiers（78-camel-source.md §2.4）："能程序化验证的绝不靠LLM"——
  三标签校验与推断全部是确定性规则，零LLM调用。
- mem0 §1.1（evolution-engine-patterns.md）："失败必须可见，禁止静默降级"——
  每次拒绝带typed rule（invalid_scope/requires_explicit_confirmation/...），
  由调用方落TAG_REJECT/DELETE_BLOCKED审计，绝不静默。
- Letta §4.2：保护区fail-closed——task/project域事实自动路径不可删。

设计：
- SafetyTags三字段词表（fail-closed：缺失或越界即拒绝，不猜默认值给显式提议）：
    scope:       user / task / project / session   （事实属于谁的域）
    durability:  durable / transient               （长期有效 vs 暂时性）
    authority:   descriptive / prescriptive / contradiction（描述事实/规范约束/矛盾更正）
- 自动写（write_mode="auto"，覆盖dream/session_importer/ltm_add等机器写入路径）：
  只接受 user + durable + descriptive 组合；其他合法组合→拒绝并要求人工显式确认
  （write_mode="explicit"，用户CRUD路径）。
- 标签缺失时由确定性推断 infer_tags() 补齐（provenance="inferred"）：
  推断用冒号锚定标记，避免普通内容误判；memory_type=="working"→transient。
- 删除门 validate_delete_tags()：
  authority==contradiction 的事实删除必须带reason+replacement（两种模式都强制）；
  scope∈{task,project} 的事实在自动路径（mode="auto"）删除fail-closed。
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

# ── 合法词表（fail-closed：不在表内即拒绝） ──────────────────────
VALID_SCOPES = ("user", "task", "project", "session")
VALID_DURABILITIES = ("durable", "transient")
VALID_AUTHORITIES = ("descriptive", "prescriptive", "contradiction")

# DeerMem #11：自动写只接受的组合
AUTO_WRITABLE_SCOPE = "user"
AUTO_WRITABLE_DURABILITY = "durable"
AUTO_WRITABLE_AUTHORITY = "descriptive"

# DeerMem #11：自动路径删除fail-closed的域
PROTECTED_DELETE_SCOPES = ("task", "project")

# ── 推断标记（冒号/词边界锚定，保守防误判；比较时lowercase） ──────
TASK_MARKERS = ("todo:", "task:", "待办：", "任务：", "deadline:", "截止：")
PROJECT_MARKERS = ("项目：", "project:", "仓库：", "repo:", "repository:")
PRESCRIPTIVE_MARKERS = ("规则：", "rule:", "policy:", "规范：", "禁令：")
CONTRADICTION_MARKERS = ("更正：", "correction:", "已废弃：", "deprecated:")

WRITE_MODE_AUTO = "auto"
WRITE_MODE_EXPLICIT = "explicit"
DELETE_MODE_AUTO = "auto"
DELETE_MODE_EXPLICIT = "explicit"


@dataclass
class SafetyTags:
    """DeerMem记忆抽取安全标签三元组。"""

    scope: str
    durability: str
    authority: str
    provenance: str = "explicit"  # explicit（提议自带）| inferred（确定性推断）

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "durability": self.durability,
            "authority": self.authority,
            "provenance": self.provenance,
        }

    @classmethod
    def coerce(cls, tags: Any) -> tuple[Optional["SafetyTags"], str]:
        """宽容解析显式标签 → (SafetyTags, "") 或 (None, 非法字段名)。

        fail-closed：三字段必须全部存在且在词表内；缺失/越界都返回非法字段名，
        不给显式提议补默认值（"抽取提议必须带三标签"）。
        """
        if isinstance(tags, SafetyTags):
            raw = {
                "scope": tags.scope,
                "durability": tags.durability,
                "authority": tags.authority,
            }
            provenance = tags.provenance or "explicit"
        elif isinstance(tags, dict):
            raw = {
                "scope": tags.get("scope", ""),
                "durability": tags.get("durability", ""),
                "authority": tags.get("authority", ""),
            }
            provenance = str(tags.get("provenance", "explicit")) or "explicit"
        else:
            return None, "tags"
        if not isinstance(raw["scope"], str) or raw["scope"] not in VALID_SCOPES:
            return None, "scope"
        if not isinstance(raw["durability"], str) or raw["durability"] not in VALID_DURABILITIES:
            return None, "durability"
        if not isinstance(raw["authority"], str) or raw["authority"] not in VALID_AUTHORITIES:
            return None, "authority"
        return (
            cls(
                scope=raw["scope"],
                durability=raw["durability"],
                authority=raw["authority"],
                provenance=provenance,
            ),
            "",
        )


@dataclass
class TagDecision:
    """一次标签门判定结果（mem0 §1.1：拒绝必须显式可见）。"""

    accepted: bool
    rule: str = "ok"  # ok / ok_inferred / bypass / invalid_<field> /
    #                  # requires_explicit_confirmation / replacement_required / protected_scope
    reason: str = ""
    tags: SafetyTags | None = None

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "rule": self.rule,
            "reason": self.reason,
            "tags": self.tags.to_dict() if self.tags else None,
        }


def infer_tags(content: str, memory_type: str = "") -> SafetyTags:
    """确定性标签推断（CAMEL：能程序化验证的绝不靠LLM）。

    默认组合=user/durable/descriptive（自动写可接受）；
    冒号锚定标记命中时改判task/project域或prescriptive/contradiction权威；
    memory_type=="working"→transient（工作记忆不auto-promote为durable LTM，
    DeerMem #11"durable"要求）。推断结果provenance="inferred"。
    """
    scope = "user"
    durability = "durable"
    authority = "descriptive"
    text = (content or "").lower()
    for marker in TASK_MARKERS:
        if marker in text:
            scope = "task"
            break
    if scope == "user":
        for marker in PROJECT_MARKERS:
            if marker in text:
                scope = "project"
                break
    if memory_type == "working":
        durability = "transient"
    for marker in PRESCRIPTIVE_MARKERS:
        if marker in text:
            authority = "prescriptive"
            break
    for marker in CONTRADICTION_MARKERS:
        if marker in text:
            authority = "contradiction"
            break
    return SafetyTags(
        scope=scope,
        durability=durability,
        authority=authority,
        provenance="inferred",
    )


def validate_write_tags(
    tags: Any,
    content: str = "",
    memory_type: str = "",
    write_mode: str = WRITE_MODE_AUTO,
) -> TagDecision:
    """写侧安全标签门（DeerMem #11）。

    - tags为None：确定性推断（provenance=inferred）后照常校验
    - tags显式提供：三字段必须全部合法（fail-closed，缺失/越界→invalid_<field>）
    - write_mode="auto"：只接受user+durable+descriptive，其余合法组合拒绝
      （rule=requires_explicit_confirmation，需人工显式路径）
    - write_mode="explicit"：任何合法组合放行（人工确认路径）
    """
    if tags is None:
        resolved = infer_tags(content, memory_type)
    else:
        resolved, bad_field = SafetyTags.coerce(tags)
        if resolved is None:
            return TagDecision(
                accepted=False,
                rule=f"invalid_{bad_field}",
                reason=(
                    f"安全标签字段'{bad_field}'缺失或越界（fail-closed）——"
                    f"scope∈{VALID_SCOPES}, durability∈{VALID_DURABILITIES}, "
                    f"authority∈{VALID_AUTHORITIES}"
                ),
                tags=None,
            )

    if write_mode == WRITE_MODE_AUTO:
        mismatched = []
        if resolved.scope != AUTO_WRITABLE_SCOPE:
            mismatched.append("scope")
        if resolved.durability != AUTO_WRITABLE_DURABILITY:
            mismatched.append("durability")
        if resolved.authority != AUTO_WRITABLE_AUTHORITY:
            mismatched.append("authority")
        if mismatched:
            return TagDecision(
                accepted=False,
                rule="requires_explicit_confirmation",
                reason=(
                    f"自动写只接受scope={AUTO_WRITABLE_SCOPE}+"
                    f"durability={AUTO_WRITABLE_DURABILITY}+"
                    f"authority={AUTO_WRITABLE_AUTHORITY}；"
                    f"{'/'.join(mismatched)}不符"
                    f"（实际: {resolved.scope}/{resolved.durability}/"
                    f"{resolved.authority}, provenance={resolved.provenance}）"
                    "——需人工显式确认（write_mode=explicit）"
                ),
                tags=resolved,
            )

    rule = "ok" if resolved.provenance == "explicit" else "ok_inferred"
    return TagDecision(accepted=True, rule=rule, reason="", tags=resolved)


def validate_delete_tags(
    tags: Any,
    content: str = "",
    reason: str = "",
    replacement: str = "",
    mode: str = DELETE_MODE_AUTO,
) -> TagDecision:
    """删除安全门（DeerMem #11）。

    - tags缺失：从content确定性推断（既有记忆无标签时的兼容路径）
    - 显式标签非法（stored metadata被污染）→invalid_<field> fail-closed
    - authority==contradiction：删除必须带reason+replacement（两种模式都强制）
    - scope∈{task,project}：mode="auto"时fail-closed（protected_scope）；
      mode="explicit"（人工CRUD路径）放行
    """
    if tags is None:
        resolved = infer_tags(content)
    else:
        resolved, bad_field = SafetyTags.coerce(tags)
        if resolved is None:
            return TagDecision(
                accepted=False,
                rule=f"invalid_{bad_field}",
                reason=f"已存记忆的安全标签损坏（字段'{bad_field}'非法），删除fail-closed",
                tags=None,
            )

    if resolved.authority == "contradiction" and (not reason or not replacement):
        return TagDecision(
            accepted=False,
            rule="replacement_required",
            reason=(
                "矛盾事实（authority=contradiction）删除必须同时带reason与"
                "replacement（DeerMem #11），缺失即fail-closed"
            ),
            tags=resolved,
        )

    if resolved.scope in PROTECTED_DELETE_SCOPES and mode == DELETE_MODE_AUTO:
        return TagDecision(
            accepted=False,
            rule="protected_scope",
            reason=(
                f"scope={resolved.scope}域事实在自动路径删除被fail-closed拦截"
                "（DeerMem #11：task/project域删除需人工显式操作）"
            ),
            tags=resolved,
        )

    return TagDecision(accepted=True, rule="ok", reason="", tags=resolved)


def tags_vocab() -> dict:
    """词表快照（API可观测性用）。"""
    return {
        "scopes": list(VALID_SCOPES),
        "durabilities": list(VALID_DURABILITIES),
        "authorities": list(VALID_AUTHORITIES),
        "auto_writable": {
            "scope": AUTO_WRITABLE_SCOPE,
            "durability": AUTO_WRITABLE_DURABILITY,
            "authority": AUTO_WRITABLE_AUTHORITY,
        },
        "protected_delete_scopes": list(PROTECTED_DELETE_SCOPES),
    }
