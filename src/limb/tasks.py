"""RPA Task definitions — actions, steps, and workflow sequences.

Defines the building blocks for RPA automation:
- Single actions (click, type, navigate, screenshot, etc.)
- Step sequences (ordered list of actions)
- Templates for common workflows (form fill, data extraction, etc.)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    EXTRACT = "extract"
    SCROLL = "scroll"
    KEY_PRESS = "key_press"
    HOVER = "hover"
    SUBMIT = "submit"
    CONDITIONAL = "conditional"
    CUSTOM = "custom"


@dataclass
class Action:
    """A single RPA action."""
    action_type: ActionType
    target: str = ""          # CSS selector, URL, or element identifier
    value: str = ""           # Text to type, option to select, etc.
    description: str = ""
    timeout: int = 30         # seconds
    retry: int = 0            # number of retries on failure
    optional: bool = False    # if True, failure doesn't stop execution
    condition: str = ""       # for conditional actions: JS expression
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type.value,
            "target": self.target,
            "value": self.value,
            "description": self.description,
            "timeout": self.timeout,
            "retry": self.retry,
            "optional": self.optional,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Action:
        return cls(
            action_type=ActionType(data.get("action_type", "custom")),
            target=data.get("target", ""),
            value=data.get("value", ""),
            description=data.get("description", ""),
            timeout=data.get("timeout", 30),
            retry=data.get("retry", 0),
            optional=data.get("optional", False),
            condition=data.get("condition", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class StepResult:
    """Result of executing a single step."""
    action_type: str
    success: bool
    action_index: int = 0
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    screenshot_path: str = ""


@dataclass
class TaskTemplate:
    """Reusable RPA task template."""
    template_id: str
    name: str
    description: str
    category: str  # "form_fill", "data_extract", "navigation", "custom"
    actions: list[Action]
    variables: list[dict] = field(default_factory=list)  # [{name, description, default}]
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "action_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
            "variables": self.variables,
            "tags": self.tags,
        }


# ── Built-in Templates ───────────────────────────────────

BUILTIN_TEMPLATES = [
    TaskTemplate(
        template_id="tpl-form-fill",
        name="表单填写",
        description="自动填写网页表单",
        category="form_fill",
        actions=[
            Action(ActionType.NAVIGATE, target="{{url}}", description="打开目标页面"),
            Action(ActionType.WAIT, target="form", timeout=10, description="等待表单加载"),
            Action(ActionType.TYPE, target="{{field_selector}}", value="{{field_value}}", description="填写字段"),
            Action(ActionType.SUBMIT, target="form", description="提交表单"),
            Action(ActionType.SCREENSHOT, description="截图确认"),
        ],
        variables=[
            {"name": "url", "description": "目标URL", "default": ""},
            {"name": "field_selector", "description": "字段CSS选择器", "default": ""},
            {"name": "field_value", "description": "填写内容", "default": ""},
        ],
        tags=["form", "automation"],
    ),
    TaskTemplate(
        template_id="tpl-data-extract",
        name="数据提取",
        description="从网页提取结构化数据",
        category="data_extract",
        actions=[
            Action(ActionType.NAVIGATE, target="{{url}}", description="打开目标页面"),
            Action(ActionType.WAIT, target="{{container_selector}}", timeout=10, description="等待内容加载"),
            Action(ActionType.EXTRACT, target="{{container_selector}}", description="提取数据"),
        ],
        variables=[
            {"name": "url", "description": "目标URL", "default": ""},
            {"name": "container_selector", "description": "数据容器CSS选择器", "default": ""},
        ],
        tags=["extract", "scraping"],
    ),
    TaskTemplate(
        template_id="tpl-login",
        name="自动登录",
        description="自动化登录流程",
        category="navigation",
        actions=[
            Action(ActionType.NAVIGATE, target="{{login_url}}", description="打开登录页"),
            Action(ActionType.WAIT, target="input[type='text'], input[type='email']", timeout=10, description="等待登录表单"),
            Action(ActionType.TYPE, target="{{username_selector}}", value="{{username}}", description="输入用户名"),
            Action(ActionType.TYPE, target="{{password_selector}}", value="{{password}}", description="输入密码"),
            Action(ActionType.CLICK, target="{{submit_selector}}", description="点击登录"),
            Action(ActionType.WAIT, timeout=5, description="等待登录完成"),
            Action(ActionType.SCREENSHOT, description="截图确认"),
        ],
        variables=[
            {"name": "login_url", "description": "登录页URL", "default": ""},
            {"name": "username", "description": "用户名", "default": ""},
            {"name": "password", "description": "密码", "default": ""},
            {"name": "username_selector", "description": "用户名输入框", "default": "input[type='text']"},
            {"name": "password_selector", "description": "密码输入框", "default": "input[type='password']"},
            {"name": "submit_selector", "description": "提交按钮", "default": "button[type='submit']"},
        ],
        tags=["login", "auth"],
    ),
    TaskTemplate(
        template_id="tpl-screenshot",
        name="页面截图",
        description="截取网页完整或区域截图",
        category="custom",
        actions=[
            Action(ActionType.NAVIGATE, target="{{url}}", description="打开目标页面"),
            Action(ActionType.WAIT, timeout=5, description="等待页面加载"),
            Action(ActionType.SCREENSHOT, target="{{selector}}", description="截图"),
        ],
        variables=[
            {"name": "url", "description": "目标URL", "default": ""},
            {"name": "selector", "description": "截图区域选择器 (可选)", "default": ""},
        ],
        tags=["screenshot", "capture"],
    ),
]
