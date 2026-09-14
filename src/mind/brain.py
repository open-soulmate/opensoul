"""SoulBrain — Agent的大脑主控，调度四层认知流水线。"""

import logging
import re
import uuid

from src.cortex.chain_of_thought import ChainOfThought
from src.cortex.project_memory import ProjectMemory
from src.cortex.risk_assessor import RiskAssessor
from src.cortex.reflector import Reflector
from src.learn.experience import ExperienceMemory
from src.mind.user_memory import UserMemory
from src.models.cognitive import Decision, Intent, TaskContext, Verification

logger = logging.getLogger(__name__)


class SoulBrain:
    """Agent的大脑 — 调度四层认知流水线。

    不替代工具，是工具的大脑层。
    只做思考、评估、决策、复盘，不直接操作文件。
    """

    def __init__(self, repo_root: str):
        self.project_memory = ProjectMemory(repo_root)
        self.user_memory = UserMemory()
        self.experience = ExperienceMemory()
        self.chain_of_thought = ChainOfThought()
        self.assessor = RiskAssessor(self.project_memory, self.experience)
        self.reflector = Reflector()
        logger.info("SoulBrain初始化完成, repo=%s", repo_root)

    async def think(self, user_input: str, task_ctx: TaskContext) -> Decision:
        """思考主入口：用户输入 → 执行决策"""
        task_ctx.user_input = user_input
        if not task_ctx.task_id:
            task_ctx.task_id = str(uuid.uuid4())[:8]

        # 1. 意图理解（Phase 1: 规则匹配；Phase 2: 用ChainOfThought做LLM理解）
        intent = await self._understand_intent(user_input, task_ctx)
        task_ctx.intent = intent

        # 2. 风险评估
        risk = await self.assessor.assess(intent, task_ctx)
        task_ctx.risk = risk

        # 3. 策略决策
        decision = self._decide(intent, risk, task_ctx)
        task_ctx.decision = decision

        logger.info("思考完成: goal=%s, files=%s, risk=%s, execute=%s",
                     intent.goal, intent.target_files, risk.overall_level, decision.execute_mode)
        return decision

    async def verify(self, action: str, result: dict, task_ctx: TaskContext) -> Verification:
        """执行后反思验证 — 无论成功/失败/异常都进入复盘"""
        verification = await self.reflector.reflect(action, result, task_ctx)
        task_ctx.verification = verification

        # 学习
        self.experience.record(action, result, verification, task_ctx)

        task_ctx.history.append({
            "action": action,
            "success": verification.success,
            "checks": [(c.name, c.passed) for c in verification.checks],
        })
        return verification

    async def _understand_intent(self, user_input: str, task_ctx: TaskContext) -> Intent:
        """意图理解：Phase 1用规则匹配，Phase 2用ChainOfThought"""
        text = user_input.lower()
        target_files = self._extract_target_files(user_input)
        goal = self._classify_goal(text)
        change_size = self._estimate_change_size(text, goal)

        if len(target_files) > 3:
            modify_scope = "multi_file"
        elif len(target_files) > 1:
            modify_scope = "multi_file"
        elif goal == "create":
            modify_scope = "project_wide"
        elif any(w in text for w in ["方法", "函数", "function", "method", "def "]):
            modify_scope = "single_method"
        else:
            modify_scope = "single_file"

        return Intent(
            user_prompt=user_input,
            target_files=target_files,
            modify_scope=modify_scope,
            goal=goal,
            change_size=change_size,
        )

    def _extract_target_files(self, text: str) -> list[str]:
        files = []
        for m in re.finditer(r'[\w/\\.-]+\.\w+', text):
            candidate = m.group(0).strip("\"'")
            full = self.project_memory.repo_root / candidate
            if full.exists():
                files.append(candidate)
        return files

    def _classify_goal(self, text: str) -> str:
        if any(w in text for w in ["创建", "新建", "create", "添加文件"]):
            return "create"
        if any(w in text for w in ["重写", "rewrite", "完全重写"]):
            return "rewrite"
        if any(w in text for w in ["重构", "refactor", "优化", "重组织"]):
            return "refactor"
        if any(w in text for w in ["修复", "修", "fix", "bug", "错误", "问题"]):
            return "fix_bug"
        if any(w in text for w in ["添加", "增加", "新增", "add", "feature", "功能"]):
            return "add_feature"
        return "fix_bug"

    def _estimate_change_size(self, text: str, goal: str) -> str:
        if goal in ("create", "rewrite"):
            return "large"
        if any(w in text for w in ["重写", "整个", "全部", "全面", "大规模"]):
            return "large"
        if any(w in text for w in ["小改", "微调", "一点点", "修改一下"]):
            return "small"
        if any(w in text for w in ["重构", "优化", "reorganize"]):
            return "medium"
        return "small"

    def _decide(self, intent: Intent, risk, task_ctx: TaskContext) -> Decision:
        if intent.goal == "create":
            edit_mode = "full"
        else:
            edit_mode = "patch"

        if risk.overall_level == "critical":
            execute_mode = "deny"
            confirm_prompt = f"风险过高，拒绝执行：{risk.recommendation}"
        elif risk.overall_level == "high":
            if self.user_memory.should_auto_execute("high"):
                execute_mode = "auto"
                confirm_prompt = None
            else:
                execute_mode = "confirm_required"
                confirm_prompt = f"高风险操作，需要确认：{risk.recommendation}"
        elif risk.overall_level == "medium":
            if self.user_memory.should_auto_execute("medium"):
                execute_mode = "auto"
                confirm_prompt = None
            else:
                execute_mode = "confirm_required"
                confirm_prompt = f"中风险操作：{risk.recommendation}"
        else:
            execute_mode = "auto"
            confirm_prompt = None

        return Decision(
            intent=intent, risk=risk, edit_mode=edit_mode,
            execute_mode=execute_mode, confirm_prompt=confirm_prompt,
        )

    def get_status(self) -> dict:
        return {
            "project_memory": self.project_memory.get_stats(),
            "experience": self.experience.get_stats(),
            "user_preferences": self.user_memory.preferences,
        }
