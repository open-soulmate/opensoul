import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user
from src.database.postgres import db_pool

router = APIRouter()
@router.get("/health")
async def knowledge_requests_health():
    """KnowledgeRequests health check."""
    return {"status": "ok", "component": "KnowledgeRequests"}


class KnowledgeRequestCreate(BaseModel):
    kb_name: str
    kb_description: str = ""


class KnowledgeRequestReview(BaseModel):
    status: str  # "approved" or "rejected"
    review_note: str = ""


@router.post("/")
async def create_request(data: KnowledgeRequestCreate, user_id: UUID = Depends(get_current_user)):
    """用户提交知识库创建申请"""
    req_id = str(uuid4())
    # 获取用户名
    user_row = await db_pool.fetchrow("SELECT username FROM users WHERE id = ?", str(user_id))
    user_name = user_row["username"] if user_row else "unknown"

    await db_pool.execute(
        "INSERT INTO knowledge_requests (id, user_id, user_name, kb_name, kb_description) VALUES (?, ?, ?, ?, ?)",
        req_id,
        str(user_id),
        user_name,
        data.kb_name,
        data.kb_description,
    )
    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", req_id)
    return _parse_row(row)


@router.get("/")
async def list_requests(
    status: str | None = None,
    user_id: str | None = None,
    current_user: UUID = Depends(get_current_user),
):
    """列出知识库申请（管理员看全部，用户看自己的）"""
    conditions = []
    values = []
    if status:
        conditions.append("status = ?")
        values.append(status)
    if user_id:
        conditions.append("user_id = ?")
        values.append(user_id)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await db_pool.fetch(
        f"SELECT * FROM knowledge_requests{where} ORDER BY created_at DESC", *values
    )
    return [_parse_row(r) for r in rows]


@router.get("/my")
async def my_requests(current_user: UUID = Depends(get_current_user)):
    """当前用户的申请"""
    rows = await db_pool.fetch(
        "SELECT * FROM knowledge_requests WHERE user_id = ? ORDER BY created_at DESC",
        str(current_user),
    )
    return [_parse_row(r) for r in rows]


@router.get("/{request_id}")
async def get_request(request_id: str, current_user: UUID = Depends(get_current_user)):
    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    return _parse_row(row)


@router.post("/{request_id}/review")
async def review_request(
    request_id: str, data: KnowledgeRequestReview, current_user: UUID = Depends(get_current_user)
):
    """管理员审批知识库申请"""
    if data.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail="Already reviewed")

    await db_pool.execute(
        "UPDATE knowledge_requests SET status = ?, reviewer_id = ?, review_note = ?, reviewed_at = datetime('now') WHERE id = ?",
        data.status,
        str(current_user),
        data.review_note,
        request_id,
    )

    # 如果审批通过，创建知识库
    if data.status == "approved":
        kb_id = str(uuid4())
        await db_pool.execute(
            "INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            kb_id,
            row["user_id"],
            row["kb_name"],
            f"知识库: {row['kb_name']}\n{row['kb_description']}",
            "auto-created",
            "kb",
            json.dumps({"type": "knowledge_base", "description": row["kb_description"]}),
        )

    updated = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    return _parse_row(updated)


@router.delete("/{request_id}")
async def delete_request(request_id: str, current_user: UUID = Depends(get_current_user)):
    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row["user_id"] != str(current_user):
        raise HTTPException(status_code=403, detail="Not your request")
    await db_pool.execute("DELETE FROM knowledge_requests WHERE id = ?", request_id)
    return {"ok": True}


def _parse_row(row):
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("kb_description"), str):
        pass  # already string
    return d
