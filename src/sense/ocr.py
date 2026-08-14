"""OCR engine — image/PDF → text via Tesseract."""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

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
    """Tesseract-based OCR with multi-language support."""

    # Default languages: Chinese Simplified + English
    DEFAULT_LANG = "chi_sim+eng"

    def __init__(self, lang: str | None = None, tesseract_cmd: str | None = None):
        self.lang = lang or self.DEFAULT_LANG
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def image_to_text(
        self,
        image_bytes: bytes,
        lang: str | None = None,
        preprocess: bool = True,
    ) -> OCRResult:
        """OCR a single image."""
        if not HAS_TESSERACT:
            return OCRResult(text="[OCR unavailable: pytesseract not installed]", confidence=0, language="", engine="none")

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
        """OCR all pages of a PDF."""
        if not HAS_PDF2IMAGE:
            return OCRResult(text="[PDF OCR unavailable: pdf2image not installed]", confidence=0, language="", engine="none")

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

    @staticmethod
    def _preprocess(img: Image.Image) -> Image.Image:
        """Basic preprocessing: grayscale + contrast boost."""
        if img.mode != "L":
            img = img.convert("L")
        # Simple threshold
        return img.point(lambda x: 0 if x < 128 else 255, "1")
