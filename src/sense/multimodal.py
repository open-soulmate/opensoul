"""Multimodal analyzer — image/video metadata extraction + description."""

from __future__ import annotations

import io
import json
import os
import tempfile
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
        """Extract frames from video using ffmpeg."""
        import subprocess
        import glob as _glob

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        out_dir = tempfile.mkdtemp(prefix="frames_")
        try:
            # Use ffmpeg to extract frames at given interval
            cmd = [
                "ffmpeg", "-i", tmp_path,
                "-vf", f"fps=1/{interval}",
                "-frames:v", str(max_frames),
                "-q:v", "2",
                os.path.join(out_dir, "frame_%04d.jpg"),
            ]
            subprocess.run(cmd, capture_output=True, timeout=60, check=False)

            frames = []
            for fp in sorted(_glob.glob(os.path.join(out_dir, "frame_*.jpg"))):
                with open(fp, "rb") as f:
                    frames.append(f.read())
            return frames
        finally:
            os.unlink(tmp_path)
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)

    def analyze_video(self, video_bytes: bytes) -> VideoAnalysis:
        """Extract video metadata using ffprobe."""
        import subprocess
        import json as _json

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"ffprobe failed: {result.stderr[:200]}")

            info = _json.loads(result.stdout)
            video_stream = next(
                (s for s in info.get("streams", []) if s.get("codec_type") == "video"),
                {},
            )
            fmt = info.get("format", {})

            # Parse frame rate
            fps_str = video_stream.get("r_frame_rate", "0/1")
            try:
                num, den = fps_str.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 0.0
            except (ValueError, ZeroDivisionError):
                fps = 0.0

            return VideoAnalysis(
                duration=float(fmt.get("duration", 0)),
                width=int(video_stream.get("width", 0)),
                height=int(video_stream.get("height", 0)),
                fps=round(fps, 2),
                codec=video_stream.get("codec_name", "unknown"),
                file_size=len(video_bytes),
            )
        finally:
            os.unlink(tmp_path)

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
