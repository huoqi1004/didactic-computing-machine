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
