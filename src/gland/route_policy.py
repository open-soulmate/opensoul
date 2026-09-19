"""模型路由策略 — UI路由按钮 ↔ 运行时LLM调用的接线层（模型路由4按钮接线）

事实源约定：
- online target: src.api.llm._get_config()（设置页UI保存的provider/key/model，实时）
- local target: 本机Ollama (deepseek-r1:latest，机器上实际存在的本地模型)
- mode/规则持久化: data/model_router_config.json（与/api/model-router共用）

4种模式语义：
- cost(省钱):        prefer=local，local失败可降级online
- balance(均衡):     prefer=online(主)，local兜底
- intelligence(智力): prefer=online，不走local
- auto(自动):        按routing-config规则检测消息复杂度动态选
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "model_router_config.json"

# 机器上实际存在的本地模型（Ollama /api/tags验证过）
LOCAL_TARGET = {
    "provider": "ollama",
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "deepseek-r1:latest",
    "api_key": "",
}

VALID_MODES = ("cost", "balance", "intelligence", "auto")

DEFAULT_ROUTING_RULES = {
    "enabled": True,
    "mode": "auto",
    "defaultStrategy": "local-first",
    # 0.25=检测到question/code等信号即走online，短问候(0.0)仍走local
    "complexityThreshold": 0.25,
    "autoParams": {
        "shortTextThreshold": 50,
        "codeDetection": True,
        "questionDetection": True,
        "imageAnalysis": True,
    },
    "rules": [],
}

_CODE_RE = re.compile(r"```|def |class |import |function |SELECT |<\w+>|{\s*[\"']")
_QUESTION_RE = re.compile(r"[?？]|如何|为什么|怎么|什么|解释|分析|帮我|写一|debug", re.I)


def _read_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_config(data: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # 写盘失败不阻塞运行


def get_mode() -> str:
    """当前路由模式（持久化，重启不丢）"""
    mode = _read_config().get("routerMode", "balance")
    return mode if mode in VALID_MODES else "balance"


def save_mode(mode: str) -> str:
    if mode not in VALID_MODES:
        mode = "balance"
    data = _read_config()
    data["routerMode"] = mode
    _write_config(data)
    return mode


def get_routing_rules() -> dict:
    """自动路由规则配置（/api/model-router/routing-config同源）"""
    data = _read_config()
    rules = data.get("routingRules")
    if isinstance(rules, dict):
        merged = dict(DEFAULT_ROUTING_RULES)
        merged.update(rules)
        return merged
    return dict(DEFAULT_ROUTING_RULES)


def save_routing_rules(rules: dict) -> None:
    data = _read_config()
    data["routingRules"] = rules
    _write_config(data)


def online_target() -> dict:
    """在线LLM target — 实时读设置页UI保存的配置（/api/llm/config同源）"""
    try:
        from src.api.llm import _get_config
        cfg = _get_config()
        return {
            "provider": "online",
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "api_key": cfg.get("api_key", "") or "",
        }
    except Exception:
        return {"provider": "online", "base_url": "", "model": "", "api_key": ""}


def local_target() -> dict:
    return dict(LOCAL_TARGET)


def _decision_feedback_swap(prefer: str, primary: dict, backup: dict) -> tuple[bool, str]:
    """P0-6 TradingAgents决策延迟回填·读路径①：按决策日志中provider真实成功率反馈
    决定是否主备互换——"带真实反馈信号的记忆直接影响下一次决策"（进化闭环最小实证）。

    数据源：src/hippo/decision_log.get_provider_stats("llm_routing")——由
    src/api/chat.py每次LLM调用回填的attempts_detail聚合（provider×ok逐次真实结果）。

    规则（保守、确定性，无数据时行为与接线前完全一致）：
    - 首选provider：窗口内attempts≥5且failure_rate≥0.5 → 候选互换；
    - 备选provider有数据（≥5次）：须failure_rate比首选低0.3以上才互换
      （备选同样糟糕时不换——没有更好的可换）；
    - 备选无数据：仅当首选failure_rate≥0.8（系统性失败）才切到未知备选（failover语义）；
    - decision_log不可用/无数据 → 返回(False, "")，路由行为不变（失败静默降级）。
    """
    try:
        from src.hippo.decision_log import get_decision_log
        stats = get_decision_log().get_provider_stats(domain="llm_routing", window=100)
    except Exception:
        return False, ""
    if not stats:
        return False, ""
    p_name = primary.get("provider", "")
    b_name = backup.get("provider", "")
    p = stats.get(p_name)
    if not p or not b_name or p_name == b_name:
        return False, ""
    p_rate = p.get("failure_rate", 0.0)
    p_n = p.get("attempts", 0)
    if p_n < 5 or p_rate < 0.5:
        return False, ""
    b = stats.get(b_name)
    if b and b.get("attempts", 0) >= 5:
        b_rate = b.get("failure_rate", 1.0)
        if b_rate < p_rate - 0.3:
            return True, (f"{p_name}近{p_n}次失败率{p_rate:.0%}，"
                          f"{b_name}失败率{b_rate:.0%}→主备互换")
        return False, ""
    if p_rate >= 0.8:
        return True, f"{p_name}近{p_n}次失败率{p_rate:.0%}（系统性失败）→切换到{b_name}"
    return False, ""


def detect_complexity(message: str, auto_params: dict) -> tuple[float, dict]:
    """auto模式的复杂度启发式（0=简单→local，1=复杂→online）"""
    flags = {"code": False, "question": False, "short": False, "image": False}
    score = 0.0
    if not message:
        return 0.2, flags
    threshold = auto_params.get("shortTextThreshold", 50)
    if len(message) < threshold:
        flags["short"] = True
        score += 0.0
    else:
        score += 0.3
    if auto_params.get("codeDetection", True) and _CODE_RE.search(message):
        flags["code"] = True
        score += 0.45
    if auto_params.get("questionDetection", True) and _QUESTION_RE.search(message):
        flags["question"] = True
        score += 0.25
    if auto_params.get("imageAnalysis", True) and re.search(r"图片|图像|image|photo", message, re.I):
        flags["image"] = True
        score += 0.45
    return min(score, 1.0), flags


def resolve_target(message: str = "") -> dict:
    """路由决策入口：mode + 消息 → 具体LLM调用target

    返回: {mode, prefer, fallback, target{provider,base_url,model,api_key}, reason, complexity?}
    """
    mode = get_mode()
    online = online_target()
    local = local_target()
    complexity = None

    if mode == "cost":
        prefer, fallback, reason = "local", "online", "cost模式：本地模型优先，失败降级在线"
        primary, backup = local, online
    elif mode == "intelligence":
        prefer, fallback, reason = "online", None, "intelligence模式：在线模型优先"
        primary, backup = online, local
    elif mode == "auto":
        rules = get_routing_rules()
        if rules.get("enabled", True):
            params = rules.get("autoParams", DEFAULT_ROUTING_RULES["autoParams"])
            complexity, flags = detect_complexity(message, params)
            threshold = rules.get("complexityThreshold", 0.5)
            if complexity >= threshold:
                prefer, fallback, primary, backup = "online", "local", online, local
                reason = f"auto模式：复杂度{complexity:.2f}≥{threshold}→在线 flags={flags}"
            else:
                prefer, fallback, primary, backup = "local", "online", local, online
                reason = f"auto模式：复杂度{complexity:.2f}<{threshold}→本地 flags={flags}"
        else:
            prefer, fallback, primary, backup = "online", "local", online, local
            reason = "auto模式：规则未启用→在线"
    else:  # balance (default)
        prefer, fallback, reason = "online", "local", "balance模式：在线主链，本地兜底"
        primary, backup = online, local

    # P0-6决策延迟回填·读路径①：决策记忆反馈影响本次路由。
    # 仅auto/balance模式（用户显式选择的cost/intelligence语义不覆盖）；无数据/异常时静默不变。
    if mode in ("auto", "balance") and fallback:
        swapped, fb_note = _decision_feedback_swap(prefer, primary, backup)
        if swapped:
            primary, backup = backup, primary
            reason = f"{reason}；决策记忆反馈：{fb_note}"

    out = {
        "mode": mode,
        "prefer": prefer,
        "fallback": fallback,
        "target": dict(primary),
        "backup_target": dict(backup) if fallback else None,
        "reason": reason,
    }
    if complexity is not None:
        out["complexity"] = round(complexity, 2)
    return out
