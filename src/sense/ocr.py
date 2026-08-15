"""OCR engine — image/PDF → text via Tesseract or LLM vision fallback."""
from __future__ import annotations

import base64
import io
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

try:
    import pytesseract

    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    from pdf2image import convert_from_bytes

    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False


@dataclass
class OCRResult:
    text: str
    confidence: float
    language: str
    pages: list[dict] = field(default_factory=list)
    engine: str = "tesseract"


class OCREngine:
    """OCR with Tesseract primary + LLM vision fallback.

    When Tesseract is unavailable, the engine falls back to a vision-capable
    LLM via the Gland gateway (OpenAI-compatible /chat/completions with image_url).
    """

    # Default languages: Chinese Simplified + English
    DEFAULT_LANG = "chi_sim+eng"

    def __init__(self, lang: str | None = None, tesseract_cmd: str | None = None):
        self.lang = lang or self.DEFAULT_LANG
        if tesseract_cmd and HAS_TESSERACT:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self._llm_gateway = None  # Set externally via set_llm_gateway()

    def set_llm_gateway(self, gateway) -> None:
        """Inject the Gland ModelRouter for LLM-based OCR fallback."""
        self._llm_gateway = gateway

    # ── Tesseract OCR ─────────────────────────────────────────────

    def image_to_text(
        self,
        image_bytes: bytes,
        lang: str | None = None,
        preprocess: bool = True,
    ) -> OCRResult:
        """OCR a single image via Tesseract."""
        if not HAS_TESSERACT:
            return OCRResult(
                text="[OCR unavailable: pytesseract not installed]",
                confidence=0, language="", engine="none",
            )

        img = Image.open(io.BytesIO(image_bytes))
        if preprocess:
            img = self._preprocess(img)

        use_lang = lang or self.lang

        # Get detailed data
        data = pytesseract.image_to_data(img, lang=use_lang, output_type=pytesseract.Output.DICT)
        confidences = [int(c) for c in data["conf"] if int(c) > 0]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0

        text = pytesseract.image_to_string(img, lang=use_lang).strip()

        return OCRResult(
            text=text,
            confidence=round(avg_conf, 2),
            language=use_lang,
            pages=[{"page": 1, "text": text, "confidence": round(avg_conf, 2)}],
            engine="tesseract",
        )

    def pdf_to_text(
        self,
        pdf_bytes: bytes,
        lang: str | None = None,
        dpi: int = 300,
        max_pages: int = 50,
    ) -> OCRResult:
        """OCR all pages of a PDF via Tesseract."""
        if not HAS_PDF2IMAGE:
            return OCRResult(
                text="[PDF OCR unavailable: pdf2image not installed]",
                confidence=0, language="", engine="none",
            )

        images = convert_from_bytes(pdf_bytes, dpi=dpi)
        use_lang = lang or self.lang
        pages: list[dict] = []
        all_text_parts: list[str] = []
        total_conf = 0.0

        for i, img in enumerate(images[:max_pages]):
            data = pytesseract.image_to_data(img, lang=use_lang, output_type=pytesseract.Output.DICT)
            confidences = [int(c) for c in data["conf"] if int(c) > 0]
            avg_conf = sum(confidences) / len(confidences) if confidences else 0
            text = pytesseract.image_to_string(img, lang=use_lang).strip()

            pages.append({"page": i + 1, "text": text, "confidence": round(avg_conf, 2)})
            all_text_parts.append(text)
            total_conf += avg_conf

        avg = total_conf / len(pages) if pages else 0
        return OCRResult(
            text="\n\n---\n\n".join(all_text_parts),
            confidence=round(avg, 2),
            language=use_lang,
            pages=pages,
            engine="tesseract",
        )

    def list_languages(self) -> list[str]:
        """List available Tesseract languages."""
        if not HAS_TESSERACT:
            return []
        return pytesseract.get_languages()

    # ── LLM Vision Fallback (async) ───────────────────────────────

    async def image_to_text_via_llm(
        self,
        image_bytes: bytes,
        language: str | None = None,
    ) -> OCRResult:
        """OCR an image using a vision-capable LLM via the Gland gateway.

        Sends the image as base64 to the LLM with a prompt to extract all text.
        Works with any OpenAI-compatible vision API (GPT-4o, MiMo-Vision, etc.).
        """
        if not self._llm_gateway:
            return OCRResult(
                text="[LLM OCR unavailable: no gateway configured]",
                confidence=0, language="", engine="none",
            )

        # Detect image format
        try:
            img = Image.open(io.BytesIO(image_bytes))
            fmt = (img.format or "PNG").lower()
        except Exception:
            fmt = "png"

        b64 = base64.b64encode(image_bytes).decode("ascii")
        mime = f"image/{fmt}" if fmt != "jpg" else "image/jpeg"

        lang_hint = ""
        if language:
            lang_hint = f" The text is primarily in {language}."
        elif self.lang:
            lang_hint = f" The text may contain Chinese and English."

        prompt = (
            f"Extract ALL text from this image exactly as it appears.{lang_hint}\n"
            "Return only the extracted text, preserving line breaks and layout as much as possible.\n"
            "Do not add any commentary or explanation."
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ]

        from src.gland.router import TaskType

        try:
            result = await self._llm_gateway.chat(
                messages=messages,
                task=TaskType.VISION,
                temperature=0.1,
                max_tokens=4096,
            )
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            text = text.strip()

            # Extract usage for confidence heuristic
            usage = result.get("usage", {})
            # We don't have a real confidence score from LLM, estimate based on response length
            confidence = min(95.0, max(50.0, len(text) * 0.5)) if text else 0.0

            return OCRResult(
                text=text,
                confidence=round(confidence, 2),
                language=language or self.lang or "auto",
                pages=[{"page": 1, "text": text, "confidence": round(confidence, 2)}],
                engine="llm_vision",
            )
        except Exception as e:
            logger.warning("LLM vision OCR failed: %s", e)
            return OCRResult(
                text=f"[LLM OCR failed: {e}]",
                confidence=0, language="", engine="llm_vision_error",
            )

    async def pdf_to_text_via_llm(
        self,
        pdf_bytes: bytes,
        language: str | None = None,
        max_pages: int = 20,
    ) -> OCRResult:
        """OCR a PDF by converting each page to an image and sending to LLM.

        Note: This is slower and more expensive than Tesseract. Limited to max_pages.
        """
        if not HAS_PDF2IMAGE:
            return OCRResult(
                text="[PDF LLM OCR unavailable: pdf2image not installed]",
                confidence=0, language="", engine="none",
            )

        images = convert_from_bytes(pdf_bytes, dpi=150)  # Lower DPI for LLM
        pages: list[dict] = []
        all_text_parts: list[str] = []
        total_conf = 0.0

        for i, img in enumerate(images[:max_pages]):
            # Convert PIL image to bytes
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page_bytes = buf.getvalue()

            result = await self.image_to_text_via_llm(page_bytes, language=language)
            pages.append({
                "page": i + 1,
                "text": result.text,
                "confidence": result.confidence,
            })
            all_text_parts.append(result.text)
            total_conf += result.confidence

        avg = total_conf / len(pages) if pages else 0
        return OCRResult(
            text="\n\n---\n\n".join(all_text_parts),
            confidence=round(avg, 2),
            language=language or self.lang or "auto",
            pages=pages,
            engine="llm_vision",
        )

    # ── Smart OCR (auto-select best engine) ───────────────────────

    async def smart_image_to_text(
        self,
        image_bytes: bytes,
        language: str | None = None,
        preprocess: bool = True,
    ) -> OCRResult:
        """Try Tesseract first, fall back to LLM vision if unavailable or fails."""
        if HAS_TESSERACT:
            try:
                result = self.image_to_text(image_bytes, lang=language, preprocess=preprocess)
                if result.text and not result.text.startswith("["):
                    return result
                logger.info("Tesseract returned empty/placeholder, trying LLM fallback")
            except Exception as e:
                logger.info("Tesseract failed (%s), trying LLM fallback", e)

        # Fallback to LLM
        return await self.image_to_text_via_llm(image_bytes, language=language)

    async def smart_pdf_to_text(
        self,
        pdf_bytes: bytes,
        language: str | None = None,
        dpi: int = 300,
        max_pages: int = 50,
    ) -> OCRResult:
        """Try Tesseract first, fall back to LLM vision if unavailable or fails."""
        if HAS_TESSERACT and HAS_PDF2IMAGE:
            try:
                result = self.pdf_to_text(pdf_bytes, lang=language, dpi=dpi, max_pages=max_pages)
                if result.text and not result.text.startswith("["):
                    return result
                logger.info("Tesseract PDF OCR returned empty/placeholder, trying LLM fallback")
            except Exception as e:
                logger.info("Tesseract PDF OCR failed (%s), trying LLM fallback", e)

        # Fallback to LLM
        return await self.pdf_to_text_via_llm(pdf_bytes, language=language, max_pages=min(max_pages, 20))

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _preprocess(img: Image.Image) -> Image.Image:
        """Basic preprocessing: grayscale + contrast boost."""
        if img.mode != "L":
            img = img.convert("L")
        # Simple threshold
        return img.point(lambda x: 0 if x < 128 else 255, "1")
