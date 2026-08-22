"""Personality Engine — 对话人格库、语气风格调节。"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Personality:
    """A conversation personality/role."""

    personality_id: str
    name: str
    description: str = ""
    tone: str = "neutral"  # neutral, friendly, professional, humorous, empathetic, assertive
    language_style: str = "normal"  # formal, casual, normal, technical, poetic
    emoji_usage: str = "moderate"  # none, minimal, moderate, heavy
    response_length: str = "normal"  # brief, normal, detailed, verbose
    traits: list[str] = field(default_factory=list)  # e.g. ["patient", "curious", "witty"]
    system_prompt_suffix: str = ""  # Extra system prompt to append
    builtin: bool = False
    usage_count: int = 0
    created_at: float = field(default_factory=time.time)


# Built-in personalities
_BUILTIN_PERSONALITIES = [
    Personality(
        personality_id="default",
        name="默认助手",
        description="中性、专业的AI助手",
        tone="neutral",
        language_style="normal",
        emoji_usage="minimal",
        response_length="normal",
        traits=["helpful", "accurate", "concise"],
        builtin=True,
    ),
    Personality(
        personality_id="friendly",
        name="暖阳",
        description="温暖友善的伙伴，喜欢用亲切的语气",
        tone="friendly",
        language_style="casual",
        emoji_usage="moderate",
        response_length="normal",
        traits=["warm", "encouraging", "patient"],
        system_prompt_suffix="用温暖友善的语气交流，像老朋友一样。",
        builtin=True,
    ),
    Personality(
        personality_id="professional",
        name="精英",
        description="严谨专业的顾问风格",
        tone="professional",
        language_style="formal",
        emoji_usage="none",
        response_length="detailed",
        traits=["precise", "analytical", "structured"],
        system_prompt_suffix="使用专业严谨的措辞，注重逻辑和结构。",
        builtin=True,
    ),
    Personality(
        personality_id="humorous",
        name="段子手",
        description="幽默风趣，喜欢用轻松的方式交流",
        tone="humorous",
        language_style="casual",
        emoji_usage="heavy",
        response_length="normal",
        traits=["witty", "playful", "creative"],
        system_prompt_suffix="用幽默轻松的方式交流，适当加入有趣的比喻和段子。",
        builtin=True,
    ),
    Personality(
        personality_id="empathetic",
        name="知心",
        description="善于倾听和理解的陪伴者",
        tone="empathetic",
        language_style="normal",
        emoji_usage="moderate",
        response_length="normal",
        traits=["understanding", "supportive", "gentle"],
        system_prompt_suffix="以共情和理解的态度回应，关注对方的情绪。",
        builtin=True,
    ),
    Personality(
        personality_id="tutor",
        name="导师",
        description="耐心的教学者，善于用例子解释",
        tone="friendly",
        language_style="normal",
        emoji_usage="minimal",
        response_length="detailed",
        traits=["patient", "educational", "encouraging"],
        system_prompt_suffix="像耐心的导师一样，用清晰的例子和类比来解释。",
        builtin=True,
    ),
    Personality(
        personality_id="creative",
        name="灵感缪斯",
        description="充满创意和想象力",
        tone="friendly",
        language_style="poetic",
        emoji_usage="moderate",
        response_length="normal",
        traits=["imaginative", "inspiring", "artistic"],
        system_prompt_suffix="用富有创意和想象力的方式表达。",
        builtin=True,
    ),
    Personality(
        personality_id="concise",
        name="极简",
        description="简明扼要，只说重点",
        tone="assertive",
        language_style="normal",
        emoji_usage="none",
        response_length="brief",
        traits=["efficient", "direct", "focused"],
        system_prompt_suffix="回答尽量简短，只保留核心信息，不废话。",
        builtin=True,
    ),
]


class PersonalityManager:
    """Manage conversation personalities."""

    def __init__(self, data_dir: str = ""):
        self._data_dir = data_dir or os.path.expanduser("~/.opensoul/mind_profiles")
        os.makedirs(self._data_dir, exist_ok=True)
        self._builtin = {p.personality_id: p for p in _BUILTIN_PERSONALITIES}
        self._user: dict[str, Personality] = {}
        self._active: str = "default"
        self._load_user()

    def _load_user(self):
        path = os.path.join(self._data_dir, "personalities.json")
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for item in data:
                p = Personality(**item)
                p.builtin = False
                self._user[p.personality_id] = p
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
    def _save_user(self):
        path = os.path.join(self._data_dir, "personalities.json")
        data = [asdict(p) for p in self._user.values()]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def list_all(self) -> list[Personality]:
        return list(self._builtin.values()) + list(self._user.values())

    def get(self, personality_id: str) -> Personality | None:
        return self._builtin.get(personality_id) or self._user.get(personality_id)

    def get_active(self) -> Personality:
        return self.get(self._active) or self._builtin["default"]

    def set_active(self, personality_id: str) -> bool:
        if personality_id in self._builtin or personality_id in self._user:
            self._active = personality_id
            return True
        return False

    def create(self, data: dict) -> Personality:
        pid = data.get("personality_id") or f"user-{int(time.time())}"
        p = Personality(
            personality_id=pid,
            name=data.get("name", pid),
            description=data.get("description", ""),
            tone=data.get("tone", "neutral"),
            language_style=data.get("language_style", "normal"),
            emoji_usage=data.get("emoji_usage", "moderate"),
            response_length=data.get("response_length", "normal"),
            traits=data.get("traits", []),
            system_prompt_suffix=data.get("system_prompt_suffix", ""),
        )
        self._user[pid] = p
        self._save_user()
        return p

    def update(self, personality_id: str, updates: dict) -> bool:
        if personality_id not in self._user:
            return False
        p = self._user[personality_id]
        for k, v in updates.items():
            if hasattr(p, k) and k not in (
                "personality_id",
                "builtin",
                "usage_count",
                "created_at",
            ):
                setattr(p, k, v)
        self._save_user()
        return True

    def delete(self, personality_id: str) -> bool:
        if personality_id in self._builtin or personality_id not in self._user:
            return False
        del self._user[personality_id]
        if self._active == personality_id:
            self._active = "default"
        self._save_user()
        return True

    def build_system_prompt(self, personality_id: str = "", base_prompt: str = "") -> str:
        """Build a complete system prompt with personality traits."""
        p = self.get(personality_id) if personality_id else self.get_active()
        if not p:
            p = self._builtin["default"]

        parts = [base_prompt] if base_prompt else []

        # Add personality instructions
        parts.append(f"你的性格特点：{', '.join(p.traits)}")

        tone_map = {
            "neutral": "使用中性、平衡的语气",
            "friendly": "使用温暖友善的语气",
            "professional": "使用专业正式的语气",
            "humorous": "使用轻松幽默的语气",
            "empathetic": "使用关怀体贴的语气",
            "assertive": "使用坚定自信的语气",
        }
        if p.tone in tone_map:
            parts.append(tone_map[p.tone])

        length_map = {
            "brief": "回答尽量简短",
            "normal": "回答长度适中",
            "detailed": "回答要详细、有深度",
            "verbose": "回答要非常全面详尽",
        }
        if p.response_length in length_map:
            parts.append(length_map[p.response_length])

        emoji_map = {
            "none": "不要使用emoji",
            "minimal": "偶尔使用少量emoji",
            "moderate": "适当使用emoji增强表达",
            "heavy": "多使用emoji让对话更生动",
        }
        if p.emoji_usage in emoji_map:
            parts.append(emoji_map[p.emoji_usage])

        if p.system_prompt_suffix:
            parts.append(p.system_prompt_suffix)

        return "\n".join(parts)

    def stats(self) -> dict:
        return {
            "total_personalities": len(self._builtin) + len(self._user),
            "builtin_count": len(self._builtin),
            "user_count": len(self._user),
            "active": self._active,
        }
