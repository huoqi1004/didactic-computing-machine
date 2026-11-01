"""
多租户隔离 - 租户模型与注册表
===============================

企业就绪（P3-1）核心模块，为 ERP 系统提供多租户能力：

1. **租户模型**：``Tenant`` 定义租户实体（名称/状态/套餐/配额）
2. **租户注册表**：``TenantRegistry`` 管理租户生命周期（注册/启停/查询）
3. **配额模型**：``TenantQuota`` 定义租户资源上限（Agent数/存储/API调用/用户数）

数据隔离策略：
- 租户ID贯穿业务数据主键（如 ``{tenant_id}:{aggregate_id}``）
- 请求级租户上下文由 ``context.py`` 的 ``TenantContext`` 管理
- 资源配额由 ``quota.py`` 的 ``QuotaManager`` 执行

典型用法::

    from app.core.tenant import tenant_registry
    tenant = tenant_registry.register("acme", "Acme 制造集团")
    # 后续所有业务请求通过上下文获取当前租户
"""
from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

logger = structlog.get_logger()


class TenantStatus(str, Enum):
    """租户状态"""
    ACTIVE = "active"        # 正常服务
    TRIAL = "trial"          # 试用期
    SUSPENDED = "suspended"  # 已暂停（欠费/违规）
    DELETED = "deleted"      # 已删除（保留审计）


class TenantPlan(str, Enum):
    """租户套餐"""
    FREE = "free"                  # 免费版
    PROFESSIONAL = "professional"  # 专业版
    ENTERPRISE = "enterprise"      # 企业版


@dataclass
class TenantQuota:
    """租户资源配额"""
    max_agents: int = 33           # 最大 Agent 数量
    max_users: int = 50            # 最大用户数
    max_storage_mb: int = 10240    # 最大存储（MB）
    max_api_calls_per_min: int = 600  # API 调用频率上限
    max_concurrent_sagas: int = 20 # 并发 Saga 事务上限

    def to_dict(self) -> dict:
        return {
            "max_agents": self.max_agents,
            "max_users": self.max_users,
            "max_storage_mb": self.max_storage_mb,
            "max_api_calls_per_min": self.max_api_calls_per_min,
            "max_concurrent_sagas": self.max_concurrent_sagas,
        }

    @staticmethod
    def for_plan(plan: TenantPlan) -> "TenantQuota":
        """按套餐返回默认配额"""
        if plan == TenantPlan.FREE:
            return TenantQuota(max_agents=5, max_users=10, max_storage_mb=1024,
                               max_api_calls_per_min=60, max_concurrent_sagas=3)
        if plan == TenantPlan.PROFESSIONAL:
            return TenantQuota(max_agents=33, max_users=100, max_storage_mb=10240,
                               max_api_calls_per_min=600, max_concurrent_sagas=20)
        return TenantQuota(max_agents=100, max_users=500, max_storage_mb=51200,
                           max_api_calls_per_min=3000, max_concurrent_sagas=100)


@dataclass
class Tenant:
    """租户实体"""
    tenant_id: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE
    plan: TenantPlan = TenantPlan.PROFESSIONAL
    quota: TenantQuota = field(default_factory=TenantQuota)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "status": self.status.value,
            "plan": self.plan.value,
            "quota": self.quota.to_dict(),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class TenantRegistry:
    """
    租户注册表

    管理所有租户的生命周期：
    - ``register`` / ``unregister``：注册与注销
    - ``suspend`` / ``activate``：暂停与恢复
    - ``get`` / ``list``：查询
    - ``is_active``：校验租户是否可用
    """

    def __init__(self):
        self._tenants: dict[str, Tenant] = {}
        # 默认租户：单租户模式下使用
        self._tenants["default"] = Tenant(
            tenant_id="default",
            name="默认租户",
            status=TenantStatus.ACTIVE,
            plan=TenantPlan.ENTERPRISE,
        )

    def register(
        self,
        name: str,
        plan: TenantPlan | str = TenantPlan.PROFESSIONAL,
        tenant_id: Optional[str] = None,
    ) -> Tenant:
        """
        注册新租户。

        Args:
            name: 租户名称
            plan: 套餐（``TenantPlan`` 枚举或字符串如 ``"free"``）
            tenant_id: 自定义租户ID（默认自动生成）

        Returns:
            Tenant: 新创建的租户
        """
        # 兼容字符串套餐
        if isinstance(plan, str):
            plan = TenantPlan(plan)

        tid = tenant_id or f"tnt-{str(uuid4())[:8]}"
        if tid in self._tenants:
            logger.warning("tenant.duplicate_id", tenant_id=tid)
            return self._tenants[tid]

        tenant = Tenant(
            tenant_id=tid,
            name=name,
            plan=plan,
            quota=TenantQuota.for_plan(plan),
        )
        self._tenants[tid] = tenant
        logger.info("tenant.registered",
                    tenant_id=tid, name=name, plan=plan.value)
        return tenant

    def get(self, tenant_id: str) -> Optional[Tenant]:
        """获取租户"""
        return self._tenants.get(tenant_id)

    def list(self, status: Optional[TenantStatus] = None) -> list[dict]:
        """列出租户（可按状态过滤）"""
        tenants = [
            t for t in self._tenants.values()
            if status is None or t.status == status
        ]
        return [t.to_dict() for t in tenants]

    def suspend(self, tenant_id: str) -> dict:
        """暂停租户（业务数据保留但禁止访问）"""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return {"error": "tenant_not_found", "tenant_id": tenant_id}
        if tenant.status == TenantStatus.DELETED:
            return {"error": "tenant_deleted", "tenant_id": tenant_id}
        tenant.status = TenantStatus.SUSPENDED
        logger.warning("tenant.suspended", tenant_id=tenant_id)
        return {"status": "success", "tenant": tenant.to_dict()}

    def activate(self, tenant_id: str) -> dict:
        """恢复租户服务"""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return {"error": "tenant_not_found", "tenant_id": tenant_id}
        if tenant.status == TenantStatus.DELETED:
            return {"error": "tenant_deleted", "tenant_id": tenant_id}
        tenant.status = TenantStatus.ACTIVE
        logger.info("tenant.activated", tenant_id=tenant_id)
        return {"status": "success", "tenant": tenant.to_dict()}

    def unregister(self, tenant_id: str) -> dict:
        """注销租户（标记为已删除，保留审计记录）"""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return {"error": "tenant_not_found", "tenant_id": tenant_id}
        tenant.status = TenantStatus.DELETED
        logger.warning("tenant.unregistered", tenant_id=tenant_id)
        return {"status": "success", "tenant_id": tenant_id}

    def is_active(self, tenant_id: str) -> bool:
        """校验租户是否处于可用状态"""
        tenant = self._tenants.get(tenant_id)
        return tenant is not None and tenant.status in (
            TenantStatus.ACTIVE, TenantStatus.TRIAL,
        )

    def get_quota(self, tenant_id: str) -> Optional[TenantQuota]:
        """获取租户配额"""
        tenant = self._tenants.get(tenant_id)
        return tenant.quota if tenant else None


# 全局单例
tenant_registry = TenantRegistry()
