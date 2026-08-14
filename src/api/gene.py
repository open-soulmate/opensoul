"""OpenGene API — 基因系统：模板库管理、模板实例化。"""

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


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def gene_health():
    """OpenGene health check."""
    return {
        "status": "ok",
        "component": "OpenGene",
        **engine.stats(),
    }
