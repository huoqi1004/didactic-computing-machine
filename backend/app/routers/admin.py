"""系统管理路由（用户增删改查/日志/参数）。"""
from __future__ import annotations

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.security import hash_password
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/admin", tags=["系统管理"])


@router.get("/users")
async def list_users(user: dict = Depends(auth_user)):
    if user["role"] not in ("sys_admin", "dept_admin"):
        raise HTTPException(403, "仅管理员可查看用户列表")
    from app.models import User

    session = get_session()
    async with session as s:
        rows = (await s.execute(select(User).order_by(User.id))).scalars().all()
    return {"items": [{"id": u.id, "username": u.username,
                       "display_name": u.display_name, "role": u.role,
                       "department": u.department, "status": u.status}
                      for u in rows]}


@router.get("/logs")
async def list_logs(user: dict = Depends(auth_user)):
    """系统日志查看（sys_admin 专属，FR-O3/运维排障）。"""
    if user["role"] != "sys_admin":
        raise HTTPException(403, "仅系统管理员可查看日志")
    from sqlalchemy import text

    from app.core.database import get_engine

    engine = get_engine()
    if engine is None:
        return {"items": []}
    from sqlalchemy import text as _t

    async with engine.connect() as conn:
        rows = (await conn.execute(_t(
            "SELECT entry_id, actor, action, result, timestamp "
            "FROM audit_chain ORDER BY seq DESC LIMIT 100"))).mappings().all()
    return {"items": [dict(r) for r in rows]}


class UserStatusUpdate(BaseModel):
    status: str  # active / pending / disabled


@router.patch("/users/{user_id}/status")
async def update_user_status(user_id: int, req: UserStatusUpdate,
                             user: dict = Depends(auth_user)):
    """审核激活/停用用户（sys_admin 全局，dept_admin 仅限本部门）。"""
    if req.status not in ("active", "pending", "disabled"):
        raise HTTPException(400, "非法状态")
    from app.models import User

    session = get_session()
    async with session as s:
        row = (await s.execute(
            select(User).where(User.id == user_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "用户不存在")
        if (user["role"] == "dept_admin"
                and row.department != user["department"]):
            raise HTTPException(403, "部门管理员仅可管理本部门用户")
        if user["role"] not in ("sys_admin", "dept_admin"):
            raise HTTPException(403, "仅管理员可变更用户状态")
        if row.role == "sys_admin" and user["role"] != "sys_admin":
            raise HTTPException(403, "不可变更系统管理员状态")
        row.status = req.status
        await s.commit()
    return {"id": row.id, "username": row.username, "status": row.status}
