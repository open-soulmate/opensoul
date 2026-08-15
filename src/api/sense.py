"""OpenSense API — 感官感知：OCR图像识别、ASR语音转写、多模态解析。"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.sense.ocr import OCREngine
from src.sense.asr import ASREngine
from src.sense.multimodal import MultimodalAnalyzer

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
ocr_engine = OCREngine()
asr_engine = ASREngine(model_size="base")
multimodal = MultimodalAnalyzer()


# ── Request Schemas ────────────────────────────────────────

class OCRRequest(BaseModel):
    language: str | None = None
    preprocess: bool = True


class ASRRequest(BaseModel):
    language: str | None = None
    format: str = "wav"


# ── OCR Endpoints ──────────────────────────────────────────

@router.post("/ocr/image")
async def ocr_image(
    file: UploadFile = File(...),
    language: str = Form(default=None),
    preprocess: bool = Form(default=True),
):
    """OCR an image file → extract text."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    data = await file.read()
    result = ocr_engine.image_to_text(data, lang=language, preprocess=preprocess)

    return {
        "text": result.text,
        "confidence": result.confidence,
        "language": result.language,
        "engine": result.engine,
        "pages": result.pages,
    }


@router.post("/ocr/pdf")
async def ocr_pdf(
    file: UploadFile = File(...),
    language: str = Form(default=None),
    dpi: int = Form(default=300),
    max_pages: int = Form(default=50),
):
    """OCR a PDF file → extract text from all pages."""
    if file.content_type != "application/pdf":
        raise HTTPException(400, "File must be a PDF")

    data = await file.read()
    result = ocr_engine.pdf_to_text(data, lang=language, dpi=dpi, max_pages=max_pages)

    return {
        "text": result.text,
        "confidence": result.confidence,
        "language": result.language,
        "engine": result.engine,
        "pages": result.pages,
        "total_pages": len(result.pages),
    }


@router.get("/ocr/languages")
async def ocr_languages():
    """List available OCR languages."""
    langs = ocr_engine.list_languages()
    return {"languages": langs, "default": ocr_engine.lang}


# ── ASR Endpoints ──────────────────────────────────────────

@router.post("/asr/transcribe")
async def asr_transcribe(
    file: UploadFile = File(...),
    language: str = Form(default=None),
    format: str = Form(default="wav"),
):
    """Transcribe audio file → text."""
    allowed_types = [
        "audio/wav", "audio/x-wav", "audio/mp3", "audio/mpeg",
        "audio/ogg", "audio/flac", "audio/webm", "audio/mp4",
        "audio/x-m4a", "application/octet-stream",
    ]
    if file.content_type and file.content_type not in allowed_types:
        # Be lenient — still try to process
        pass

    data = await file.read()
    result = asr_engine.transcribe(data, language=language, format=format)

    return {
        "text": result.text,
        "language": result.language,
        "duration_seconds": result.duration_seconds,
        "engine": result.engine,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in result.segments
        ],
    }


@router.get("/asr/models")
async def asr_models():
    """List available ASR models."""
    return {
        "models": asr_engine.list_models(),
        "current": asr_engine.model_size,
        "backend": asr_engine.backend,
    }


@router.post("/asr/model")
async def set_asr_model(model_size: str = Query(...)):
    """Switch ASR model size."""
    valid = asr_engine.list_models()
    if model_size not in valid:
        raise HTTPException(400, f"Invalid model. Choose from: {valid}")
    asr_engine.model_size = model_size
    asr_engine._model = None  # Force reload
    return {"message": f"ASR model set to '{model_size}'", "model": model_size}


# ── Multimodal Endpoints ──────────────────────────────────

@router.post("/analyze/image")
async def analyze_image(
    file: UploadFile = File(...),
):
    """Analyze image metadata: dimensions, format, EXIF, colors."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    data = await file.read()
    result = multimodal.analyze_image(data)

    return {
        "width": result.width,
        "height": result.height,
        "format": result.format,
        "mode": result.mode,
        "file_size": result.file_size,
        "exif": result.exif,
        "dominant_colors": result.dominant_colors,
        "description": result.description,
    }


# ── Video Endpoints ─────────────────────────────────────────

@router.post("/analyze/video")
async def analyze_video(
    file: UploadFile = File(...),
):
    """Analyze video metadata: duration, resolution, fps, codec."""
    if not file.content_type or not file.content_type.startswith("video/"):
        # Accept anyway if extension looks like video
        pass

    data = await file.read()
    try:
        result = multimodal.analyze_video(data)
    except Exception as e:
        raise HTTPException(400, f"Video analysis failed: {e}")

    return {
        "duration": result.duration,
        "width": result.width,
        "height": result.height,
        "fps": result.fps,
        "codec": result.codec,
        "file_size": result.file_size,
        "thumbnail_path": result.thumbnail_path,
    }


@router.post("/video/extract-frames")
async def extract_frames(
    file: UploadFile = File(...),
    interval: float = Form(default=1.0),
    max_frames: int = Form(default=10),
):
    """Extract frames from video as JPEG images (base64 encoded)."""
    import base64

    data = await file.read()
    try:
        frames = multimodal.extract_frames(data, interval=interval, max_frames=max_frames)
    except Exception as e:
        raise HTTPException(400, f"Frame extraction failed: {e}")

    return {
        "frame_count": len(frames),
        "interval": interval,
        "frames": [
            {
                "index": i,
                "size_bytes": len(f),
                "base64": base64.b64encode(f).decode("ascii"),
            }
            for i, f in enumerate(frames)
        ],
    }


# ── Health / Status ────────────────────────────────────────

@router.get("/health")
async def sense_health():
    """OpenSense health check."""
    from src.sense.ocr import HAS_TESSERACT
    from src.sense.asr import HAS_WHISPER

    return {
        "status": "ok",
        "component": "OpenSense",
        "engines": {
            "ocr": {
                "available": HAS_TESSERACT,
                "engine": "tesseract",
                "languages": ocr_engine.list_languages()[:5] if HAS_TESSERACT else [],
            },
            "asr": {
                "available": HAS_WHISPER,
                "engine": "whisper",
                "model": asr_engine.model_size,
            },
            "multimodal": {
                "available": True,
                "engine": "pillow",
            },
        },
    }
