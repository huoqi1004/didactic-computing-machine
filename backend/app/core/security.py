"""认证与安全（JWT + 密码哈希）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Optional

import jwt

from app.core.jwt import get_jwt_key

_EXPIRE = int(os.environ.get("ERP_JWT_EXPIRE_MINUTES", "120"))


def hash_password(plain: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", plain.encode(), b"erp-new-salt", 120_000).hex()


def verify_password(plain: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(plain), hashed)


def create_token(user: dict[str, Any]) -> str:
    now = int(time.time())
    payload = {"sub": user["username"], "uid": user["id"], "role": user["role"],
               "dept": user.get("department", ""), "exp": now + _EXPIRE * 60}
    return jwt.encode(payload, get_jwt_key(), algorithm="HS256")


def verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, get_jwt_key(), algorithms=["HS256"])
    except Exception:
        return None



