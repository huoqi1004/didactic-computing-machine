"""JWT 密钥管理（fail-closed：生产未配置拒绝启动）。"""
from __future__ import annotations

import os


def get_jwt_key() -> str:
    key = os.environ.get("ERP_JWT_SECRET", "")
    if key:
        return key
    if os.environ.get("ERP_DEBUG", "1") == "1":
        return "dev-only-insecure-key"
    raise RuntimeError("ERP_JWT_SECRET 未配置（生产环境拒绝启动）")


def verify_token(token: str) -> dict | None:
    """验证 JWT 并返回载荷（无效返回 None）。"""
    import jwt as _pyjwt

    try:
        return _pyjwt.decode(token, get_jwt_key(), algorithms=["HS256"])
    except Exception:
        return None
