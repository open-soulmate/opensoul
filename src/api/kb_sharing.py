from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.database.postgres import db_pool
from src.api.user import get_current_user

router = APIRouter()


class SharingRequestCreate(BaseModel):
    kb_id: str
    kb_name: str


class SharingRequestReview(BaseModel):
    status: str  # "approved" or "rejected"
    review_note: str = ""


@router.post("/")
async def create_sharing_request(data: SharingRequestCreate, user_id: UUID = Depends(get_current_user)):
    """用户申请将个人知识库共享到企业知识库"""
    req_id = str(uuid4())
    user_row = await db_pool.fetchrow("SELECT username FROM users WHERE id = ?", str(user_id))
    user_name = user_row["username"] if user_row else "unknown"

    await db_pool.execute(
        "INSERT INTO kb_sharing_requests (id, user_id, user_name, kb_id, kb_name) VALUES (?, ?, ?, ?, ?)",
        req_id, str(user_id), user_name, data.kb_id, data.kb_name,
    )
    row = await db_pool.fetchrow("SELECT * FROM kb_sharing_requests WHERE id = ?", req_id)
    return dict(row)


@router.get("/")
async def list_sharing_requests(status: str | None = None, user_id: str | None = None, current_user: UUID = Depends(get_current_user)):
    """列出共享申请"""
    conditions = []
    values = []
    if status:
        conditions.append("status = ?")
        values.append(status)
    if user_id:
        conditions.append("user_id = ?")
        values.append(user_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await db_pool.fetch(f"SELECT * FROM kb_sharing_requests{where} ORDER BY created_at DESC", *values)
    return [dict(r) for r in rows]


@router.get("/my")
async def my_sharing_requests(current_user: UUID = Depends(get_current_user)):
    rows = await db_pool.fetch("SELECT * FROM kb_sharing_requests WHERE user_id = ? ORDER BY created_at DESC", str(current_user))
    return [dict(r) for r in rows]


@router.post("/{request_id}/review")
async def review_sharing_request(request_id: str, data: SharingRequestReview, current_user: UUID = Depends(get_current_user)):
    """管理员审批共享申请"""
    if data.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    row = await db_pool.fetchrow("SELECT * FROM kb_sharing_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail="Already reviewed")

    await db_pool.execute(
        "UPDATE kb_sharing_requests SET status = ?, reviewer_id = ?, review_note = ?, reviewed_at = datetime('now') WHERE id = ?",
        data.status, str(current_user), data.review_note, request_id,
    )

    # 审批通过：将该用户的知识条目标记为企业共享
    if data.status == "approved":
        await db_pool.execute(
            "UPDATE knowledge SET metadata = json_set(COALESCE(metadata, '{}'), '$.shared_to_enterprise', json('true'), '$.shared_at', datetime('now'), '$.shared_by', ?) WHERE user_id = ?",
            str(row["user_id"]), str(row["user_id"]),
        )

    updated = await db_pool.fetchrow("SELECT * FROM kb_sharing_requests WHERE id = ?", request_id)
    return dict(updated)
