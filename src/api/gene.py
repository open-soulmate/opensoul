"""OpenGene API — 基因系统：模板库管理、模板实例化。"""

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.gene.templates import TemplateEngine

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
engine = TemplateEngine()


# ── Request Schemas ────────────────────────────────────────

class TemplateCreateRequest(BaseModel):
    template_id: str = ""
    name: str
    category: str  # "agent", "knowledge_base", "workflow", "skill"
    description: str = ""
    version: str = "1.0.0"
    author: str = "user"
    tags: list[str] = []
    config: dict = {}
    variables: list[dict] = []


class CloneRequest(BaseModel):
    new_id: str = ""
    new_name: str = ""
    overrides: dict = {}


class ImportRequest(BaseModel):
    templates: list[dict]
    overwrite: bool = False


class InstantiateRequest(BaseModel):
    variables: dict = {}


# ── Template Endpoints ─────────────────────────────────────

@router.get("/templates")
async def list_templates(
    category: str = Query(default=None),
    tag: str = Query(default=None),
):
    """List available templates."""
    return {"templates": engine.list_templates(category=category, tag=tag)}


@router.get("/templates/search/{query}")
async def search_templates(query: str):
    """Search templates by name, description, or tags."""
    return {"templates": engine.search(query), "query": query}


@router.get("/categories")
async def list_categories():
    """List all template categories with counts."""
    return {"categories": engine.categories()}


@router.get("/tags")
async def list_tags():
    """List all template tags with usage counts."""
    return {"tags": engine.tags()}


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get template details."""
    t = engine.get_template(template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return {
        "template_id": t.template_id,
        "name": t.name,
        "category": t.category,
        "description": t.description,
        "version": t.version,
        "author": t.author,
        "tags": t.tags,
        "config": t.config,
        "variables": t.variables,
        "usage_count": t.usage_count,
        "builtin": t.builtin,
    }


@router.post("/templates")
async def create_template(req: TemplateCreateRequest):
    """Create a new template."""
    data = req.model_dump()
    if not data.get("template_id"):
        data.pop("template_id", None)
    template = engine.create_template(data)
    return {
        "template_id": template.template_id,
        "name": template.name,
        "category": template.category,
    }


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    """Delete a user template (built-in templates cannot be deleted)."""
    if not engine.delete_template(template_id):
        raise HTTPException(400, "Cannot delete: template not found or is built-in")
    return {"message": f"Template '{template_id}' deleted"}


@router.post("/templates/{template_id}/instantiate")
async def instantiate_template(template_id: str, req: InstantiateRequest):
    """Create an instance from a template with variable substitution."""
    result = engine.instantiate(template_id, req.variables)
    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result


# ── Export / Import / Clone ────────────────────────────────

@router.get("/templates/{template_id}/export")
async def export_template(template_id: str):
    """Export a single template as JSON."""
    data = engine.export_template(template_id)
    if not data:
        raise HTTPException(404, "Template not found")
    return {"template": data, "format": "opensoul-gene-v1"}


@router.get("/export")
async def export_all_templates(
    category: str = Query(default=None),
    include_builtin: bool = Query(default=True),
):
    """Export all templates as a JSON bundle."""
    templates = engine.export_all(category=category, include_builtin=include_builtin)
    return {
        "format": "opensoul-gene-bundle-v1",
        "exported_at": time.time(),
        "count": len(templates),
        "templates": templates,
    }


@router.post("/templates/{template_id}/clone")
async def clone_template(template_id: str, req: CloneRequest):
    """Clone a template with optional overrides."""
    template, msg = engine.clone_template(
        template_id, new_id=req.new_id, new_name=req.new_name, overrides=req.overrides
    )
    if not template:
        raise HTTPException(400, msg)
    return {
        "message": msg,
        "template_id": template.template_id,
        "name": template.name,
    }


@router.post("/import")
async def import_templates(req: ImportRequest):
    """Import one or more templates from JSON."""
    results = []
    for data in req.templates:
        template, msg = engine.import_template(data, overwrite=req.overwrite)
        results.append({
            "template_id": data.get("template_id", "?"),
            "success": template is not None,
            "message": msg,
        })
    imported = sum(1 for r in results if r["success"])
    return {
        "imported": imported,
        "total": len(results),
        "results": results,
    }


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def gene_health():
    """OpenGene health check."""
    return {
        "status": "ok",
        "component": "OpenGene",
        **engine.stats(),
    }
