"""Emotion-Driven Decision — 情感驱动决策。

情绪不只是识别，还影响决策权重：
- 用户焦虑时 → 更保守，多确认
- 用户兴奋时 → 更大胆，少确认
- 用户愤怒时 → 更谨慎，避免出错
- Agent自身信心低时 → 更保守，多备份
"""

import logging
from src.models.cognitive import Decision, RiskAssessment

logger = logging.getLogger(__name__)

# 情绪→决策权重映射
EMOTION_WEIGHTS = {
    "joy":       {"risk_tolerance": 1.2, "confirm_threshold": 0.7, "backup_urge": 0.8},
    "trust":     {"risk_tolerance": 1.1, "confirm_threshold": 0.6, "backup_urge": 0.9},
    "surprise":  {"risk_tolerance": 0.9, "confirm_threshold": 0.5, "backup_urge": 1.0},
    "sadness":   {"risk_tolerance": 0.8, "confirm_threshold": 0.4, "backup_urge": 1.2},
    "anger":     {"risk_tolerance": 0.7, "confirm_threshold": 0.3, "backup_urge": 1.3},
    "fear":      {"risk_tolerance": 0.6, "confirm_threshold": 0.3, "backup_urge": 1.5},
    "neutral":   {"risk_tolerance": 1.0, "confirm_threshold": 0.5, "backup_urge": 1.0},
}


class EmotionDrivenDecision:
    """情感驱动决策：情绪影响决策权重"""

    def __init__(self):
        pass

    def adjust_decision(self, decision: Decision, user_emotion: str = "neutral",
                        agent_confidence: float = 0.8) -> Decision:
        """根据情绪调整决策"""
        emotion_weight = EMOTION_WEIGHTS.get(user_emotion, EMOTION_WEIGHTS["neutral"])

        # 风险等级调整
        risk_level = decision.risk.overall_level
        adjusted_execute = decision.execute_mode

        # 低信心 + 负面情绪 → 更保守
        if agent_confidence < 0.5 and emotion_weight["risk_tolerance"] < 0.8:
            if risk_level in ("low", "medium"):
                adjusted_execute = "confirm_required"
                decision.confirm_prompt = f"⚠️ 信心不足({agent_confidence:.0%})，需要确认：{decision.risk.recommendation}"

        # 高风险 + 负面情绪 → 必须确认
        if risk_level in ("high", "critical") and emotion_weight["risk_tolerance"] < 0.8:
            adjusted_execute = "confirm_required"

        # 高信心 + 正面情绪 + 低风险 → 可以自动执行
        if agent_confidence > 0.8 and emotion_weight["risk_tolerance"] > 1.0 and risk_level == "low":
            adjusted_execute = "auto"

        # 备份建议（负面情绪 → 更倾向备份）
        if emotion_weight["backup_urge"] > 1.0:
            decision.risk.recommendation = f"建议先备份再执行。{decision.risk.recommendation}"

        if adjusted_execute != decision.execute_mode:
            logger.info("情感调整: emotion=%s, confidence=%.1f, %s → %s",
                        user_emotion, agent_confidence, decision.execute_mode, adjusted_execute)
            decision.execute_mode = adjusted_execute

        return decision

    def get_emotion_sensitivity(self, user_emotion: str) -> dict:
        """获取情绪敏感度"""
        return EMOTION_WEIGHTS.get(user_emotion, EMOTION_WEIGHTS["neutral"])
