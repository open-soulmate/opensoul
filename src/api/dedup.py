from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.api.user import get_current_user
from src.services.deduplication import deduplicate_knowledge, get_duplicate_report

router = APIRouter()
@router.get("/health")
async def dedup_health():
    """Dedup health check."""
    return {"status": "ok", "component": "Dedup"}


@router.post("/deduplicate")
async def run_deduplication(
    user_id: str | None = Query(None, description="指定用户ID，留空则全库去重"),
    current_user: UUID = Depends(get_current_user),
):
    """执行知识库去重"""
    result = await deduplicate_knowledge(user_id)
    return result


@router.get("/duplicates")
async def list_duplicates(
    user_id: str | None = Query(None, description="指定用户ID"),
    current_user: UUID = Depends(get_current_user),
):
    """查看重复报告（不删除）"""
    report = await get_duplicate_report(user_id)
    return {"total_pairs": len(report), "duplicates": report}
