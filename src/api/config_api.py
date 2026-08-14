from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config_manager import config_manager

router = APIRouter()


class ConfigUpdate(BaseModel):
    data: dict


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
