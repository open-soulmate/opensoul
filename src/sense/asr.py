"""ASR engine — audio → text.

Supports multiple backends:
- whisper (local)
- OpenAI Whisper API (remote)
- Placeholder for future providers
"""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import whisper

    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False


@dataclass
class ASRSegment:
    start: float
    end: float
    text: str


@dataclass
class ASRResult:
    text: str
    language: str
    segments: list[ASRSegment] = field(default_factory=list)
    duration_seconds: float = 0.0
    engine: str = "whisper"


class ASREngine:
    """Speech-to-text engine with pluggable backends."""

    def __init__(self, model_size: str = "base", backend: str = "whisper"):
        self.model_size = model_size
        self.backend = backend
        self._model = None

    def _get_model(self):
        if self._model is None:
            if not HAS_WHISPER:
                raise RuntimeError("whisper package not installed")
            self._model = whisper.load_model(self.model_size)
        return self._model

    def transcribe(
        self,
        audio_bytes: bytes,
        language: str | None = None,
        format: str = "wav",
    ) -> ASRResult:
        """Transcribe audio bytes to text."""
        if self.backend == "whisper":
            return self._transcribe_whisper(audio_bytes, language, format)
        else:
            return ASRResult(text=f"[ASR backend '{self.backend}' not implemented]", language="", engine="none")

    def _transcribe_whisper(
        self,
        audio_bytes: bytes,
        language: str | None = None,
        format: str = "wav",
    ) -> ASRResult:
        if not HAS_WHISPER:
            return ASRResult(text="[whisper not installed]", language="", engine="none")

        model = self._get_model()

        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            opts = {}
            if language:
                opts["language"] = language

            result = model.transcribe(tmp_path, **opts)

            segments = [
                ASRSegment(start=seg["start"], end=seg["end"], text=seg["text"].strip())
                for seg in result.get("segments", [])
            ]

            duration = segments[-1].end if segments else 0.0

            return ASRResult(
                text=result["text"].strip(),
                language=result.get("language", ""),
                segments=segments,
                duration_seconds=round(duration, 2),
                engine="whisper",
            )
        finally:
            os.unlink(tmp_path)

    def list_models(self) -> list[str]:
        """List available whisper model sizes."""
        return ["tiny", "base", "small", "medium", "large"]
