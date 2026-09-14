"""SoulBrain — Agent的大脑主控，完整认知层集成。

集成模块：
- ProjectMemory: 文件索引+依赖图
- ExperienceMemory: 经验记忆(SQLite)
- UserMemory: 用户偏好(SQLite)
- RiskAssessor: 风险评估
- Reflector: 反思器
- LongTermLearning: 长期学习
- EmotionDrivenDecision: 情感驱动决策
- Metacognition: 元认知
- SelfEvolution: 自我进化
- MultiAgentCoordinator: 多Agent协调
- EmotionAnalyzer: 情绪识别
"""

import logging
import re
import time
import uuid

from src.cortex.chain_of_thought import ChainOfThought
from src.cortex.project_memory import ProjectMemory
from src.cortex.risk_assessor import RiskAssessor
from src.cortex.reflector import Reflector
from src.cortex.multi_agent_coord import MultiAgentCoordinator
from src.learn.experience import ExperienceMemory
from src.learn.long_term import LongTermLearning
from src.mind.user_memory import UserMemory
from src.mind.emotion import EmotionAnalyzer
from src.mind.emotion_decision import EmotionDrivenDecision
from src.mirror.metacognition import Metacognition
from src.heredity.self_evolution import SelfEvolution
from src.models.cognitive import Decision, Intent, TaskContext, Verification

logger = logging.getLogger(__name__)


class SoulBrain:
    """Agent的大脑 — 完整认知层。

    think()流程：
    1. 意图理解（规则+ChainOfThought）
    2. 情绪识别（用户情绪→决策权重调整）
    3. 经验参考（历史类似操作）
    4. 学习推荐（长期学习模式）
    5. 风险评估（项目记忆+历史失败）
    6. 多Agent冲突检查
    7. 策略决策（综合所有信号）
    8. 元认知记录（决策+推理过程）

    verify()流程：
    1. 反思（语法检查+意图匹配）
    2. 经验记录
    3. 元认知记录结果
    """

    def __init__(self, tenant_id: str = "default", agent_id: str = "default",
                 repo_root: str = "", db_pool=None):
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.repo_root = repo_root
        self.db = db_pool
        self._initialized = False

        # 延迟初始化的模块
        self._project_memory = None
        self._experience = None
        self._user_memory = None
        self._learning = None
        self._emotion_analyzer = None
        self._emotion_decision = None
        self._metacognition = None
        self._evolution = None
        self._coordinator = None
        self._assessor = None
        self._reflector = None

    async def _ensure_init(self):
        if self._initialized:
            return

        self._project_memory = ProjectMemory(self.repo_root) if self.repo_root else None
        self._experience = ExperienceMemory(self.db, self.tenant_id, self.agent_id)
        self._user_memory = UserMemory(self.db, self.tenant_id, "default")
        self._learning = LongTermLearning(self.db, self.tenant_id, self.agent_id)
        self._emotion_analyzer = EmotionAnalyzer()
        self._emotion_decision = EmotionDrivenDecision()
        self._metacognition = Metacognition(self.db, self.tenant_id, self.agent_id)
        self._evolution = SelfEvolution(self.db, self.tenant_id, self.agent_id)
        self._coordinator = MultiAgentCoordinator(self.db, self.tenant_id)
        self._reflector = Reflector()
        if self._project_memory:
            self._assessor = RiskAssessor(self._project_memory, self._experience)
        self._initialized = True
        logger.info("SoulBrain完整初始化: tenant=%s, agent=%s", self.tenant_id, self.agent_id)

    async def think(self, user_input: str, task_ctx: TaskContext) -> Decision:
        """完整思考流程"""
        await self._ensure_init()

        task_ctx.user_input = user_input
        if not task_ctx.task_id:
            task_ctx.task_id = str(uuid.uuid4())[:8]

        # 1. 意图理解
        intent = await self._understand_intent(user_input, task_ctx)
        task_ctx.intent = intent

        # 2. 情绪识别
        emotion_result = self._emotion_analyzer.analyze(user_input)
        user_emotion = emotion_result.primary_emotion
        logger.info("情绪识别: %s (%.1f%%)", user_emotion, emotion_result.confidence * 100)

        # 3. 经验参考
        relevant_exp = await self._experience.get_relevant_experience(intent)
        if relevant_exp:
            logger.info("找到%d条相关经验", len(relevant_exp))

        # 4. 学习推荐
        recommendations = await self._learning.get_recommendations(intent.goal)
        if recommendations:
            logger.info("学习推荐: %d条模式", len(recommendations))

        # 5. 风险评估
        risk = await self._assess_risk(intent, task_ctx)
        task_ctx.risk = risk

        # 6. 多Agent冲突检查
        if intent.target_files:
            conflicts = await self._coordinator.check_file_conflict(self.agent_id, intent.target_files)
            if conflicts:
                risk.risks.append({"title": "文件冲突", "level": "medium",
                                    "desc": f"Agent {[c['agent_id'] for c in conflicts]} 正在修改相同文件"})
                risk.overall_level = "high" if risk.overall_level == "low" else risk.overall_level

        # 7. 策略决策
        decision = await self._decide(intent, risk, task_ctx)

        # 7.1 情感调整
        agent_confidence = 0.8  # 默认信心
        if relevant_exp:
            success_rate = sum(1 for e in relevant_exp if e.outcome == "success") / len(relevant_exp)
            agent_confidence = success_rate
        decision = self._emotion_decision.adjust_decision(decision, user_emotion, agent_confidence)

        task_ctx.decision = decision

        # 8. 元认知记录
        await self._metacognition.log_decision(
            decision=f"{intent.goal}:{intent.target_files}",
            reasoning=f"风险={risk.overall_level}, 情绪={user_emotion}, 信心={agent_confidence:.1%}",
            confidence=agent_confidence,
        )

        # 注册到多Agent协调
        await self._coordinator.heartbeat(
            self.agent_id, "thinking",
            current_task=user_input[:100],
            current_files=intent.target_files,
        )

        logger.info("思考完成: goal=%s, risk=%s, emotion=%s, execute=%s",
                     intent.goal, risk.overall_level, user_emotion, decision.execute_mode)
        return decision

    async def verify(self, action: str, result: dict, task_ctx: TaskContext) -> Verification:
        """完整验证流程"""
        await self._ensure_init()

        # 1. 反思
        verification = await self._reflector.reflect(action, result, task_ctx)
        task_ctx.verification = verification

        # 2. 经验记录
        await self._experience.record(action, result, verification, task_ctx)

        # 3. 元认知记录结果
        await self._metacognition.log_outcome(
            decision=action[:200],
            outcome="success" if verification.success else "failure",
            lesson=verification.fix or "",
        )

        # 4. 注册到历史
        task_ctx.history.append({
            "action": action,
            "success": verification.success,
            "checks": [(c.name, c.passed) for c in verification.checks],
        })

        # 5. 更新多Agent状态
        await self._coordinator.heartbeat(self.agent_id, "idle")

        return verification

    async def _understand_intent(self, user_input: str, task_ctx: TaskContext) -> Intent:
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
        return {
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "project_memory": self._project_memory.get_stats() if self._project_memory else {},
            "experience": await self._experience.get_stats(),
            "user_memory": await self._user_memory.get_stats(),
            "learning": await self._learning.get_stats(),
            "metacognition": await self._metacognition.get_stats(),
            "evolution": await self._evolution.get_stats(),
            "coordinator": await self._coordinator.get_stats(),
        }

    async def evolve(self) -> list[dict]:
        """触发自我进化"""
        await self._ensure_init()
        return await self._evolution.analyze_and_evolve()

    async def learn(self) -> dict:
        """触发长期学习"""
        await self._ensure_init()
        await self._learning.extract_patterns()
        return await self._learning.get_stats()
