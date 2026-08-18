"""OpenLimb — 四肢：RPA执行器、浏览器自动化、表单填报。"""

from src.limb.executor import RPAExecutor
from src.limb.tasks import BUILTIN_TEMPLATES, Action, ActionType, StepResult, TaskTemplate

__all__ = ["RPAExecutor", "Action", "ActionType", "TaskTemplate", "StepResult", "BUILTIN_TEMPLATES"]
