from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config_manager import ALL_ORGANS, config_manager

router = APIRouter()
@router.get("/health")
@router.get("/config/health")
async def config_api_health():
    """ConfigAPI health check."""
    return {"status": "ok", "component": "ConfigAPI"}


class ConfigUpdate(BaseModel):
    data: dict


class OrganToggle(BaseModel):
    enabled: bool


class BulkOrganToggle(BaseModel):
    organs: dict[str, bool]


@router.get("/config")
async def get_full_config():
    return config_manager.get()


@router.get("/config/{section}")
async def get_section(section: str):
    result = config_manager.get(section=section)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Section '{section}' not found")
    return {section: result}


@router.put("/config")
async def update_config(body: ConfigUpdate):
    updated = config_manager.update(body.data)
    return updated


@router.get("/organs")
async def list_organs_config():
    """List all organs with their config-enabled status."""
    organs_config = config_manager.get(section="organs") or {}
    result = []
    for organ in ALL_ORGANS:
        cfg = organs_config.get(organ, {})
        result.append(
            {
                "key": organ,
                "enabled": cfg.get("enabled", True),
                "config": cfg,
            }
        )
    return {"organs": result, "total": len(result)}


@router.put("/organs/bulk")
async def bulk_toggle_organs(body: BulkOrganToggle):
    """Enable or disable multiple organs at once."""
    current = config_manager.get() or {}
    organs = current.get("organs", {})
    updated = []
    for key, enabled in body.organs.items():
        if key not in ALL_ORGANS:
            continue
        if key not in organs:
            organs[key] = {}
        organs[key]["enabled"] = enabled
        updated.append({"organ": key, "enabled": enabled})
    config_manager.update({"organs": organs})
    return {"updated": updated, "count": len(updated)}


@router.put("/organs/{organ_key}")
async def update_organ_config(organ_key: str, body: OrganToggle):
    """Enable or disable an organ via config."""
    if organ_key not in ALL_ORGANS:
        raise HTTPException(
            status_code=404, detail=f"Unknown organ: {organ_key}. Valid: {ALL_ORGANS}"
        )

    current = config_manager.get() or {}
    organs = current.get("organs", {})
    if organ_key not in organs:
        organs[organ_key] = {}
    organs[organ_key]["enabled"] = body.enabled
    config_manager.update({"organs": organs})

    return {
        "organ": organ_key,
        "enabled": body.enabled,
        "message": f"Organ '{organ_key}' {'enabled' if body.enabled else 'disabled'}",
    }
