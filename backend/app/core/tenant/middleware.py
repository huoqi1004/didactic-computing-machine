"""
多租户隔离 - FastAPI 中间件
============================

从请求上下文提取租户并注入租户上下文：

- 若 ``request.state.user`` 存在且含 ``tenant_id``，则绑定该租户
- 否则回退到默认租户（``default``）
- 在中间件内完成租户配额校验（API 调用频率）

挂载位置：需在 AuthMiddleware 之后执行（依赖已认证的 user）。
"""
from __future__ import annotations

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.core.tenant.context import tenant_context
from app.core.tenant.quota import quota_manager, RESOURCE_API_CALLS

logger = structlog.get_logger()

# 不计入 API 配额的白名单路径（监控/健康检查）
_QUOTA_EXEMPT_PATHS = (
    "/metrics", "/health", "/docs", "/openapi.json", "/redoc",
)


class TenantMiddleware(BaseHTTPMiddleware):
    """
    租户隔离中间件

    职责：
    1. 从已认证用户提取 ``tenant_id`` 并绑定租户上下文
    2. 执行 API 调用配额校验（超限返回 429）
    3. 请求结束时清理租户上下文
    """

    async def dispatch(self, request: Request, call_next):
        # 1. 提取租户
        user = getattr(request.state, "user", None)
        tenant_id = user.get("tenant_id", "default") if user else "default"
        bound = tenant_context.set_tenant(tenant_id)

        # 2. 配额校验（跳过白名单路径）
        path = request.url.path
        if not any(path.startswith(p) for p in _QUOTA_EXEMPT_PATHS):
            check = quota_manager.check(bound, RESOURCE_API_CALLS)
            if not check["allowed"]:
                from fastapi.responses import JSONResponse
                logger.warning("tenant.api_quota_exceeded",
                               tenant_id=bound,
                               limit=check["limit"])
                tenant_context.clear()
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "租户API调用频率超出配额",
                        "limit": check["limit"],
                        "resource": RESOURCE_API_CALLS,
                    },
                )
            quota_manager.consume(bound, RESOURCE_API_CALLS)

        try:
            response = await call_next(request)
            return response
        finally:
            tenant_context.clear()
