"""Emotion Analyzer — 文本情绪识别引擎。"""

import re
import time
from dataclasses import dataclass, field


@dataclass
class EmotionResult:
    """Result of emotion analysis."""
    primary_emotion: str        # e.g. "joy", "sadness"
    confidence: float           # 0.0 ~ 1.0
    emotions: dict[str, float]  # {"joy": 0.8, "anger": 0.1, ...}
    valence: float              # -1.0 (negative) to 1.0 (positive)
    arousal: float              # 0.0 (calm) to 1.0 (excited)
    sentiment: str              # "positive", "negative", "neutral"
    keywords: list[str]         # trigger keywords found
    elapsed_ms: int = 0


# Emotion lexicons (simplified but effective)
_EMOTION_LEXICON = {
    "joy": {
        "keywords": [
            "开心", "高兴", "快乐", "幸福", "喜欢", "太棒了", "好极了", "赞", "awesome",
            "happy", "joy", "great", "wonderful", "love", "excellent", "amazing", "fantastic",
            "amazing", "beautiful", "perfect", "brilliant", "excited", "pleased", "delighted",
            "庆祝", "成功", "完成", "棒", "不错", "满意", "笑", "感谢", "谢谢",
        ],
        "weight": 1.0,
        "valence": 0.8,
        "arousal": 0.6,
    },
    "sadness": {
        "keywords": [
            "伤心", "难过", "悲伤", "失望", "遗憾", "可惜", "心痛", "哭", "孤独",
            "sad", "sorry", "disappointed", "unfortunate", "miss", "lonely", "depressed",
            "crying", "heartbroken", "grief", "loss", "sorrow", "melancholy",
            "失败", "失去", "分离", "告别", "无奈", "后悔",
        ],
        "weight": 0.9,
        "valence": -0.7,
        "arousal": 0.3,
    },
    "anger": {
        "keywords": [
            "生气", "愤怒", "讨厌", "烦", "火大", "混蛋", "可恶", "受够了",
            "angry", "hate", "furious", "annoying", "frustrated", "stupid", "damn",
            "terrible", "worst", "awful", "disgusting", "ridiculous", "absurd",
            "垃圾", "废物", "滚", "闭嘴", "气死", "恼火",
        ],
        "weight": 1.0,
        "valence": -0.8,
        "arousal": 0.9,
    },
    "fear": {
        "keywords": [
            "害怕", "恐惧", "担心", "焦虑", "紧张", "不安", "恐慌", "吓",
            "afraid", "fear", "worried", "anxious", "nervous", "scared", "panic",
            "terrified", "horror", "dread", "uneasy", "alarming", "threatening",
            "危险", "威胁", "可怕", "吓人", "崩溃",
        ],
        "weight": 0.9,
        "valence": -0.6,
        "arousal": 0.7,
    },
    "surprise": {
        "keywords": [
            "惊讶", "意外", "没想到", "震惊", "吃惊", "不敢相信", "天啊", "哇",
            "surprised", "shocked", "unexpected", "unbelievable", "incredible", "wow",
            "omg", "amazing", "astonishing", "remarkable", "stunning",
            "居然", "竟然", "出乎意料", "万万没想到",
        ],
        "weight": 0.8,
        "valence": 0.2,
        "arousal": 0.8,
    },
    "trust": {
        "keywords": [
            "信任", "相信", "可靠", "放心", "安心", "踏实", "认可", "赞同",
            "trust", "believe", "reliable", "confident", "sure", "certain", "faith",
            "dependable", "honest", "loyal", "support", "agree", "accept",
            "没问题", "可以的", "好的", "同意", "支持",
        ],
        "weight": 0.7,
        "valence": 0.5,
        "arousal": 0.3,
    },
    "anticipation": {
        "keywords": [
            "期待", "盼望", "希望", "渴望", "展望", "即将", "马上",
            "expect", "hope", "anticipate", "looking forward", "eager", "soon",
            "要", "会", "准备", "计划", "打算", "接下来",
        ],
        "weight": 0.6,
        "valence": 0.3,
        "arousal": 0.5,
    },
    "confusion": {
        "keywords": [
            "困惑", "不懂", "不明白", "啥意思", "什么意思", "不理解", "搞不懂",
            "confused", "don't understand", "unclear", "uncertain", "what", "why",
            "how", "huh", "puzzled", "perplexed", "bewildered",
            "？", "？？", "???",
        ],
        "weight": 0.7,
        "valence": -0.2,
        "arousal": 0.4,
    },
}

# Sentiment keywords for quick polarity detection
_POSITIVE_SIGNALS = {"好", "棒", "赞", "优秀", "完美", "喜欢", "感谢", "开心",
                     "good", "great", "nice", "perfect", "love", "thanks", "happy", "well"}
_NEGATIVE_SIGNALS = {"差", "坏", "糟", "烂", "讨厌", "失望", "失败",
                     "bad", "poor", "terrible", "fail", "wrong", "error", "bug", "issue"}


class EmotionAnalyzer:
    """Rule-based + keyword emotion analyzer for text."""

    def __init__(self):
        self._total_analyzed = 0
        self._emotion_counts: dict[str, int] = {}

    def analyze(self, text: str) -> EmotionResult:
        """Analyze emotion in text."""
        start = time.time()
        text_lower = text.lower()
        words = set(re.findall(r'[\w\u4e00-\u9fff]+', text_lower))

        # Score each emotion
        scores: dict[str, float] = {}
        found_keywords: dict[str, list[str]] = {}

        for emotion, config in _EMOTION_LEXICON.items():
            score = 0.0
            hits = []
            for kw in config["keywords"]:
                if kw.lower() in text_lower:
                    score += config["weight"]
                    hits.append(kw)
            if score > 0:
                scores[emotion] = min(score / 3.0, 1.0)  # Normalize
                found_keywords[emotion] = hits

        # Normalize scores to sum to 1
        total = sum(scores.values())
        if total > 0:
            scores = {k: v / total for k, v in scores.items()}

        # Find primary emotion
        if scores:
            primary = max(scores, key=scores.get)  # type: ignore
            confidence = scores[primary]
        else:
            primary = "neutral"
            confidence = 1.0
            scores["neutral"] = 1.0

        # Calculate valence and arousal
        valence = 0.0
        arousal = 0.0
        for emotion, score in scores.items():
            if emotion in _EMOTION_LEXICON:
                valence += _EMOTION_LEXICON[emotion]["valence"] * score
                arousal += _EMOTION_LEXICON[emotion]["arousal"] * score

        # Sentiment
        pos_count = sum(1 for w in words if w in _POSITIVE_SIGNALS)
        neg_count = sum(1 for w in words if w in _NEGATIVE_SIGNALS)
        if pos_count > neg_count:
            sentiment = "positive"
        elif neg_count > pos_count:
            sentiment = "negative"
        else:
            sentiment = "positive" if valence > 0.1 else "negative" if valence < -0.1 else "neutral"

        # All found keywords
        all_keywords = []
        for kw_list in found_keywords.values():
            all_keywords.extend(kw_list)

        self._total_analyzed += 1
        self._emotion_counts[primary] = self._emotion_counts.get(primary, 0) + 1

        return EmotionResult(
            primary_emotion=primary,
            confidence=round(confidence, 3),
            emotions={k: round(v, 3) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
            valence=round(valence, 3),
            arousal=round(arousal, 3),
            sentiment=sentiment,
            keywords=all_keywords[:10],
            elapsed_ms=int((time.time() - start) * 1000),
        )

    def stats(self) -> dict:
        return {
            "total_analyzed": self._total_analyzed,
            "emotion_distribution": self._emotion_counts,
        }
