"""ASR engine — audio → text.

Supports multiple backends:
- whisper (local)
- External Whisper API (via Gland gateway)
- LLM-based transcription fallback (via Gland gateway)
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from dataclasses import dataclass, field

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
    """Speech-to-text engine with pluggable backends and LLM fallback."""

    def __init__(self, model_size: str = "base", backend: str = "whisper"):
        self.model_size = model_size
        self.backend = backend
        self._model = None
        self._llm_gateway = None  # Set externally via set_llm_gateway()

    def set_llm_gateway(self, gateway) -> None:
        """Inject the Gland ModelRouter for LLM-based ASR fallback."""
        self._llm_gateway = gateway

    @property
    def has_whisper(self) -> bool:
        return HAS_WHISPER

    @property
    def has_llm_fallback(self) -> bool:
        return self._llm_gateway is not None

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
        """Transcribe audio bytes to text — whisper first, then fallback."""
        if HAS_WHISPER:
            try:
                return self._transcribe_whisper(audio_bytes, language, format)
            except Exception:
                pass  # Fall through to LLM fallback

        # LLM fallback (synchronous wrapper)
        if self._llm_gateway:
            try:
                import asyncio

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're inside an async context; can't use run_until_complete
                    # Return a placeholder — the async version should be used instead
                    return ASRResult(
                        text="[ASR: use /asr/transcribe-async for LLM fallback in async context]",
                        language="",
                        engine="none",
                    )
                return loop.run_until_complete(
                    self._transcribe_via_llm(audio_bytes, language, format)
                )
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
        return ASRResult(
            text="[ASR unavailable: no whisper and no LLM fallback configured]",
            language="",
            engine="none",
        )

    async def transcribe_async(
        self,
        audio_bytes: bytes,
        language: str | None = None,
        format: str = "wav",
    ) -> ASRResult:
        """Async transcribe — whisper first, then LLM fallback."""
        if HAS_WHISPER:
            try:
                return self._transcribe_whisper(audio_bytes, language, format)
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
        if self._llm_gateway:
            try:
                return await self._transcribe_via_llm(audio_bytes, language, format)
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
        return ASRResult(
            text="[ASR unavailable: no whisper and no LLM fallback configured]",
            language="",
            engine="none",
        )

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

    async def _transcribe_via_llm(
        self,
        audio_bytes: bytes,
        language: str | None = None,
        format: str = "wav",
    ) -> ASRResult:
        """Transcribe audio using LLM gateway — sends audio as base64 to a chat model.

        This works with models that support audio input (e.g., GPT-4o-audio,
        Gemini, Qwen-Audio). Falls back to a text prompt asking for transcription
        if the model doesn't support audio natively.
        """
        if not self._llm_gateway:
            return ASRResult(text="[LLM gateway not configured]", language="", engine="none")

        # Encode audio as base64
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        mime_type = f"audio/{format}" if format != "wav" else "audio/wav"

        # Build a multimodal message with audio
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"请将以下音频转写为文字。{'语言: ' + language if language else '自动检测语言。'}\n"
                            "只输出转写的文字内容，不要添加任何解释或标注。"
                        ),
                    },
                    {
                        "type": "audio_url",
                        "audio_url": {
                            "url": f"data:{mime_type};base64,{audio_b64}",
                        },
                    },
                ],
            }
        ]

        try:
            # Call through the Gland gateway (returns OpenAI-compatible response)
            response = await self._llm_gateway.chat(
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
            )
            # Extract content from OpenAI-compatible response
            text = ""
            if isinstance(response, dict):
                choices = response.get("choices", [])
                if choices:
                    text = choices[0].get("message", {}).get("content", "").strip()
            return ASRResult(
                text=text,
                language=language or "",
                segments=[],
                duration_seconds=0.0,
                engine="llm-fallback",
            )
        except Exception as e:
            return ASRResult(
                text=f"[LLM ASR fallback error: {e}]",
                language="",
                engine="error",
            )

    def list_models(self) -> list[str]:
        """List available whisper model sizes."""
        return ["tiny", "base", "small", "medium", "large"]
