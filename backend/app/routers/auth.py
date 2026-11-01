"""认证路由（登录/JWT/用户管理）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jwt import verify_token
from fastapi import Depends

from app.core.deps import auth_user
from app.core.rbac import ROLE_LABELS, get_permissions
from app.core.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    from app.core.database import get_session

    session = get_session()
    if session is None:
        raise HTTPException(503, "数据库不可用")
    from sqlalchemy import select

    from app.models import User

    async with session as s:
        row = (await s.execute(
            select(User).where(User.username == req.username))).scalar_one_or_none()
        if row is None or row.status != "active" or not verify_password(
                req.password, row.password_hash):
            raise HTTPException(401, "用户名或密码错误")
        token = create_token({"id": row.id, "username": row.username,
                              "role": row.role, "department": row.department})
        return {"access_token": token, "token_type": "Bearer",
                "user": {"username": row.username, "display_name": row.display_name,
                         "role": row.role, "department": row.department,
                         "role_label": ROLE_LABELS.get(row.role, row.role)}}


@router.get("/me")
async def me(user: dict = Depends(auth_user)):
    return {"username": user["username"], "role": user["role"],
            "department": user["department"],
            "permissions": get_permissions(user["role"])}
