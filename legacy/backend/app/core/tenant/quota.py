"""
多租户隔离 - 资源配额管理
==========================

按租户执行资源配额限制，防止单租户耗尽系统资源：

- ``QuotaManager.check``：检查操作是否超出配额
- ``QuotaManager.consume``：消耗配额（计数）
- ``QuotaManager.release``：释放配额
- 配额类型：Agent 数 / 存储 / API 调用频率 / 并发 Saga

实现：
- API 调用频率使用滑动窗口计数（内存）
- 并发 Saga 使用活跃计数（进入时 +1，退出时 -1）
- 所有配额从 ``TenantRegistry`` 的租户配置读取
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Optional

import structlog

from app.core.tenant.tenant import TenantQuota, tenant_registry

logger = structlog.get_logger()

# 配额资源类型
RESOURCE_AGENTS = "agents"
RESOURCE_USERS = "users"
RESOURCE_STORAGE_MB = "storage_mb"
RESOURCE_API_CALLS = "api_calls"
RESOURCE_CONCURRENT_SAGAS = "concurrent_sagas"

# 配额 → 租户字段映射
_QUOTA_FIELD_MAP = {
    RESOURCE_AGENTS: "max_agents",
    RESOURCE_USERS: "max_users",
    RESOURCE_STORAGE_MB: "max_storage_mb",
    RESOURCE_API_CALLS: "max_api_calls_per_min",
    RESOURCE_CONCURRENT_SAGAS: "max_concurrent_sagas",
}


class QuotaManager:
    """
    租户资源配额管理器
    """

    def __init__(self):
        # API 调用滑动窗口：{tenant_id: deque[timestamp]}
        self._api_windows: dict[str, deque[float]] = defaultdict(deque)
        # 并发计数：{tenant_id: {resource: count}}
        self._concurrent: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    def _get_limit(self, tenant_id: str, resource: str) -> Optional[int]:
        """获取租户的配额上限"""
        quota = tenant_registry.get_quota(tenant_id)
        if quota is None:
            return None
        field = _QUOTA_FIELD_MAP.get(resource)
        if field is None:
            return None
        return getattr(quota, field, None)

    def check(self, tenant_id: str, resource: str) -> dict:
        """
        检查租户操作是否超出配额。

        Returns:
            dict: ``{"allowed": bool, "current": int, "limit": int, "resource": str}``
        """
        limit = self._get_limit(tenant_id, resource)
        if limit is None:
            # 租户不存在或资源未知：默认允许（不阻塞）
            return {"allowed": True, "current": 0, "limit": None, "resource": resource}

        if resource == RESOURCE_API_CALLS:
            current = self._count_api_calls(tenant_id, limit)
        else:
            current = self._concurrent[tenant_id].get(resource, 0)

        allowed = current < limit
        if not allowed:
            logger.warning("quota.exceeded",
                           tenant_id=tenant_id,
                           resource=resource,
                           current=current,
                           limit=limit)
        return {
            "allowed": allowed,
            "current": current,
            "limit": limit,
            "resource": resource,
        }

    def consume(self, tenant_id: str, resource: str) -> bool:
        """
        消耗配额。

        - API 调用：加入滑动窗口（自动按分钟过期）
        - 其他资源：并发计数 +1（需先通过 ``check``）

        Returns:
            bool: 消耗成功返回 True，超限返回 False
        """
        result = self.check(tenant_id, resource)
        if not result["allowed"]:
            return False

        if resource == RESOURCE_API_CALLS:
            self._api_windows[tenant_id].append(time.monotonic())
        else:
            self._concurrent[tenant_id][resource] += 1
        return True

    def release(self, tenant_id: str, resource: str) -> None:
        """释放配额（并发资源用完后调用）"""
        if resource == RESOURCE_API_CALLS:
            return
        current = self._concurrent[tenant_id].get(resource, 0)
        if current > 0:
            self._concurrent[tenant_id][resource] = current - 1

    def _count_api_calls(self, tenant_id: str, limit: int) -> int:
        """统计当前滑动窗口内的 API 调用次数（自动清理过期）"""
        window = self._api_windows[tenant_id]
        now = time.monotonic()
        window_start = now - 60.0
        # 移除窗口外的记录
        while window and window[0] < window_start:
            window.popleft()
        # 防止无限增长
        if len(window) > limit * 2:
            window = deque(
                [t for t in window if t >= window_start],
                maxlen=limit * 4,
            )
            self._api_windows[tenant_id] = window
        return len(window)

    def get_usage(self, tenant_id: str) -> dict:
        """
        获取租户所有资源的当前用量。

        Returns:
            dict: ``{resource: {"current": n, "limit": n}}``
        """
        usage = {}
        for resource in _QUOTA_FIELD_MAP:
            limit = self._get_limit(tenant_id, resource)
            if resource == RESOURCE_API_CALLS:
                current = self._count_api_calls(tenant_id, limit or 0) if limit else 0
            else:
                current = self._concurrent[tenant_id].get(resource, 0)
            usage[resource] = {"current": current, "limit": limit}
        return usage

    def reset(self, tenant_id: str) -> None:
        """重置租户配额用量（租户升级/重置时调用）"""
        self._api_windows.pop(tenant_id, None)
        self._concurrent.pop(tenant_id, None)


# 全局单例
quota_manager = QuotaManager()
