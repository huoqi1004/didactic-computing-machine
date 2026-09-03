"""
多租户隔离 - 租户上下文
========================

使用 ``contextvars`` 实现请求级租户隔离：

- ``TenantContext``：为每个请求绑定当前租户，业务代码通过 ``get_current_tenant()`` 获取
- 与 FastAPI 中间件集成：从 JWT 的 ``tenant_id`` 声明提取租户并注入上下文
- 未启用多租户或未指定租户时，回退到默认租户（``default``）

典型用法::

    from app.core.tenant.context import tenant_context, get_current_tenant

    @app.middleware("http")
    async def tenant_middleware(request, call_next):
        await tenant_context.set_tenant(request.state.user.get("tenant_id", "default"))
        try:
            return await call_next(request)
        finally:
            tenant_context.clear()
"""
from __future__ import annotations

import contextvars
from typing import Optional

import structlog

from app.core.tenant.tenant import tenant_registry

logger = structlog.get_logger()

# 当前租户上下文变量（每请求独立）
_tenant_context_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_tenant_id", default="default"
)


class TenantContext:
    """租户上下文管理器"""

    @staticmethod
    def set_tenant(tenant_id: str) -> str:
        """
        设置当前租户。

        若租户不存在或已暂停，回退到默认租户并记录警告。
        """
        if tenant_id and tenant_registry.is_active(tenant_id):
            _tenant_context_var.set(tenant_id)
            return tenant_id

        # 回退逻辑
        fallback = "default"
        if tenant_id:
            logger.warning("tenant.context_fallback",
                           requested=tenant_id, fallback=fallback,
                           reason="tenant_inactive_or_missing")
        _tenant_context_var.set(fallback)
        return fallback

    @staticmethod
    def get_tenant() -> str:
        """获取当前租户ID"""
        return _tenant_context_var.get()

    @staticmethod
    def get_tenant_name() -> str:
        """获取当前租户名称"""
        tid = _tenant_context_var.get()
        tenant = tenant_registry.get(tid)
        return tenant.name if tenant else tid

    @staticmethod
    def clear() -> None:
        """清除当前租户（请求结束调用）"""
        _tenant_context_var.set("default")

    @staticmethod
    def is_tenant_enabled() -> bool:
        """是否处于多租户模式（非默认租户即视为启用）"""
        return _tenant_context_var.get() != "default"


# 全局单例
tenant_context = TenantContext()


def get_current_tenant() -> str:
    """获取当前请求的租户ID（业务代码统一入口）"""
    return tenant_context.get_tenant()


def with_tenant_id(entity_id: str) -> str:
    """
    为业务实体ID添加租户前缀，实现数据隔离。

    ``with_tenant_id("prod-001")`` → ``"default:prod-001"``
    """
    return f"{tenant_context.get_tenant()}:{entity_id}"


def parse_tenant_id(scoped_id: str) -> tuple[str, str]:
    """
    解析带租户前缀的实体ID。

    ``parse_tenant_id("acme:prod-001")`` → ``("acme", "prod-001")``
    """
    if ":" in scoped_id:
        tenant_part, _, entity_part = scoped_id.partition(":")
        return tenant_part, entity_part
    return "default", scoped_id


def assert_tenant_access(scoped_id: str) -> bool:
    """
    校验实体ID是否属于当前租户（跨租户访问拦截）。

    Returns:
        bool: 允许访问返回 True，跨租户返回 False
    """
    owner_tenant, _ = parse_tenant_id(scoped_id)
    current = tenant_context.get_tenant()
    allowed = owner_tenant == current
    if not allowed:
        logger.warning("tenant.cross_tenant_access_blocked",
                       owner_tenant=owner_tenant,
                       current_tenant=current,
                       entity=scoped_id)
    return allowed
