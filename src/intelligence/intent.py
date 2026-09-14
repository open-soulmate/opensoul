"""Intent classification for OpenIntelligence.

Routes user messages to appropriate processing strategies based on
detected intent. Borrowed from Rasa NLU + DistilBERT intent detection.

9 intents: coding / file_op / web_search / research / writing / system /
task_management / configuration / conversation
"""

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

logger = logging.getLogger("opensoul.intelligence.intent")


class Intent(StrEnum):
    CODING = "coding"
    FILE_OP = "file_operation"
    WEB_SEARCH = "web_search"
    RESEARCH = "research"
    WRITING = "writing"
    SYSTEM = "system"
    CONVERSATION = "conversation"
    TASK_MANAGEMENT = "task_management"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    intent: Intent
    confidence: float
    sub_intents: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
    routing_strategy: dict = field(default_factory=dict)


INTENT_PATTERNS: dict[Intent, dict] = {
    Intent.CODING: {
        "keywords": [
            "代码", "函数", "bug", "调试", "测试", "重构", "实现", "开发",
            "code", "function", "debug", "test", "refactor", "implement",
            "python", "javascript", "typescript", "java", "rust", "go",
            "报错", "错误", "修复", "fix", "error", "exception",
        ],
        "tools": ["terminal", "read_file", "write_file", "patch"],
        "weight": 1.2,
        "strategy": {
            "max_iterations": 10,
            "enable_terminal": True,
            "enable_file_ops": True,
            "context_verbosity": "detailed",
        },
    },
    Intent.FILE_OP: {
        "keywords": [
            "文件", "目录", "读取", "写入", "创建", "删除", "复制", "移动",
            "file", "directory", "read", "write", "create", "delete", "copy", "move",
            "打开", "保存", "导出", "导入", "open", "save", "export", "import",
        ],
        "tools": ["read_file", "write_file", "search_files", "terminal"],
        "weight": 1.0,
        "strategy": {
            "max_iterations": 5,
            "enable_terminal": True,
            "enable_file_ops": True,
            "context_verbosity": "normal",
        },
    },
    Intent.WEB_SEARCH: {
        "keywords": [
            "搜索", "查询", "查找", "网上", "百度", "谷歌",
            "search", "google", "look up", "find online",
            "最新", "新闻", "资讯", "latest", "news",
        ],
        "tools": ["web_search", "web_extract", "browser_exec"],
        "weight": 1.0,
        "strategy": {
            "max_iterations": 3,
            "enable_browser": True,
            "context_verbosity": "normal",
        },
    },
    Intent.RESEARCH: {
        "keywords": [
            "调研", "分析", "研究", "对比", "评估", "报告",
            "research", "analyze", "study", "compare", "evaluate", "report",
            "为什么", "如何", "怎么样", "why", "how",
        ],
        "tools": ["web_search", "read_file", "write_file"],
        "weight": 0.9,
        "strategy": {
            "max_iterations": 5,
            "enable_browser": True,
            "context_verbosity": "detailed",
        },
    },
    Intent.WRITING: {
        "keywords": [
            "写", "撰写", "编写", "文章", "文档", "方案", "报告", "总结",
            "write", "draft", "article", "document", "report", "summary",
            "公众号", "博客", "blog", "邮件", "email",
        ],
        "tools": ["write_file", "web_search"],
        "weight": 1.0,
        "strategy": {
            "max_iterations": 5,
            "context_verbosity": "detailed",
        },
    },
    Intent.SYSTEM: {
        "keywords": [
            "运行", "执行", "安装", "部署", "启动", "停止", "重启",
            "run", "execute", "install", "deploy", "start", "stop", "restart",
            "终端", "命令", "shell", "terminal", "command", "bash",
            "进程", "端口", "服务", "process", "port", "service",
        ],
        "tools": ["terminal"],
        "weight": 1.1,
        "strategy": {
            "max_iterations": 10,
            "enable_terminal": True,
            "context_verbosity": "normal",
        },
    },
    Intent.TASK_MANAGEMENT: {
        "keywords": [
            "任务", "计划", "安排", "待办", "提醒", "定时",
            "task", "plan", "schedule", "todo", "remind", "cron",
            "步骤", "流程", "优先级", "step", "workflow", "priority",
        ],
        "tools": ["todo", "cronjob"],
        "weight": 0.8,
        "strategy": {
            "max_iterations": 3,
            "context_verbosity": "normal",
        },
    },
    Intent.CONFIGURATION: {
        "keywords": [
            "配置", "设置", "修改", "调整", "偏好",
            "config", "settings", "configure", "adjust", "preference",
            "模型", "provider", "model", "端口", "port",
        ],
        "tools": ["read_file", "write_file", "terminal"],
        "weight": 0.7,
        "strategy": {
            "max_iterations": 5,
            "enable_terminal": True,
            "context_verbosity": "normal",
        },
    },
}

DEFAULT_STRATEGY = {
    "max_iterations": 3,
    "enable_terminal": False,
    "enable_file_ops": False,
    "enable_browser": False,
    "context_verbosity": "concise",
}


class IntentClassifier:
    """Classifies user message intent and routes to processing strategy."""

    def __init__(self):
        self._stats = {"total_classified": 0, "by_intent": {}}

    def classify(self, message: str) -> IntentResult:
        self._stats["total_classified"] += 1
        message_lower = message.lower()
        scores: dict[Intent, float] = {}
        matched: dict[Intent, list[str]] = {}

        for intent, config in INTENT_PATTERNS.items():
            score = 0.0
            found = []
            for kw in config["keywords"]:
                if kw in message_lower:
                    score += 1.0
                    found.append(kw)
            if score > 0:
                score *= config["weight"]
                if len(message) < 50:
                    score *= 1.5
                scores[intent] = score
                matched[intent] = found

        if not scores:
            self._stats["by_intent"]["unknown"] = (
                self._stats["by_intent"].get("unknown", 0) + 1
            )
            return IntentResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                routing_strategy=DEFAULT_STRATEGY,
            )

        best = max(scores, key=lambda k: scores[k])
        best_score = scores[best]
        max_possible = (
            len(INTENT_PATTERNS[best]["keywords"]) * INTENT_PATTERNS[best]["weight"]
        )
        confidence = min(1.0, best_score / max_possible)

        sub = [
            i.value
            for i, s in sorted(scores.items(), key=lambda x: x[1], reverse=True)
            if i != best and s > best_score * 0.5
        ]

        self._stats["by_intent"][best.value] = (
            self._stats["by_intent"].get(best.value, 0) + 1
        )

        return IntentResult(
            intent=best,
            confidence=round(confidence, 2),
            sub_intents=sub,
            keywords=matched.get(best, []),
            suggested_tools=INTENT_PATTERNS[best]["tools"],
            routing_strategy=INTENT_PATTERNS[best]["strategy"],
        )

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "known_intents": [i.value for i in Intent if i != Intent.UNKNOWN],
        }
