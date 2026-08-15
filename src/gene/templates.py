"""Template engine — manage and instantiate templates."""

from __future__ import annotations

import json
import os
import time
import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Template:
    template_id: str
    name: str
    category: str  # "agent", "knowledge_base", "workflow", "skill"
    description: str
    version: str = "1.0.0"
    author: str = "system"
    tags: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    variables: list[dict] = field(default_factory=list)  # [{name, type, default, description}]
    created_at: float = field(default_factory=time.time)
    usage_count: int = 0
    builtin: bool = False


class TemplateEngine:
    """Template registry with built-in and user-defined templates."""

    def __init__(self, storage_dir: str | Path | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.opensoul/templates"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, Template] = {}
        self._lock = threading.Lock()
        self._load_user_templates()
        self._register_builtins()

    def _load_user_templates(self):
        for f in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                t = Template(**data)
                self._templates[t.template_id] = t
            except Exception:
                pass

    def _save_template(self, template: Template):
        path = self.storage_dir / f"{template.template_id}.json"
        path.write_text(json.dumps({
            "template_id": template.template_id,
            "name": template.name,
            "category": template.category,
            "description": template.description,
            "version": template.version,
            "author": template.author,
            "tags": template.tags,
            "config": template.config,
            "variables": template.variables,
            "created_at": template.created_at,
            "usage_count": template.usage_count,
            "builtin": template.builtin,
        }, ensure_ascii=False, indent=2))

    def _register_builtins(self):
        """Register built-in starter templates."""
        builtins = [
            Template(
                template_id="agent-rag-assistant",
                name="RAG知识助手",
                category="agent",
                description="基于RAG的知识问答Agent，支持文档检索和智能回答",
                tags=["rag", "qa", "knowledge"],
                config={
                    "system_prompt": "你是一个知识助手，基于检索到的文档内容回答用户问题。如果文档中没有相关信息，请明确告知。",
                    "model": "gpt-4",
                    "temperature": 0.3,
                    "tools": ["knowledge_search", "web_search"],
                    "knowledge_base_ids": [],
                },
                variables=[
                    {"name": "knowledge_base_ids", "type": "list", "default": [], "description": "关联的知识库ID列表"},
                    {"name": "model", "type": "string", "default": "gpt-4", "description": "使用的模型"},
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-code-reviewer",
                name="代码审查Agent",
                category="agent",
                description="自动代码审查Agent，检测代码质量、安全漏洞和最佳实践",
                tags=["code", "review", "security"],
                config={
                    "system_prompt": "你是一个专业的代码审查员。审查代码的：1)正确性 2)安全性 3)性能 4)可维护性。给出具体改进建议。",
                    "model": "gpt-4",
                    "temperature": 0.2,
                    "tools": ["file_read", "terminal"],
                },
                variables=[
                    {"name": "language", "type": "string", "default": "python", "description": "编程语言"},
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-tech-docs",
                name="技术文档知识库",
                category="knowledge_base",
                description="技术文档知识库模板，含标准分块策略和元数据结构",
                tags=["tech", "docs", "rag"],
                config={
                    "chunk_size": 512,
                    "chunk_overlap": 50,
                    "embedding_model": "text-embedding-3-small",
                    "metadata_schema": {
                        "source": "string",
                        "category": "string",
                        "version": "string",
                        "language": "string",
                    },
                },
                variables=[
                    {"name": "name", "type": "string", "default": "技术文档库", "description": "知识库名称"},
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-customer-support",
                name="客服知识库",
                category="knowledge_base",
                description="客服FAQ和产品文档知识库，支持多轮对话上下文",
                tags=["customer", "support", "faq"],
                config={
                    "chunk_size": 256,
                    "chunk_overlap": 30,
                    "embedding_model": "text-embedding-3-small",
                    "metadata_schema": {
                        "product": "string",
                        "version": "string",
                        "category": "string",
                    },
                },
                variables=[
                    {"name": "product_name", "type": "string", "default": "", "description": "产品名称"},
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-doc-processing",
                name="文档处理流水线",
                category="workflow",
                description="文档采集→解析→分块→入库→索引 的标准处理流水线",
                tags=["document", "pipeline", "processing"],
                config={
                    "steps": [
                        {"name": "采集", "type": "collect", "config": {"source": "file_watch"}},
                        {"name": "解析", "type": "parse", "config": {"ocr": True}},
                        {"name": "分块", "type": "chunk", "config": {"strategy": "semantic"}},
                        {"name": "入库", "type": "store", "config": {"target": "knowledge_base"}},
                        {"name": "索引", "type": "index", "config": {"engines": ["vector", "fulltext"]}},
                    ],
                    "trigger": {"type": "file_watch", "paths": []},
                },
                variables=[
                    {"name": "watch_paths", "type": "list", "default": [], "description": "监控目录列表"},
                    {"name": "target_kb", "type": "string", "default": "", "description": "目标知识库ID"},
                ],
                builtin=True,
            ),
            Template(
                template_id="skill-web-scraper",
                name="网页采集Skill",
                category="skill",
                description="网页数据采集和清洗Skill模板",
                tags=["web", "scraper", "collect"],
                config={
                    "url_patterns": [],
                    "selectors": {},
                    "schedule": "0 */6 * * *",
                    "output_format": "json",
                },
                variables=[
                    {"name": "target_url", "type": "string", "default": "", "description": "目标URL"},
                    {"name": "schedule", "type": "string", "default": "0 */6 * * *", "description": "定时表达式"},
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-data-analyst",
                name="数据分析Agent",
                category="agent",
                description="数据分析Agent，支持CSV/JSON数据探索、统计分析和可视化建议",
                tags=["data", "analysis", "statistics", "visualization"],
                config={
                    "system_prompt": "你是一个数据分析专家。帮助用户探索数据、发现模式、提供统计分析和可视化建议。用清晰的中文解释分析结果。",
                    "model": "gpt-4",
                    "temperature": 0.3,
                    "tools": ["file_read", "terminal", "knowledge_search"],
                },
                variables=[
                    {"name": "data_source", "type": "string", "default": "", "description": "数据源路径或描述"},
                    {"name": "analysis_focus", "type": "string", "default": "general", "description": "分析重点 (general/trend/correlation/anomaly)"},
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-translator",
                name="多语言翻译Agent",
                category="agent",
                description="专业翻译Agent，支持中英日韩多语言互译，保持术语一致性",
                tags=["translation", "multilingual", "i18n"],
                config={
                    "system_prompt": "你是一个专业翻译。准确翻译内容，保持原文风格和语气。对于专业术语，首次出现时附上原文。支持中英日韩互译。",
                    "model": "gpt-4",
                    "temperature": 0.2,
                    "tools": ["knowledge_search"],
                },
                variables=[
                    {"name": "source_lang", "type": "string", "default": "auto", "description": "源语言 (auto/zh/en/ja/ko)"},
                    {"name": "target_lang", "type": "string", "default": "en", "description": "目标语言"},
                    {"name": "glossary_kb", "type": "string", "default": "", "description": "术语表知识库ID"},
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-monitoring-alert",
                name="监控告警流水线",
                category="workflow",
                description="系统监控→阈值检测→告警通知 的自动化运维流水线",
                tags=["monitoring", "alert", "devops", "ops"],
                config={
                    "steps": [
                        {"name": "采集指标", "type": "collect", "config": {"source": "vital", "interval_seconds": 60}},
                        {"name": "阈值检测", "type": "check", "config": {"rules": [
                            {"metric": "cpu_percent", "threshold": 90, "operator": ">"},
                            {"metric": "memory_percent", "threshold": 85, "operator": ">"},
                            {"metric": "disk_percent", "threshold": 90, "operator": ">"},
                        ]}},
                        {"name": "发送告警", "type": "notify", "config": {"channels": ["echo"], "severity": "warning"}},
                    ],
                    "trigger": {"type": "interval", "interval_seconds": 300},
                },
                variables=[
                    {"name": "cpu_threshold", "type": "number", "default": 90, "description": "CPU告警阈值 (%)"},
                    {"name": "memory_threshold", "type": "number", "default": 85, "description": "内存告警阈值 (%)"},
                    {"name": "alert_channels", "type": "list", "default": ["echo"], "description": "告警通知通道"},
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-backup-schedule",
                name="定时备份流水线",
                category="workflow",
                description="定时备份→压缩→校验→清理旧备份 的自动化灾备流水线",
                tags=["backup", "disaster-recovery", "schedule", "marrow"],
                config={
                    "steps": [
                        {"name": "创建备份", "type": "backup", "config": {"source": "marrow", "incremental": True}},
                        {"name": "完整性校验", "type": "verify", "config": {"checksum": "sha256"}},
                        {"name": "清理旧备份", "type": "cleanup", "config": {"keep_days": 30, "keep_min": 5}},
                    ],
                    "trigger": {"type": "cron", "schedule": "0 3 * * *"},
                },
                variables=[
                    {"name": "backup_dirs", "type": "list", "default": ["~/.opensoul/data"], "description": "备份目录列表"},
                    {"name": "keep_days", "type": "number", "default": 30, "description": "保留天数"},
                    {"name": "schedule", "type": "string", "default": "0 3 * * *", "description": "Cron表达式"},
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-personal-notes",
                name="个人笔记知识库",
                category="knowledge_base",
                description="个人笔记和学习记录知识库，支持Markdown导入和语义搜索",
                tags=["notes", "personal", "learning", "markdown"],
                config={
                    "chunk_size": 384,
                    "chunk_overlap": 64,
                    "embedding_model": "text-embedding-3-small",
                    "metadata_schema": {
                        "topic": "string",
                        "date": "date",
                        "source": "string",
                        "tags": "list",
                    },
                    "auto_index": True,
                    "supported_formats": ["markdown", "txt", "pdf"],
                },
                variables=[
                    {"name": "name", "type": "string", "default": "我的笔记", "description": "知识库名称"},
                    {"name": "auto_tag", "type": "boolean", "default": True, "description": "自动标签"},
                ],
                builtin=True,
            ),
        ]

        for t in builtins:
            if t.template_id not in self._templates:
                self._templates[t.template_id] = t

    def list_templates(self, category: str | None = None, tag: str | None = None) -> list[dict]:
        with self._lock:
            templates = list(self._templates.values())

        if category:
            templates = [t for t in templates if t.category == category]
        if tag:
            templates = [t for t in templates if tag in t.tags]

        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "version": t.version,
                "author": t.author,
                "tags": t.tags,
                "variables": t.variables,
                "usage_count": t.usage_count,
                "builtin": t.builtin,
            }
            for t in sorted(templates, key=lambda x: x.created_at, reverse=True)
        ]

    def get_template(self, template_id: str) -> Template | None:
        with self._lock:
            return self._templates.get(template_id)

    def instantiate(self, template_id: str, variable_values: dict | None = None) -> dict:
        """Create an instance from a template with variable substitution."""
        with self._lock:
            template = self._templates.get(template_id)
        if not template:
            return {"success": False, "error": f"Template '{template_id}' not found"}

        config = copy.deepcopy(template.config)
        variables = variable_values or {}

        # Apply variable defaults
        for var in template.variables:
            name = var["name"]
            if name not in variables:
                variables[name] = var.get("default", "")

        # Simple variable substitution in config values
        config = self._substitute(config, variables)

        with self._lock:
            template.usage_count += 1

        return {
            "success": True,
            "template_id": template_id,
            "name": template.name,
            "category": template.category,
            "config": config,
            "variables": variables,
        }

    def create_template(self, data: dict) -> Template:
        """Create a new user template."""
        template = Template(
            template_id=data.get("template_id", f"user-{int(time.time())}"),
            name=data["name"],
            category=data["category"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "user"),
            tags=data.get("tags", []),
            config=data.get("config", {}),
            variables=data.get("variables", []),
        )
        with self._lock:
            self._templates[template.template_id] = template
        self._save_template(template)
        return template

    def delete_template(self, template_id: str) -> bool:
        with self._lock:
            t = self._templates.get(template_id)
            if not t:
                return False
            if t.builtin:
                return False
            del self._templates[template_id]

        path = self.storage_dir / f"{template_id}.json"
        if path.exists():
            path.unlink()
        return True

    @staticmethod
    def _substitute(obj: Any, variables: dict) -> Any:
        if isinstance(obj, str):
            for k, v in variables.items():
                obj = obj.replace(f"{{{{{k}}}}}", str(v))
            return obj
        elif isinstance(obj, dict):
            return {k: TemplateEngine._substitute(v, variables) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [TemplateEngine._substitute(item, variables) for item in obj]
        return obj

    def stats(self) -> dict:
        with self._lock:
            categories = {}
            for t in self._templates.values():
                categories[t.category] = categories.get(t.category, 0) + 1
            return {
                "total_templates": len(self._templates),
                "builtin_count": sum(1 for t in self._templates.values() if t.builtin),
                "user_count": sum(1 for t in self._templates.values() if not t.builtin),
                "by_category": categories,
            }
