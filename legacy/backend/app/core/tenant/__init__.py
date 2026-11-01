"""
多租户隔离模块（P3-1 企业就绪）
================================

提供：
- ``Tenant`` / ``TenantRegistry``：租户模型与注册表（tenant.py）
- ``TenantContext``：请求级租户上下文（context.py）
- ``QuotaManager``：资源配额管理（quota.py）
- ``TenantMiddleware``：FastAPI 中间件，从 JWT 提取租户（middleware.py）
"""
from app.core.tenant.tenant import (
    Tenant,
    TenantQuota,
    TenantRegistry,
    TenantStatus,
    TenantPlan,
    tenant_registry,
)
from app.core.tenant.context import (
    TenantContext,
    tenant_context,
    get_current_tenant,
    with_tenant_id,
    parse_tenant_id,
    assert_tenant_access,
)
from app.core.tenant.quota import (
    QuotaManager,
    quota_manager,
    RESOURCE_AGENTS,
    RESOURCE_USERS,
    RESOURCE_STORAGE_MB,
    RESOURCE_API_CALLS,
    RESOURCE_CONCURRENT_SAGAS,
)
from app.core.tenant.middleware import TenantMiddleware

__all__ = [
    "Tenant", "TenantQuota", "TenantRegistry", "TenantStatus", "TenantPlan",
    "tenant_registry",
    "TenantContext", "tenant_context", "get_current_tenant",
    "with_tenant_id", "parse_tenant_id", "assert_tenant_access",
    "QuotaManager", "quota_manager",
    "RESOURCE_AGENTS", "RESOURCE_USERS", "RESOURCE_STORAGE_MB",
    "RESOURCE_API_CALLS", "RESOURCE_CONCURRENT_SAGAS",
    "TenantMiddleware",
]
