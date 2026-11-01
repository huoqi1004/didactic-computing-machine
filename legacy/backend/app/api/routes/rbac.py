"""
RBAC 管理路由 (RBAC Routes)
============================

提供用户、角色、权限、部门的 CRUD 管理接口。
所有接口需认证 + 对应权限。

端点列表：
    === 用户管理 ===
    GET    /api/v1/rbac/users            列出所有用户
    POST   /api/v1/rbac/users            创建用户
    GET    /api/v1/rbac/users/{id}       获取用户详情
    PUT    /api/v1/rbac/users/{id}       更新用户
    DELETE /api/v1/rbac/users/{id}       删除用户
    GET    /api/v1/rbac/users/{id}/roles  获取用户角色
    POST   /api/v1/rbac/users/{id}/roles 分配角色
    DELETE /api/v1/rbac/users/{id}/roles 收回角色
    GET    /api/v1/rbac/users/{id}/permissions 获取用户权限

    === 角色管理 ===
    GET    /api/v1/rbac/roles            列出所有角色
    GET    /api/v1/rbac/roles/{code}     获取角色详情
    GET    /api/v1/rbac/roles/{code}/permissions 获取角色权限

    === 权限管理 ===
    GET    /api/v1/rbac/permissions      列出所有权限定义

    === 部门管理 ===
    GET    /api/v1/rbac/departments      列出所有部门

    === 统计 ===
    GET    /api/v1/rbac/stats            RBAC引擎统计
"""
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.core.rbac import rbac_engine
from app.core.auth import PasswordHasher
from app.core.security.audit import audit_chain
from app.api.middleware.auth import get_current_user, require_permission

import structlog

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/rbac", tags=["RBAC权限管理"])


async def _audit_rbac_change(
    actor_id: str,
    action: str,
    target: str,
    details: dict,
    result: str = "success",
) -> None:
    """
    记录 RBAC 变更审计（D-05：RBAC 变更 100% 审计）。

    失败不抛异常，不影响主流程。
    """
    try:
        await audit_chain.log(
            actor_did=actor_id,
            action=f"rbac.{action}",
            resource=target,
            result=result,
            details=details,
            risk_level="high",
        )
    except Exception as audit_err:  # noqa: BLE001
        logger.warning("rbac.audit_failed", action=action, error=str(audit_err))


# ===================================================================
# 请求模型
# ===================================================================

class CreateUserRequest(BaseModel):
    """创建用户请求"""
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=64)
    department: str = Field(..., description="部门名称")
    email: str = Field(..., description="邮箱地址")
    phone: Optional[str] = Field(None, description="手机号")
    position: Optional[str] = Field(None, description="职位")


class UpdateUserRequest(BaseModel):
    """更新用户请求"""
    full_name: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    position: Optional[str] = None
    status: Optional[str] = Field(None, description="用户状态: active/disabled")
    password: Optional[str] = Field(None, min_length=6, description="重置密码")


class AssignRoleRequest(BaseModel):
    """分配角色请求"""
    role_code: str = Field(..., description="角色编码")


class AssignPermissionRequest(BaseModel):
    """给角色分配权限请求"""
    permission_id: str = Field(..., description="权限ID (resource:action)")


# ===================================================================
# 用户管理
# ===================================================================

@router.get("/users", summary="列出所有用户")
async def list_users(
    current_user: dict = Depends(get_current_user),
    department: Optional[str] = Query(None, description="按部门过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
):
    """列出所有用户，支持按部门和状态过滤。"""
    users = rbac_engine.list_users()
    if department:
        users = [u for u in users if u["department"] == department]
    if status:
        users = [u for u in users if u.get("status") == status]
    return {"users": users, "count": len(users)}


@router.post("/users", summary="创建用户")
async def create_user(
    req: CreateUserRequest,
    current_user: dict = Depends(require_permission("user", "manage")),
):
    """创建新用户（需要 user:manage 权限）。"""
    # 检查用户名是否已存在
    existing = rbac_engine.get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"用户名已存在: {req.username}")

    user_id = f"user_{uuid4().hex[:10]}"
    password_hash = PasswordHasher.hash_password(req.password)

    try:
        user = rbac_engine.add_user(
            user_id=user_id,
            username=req.username,
            password_hash=password_hash,
            full_name=req.full_name,
            department=req.department,
            email=req.email,
        )
        # 更新额外字段
        if req.phone or req.position:
            rbac_engine.update_user(user_id, phone=req.phone, position=req.position)

        logger.info("rbac.user_created", user_id=user_id, username=req.username, created_by=current_user["user_id"])
        await _audit_rbac_change(
            current_user["user_id"], "user.create", user_id,
            {"username": req.username, "department": req.department},
        )
        return {"message": "用户创建成功", "user": user}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users/{user_id}", summary="获取用户详情")
async def get_user(user_id: str, current_user: dict = Depends(get_current_user)):
    """获取指定用户的详细信息。"""
    user = rbac_engine.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.put("/users/{user_id}", summary="更新用户")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    current_user: dict = Depends(require_permission("user", "manage")),
):
    """更新用户信息（需要 user:manage 权限）。"""
    user = rbac_engine.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    update_fields = req.model_dump(exclude_none=True)

    # 密码重置
    if "password" in update_fields:
        password_hash = PasswordHasher.hash_password(update_fields.pop("password"))
        update_fields["password_hash"] = password_hash

    try:
        updated = rbac_engine.update_user(user_id, **update_fields)
        logger.info("rbac.user_updated", user_id=user_id, fields=list(update_fields.keys()), updated_by=current_user["user_id"])
        await _audit_rbac_change(
            current_user["user_id"], "user.update", user_id,
            {"fields": [f for f in update_fields if f != "password_hash"]},
        )
        return {"message": "用户更新成功", "user": updated}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: str,
    current_user: dict = Depends(require_permission("user", "manage")),
):
    """删除用户（需要 user:manage 权限）。"""
    user = rbac_engine.get_user(user_id)
    if user_id == current_user["user_id"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")

    success = rbac_engine.delete_user(user_id)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")

    logger.info("rbac.user_deleted", user_id=user_id, deleted_by=current_user["user_id"])
    await _audit_rbac_change(
        current_user["user_id"], "user.delete", user_id,
        {"username": user.get("username") if user else None},
    )
    return {"message": "用户已删除", "user_id": user_id}


# ===================================================================
# 用户-角色管理
# ===================================================================

@router.get("/users/{user_id}/roles", summary="获取用户角色列表")
async def get_user_roles(user_id: str, current_user: dict = Depends(get_current_user)):
    """获取指定用户已分配的角色列表。"""
    user = rbac_engine.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    roles = rbac_engine.get_user_roles(user_id)
    return {"user_id": user_id, "roles": roles, "count": len(roles)}


@router.post("/users/{user_id}/roles", summary="为用户分配角色")
async def assign_role(
    user_id: str,
    req: AssignRoleRequest,
    current_user: dict = Depends(require_permission("role", "manage")),
):
    """为用户分配角色（需要 role:manage 权限）。"""
    user = rbac_engine.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    role = rbac_engine.get_role(req.role_code)
    if role is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {req.role_code}")

    success = rbac_engine.assign_role(user_id, req.role_code)
    if not success:
        raise HTTPException(status_code=400, detail="角色分配失败")

    logger.info("rbac.role_assigned", user_id=user_id, role_code=req.role_code, assigned_by=current_user["user_id"])
    await _audit_rbac_change(
        current_user["user_id"], "role.assign", user_id,
        {"role_code": req.role_code},
    )
    return {"message": "角色分配成功", "user_id": user_id, "role_code": req.role_code}


@router.delete("/users/{user_id}/roles", summary="收回用户角色")
async def revoke_role(
    user_id: str,
    req: AssignRoleRequest,
    current_user: dict = Depends(require_permission("role", "manage")),
):
    """收回用户的角色（需要 role:manage 权限）。"""
    success = rbac_engine.revoke_role(user_id, req.role_code)
    if not success:
        raise HTTPException(status_code=400, detail="角色收回失败（用户不存在或未分配该角色）")

    logger.info("rbac.role_revoked", user_id=user_id, role_code=req.role_code, revoked_by=current_user["user_id"])
    await _audit_rbac_change(
        current_user["user_id"], "role.revoke", user_id,
        {"role_code": req.role_code},
    )
    return {"message": "角色已收回", "user_id": user_id, "role_code": req.role_code}


@router.get("/users/{user_id}/permissions", summary="获取用户权限列表")
async def get_user_permissions(user_id: str, current_user: dict = Depends(get_current_user)):
    """获取指定用户的所有权限（聚合所有角色的权限）。"""
    user = rbac_engine.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    permissions = sorted(rbac_engine.get_user_permissions(user_id))
    return {"user_id": user_id, "permissions": permissions, "count": len(permissions)}


# ===================================================================
# 角色管理
# ===================================================================

@router.get("/roles", summary="列出所有角色")
async def list_roles(current_user: dict = Depends(get_current_user)):
    """列出系统中所有角色定义。"""
    roles = rbac_engine.list_roles()
    return {"roles": roles, "count": len(roles)}


@router.get("/roles/{role_code}", summary="获取角色详情")
async def get_role(role_code: str, current_user: dict = Depends(get_current_user)):
    """获取指定角色的详细信息。"""
    role = rbac_engine.get_role(role_code)
    if role is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {role_code}")
    return role


@router.get("/roles/{role_code}/permissions", summary="获取角色权限列表")
async def get_role_permissions(role_code: str, current_user: dict = Depends(get_current_user)):
    """获取指定角色的所有权限。"""
    role = rbac_engine.get_role(role_code)
    if role is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {role_code}")

    perm_ids = rbac_engine._role_permissions.get(role_code, set())
    permissions = [
        rbac_engine._permissions.get(pid, {"permission_id": pid})
        for pid in sorted(perm_ids)
    ]
    return {"role_code": role_code, "permissions": permissions, "count": len(permissions)}


# ===================================================================
# 权限管理
# ===================================================================

@router.get("/permissions", summary="列出所有权限定义")
async def list_permissions(current_user: dict = Depends(get_current_user)):
    """列出系统中所有权限定义。"""
    permissions = rbac_engine.list_permissions()
    return {"permissions": permissions, "count": len(permissions)}


# ===================================================================
# 部门管理
# ===================================================================

@router.get("/departments", summary="列出所有部门")
async def list_departments(current_user: dict = Depends(get_current_user)):
    """列出系统中所有部门。"""
    departments = rbac_engine.list_departments()
    return {"departments": departments, "count": len(departments)}


# ===================================================================
# 统计
# ===================================================================

@router.get("/stats", summary="RBAC引擎统计信息")
async def get_stats(current_user: dict = Depends(get_current_user)):
    """获取RBAC引擎的统计信息。"""
    stats = rbac_engine.get_stats()
    return stats
