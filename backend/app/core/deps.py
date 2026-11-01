"""会话依赖（JWT → 当前用户；角色强制）。"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException

from app.core.jwt import verify_token


async def auth_user(authorization: str = Header("")) -> dict[str, Any]:
    """从 Authorization: Bearer <JWT> 解析当前用户。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未认证")
    payload = verify_token(authorization[7:])
    if payload is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    return {"username": payload.get("sub", ""), "uid": payload.get("uid"),
            "role": payload.get("role", ""), "department": payload.get("dept", "")}


def require_role(*roles: str):
    """角色依赖工厂。"""
    async def _check(authorization: str = Header("")) -> dict:
        user = await auth_user(authorization)
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="角色权限不足")
        return user
    return _check
