"""P0-4/P1: 上下文逐项token归因 — claude-code SDKContextUsage移植

调研来源：feature-matrix/10-claude-code-source.md #7（SDKContextUsage：total_tokens/
raw_max_tokens/percentage/over_limit{tokens_over, kind: hard_limit|compaction_window} +
四类明细数组 mcp_tools[]/memory_files[]/agents[]/skills[]，每项多少token一目了然）；
SUMMARY.md P0-4/P1"token逐项归因（per-tool/per-agent）"——用户"不知道上下文被什么吃掉了"
的唯一行业级解法。52-langfuse-source.md #15：langfuse只有总量统计，升级方向=按span逐项归因。

镜像关系：acp-proxy/agent/token_attribution.py 是本模块在agent运行时（独立venv/进程，
无法import opensoul）的同启发式镜像实现，两侧estimate_tokens公式必须保持一致
（两侧测试套件各自断言同一公式）。改动任一侧时同步另一侧。

token估算启发式（两侧一致）：
- CJK字符（汉字/全角标点）≈ 1 token/字符（保守估计，实际多数tokenizer中文≈1~1.5字符/token）
- 其余字符 ≈ 4字符/token（kilocode chars/4公式，context_compression._estimate_tokens同源）
- 估算非精确计数——用途是"哪一项最吃上下文"的相对归因，不是计费

失败纪律（mem0 §1.1 + 本项目fail-safe惯例）：归因是观测性旁路，任何异常不得阻断
LLM请求主路径（调用方均以try/except包裹，失败仅debug日志）。
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── item kinds（与SDKContextUsage四类明细对齐 + OpenSoul扩展类别）──
KIND_MCP_TOOL = "mcp_tool"
KIND_BUILTIN_TOOL = "builtin_tool"
KIND_EVOLUTION_TOOL = "evolution_tool"
KIND_TOOL = "tool"  # 未分类工具
KIND_MEMORY = "memory_file"
KIND_AGENT = "agent"
KIND_SKILL = "skill"
KIND_SYSTEM_PROMPT = "system_prompt"
KIND_MESSAGE = "message"
KIND_TOOL_RESULT = "tool_result"
KIND_RAG = "rag_context"
KIND_PREFERENCE = "preference"
KIND_IMPROVEMENT = "improvement"
KIND_OTHER = "other"

# SDKContextUsage明细数组key映射；未映射的kind聚进sections
_LIST_KEYS = {
    KIND_MCP_TOOL: "mcp_tools",
    KIND_BUILTIN_TOOL: "builtin_tools",
    KIND_EVOLUTION_TOOL: "evolution_tools",
    KIND_TOOL: "tools",
    KIND_MEMORY: "memory_files",
    KIND_AGENT: "agents",
    KIND_SKILL: "skills",
}

# 模型上下文窗口粗表（子串匹配，未命中走default）。
# 数值取公开规格；本地ollama模型实际窗口以num_ctx为准（可用CONTEXT_WINDOW_TOKENS env覆盖）。
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "deepseek": 65536,
    "mimo": 65536,
    "qwen": 32768,
    "glm": 128000,
    "gpt-4o": 128000,
    "gpt-4": 128000,
    "gpt-3.5": 16385,
    "claude": 200000,
    "kimi": 128000,
}
DEFAULT_CONTEXT_WINDOW = 32768  # 与llm_engine._truncate_context默认max_tokens一致
DEFAULT_COMPACTION_RATIO = 0.6  # context_compression.DEFAULT_BUDGET_RATIO


def resolve_context_window(model: str | None, default: int = DEFAULT_CONTEXT_WINDOW) -> int:
    """按模型名子串解析上下文窗口；未命中返回default。"""
    if not model:
        return default
    m = model.lower()
    for key, window in MODEL_CONTEXT_LIMITS.items():
        if key in m:
            return window
    return default


def estimate_tokens(text: str) -> int:
    """token估算（CJK≈1token/字符，其余chars//4）。与acp-proxy镜像实现公式一致。"""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            cjk += 1
    return cjk + (len(text) - cjk) // 4


@dataclass
class ContextItem:
    """上下文中一个可归因单元（一个工具定义/一份记忆/一个skill/一段系统提示）。"""

    kind: str
    name: str
    source: str = ""
    tokens: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"name": self.name, "source": self.source, "tokens": self.tokens}
        if self.extra:
            d.update(self.extra)
        return d


def items_from_openai_tools(
    tools: list[dict] | None, source: str, kind: str | None = None
) -> list[ContextItem]:
    """OpenAI function-calling工具定义列表 → 逐工具ContextItem（per-tool归因）。

    source: "mcp" | "builtin" | "evolution" | 自定义；kind缺省按source推导。
    """
    if not tools:
        return []
    if kind is None:
        kind = {
            "mcp": KIND_MCP_TOOL,
            "builtin": KIND_BUILTIN_TOOL,
            "evolution": KIND_EVOLUTION_TOOL,
        }.get(source, KIND_TOOL)
    items: list[ContextItem] = []
    for t in tools:
        try:
            fn = (t or {}).get("function", {}) or {}
            name = str(fn.get("name") or "unnamed_tool")
            tokens = estimate_tokens(json.dumps(t, ensure_ascii=False))
            items.append(ContextItem(kind=kind, name=name, source=source, tokens=tokens))
        except Exception:
            continue
    return items


def build_context_usage(
    items: list[ContextItem],
    max_tokens: int | None = None,
    compaction_tokens: int | None = None,
    model: str | None = None,
) -> dict:
    """构建SDKContextUsage形态的归因结果。

    - total_tokens / raw_max_tokens / percentage
    - over_limit: None | {tokens_over, kind: "hard_limit"|"compaction_window"}
      hard_limit=超过模型窗口（请求会失败）；compaction_window=超过压缩触发线
      （还能跑，但该触发上下文压缩了）——claude-code对两种超限性质的区分。
    - 明细数组：mcp_tools/builtin_tools/evolution_tools/tools/memory_files/agents/skills
    - sections：其余类别按kind聚合（system_prompt/message/tool_result/rag_context等）
    - top_consumers：全部item按tokens降序前10（"什么最吃上下文"直接答案）
    """
    if max_tokens is None:
        max_tokens = resolve_context_window(model)
    max_tokens = max(1, int(max_tokens))
    if compaction_tokens is None:
        compaction_tokens = int(max_tokens * DEFAULT_COMPACTION_RATIO)

    total = sum(i.tokens for i in items)
    percentage = round(total * 100.0 / max_tokens, 1)

    over_limit: dict | None = None
    if total > max_tokens:
        over_limit = {"tokens_over": total - max_tokens, "kind": "hard_limit"}
    elif total > compaction_tokens:
        over_limit = {
            "tokens_over": total - compaction_tokens,
            "kind": "compaction_window",
        }

    lists: dict[str, list] = {k: [] for k in _LIST_KEYS.values()}
    sections: dict[str, int] = defaultdict(int)
    for it in items:
        key = _LIST_KEYS.get(it.kind)
        if key:
            lists[key].append(it.to_dict())
        else:
            sections[it.kind] += it.tokens

    ranked = sorted(items, key=lambda i: i.tokens, reverse=True)
    top = [
        {
            "name": i.name,
            "kind": i.kind,
            "source": i.source,
            "tokens": i.tokens,
            "percentage": round(i.tokens * 100.0 / max_tokens, 1),
        }
        for i in ranked[:10]
    ]

    return {
        "total_tokens": total,
        "raw_max_tokens": max_tokens,
        "compaction_tokens": compaction_tokens,
        "percentage": percentage,
        "over_limit": over_limit,
        **lists,
        "sections": dict(sections),
        "top_consumers": top,
        "item_count": len(items),
    }


class ContextAttributor:
    """进程内归因记录器（环形缓冲 + 可选JSONL账本）。

    账本用途：跨进程读取（API进程读agent进程写，与tool_output spill账本同模式）。
    """

    def __init__(self, max_records: int = 50, ledger_path: str | None = None):
        self._records: deque[dict] = deque(maxlen=max_records)
        self.ledger_path = ledger_path
        if ledger_path:
            try:
                from pathlib import Path

                Path(ledger_path).parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    def record(
        self,
        usage: dict,
        session_id: str = "",
        provider: str = "",
        model: str = "",
        actual_prompt_tokens: int | None = None,
    ) -> dict:
        rec = {
            "ts": time.time(),
            "session_id": session_id,
            "provider": provider,
            "model": model,
            "usage": usage,
            "actual_prompt_tokens": actual_prompt_tokens,
        }
        if actual_prompt_tokens is not None:
            # 估算vs真实prompt_tokens偏差——归因估算的自我校准信号
            rec["estimate_gap"] = actual_prompt_tokens - int(usage.get("total_tokens", 0))
        self._records.append(rec)
        if self.ledger_path:
            try:
                with open(self.ledger_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as exc:
                logger.debug("token attribution ledger write failed (non-fatal): %s", exc)
        return rec

    def backfill_actual(self, session_id: str, actual_prompt_tokens: int | None) -> dict | None:
        """P1 provider usage回填：流式请求从SSE chunk拿到provider真实prompt_tokens后，
        回填到最近一条匹配session的归因记录，计算estimate_gap（估算vs真实偏差）。

        为什么需要：estimate_tokens是启发式估算（只算了我方组装的记忆/RAG/问题），而provider
        返回的prompt_tokens是权威计数（还含chat模板/系统提示等我方未归因的开销）。二者差值
        estimate_gap量化"隐藏开销"——正是用户"不知道上下文被什么吃掉了"里估算覆盖不到的部分，
        同时是估算器自我校准的信号源（record(actual_prompt_tokens=...)预留的字段由此真正接线）。

        找不到匹配session记录时返回None（fail-safe，不新建记录、不抛异常）。
        """
        if actual_prompt_tokens is None:
            return None
        try:
            for rec in reversed(self._records):
                if rec.get("session_id") == session_id:
                    rec["actual_prompt_tokens"] = actual_prompt_tokens
                    rec["estimate_gap"] = actual_prompt_tokens - int(
                        rec.get("usage", {}).get("total_tokens", 0)
                    )
                    if self.ledger_path:
                        try:
                            with open(self.ledger_path, "a", encoding="utf-8") as f:
                                f.write(
                                    json.dumps(
                                        {
                                            "backfill": True,
                                            "ts": time.time(),
                                            "session_id": session_id,
                                            "actual_prompt_tokens": actual_prompt_tokens,
                                            "estimate_gap": rec["estimate_gap"],
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                        except Exception as exc:
                            logger.debug(
                                "token attribution backfill ledger write failed (non-fatal): %s",
                                exc,
                            )
                    return rec
        except Exception as exc:
            logger.debug("token attribution backfill unavailable (non-fatal): %s", exc)
        return None

    def recent(self, limit: int = 20) -> list[dict]:
        recs = list(self._records)
        return recs[-limit:][::-1]  # 最新在前

    def summary(self) -> dict:
        recs = list(self._records)
        if not recs:
            return {"total_records": 0}
        totals = [r["usage"].get("total_tokens", 0) for r in recs]
        over = sum(1 for r in recs if r["usage"].get("over_limit"))
        agg: dict[tuple, dict] = {}
        for r in recs:
            for c in r["usage"].get("top_consumers", []):
                key = (c.get("kind"), c.get("name"))
                slot = agg.setdefault(
                    key,
                    {
                        "kind": c.get("kind"),
                        "name": c.get("name"),
                        "source": c.get("source"),
                        "tokens_total": 0,
                        "times_seen": 0,
                    },
                )
                slot["tokens_total"] += c.get("tokens", 0)
                slot["times_seen"] += 1
        top = sorted(agg.values(), key=lambda s: s["tokens_total"], reverse=True)[:10]
        for s in top:
            s["avg_tokens"] = s["tokens_total"] // max(1, s["times_seen"])
        latest = recs[-1]
        return {
            "total_records": len(recs),
            "over_limit_records": over,
            "avg_total_tokens": sum(totals) // len(totals),
            "max_total_tokens": max(totals),
            "latest": {
                "ts": latest["ts"],
                "session_id": latest.get("session_id", ""),
                "model": latest.get("model", ""),
                "total_tokens": latest["usage"].get("total_tokens", 0),
                "percentage": latest["usage"].get("percentage", 0),
                "over_limit": latest["usage"].get("over_limit"),
                "raw_max_tokens": latest["usage"].get("raw_max_tokens", 0),
            },
            "top_consumers": top,
        }


# 进程内单例（chat API路径使用；acp-proxy侧用JSONL账本跨进程）
_attributor: ContextAttributor | None = None


def get_attributor() -> ContextAttributor:
    global _attributor
    if _attributor is None:
        _attributor = ContextAttributor()
    return _attributor


def reset_attributor() -> None:
    """测试隔离用：重置进程内单例。"""
    global _attributor
    _attributor = None
