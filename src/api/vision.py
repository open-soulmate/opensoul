"""OpenVision API — 视觉成像中枢：图表、思维导图生成。"""

import time
import base64
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.vision.chart_generator import ChartGenerator
from src.vision.mindmap import MindMapGenerator

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
charts = ChartGenerator()
mindmaps = MindMapGenerator()


# ── Request Schemas ────────────────────────────────────────

class BarChartRequest(BaseModel):
    labels: list[str]
    values: list[float]
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    color: str = "#e11d48"
    save_output: bool = False
    output_name: str = ""


class LineChartRequest(BaseModel):
    x: list  # list of x values (strings or numbers)
    series: dict[str, list[float]]  # {"series_name": [values]}
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    save_output: bool = False
    output_name: str = ""


class PieChartRequest(BaseModel):
    labels: list[str]
    values: list[float]
    title: str = ""
    save_output: bool = False
    output_name: str = ""


class ScatterRequest(BaseModel):
    x: list[float]
    y: list[float]
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    save_output: bool = False
    output_name: str = ""


class MindMapRequest(BaseModel):
    root: dict  # {"label": "...", "children": [...]}
    title: str = "Mind Map"
    layout: str = "radial"
    save_output: bool = False
    output_name: str = ""


# ── Chart Endpoints ────────────────────────────────────────

@router.post("/chart/bar")
async def create_bar_chart(req: BarChartRequest):
    """Generate a bar chart."""
    if not req.labels or not req.values:
        raise HTTPException(400, "Labels and values required")
    if len(req.labels) != len(req.values):
        raise HTTPException(400, "Labels and values must have same length")

    result = charts.bar_chart(
        labels=req.labels, values=req.values, title=req.title,
        xlabel=req.xlabel, ylabel=req.ylabel, color=req.color,
    )

    output_file = ""
    if req.save_output:
        fname = req.output_name or f"bar-{int(time.time())}"
        output_file = charts.save_output(result.image_data, fname, result.format)

    return StreamingResponse(
        iter([result.image_data]),
        media_type=f"image/{result.format}" if result.format != "txt" else "text/plain",
        headers={
            "X-Chart-Engine": result.engine,
            "X-Chart-Type": result.chart_type,
            "X-Chart-Elapsed-Ms": str(int(result.elapsed_seconds * 1000)),
            "X-Output-File": output_file,
        },
    )


@router.post("/chart/line")
async def create_line_chart(req: LineChartRequest):
    """Generate a line chart."""
    if not req.x or not req.series:
        raise HTTPException(400, "x values and at least one series required")

    result = charts.line_chart(
        x=req.x, series=req.series, title=req.title,
        xlabel=req.xlabel, ylabel=req.ylabel,
    )

    output_file = ""
    if req.save_output:
        fname = req.output_name or f"line-{int(time.time())}"
        output_file = charts.save_output(result.image_data, fname, result.format)

    return StreamingResponse(
        iter([result.image_data]),
        media_type=f"image/{result.format}" if result.format != "txt" else "text/plain",
        headers={
            "X-Chart-Engine": result.engine,
            "X-Chart-Type": result.chart_type,
            "X-Output-File": output_file,
        },
    )


@router.post("/chart/pie")
async def create_pie_chart(req: PieChartRequest):
    """Generate a pie chart."""
    if not req.labels or not req.values:
        raise HTTPException(400, "Labels and values required")

    result = charts.pie_chart(labels=req.labels, values=req.values, title=req.title)

    output_file = ""
    if req.save_output:
        fname = req.output_name or f"pie-{int(time.time())}"
        output_file = charts.save_output(result.image_data, fname, result.format)

    return StreamingResponse(
        iter([result.image_data]),
        media_type=f"image/{result.format}" if result.format != "txt" else "text/plain",
        headers={"X-Chart-Engine": result.engine, "X-Output-File": output_file},
    )


@router.post("/chart/scatter")
async def create_scatter_plot(req: ScatterRequest):
    """Generate a scatter plot."""
    if not req.x or not req.y:
        raise HTTPException(400, "x and y values required")

    result = charts.scatter_plot(
        x=req.x, y=req.y, title=req.title,
        xlabel=req.xlabel, ylabel=req.ylabel,
    )

    output_file = ""
    if req.save_output:
        fname = req.output_name or f"scatter-{int(time.time())}"
        output_file = charts.save_output(result.image_data, fname, result.format)

    return StreamingResponse(
        iter([result.image_data]),
        media_type=f"image/{result.format}" if result.format != "txt" else "text/plain",
        headers={"X-Chart-Engine": result.engine, "X-Output-File": output_file},
    )


# ── Chart JSON Endpoints (return metadata only) ───────────

@router.post("/chart/bar/json")
async def bar_chart_json(req: BarChartRequest):
    """Generate bar chart, return metadata only."""
    result = charts.bar_chart(
        labels=req.labels, values=req.values, title=req.title,
        xlabel=req.xlabel, ylabel=req.ylabel, color=req.color,
    )
    output_file = ""
    if req.save_output:
        fname = req.output_name or f"bar-{int(time.time())}"
        output_file = charts.save_output(result.image_data, fname, result.format)
    return {
        "format": result.format, "engine": result.engine,
        "chart_type": result.chart_type, "size_bytes": result.size_bytes,
        "elapsed_ms": int(result.elapsed_seconds * 1000),
        "output_file": output_file,
        "image_base64": base64.b64encode(result.image_data).decode() if result.format == "png" else "",
    }


# ── Mind Map Endpoints ─────────────────────────────────────

@router.post("/mindmap")
async def create_mindmap(req: MindMapRequest):
    """Generate a mind map image."""
    if not req.root or "label" not in req.root:
        raise HTTPException(400, "Root must have a 'label' field")

    result = mindmaps.generate(root=req.root, title=req.title, layout=req.layout)

    output_file = ""
    if req.save_output:
        fname = req.output_name or f"mindmap-{int(time.time())}"
        output_file = charts.save_output(result["image_data"], fname, result["format"])

    return StreamingResponse(
        iter([result["image_data"]]),
        media_type=f"image/{result['format']}" if result["format"] != "txt" else "text/plain",
        headers={
            "X-Engine": result.get("engine", ""),
            "X-Output-File": output_file,
        },
    )


@router.post("/mindmap/json")
async def mindmap_json(req: MindMapRequest):
    """Generate mind map, return metadata only."""
    result = mindmaps.generate(root=req.root, title=req.title, layout=req.layout)
    output_file = ""
    if req.save_output:
        fname = req.output_name or f"mindmap-{int(time.time())}"
        output_file = charts.save_output(result["image_data"], fname, result["format"])
    return {
        "format": result["format"],
        "engine": result.get("engine", ""),
        "size_bytes": len(result["image_data"]),
        "elapsed_seconds": result.get("elapsed_seconds", 0),
        "output_file": output_file,
        "image_base64": base64.b64encode(result["image_data"]).decode() if result["format"] == "png" else "",
    }


# ── Output Endpoints ───────────────────────────────────────

@router.get("/outputs")
async def list_outputs():
    """List saved vision output files."""
    return {"outputs": charts.list_outputs()}


@router.delete("/outputs/{filename}")
async def delete_output(filename: str):
    """Delete a saved output file."""
    import os
    path = os.path.join(charts._output_dir, filename)
    if os.path.exists(path):
        os.unlink(path)
        return {"message": "deleted"}
    raise HTTPException(404, "File not found")


# ── Stats ──────────────────────────────────────────────────

@router.get("/stats")
async def vision_stats():
    """OpenVision detailed statistics."""
    output_list = charts.list_outputs()
    return {
        "status": "ok",
        "component": "OpenVision",
        **charts.stats(),
        "saved_outputs": len(output_list),
        "output_dir": charts._output_dir,
    }


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def vision_health():
    """OpenVision health check."""
    return {
        "status": "ok",
        "component": "OpenVision",
        **charts.stats(),
    }
