"""
API 鉴权中间件
==============
JWT 认证 + RBAC 权限校验，挂载到 FastAPI 请求处理链。

设计说明：
- AuthMiddleware: 从 Header 提取 JWT → 验证 → 注入 current_user 到 request.state
- PermissionMiddleware: 路径+方法 → resource:action 映射 → RBAC 校验
- 公开端点白名单：/api/v1/auth/login, /api/v1/health, /docs, /openapi.json 等
- get_current_user: FastAPI Depends 依赖注入，用于路由函数获取当前用户
- require_permission: 装饰器/依赖工厂，用于路由级权限控制
"""
import re
import structlog
from typing import Optional, Callable
from fastapi import Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from app.core.auth import jwt_manager, session_manager
from app.core.rbac import rbac_engine

logger = structlog.get_logger()

# ========== 公开端点白名单 ==========
# 这些路径不需要认证（正则匹配）
PUBLIC_PATHS = [
    r"^/api/v1/auth/login$",
    r"^/api/v1/auth/refresh$",
    r"^/api/v1/health",
    r"^/health",
    r"^/metrics",
    r"^/docs",
    r"^/openapi\.json",
    r"^/redoc",
    r"^/$",  # 根端点
    r"^/api/v1/document/templates$",  # 文档模板列表（公开）
]

# ========== 路径 → 权限映射规则 ==========
# 将 API 路径+方法映射到 resource:action
# 格式: (路径正则, 方法, 资源, 动作)
PERMISSION_RULES = [
    # Agent 管理
    (r"^/api/v1/agents", "GET", "agent", "read"),
    (r"^/api/v1/agents", "POST", "agent", "manage"),
    (r"^/api/v1/agents/.+/(pause|resume|takeover|release)", "POST", "agent", "manage"),
    # 库存
    (r"^/api/v1/inventory", "GET", "inventory", "read"),
    (r"^/api/v1/inventory", "POST", "inventory", "create"),
    (r"^/api/v1/inventory", "PUT", "inventory", "update"),
    # 会计
    (r"^/api/v1/accounting", "GET", "accounting", "read"),
    (r"^/api/v1/accounting/vouchers/.+/approve", "POST", "accounting", "approve"),
    (r"^/api/v1/accounting", "POST", "accounting", "create"),
    # 发票
    (r"^/api/v1/invoice", "GET", "invoice", "read"),
    (r"^/api/v1/invoice/issue", "POST", "invoice", "create"),
    (r"^/api/v1/invoice/.+/(verify|cancel)", "POST", "invoice", "update"),
    # 报销
    (r"^/api/v1/reimbursement", "GET", "reimbursement", "read"),
    (r"^/api/v1/reimbursement/submit", "POST", "reimbursement", "create"),
    (r"^/api/v1/reimbursement/.+/(verify|approve|pay)", "POST", "reimbursement", "approve"),
    # 税务
    (r"^/api/v1/tax", "GET", "tax", "read"),
    (r"^/api/v1/tax", "POST", "tax", "create"),
    # 采购
    (r"^/api/v1/procurement", "GET", "procurement", "read"),
    (r"^/api/v1/procurement/orders", "POST", "procurement", "create"),
    (r"^/api/v1/procurement/orders/.+/approve", "POST", "procurement", "approve"),
    # 资金
    (r"^/api/v1/treasury", "GET", "treasury", "read"),
    (r"^/api/v1/treasury/transfers/.+/approve", "POST", "treasury", "approve"),
    # 内审
    (r"^/api/v1/audit", "GET", "audit", "read"),
    # 合规
    (r"^/api/v1/compliance", "GET", "compliance", "read"),
    (r"^/api/v1/compliance/check", "POST", "compliance", "create"),
    # 需求预测
    (r"^/api/v1/demand", "GET", "inventory", "read"),
    # 供应规划
    (r"^/api/v1/supply", "GET", "procurement", "read"),
    # 生产
    (r"^/api/v1/production", "GET", "production", "read"),
    (r"^/api/v1/production", "POST", "production", "create"),
    # 物流
    (r"^/api/v1/logistics", "GET", "production", "read"),
    (r"^/api/v1/logistics", "POST", "production", "create"),
    # 调度
    (r"^/api/v1/scheduler", "GET", "agent", "read"),
    # 安全
    (r"^/api/v1/security", "GET", "security", "read"),
    # 事件
    (r"^/api/v1/events", "GET", "system", "read"),
    # 知识
    (r"^/api/v1/knowledge/search", "POST", "knowledge", "read"),
    (r"^/api/v1/knowledge/graph", "POST", "knowledge", "read"),
    (r"^/api/v1/knowledge/rules", "POST", "knowledge", "read"),
    (r"^/api/v1/knowledge", "GET", "knowledge", "read"),
    (r"^/api/v1/knowledge/ingest", "POST", "knowledge", "create"),
    (r"^/api/v1/knowledge/categories", "POST", "knowledge", "manage"),
    (r"^/api/v1/knowledge/categories", "DELETE", "knowledge", "manage"),
    (r"^/api/v1/knowledge/documents", "DELETE", "knowledge", "delete"),
    (r"^/api/v1/knowledge/review/submit", "POST", "knowledge", "create"),
    (r"^/api/v1/knowledge/review/pending", "GET", "knowledge", "approve"),
    (r"^/api/v1/knowledge/review/.+/approve", "POST", "knowledge", "approve"),
    (r"^/api/v1/knowledge/review/.+/reject", "POST", "knowledge", "approve"),
    (r"^/api/v1/knowledge", "POST", "knowledge", "create"),
    # 版本化
    (r"^/api/v1/versioning", "GET", "system", "read"),
    # RBAC 管理
    (r"^/api/v1/rbac/users", "GET", "user", "read"),
    (r"^/api/v1/rbac/users", "POST", "user", "manage"),
    (r"^/api/v1/rbac/roles", "GET", "role", "read"),
    (r"^/api/v1/rbac/roles", "POST", "role", "manage"),
    (r"^/api/v1/rbac/permissions", "GET", "permission", "read"),
    (r"^/api/v1/rbac/assign", "POST", "role", "manage"),
    (r"^/api/v1/rbac/revoke", "POST", "role", "manage"),
    # 审批工作流
    (r"^/api/v1/workflow", "GET", "system", "read"),
    (r"^/api/v1/workflow/request", "POST", "system", "create"),
    (r"^/api/v1/workflow/.+/approve", "POST", "system", "approve"),
    (r"^/api/v1/workflow/.+/reject", "POST", "system", "approve"),
    # 管理员
    (r"^/api/v1/admin", "GET", "admin", "read"),
    (r"^/api/v1/admin", "POST", "admin", "manage"),
    (r"^/api/v1/admin", "PUT", "admin", "manage"),
    (r"^/api/v1/admin", "DELETE", "admin", "manage"),
    # HR
    (r"^/api/v1/hr", "GET", "hr", "read"),
    (r"^/api/v1/hr", "POST", "hr", "create"),
    (r"^/api/v1/hr", "PUT", "hr", "update"),
    # 招聘
    (r"^/api/v1/recruitment", "GET", "recruitment", "read"),
    (r"^/api/v1/recruitment", "POST", "recruitment", "create"),
    (r"^/api/v1/recruitment", "PUT", "recruitment", "update"),
    # 绩效
    (r"^/api/v1/performance", "GET", "performance", "read"),
    (r"^/api/v1/performance", "POST", "performance", "create"),
    # 薪酬（使用 hr 资源权限）
    (r"^/api/v1/payroll", "GET", "hr", "read"),
    (r"^/api/v1/payroll", "POST", "hr", "create"),
    (r"^/api/v1/payroll", "PUT", "hr", "update"),
    # CRM
    (r"^/api/v1/crm", "GET", "crm", "read"),
    (r"^/api/v1/crm", "POST", "crm", "create"),
    (r"^/api/v1/crm", "PUT", "crm", "update"),
    # 销售
    (r"^/api/v1/sales", "GET", "sales", "read"),
    (r"^/api/v1/sales", "POST", "sales", "create"),
    # 市场
    (r"^/api/v1/marketing", "GET", "marketing", "read"),
    (r"^/api/v1/marketing", "POST", "marketing", "create"),
    # 客服
    (r"^/api/v1/cs", "GET", "customer_service", "read"),
    (r"^/api/v1/cs", "POST", "customer_service", "create"),
    (r"^/api/v1/cs", "PUT", "customer_service", "update"),
    # 法务
    (r"^/api/v1/contracts", "GET", "contract", "read"),
    (r"^/api/v1/contracts", "POST", "contract", "create"),
    (r"^/api/v1/legal", "GET", "legal", "read"),
    (r"^/api/v1/legal", "POST", "legal", "create"),
    (r"^/api/v1/risk", "GET", "risk", "read"),
    (r"^/api/v1/risk", "POST", "risk", "create"),
    # 运营
    (r"^/api/v1/quality", "GET", "quality", "read"),
    (r"^/api/v1/quality", "POST", "quality", "create"),
    (r"^/api/v1/assets", "GET", "asset", "read"),
    (r"^/api/v1/assets", "POST", "asset", "create"),
    (r"^/api/v1/projects", "GET", "project", "read"),
    (r"^/api/v1/projects", "POST", "project", "create"),
    # 战略
    (r"^/api/v1/budget", "GET", "budget", "read"),
    (r"^/api/v1/budget", "POST", "budget", "create"),
    (r"^/api/v1/bi", "GET", "bi", "read"),
    (r"^/api/v1/esg", "GET", "esg", "read"),
    (r"^/api/v1/esg", "POST", "esg", "create"),
    # 文档生成
    (r"^/api/v1/document/generate", "POST", "document", "create"),
    (r"^/api/v1/document/download", "GET", "document", "read"),
    (r"^/api/v1/document/agents", "GET", "document", "read"),
]


def is_public_path(path: str) -> bool:
    """检查路径是否在公开白名单中"""
    for pattern in PUBLIC_PATHS:
        if re.match(pattern, path):
            return True
    return False


def resolve_permission(path: str, method: str) -> Optional[tuple]:
    """
    将请求路径+方法映射到 (resource, action) 权限对。
    返回 None 表示该路径不需要特定权限（但需要认证）。
    """
    for pattern, req_method, resource, action in PERMISSION_RULES:
        if method == req_method and re.match(pattern, path):
            return (resource, action)
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    """
    JWT 认证中间件。
    从 Authorization Header 提取 Bearer Token，验证后注入 current_user 到 request.state。
    公开端点跳过认证。
    """

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        # 公开端点跳过
        if is_public_path(path):
            return await call_next(request)

        # WebSocket 端点跳过（WS 使用独立认证逻辑）
        if path.startswith("/api/v1/ws"):
            return await call_next(request)

        # 提取 Token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "未提供认证令牌", "code": "NO_TOKEN"},
            )

        token = auth_header[7:]  # 去掉 "Bearer "

        # 验证 Token
        payload = jwt_manager.verify_token(token)
        if payload is None:
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "认证令牌无效或已过期", "code": "INVALID_TOKEN"},
            )

        # 检查会话（异步：本机 L1 + Redis 共享 L2，支持多实例一致）
        session = await session_manager.get_session(token)
        if session is None:
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "会话已失效，请重新登录", "code": "SESSION_EXPIRED"},
            )

        # RBAC 跨实例新鲜度检查（版本信号变化时重载缓存；5 秒节流）
        from app.core.rbac import rbac_engine

        await rbac_engine.ensure_fresh()

        # 注入当前用户信息到 request.state
        request.state.current_user = {
            "user_id": payload.get("user_id"),
            "username": payload.get("username"),
            "role_codes": payload.get("role_codes", []),
            "token": token,
        }

        return await call_next(request)


class PermissionMiddleware(BaseHTTPMiddleware):
    """
    RBAC 权限校验中间件。
    根据请求路径和方法，映射到 resource:action，检查当前用户是否拥有该权限。
    无映射规则的路径仅需认证（AuthMiddleware 已完成），不做权限校验。
    """

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        method = request.method

        # 公开端点跳过
        if is_public_path(path):
            return await call_next(request)

        # WebSocket 跳过
        if path.startswith("/api/v1/ws"):
            return await call_next(request)

        # OPTIONS 预检请求跳过
        if method == "OPTIONS":
            return await call_next(request)

        # 获取当前用户（AuthMiddleware 已注入）
        current_user = getattr(request.state, "current_user", None)
        if current_user is None:
            # AuthMiddleware 未通过，直接放行（AuthMiddleware 会返回401）
            return await call_next(request)

        # 解析权限要求
        perm = resolve_permission(path, method)
        if perm is None:
            # 无映射规则，仅需认证
            return await call_next(request)

        resource, action = perm

        # 检查权限
        has_perm = rbac_engine.check_permission(
            current_user["user_id"], resource, action
        )

        if not has_perm:
            logger.warning(
                "permission.denied",
                user_id=current_user["user_id"],
                username=current_user["username"],
                path=path,
                method=method,
                resource=resource,
                action=action,
            )
            return JSONResponse(
                status_code=HTTP_403_FORBIDDEN,
                content={
                    "detail": f"权限不足：需要 {resource}:{action} 权限",
                    "code": "PERMISSION_DENIED",
                    "required": f"{resource}:{action}",
                },
            )

        return await call_next(request)


# ========== FastAPI Depends 依赖注入 ==========

async def get_current_user(request: Request) -> dict:
    """
    FastAPI 依赖注入：获取当前登录用户。
    在路由函数中使用: `user = Depends(get_current_user)`
    """
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="未认证",
        )
    return current_user


def require_permission(resource: str, action: str):
    """
    FastAPI 依赖工厂：要求特定权限。
    在路由函数中使用: `@router.get(..., dependencies=[Depends(require_permission("invoice", "read"))])`
    或: `user = Depends(require_permission("invoice", "read"))`
    """
    async def check(request: Request) -> dict:
        current_user = getattr(request.state, "current_user", None)
        if current_user is None:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="未认证",
            )
        has_perm = rbac_engine.check_permission(
            current_user["user_id"], resource, action
        )
        if not has_perm:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {resource}:{action} 权限",
            )
        return current_user

    return check
