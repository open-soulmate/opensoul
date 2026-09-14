"""SoulBrain — Agent的大脑主控，多租户多Agent版本。"""

import logging
import re
import time
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

    多租户：按 tenant_id + agent_id 隔离。
    不替代工具，是工具的大脑层。只做思考、评估、决策、复盘。
    """

    def __init__(self, tenant_id: str = "default", agent_id: str = "default",
                 repo_root: str = "", db_pool=None):
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.repo_root = repo_root
        self.db = db_pool

        # 模块初始化（延迟加载，db_pool可用后再初始化）
        self._project_memory: ProjectMemory | None = None
        self._experience: ExperienceMemory | None = None
        self._user_memory: UserMemory | None = None
        self._chain_of_thought: ChainOfThought | None = None
        self._assessor: RiskAssessor | None = None
        self._reflector: Reflector | None = None
        self._initialized = False

    async def _ensure_init(self):
        """延迟初始化：确保所有模块就绪"""
        if self._initialized:
            return

        self._project_memory = ProjectMemory(self.repo_root) if self.repo_root else None
        self._experience = ExperienceMemory(self.db, self.tenant_id, self.agent_id)
        self._user_memory = UserMemory(self.db, self.tenant_id, self.agent_id)
        self._chain_of_thought = ChainOfThought()
        if self._project_memory:
            self._assessor = RiskAssessor(self._project_memory, self._experience)
        self._reflector = Reflector()
        self._initialized = True
        logger.info("SoulBrain初始化: tenant=%s, agent=%s, repo=%s", self.tenant_id, self.agent_id, self.repo_root)

    async def think(self, user_input: str, task_ctx: TaskContext) -> Decision:
        """思考主入口：用户输入 → 执行决策"""
        await self._ensure_init()

        task_ctx.user_input = user_input
        if not task_ctx.task_id:
            task_ctx.task_id = str(uuid.uuid4())[:8]

        # 1. 意图理解
        intent = await self._understand_intent(user_input, task_ctx)
        task_ctx.intent = intent

        # 2. 风险评估
        risk = await self._assess_risk(intent, task_ctx)
        task_ctx.risk = risk

        # 3. 策略决策
        decision = await self._decide(intent, risk, task_ctx)
        task_ctx.decision = decision

        logger.info("思考完成: goal=%s, files=%s, risk=%s, execute=%s",
                     intent.goal, intent.target_files, risk.overall_level, decision.execute_mode)
        return decision

    async def verify(self, action: str, result: dict, task_ctx: TaskContext) -> Verification:
        """执行后反思验证"""
        await self._ensure_init()

        verification = await self._reflector.reflect(action, result, task_ctx)
        task_ctx.verification = verification

        # 学习
        await self._experience.record(action, result, verification, task_ctx)

        task_ctx.history.append({
            "action": action,
            "success": verification.success,
            "checks": [(c.name, c.passed) for c in verification.checks],
        })
        return verification

    async def _understand_intent(self, user_input: str, task_ctx: TaskContext) -> Intent:
        """意图理解"""
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
            user_prompt=user_input, target_files=target_files,
            modify_scope=modify_scope, goal=goal, change_size=change_size,
        )

    def _extract_target_files(self, text: str) -> list[str]:
        files = []
        if not self.repo_root:
            return files
        from pathlib import Path
        for m in re.finditer(r'[\w/\\.-]+\.\w+', text):
            candidate = m.group(0).strip("\"'")
            full = Path(self.repo_root) / candidate
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
        return "small"

    async def _assess_risk(self, intent: Intent, task_ctx: TaskContext):
        if self._assessor:
            return await self._assessor.assess(intent, task_ctx)
        from src.models.cognitive import RiskAssessment
        return RiskAssessment(risks=[], overall_level="low", recommendation="正常执行")

    async def _decide(self, intent: Intent, risk, task_ctx: TaskContext) -> Decision:
        edit_mode = "full" if intent.goal == "create" else "patch"

        if risk.overall_level == "critical":
            execute_mode = "deny"
            confirm_prompt = f"风险过高，拒绝执行：{risk.recommendation}"
        elif risk.overall_level == "high":
            if await self._user_memory.should_auto_execute("high"):
                execute_mode, confirm_prompt = "auto", None
            else:
                execute_mode = "confirm_required"
                confirm_prompt = f"高风险操作，需要确认：{risk.recommendation}"
        elif risk.overall_level == "medium":
            if await self._user_memory.should_auto_execute("medium"):
                execute_mode, confirm_prompt = "auto", None
            else:
                execute_mode = "confirm_required"
                confirm_prompt = f"中风险操作：{risk.recommendation}"
        else:
            execute_mode, confirm_prompt = "auto", None

        return Decision(
            intent=intent, risk=risk, edit_mode=edit_mode,
            execute_mode=execute_mode, confirm_prompt=confirm_prompt,
        )

    async def get_status(self) -> dict:
        await self._ensure_init()
        exp_stats = await self._experience.get_stats() if self._experience else {}
        user_stats = await self._user_memory.get_stats() if self._user_memory else {}
        proj_stats = self._project_memory.get_stats() if self._project_memory else {}
        return {
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "project_memory": proj_stats,
            "experience": exp_stats,
            "user_memory": user_stats,
        }
