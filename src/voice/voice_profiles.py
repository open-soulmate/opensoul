"""Voice profiles — 语音角色配置管理。"""

import json
import os
import time
from dataclasses import dataclass, field, asdict


@dataclass
class VoiceProfile:
    """A named voice configuration."""
    profile_id: str
    name: str
    description: str = ""
    engine: str = "edge-tts"
    voice_id: str = "zh-CN-XiaoxiaoNeural"
    language: str = "zh-CN"
    rate: str = "+0%"        # e.g. "+20%", "-10%"
    pitch: str = "+0Hz"      # e.g. "+5Hz"
    volume: str = "+0%"      # e.g. "+10%"
    tags: list[str] = field(default_factory=list)
    builtin: bool = False
    usage_count: int = 0
    created_at: float = field(default_factory=time.time)


# Built-in profiles
_BUILTIN_PROFILES = [
    VoiceProfile(
        profile_id="xiaoxiao",
        name="晓晓",
        description="标准女声，温柔自然",
        voice_id="zh-CN-XiaoxiaoNeural",
        language="zh-CN",
        tags=["female", "chinese", "natural"],
        builtin=True,
    ),
    VoiceProfile(
        profile_id="yunxi",
        name="云希",
        description="标准男声，清晰干练",
        voice_id="zh-CN-YunxiNeural",
        language="zh-CN",
        tags=["male", "chinese", "clear"],
        builtin=True,
    ),
    VoiceProfile(
        profile_id="yunyang",
        name="云扬",
        description="新闻播报风格男声",
        voice_id="zh-CN-YunyangNeural",
        language="zh-CN",
        tags=["male", "chinese", "news"],
        builtin=True,
    ),
    VoiceProfile(
        profile_id="xiaoyi",
        name="晓伊",
        description="活泼年轻女声",
        voice_id="zh-CN-XiaoyiNeural",
        language="zh-CN",
        tags=["female", "chinese", "young"],
        builtin=True,
    ),
    VoiceProfile(
        profile_id="en-jenny",
        name="Jenny",
        description="English female, friendly",
        voice_id="en-US-JennyNeural",
        language="en-US",
        tags=["female", "english", "friendly"],
        builtin=True,
    ),
    VoiceProfile(
        profile_id="en-guy",
        name="Guy",
        description="English male, conversational",
        voice_id="en-US-GuyNeural",
        language="en-US",
        tags=["male", "english", "conversational"],
        builtin=True,
    ),
    VoiceProfile(
        profile_id="ja-nanami",
        name="七海",
        description="日本語 女性声",
        voice_id="ja-JP-NanamiNeural",
        language="ja-JP",
        tags=["female", "japanese"],
        builtin=True,
    ),
    VoiceProfile(
        profile_id="ko-sunhi",
        name="선희",
        description="한국어 여성",
        voice_id="ko-KR-SunHiNeural",
        language="ko-KR",
        tags=["female", "korean"],
        builtin=True,
    ),
]


class ProfileManager:
    """Manages voice profiles (built-in + user-defined)."""

    def __init__(self, data_dir: str = ""):
        self._data_dir = data_dir or os.path.expanduser("~/.opensoul/voice_profiles")
        os.makedirs(self._data_dir, exist_ok=True)
        self._builtin = {p.profile_id: p for p in _BUILTIN_PROFILES}
        self._user: dict[str, VoiceProfile] = {}
        self._load_user_profiles()

    def _load_user_profiles(self):
        path = os.path.join(self._data_dir, "profiles.json")
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for item in data:
                p = VoiceProfile(**item)
                p.builtin = False
                self._user[p.profile_id] = p
        except Exception:
            pass

    def _save_user_profiles(self):
        path = os.path.join(self._data_dir, "profiles.json")
        data = [asdict(p) for p in self._user.values()]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def list_all(self) -> list[VoiceProfile]:
        return list(self._builtin.values()) + list(self._user.values())

    def get(self, profile_id: str) -> VoiceProfile | None:
        return self._builtin.get(profile_id) or self._user.get(profile_id)

    def create(self, data: dict) -> VoiceProfile:
        pid = data.get("profile_id") or f"user-{int(time.time())}"
        p = VoiceProfile(
            profile_id=pid,
            name=data.get("name", pid),
            description=data.get("description", ""),
            engine=data.get("engine", "edge-tts"),
            voice_id=data.get("voice_id", "zh-CN-XiaoxiaoNeural"),
            language=data.get("language", "zh-CN"),
            rate=data.get("rate", "+0%"),
            pitch=data.get("pitch", "+0Hz"),
            volume=data.get("volume", "+0%"),
            tags=data.get("tags", []),
            builtin=False,
        )
        self._user[pid] = p
        self._save_user_profiles()
        return p

    def update(self, profile_id: str, updates: dict) -> bool:
        if profile_id not in self._user:
            return False
        p = self._user[profile_id]
        for k, v in updates.items():
            if hasattr(p, k) and k not in ("profile_id", "builtin", "usage_count", "created_at"):
                setattr(p, k, v)
        self._save_user_profiles()
        return True

    def delete(self, profile_id: str) -> bool:
        if profile_id in self._builtin or profile_id not in self._user:
            return False
        del self._user[profile_id]
        self._save_user_profiles()
        return True

    def increment_usage(self, profile_id: str):
        p = self.get(profile_id)
        if p:
            p.usage_count += 1
            if profile_id in self._user:
                self._save_user_profiles()

    def stats(self) -> dict:
        return {
            "total_profiles": len(self._builtin) + len(self._user),
            "builtin_count": len(self._builtin),
            "user_count": len(self._user),
            "by_language": self._count_by_language(),
        }

    def _count_by_language(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in list(self._builtin.values()) + list(self._user.values()):
            counts[p.language] = counts.get(p.language, 0) + 1
        return counts
