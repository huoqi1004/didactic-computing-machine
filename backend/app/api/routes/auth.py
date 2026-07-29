"""
认证路由 (Auth Routes)
=======================

提供登录、登出、令牌刷新、会话查询等认证相关 API。

端点列表：
    POST   /api/v1/auth/login       登录获取 JWT 令牌
    POST   /api/v1/auth/logout      登出（令牌失效）
    POST   /api/v1/auth/refresh     刷新访问令牌
    GET    /api/v1/auth/me          获取当前用户信息
    GET    /api/v1/auth/sessions    获取当前用户活跃会话
    POST   /api/v1/auth/logout-all  全端登出（注销所有会话）
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from app.core.auth import jwt_manager, session_manager, PasswordHasher
from app.core.rbac import rbac_engine
from app.api.middleware.auth import get_current_user

import structlog

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


# ===================================================================
# 请求/响应模型
# ===================================================================

class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码")


class LoginResponse(BaseModel):
    """登录响应"""
    access_token: str = Field(..., description="访问令牌")
    refresh_token: str = Field(..., description="刷新令牌")
    token_type: str = Field("Bearer", description="令牌类型")
    expires_in: int = Field(..., description="访问令牌有效期（秒）")
    user: dict = Field(..., description="用户信息")


class RefreshRequest(BaseModel):
    """刷新令牌请求"""
    refresh_token: str = Field(..., description="刷新令牌")


class LogoutRequest(BaseModel):
    """登出请求（可选传递令牌）"""
    token: Optional[str] = Field(None, description="要注销的令牌")


class UserInfoResponse(BaseModel):
    """用户信息响应"""
    user_id: str
    username: str
    full_name: str
    department: str
    email: str
    role_codes: list[str]
    roles: list[dict]
    permissions: list[str]


# ===================================================================
# 路由
# ===================================================================

@router.post("/login", response_model=LoginResponse, summary="用户登录")
async def login(req: LoginRequest):
    """
    用户登录，验证用户名密码后签发 JWT 令牌。

    返回 access_token（短期，30分钟）和 refresh_token（长期，7天）。
    """
    # 1. 查找用户
    user = rbac_engine.get_user_by_username(req.username)
    if user is None:
        logger.warning("auth.login.user_not_found", username=req.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 2. 获取完整用户记录（含 password_hash）
    full_user = rbac_engine._users.get(user["user_id"])
    if full_user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 3. 检查用户状态
    if full_user.get("status") != "active":
        logger.warning("auth.login.user_inactive", username=req.username, status=full_user.get("status"))
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")

    # 4. 验证密码
    if not PasswordHasher.verify_password(req.password, full_user["password_hash"]):
        logger.warning("auth.login.password_mismatch", username=req.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 5. 获取角色编码和权限列表
    roles = rbac_engine.get_user_roles(user["user_id"])
    role_codes = [r["role_code"] for r in roles]
    permissions = sorted(rbac_engine.get_user_permissions(user["user_id"]))

    # 6. 构建 JWT 载荷
    token_data = {
        "user_id": user["user_id"],
        "username": user["username"],
        "role_codes": role_codes,
        "full_name": user["full_name"],
        "department": user["department"],
    }

    # 7. 签发令牌
    access_token = jwt_manager.create_access_token(token_data)
    refresh_token = jwt_manager.create_refresh_token(token_data)

    # 8. 创建会话
    session_manager.create_session(user["user_id"], access_token)

    # 9. 更新最后登录时间
    rbac_engine.update_user(user["user_id"], last_login=datetime.now(timezone.utc))

    logger.info("auth.login.success", user_id=user["user_id"], username=req.username)

    # 构建用户响应（排除敏感字段，添加 role_codes 和 permissions）
    user_response = {
        "user_id": user["user_id"],
        "username": user["username"],
        "full_name": user.get("full_name", ""),
        "department": user.get("department", ""),
        "email": user.get("email", ""),
        "role_codes": role_codes,
        "permissions": permissions,
    }

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_in=jwt_manager._expire_minutes * 60,
        user=user_response,
    )


@router.post("/logout", summary="用户登出")
async def logout(request: Request, body: LogoutRequest = None):
    """
    登出，将当前令牌加入黑名单。

    可以从 Authorization Header 自动提取令牌，或在 body 中显式传递。
    """
    # 优先从 body 获取 token，其次从 header 获取
    token = None
    if body and body.token:
        token = body.token
    else:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=400, detail="未提供令牌")

    success = session_manager.invalidate_session(token)
    if not success:
        # 即使会话不存在，也返回成功（幂等操作）
        logger.debug("auth.logout.session_not_found")

    logger.info("auth.logout.success")
    return {"message": "已登出", "success": True}


@router.post("/refresh", summary="刷新访问令牌")
async def refresh_token(req: RefreshRequest):
    """
    使用刷新令牌获取新的访问令牌。

    刷新令牌必须有效且类型为 ``refresh``。
    """
    payload = jwt_manager.verify_token(req.refresh_token)
    if payload is None:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")

    # 验证令牌类型
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="令牌类型错误，需要刷新令牌")

    # 构建新的访问令牌
    token_data = {
        "user_id": payload.get("user_id"),
        "username": payload.get("username"),
        "role_codes": payload.get("role_codes", []),
        "full_name": payload.get("full_name", ""),
        "department": payload.get("department", ""),
    }

    new_access_token = jwt_manager.create_access_token(token_data)

    # 创建新会话
    session_manager.create_session(payload["user_id"], new_access_token)

    logger.info("auth.refresh.success", user_id=payload.get("user_id"))

    return {
        "access_token": new_access_token,
        "token_type": "Bearer",
        "expires_in": jwt_manager._expire_minutes * 60,
    }


@router.get("/me", response_model=UserInfoResponse, summary="获取当前用户信息")
async def get_me(current_user: dict = Depends(get_current_user)):
    """获取当前登录用户的详细信息，包括角色和权限列表。"""
    user = rbac_engine.get_user(current_user["user_id"])
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    roles = rbac_engine.get_user_roles(current_user["user_id"])
    permissions = sorted(rbac_engine.get_user_permissions(current_user["user_id"]))

    return UserInfoResponse(
        user_id=user["user_id"],
        username=user["username"],
        full_name=user["full_name"],
        department=user["department"],
        email=user["email"],
        role_codes=[r["role_code"] for r in roles],
        roles=roles,
        permissions=permissions,
    )


@router.get("/sessions", summary="获取当前用户活跃会话")
async def get_sessions(current_user: dict = Depends(get_current_user)):
    """获取当前用户的所有活跃会话列表。"""
    sessions = session_manager.get_user_sessions(current_user["user_id"])
    return {
        "sessions": [
            {
                "created_at": s["created_at"],
                "expires_at": s["expires_at"],
                "active": s["active"],
                "token_preview": s["token"][:16] + "...",
            }
            for s in sessions
        ],
        "count": len(sessions),
    }


@router.post("/logout-all", summary="全端登出")
async def logout_all(current_user: dict = Depends(get_current_user)):
    """注销当前用户的所有会话（全端登出）。"""
    count = session_manager.invalidate_user_sessions(current_user["user_id"])
    logger.info("auth.logout_all.success", user_id=current_user["user_id"], sessions_invalidated=count)
    return {"message": f"已注销 {count} 个会话", "sessions_invalidated": count}
