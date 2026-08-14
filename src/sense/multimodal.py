"""Multimodal analyzer — image/video metadata extraction + description."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS


@dataclass
class ImageAnalysis:
    width: int
    height: int
    format: str
    mode: str
    file_size: int
    exif: dict = field(default_factory=dict)
    dominant_colors: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class VideoAnalysis:
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    file_size: int
    thumbnail_path: str = ""


class MultimodalAnalyzer:
    """Extract metadata and features from images and videos."""

    def analyze_image(self, image_bytes: bytes) -> ImageAnalysis:
        """Analyze image metadata."""
        img = Image.open(io.BytesIO(image_bytes))

        exif_data = {}
        if hasattr(img, "_getexif") and img._getexif():
            for tag_id, value in img._getexif().items():
                tag = TAGS.get(tag_id, tag_id)
                try:
                    # Only include serializable values
                    json.dumps(value)
                    exif_data[str(tag)] = value
                except (TypeError, ValueError):
                    exif_data[str(tag)] = str(value)

        colors = self._extract_dominant_colors(img, n=5)

        return ImageAnalysis(
            width=img.width,
            height=img.height,
            format=img.format or "UNKNOWN",
            mode=img.mode,
            file_size=len(image_bytes),
            exif=exif_data,
            dominant_colors=colors,
            description=f"{img.width}x{img.height} {img.format} image ({img.mode})",
        )

    def extract_frames(self, video_bytes: bytes, interval: float = 1.0, max_frames: int = 10) -> list[bytes]:
        """Extract frames from video (placeholder — needs ffmpeg)."""
        # Placeholder: actual implementation would use ffmpeg via subprocess
        return []

    @staticmethod
    def _extract_dominant_colors(img: Image.Image, n: int = 5) -> list[str]:
        """Extract dominant colors using simple quantization."""
        try:
            small = img.copy()
            small.thumbnail((100, 100))
            if small.mode != "RGB":
                small = small.convert("RGB")
            quantized = small.quantize(colors=n)
            palette = quantized.getpalette()
            if not palette:
                return []
            colors = []
            for i in range(n):
                r, g, b = palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]
                colors.append(f"#{r:02x}{g:02x}{b:02x}")
            return colors
        except Exception:
            return []
