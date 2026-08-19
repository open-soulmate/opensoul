from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user
from src.services.permission import (
    add_policy,
    add_role,
    check_permission,
    get_all_policies,
    get_all_roles,
    get_user_roles,
    remove_policy,
    remove_role,
)

router = APIRouter()
@router.get("/health")
async def permission_health():
    """Permission health check."""
    return {"status": "ok", "component": "Permission"}


class RoleAssign(BaseModel):
    username: str
    role: str


class PolicyAdd(BaseModel):
    sub: str
    obj: str
    act: str


@router.get("/check")
async def check(obj: str, act: str, user_id: UUID = Depends(get_current_user)):
    """检查当前用户权限"""
    from src.services.auth import get_user_by_id

    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    allowed = check_permission(user["username"], obj, act)
    return {"username": user["username"], "obj": obj, "act": act, "allowed": allowed}


@router.post("/role")
async def assign_role(data: RoleAssign, user_id: UUID = Depends(get_current_user)):
    """分配角色（仅admin）"""
    from src.services.auth import get_user_by_id

    user = await get_user_by_id(user_id)
    if not user or not check_permission(user["username"], "user", "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    add_role(data.username, data.role)
    return {"ok": True, "username": data.username, "role": data.role}


@router.delete("/role")
async def revoke_role(data: RoleAssign, user_id: UUID = Depends(get_current_user)):
    """撤销角色（仅admin）"""
    from src.services.auth import get_user_by_id

    user = await get_user_by_id(user_id)
    if not user or not check_permission(user["username"], "user", "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    remove_role(data.username, data.role)
    return {"ok": True}


@router.get("/roles/{username}")
async def list_roles(username: str, user_id: UUID = Depends(get_current_user)):
    """查看用户角色"""
    roles = get_user_roles(username)
    return {"username": username, "roles": roles}


@router.get("/policies")
async def list_policies(user_id: UUID = Depends(get_current_user)):
    """查看所有策略（仅admin）"""
    from src.services.auth import get_user_by_id

    user = await get_user_by_id(user_id)
    if not user or not check_permission(user["username"], "user", "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    policies = get_all_policies()
    roles = get_all_roles()
    return {"policies": policies, "roles": roles}


@router.post("/policy")
async def create_policy(data: PolicyAdd, user_id: UUID = Depends(get_current_user)):
    """添加策略（仅admin）"""
    from src.services.auth import get_user_by_id

    user = await get_user_by_id(user_id)
    if not user or not check_permission(user["username"], "user", "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    add_policy(data.sub, data.obj, data.act)
    return {"ok": True}


@router.delete("/policy")
async def delete_policy(data: PolicyAdd, user_id: UUID = Depends(get_current_user)):
    """删除策略（仅admin）"""
    from src.services.auth import get_user_by_id

    user = await get_user_by_id(user_id)
    if not user or not check_permission(user["username"], "user", "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    remove_policy(data.sub, data.obj, data.act)
    return {"ok": True}
