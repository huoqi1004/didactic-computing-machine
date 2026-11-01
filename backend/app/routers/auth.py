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


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    department: str = ""


@router.post("/register")
async def register(req: RegisterRequest):
    """自助注册（默认 employee 角色，等待管理员激活启用）。"""
    from app.core.database import get_session

    session = get_session()
    if session is None:
        raise HTTPException(503, "数据库不可用")
    username = req.username.strip()
    if len(username) < 3 or len(req.password) < 6:
        raise HTTPException(400, "用户名至少3位，密码至少6位")
    from sqlalchemy import select

    from app.models import User

    async with session as s:
        exists = (await s.execute(
            select(User.id).where(User.username == username))).scalar_one_or_none()
        if exists is not None:
            raise HTTPException(409, "用户名已存在")
        row = User(username=username,
                   password_hash=hash_password(req.password),
                   display_name=req.display_name.strip() or username,
                   role="employee", department=req.department.strip(),
                   status="pending")
        s.add(row)
        await s.commit()
        return {"id": row.id, "status": row.status,
                "message": "注册成功，等待管理员审核激活"}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/change_password")
async def change_password(req: ChangePasswordRequest,
                          user: dict = Depends(auth_user)):
    from app.core.database import get_session

    session = get_session()
    if session is None:
        raise HTTPException(503, "数据库不可用")
    if len(req.new_password) < 6:
        raise HTTPException(400, "新密码至少6位")
    from sqlalchemy import select

    from app.models import User

    async with session as s:
        row = (await s.execute(
            select(User).where(User.username == user["username"]))).scalar_one_or_none()
        if row is None or not verify_password(req.old_password, row.password_hash):
            raise HTTPException(401, "原密码错误")
        row.password_hash = hash_password(req.new_password)
        await s.commit()
    return {"status": "ok", "message": "密码已更新"}
