"""OpenMind API — 心智中心：情绪识别、人格调节。"""

import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.mind.emotion import EmotionAnalyzer
from src.mind.personality import PersonalityManager

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
emotion_analyzer = EmotionAnalyzer()
personality_mgr = PersonalityManager()


# ── Request Schemas ────────────────────────────────────────

class EmotionAnalyzeRequest(BaseModel):
    text: str


class PersonalityCreateRequest(BaseModel):
    personality_id: str = ""
    name: str
    description: str = ""
    tone: str = "neutral"
    language_style: str = "normal"
    emoji_usage: str = "moderate"
    response_length: str = "normal"
    traits: list[str] = []
    system_prompt_suffix: str = ""


class PersonalityUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tone: str | None = None
    language_style: str | None = None
    emoji_usage: str | None = None
    response_length: str | None = None
    traits: list[str] | None = None
    system_prompt_suffix: str | None = None


class SetActiveRequest(BaseModel):
    personality_id: str


class BuildPromptRequest(BaseModel):
    personality_id: str = ""
    base_prompt: str = ""


# ── Emotion Endpoints ──────────────────────────────────────

@router.post("/emotion/analyze")
async def analyze_emotion(req: EmotionAnalyzeRequest):
    """Analyze emotion in text."""
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")
    if len(req.text) > 5000:
        raise HTTPException(400, "Text too long (max 5000 characters)")

    result = emotion_analyzer.analyze(req.text)
    return {
        "primary_emotion": result.primary_emotion,
        "confidence": result.confidence,
        "emotions": result.emotions,
        "valence": result.valence,
        "arousal": result.arousal,
        "sentiment": result.sentiment,
        "keywords": result.keywords,
        "elapsed_ms": result.elapsed_ms,
    }


@router.get("/emotion/keywords")
async def emotion_keywords():
    """List all emotion categories and their keywords."""
    from src.mind.emotion import _EMOTION_LEXICON
    return {
        "emotions": {
            name: {
                "keyword_count": len(config["keywords"]),
                "sample_keywords": config["keywords"][:5],
                "valence": config["valence"],
                "arousal": config["arousal"],
            }
            for name, config in _EMOTION_LEXICON.items()
        }
    }


# ── Personality Endpoints ──────────────────────────────────

@router.get("/personalities")
async def list_personalities():
    """List all personalities."""
    return {
        "personalities": [
            {
                "personality_id": p.personality_id,
                "name": p.name,
                "description": p.description,
                "tone": p.tone,
                "language_style": p.language_style,
                "emoji_usage": p.emoji_usage,
                "response_length": p.response_length,
                "traits": p.traits,
                "builtin": p.builtin,
                "usage_count": p.usage_count,
            }
            for p in personality_mgr.list_all()
        ],
        "active": personality_mgr._active,
    }


@router.get("/personalities/active")
async def get_active_personality():
    """Get the currently active personality."""
    p = personality_mgr.get_active()
    return {
        "personality_id": p.personality_id,
        "name": p.name,
        "description": p.description,
        "tone": p.tone,
        "traits": p.traits,
        "system_prompt": personality_mgr.build_system_prompt(),
    }


@router.post("/personalities/active")
async def set_active_personality(req: SetActiveRequest):
    """Set the active personality."""
    if not personality_mgr.set_active(req.personality_id):
        raise HTTPException(404, f"Personality '{req.personality_id}' not found")
    p = personality_mgr.get_active()
    return {"message": "active personality set", "personality_id": p.personality_id, "name": p.name}


@router.get("/personalities/{personality_id}")
async def get_personality(personality_id: str):
    """Get a personality."""
    p = personality_mgr.get(personality_id)
    if not p:
        raise HTTPException(404, "Personality not found")
    return {
        "personality_id": p.personality_id,
        "name": p.name,
        "description": p.description,
        "tone": p.tone,
        "language_style": p.language_style,
        "emoji_usage": p.emoji_usage,
        "response_length": p.response_length,
        "traits": p.traits,
        "system_prompt_suffix": p.system_prompt_suffix,
        "builtin": p.builtin,
    }


@router.post("/personalities")
async def create_personality(req: PersonalityCreateRequest):
    """Create a new personality."""
    p = personality_mgr.create(req.model_dump())
    return {"personality_id": p.personality_id, "name": p.name}


@router.patch("/personalities/{personality_id}")
async def update_personality(personality_id: str, req: PersonalityUpdateRequest):
    """Update a personality."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not personality_mgr.update(personality_id, updates):
        raise HTTPException(404, "Personality not found or is built-in")
    return {"message": "updated"}


@router.delete("/personalities/{personality_id}")
async def delete_personality(personality_id: str):
    """Delete a user personality."""
    if not personality_mgr.delete(personality_id):
        raise HTTPException(400, "Cannot delete: not found or is built-in")
    return {"message": "deleted"}


@router.post("/personalities/build-prompt")
async def build_prompt(req: BuildPromptRequest):
    """Build a system prompt with personality traits."""
    prompt = personality_mgr.build_system_prompt(
        personality_id=req.personality_id,
        base_prompt=req.base_prompt,
    )
    return {"system_prompt": prompt}


# ── Stats ──────────────────────────────────────────────────

@router.get("/stats")
async def mind_stats():
    """OpenMind detailed statistics."""
    all_personalities = personality_mgr.list_all()
    by_tone = {}
    for p in all_personalities:
        by_tone[p.tone] = by_tone.get(p.tone, 0) + 1

    return {
        "status": "ok",
        "component": "OpenMind",
        "emotion": emotion_analyzer.stats(),
        "personality": personality_mgr.stats(),
        "by_tone": by_tone,
        "active_personality": personality_mgr._active,
    }


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def mind_health():
    """OpenMind health check."""
    return {
        "status": "ok",
        "component": "OpenMind",
        "emotion": emotion_analyzer.stats(),
        "personality": personality_mgr.stats(),
    }
