"""
外部系统集成注册中心（P3-2 企业就绪阶段）
==========================================

管理所有外部集成连接（银行 / 税务 / 电子签章等）的注册、查询、
连接测试与整体健康检查。

凭据解析顺序（``register_defaults`` 中逐项解析）：
1. ``app.core.config.settings`` 对应字段（如 ``bank_base_url`` / ``bank_api_key``，
   若未来在 :class:`~app.core.config.Settings` 中新增这些字段将自动生效）
2. 环境变量（``ERP_BANK_BASE_URL`` / ``ERP_BANK_API_KEY`` 等，与 pydantic-settings
   的 ``ERP_`` 前缀命名保持一致）
3. 占位符（``https://api.example.com`` / ``sk-placeholder``）+ ``configured: False``

降级策略（保证开发环境 / CI 可离线运行）：
- 无真实凭据时，适配器进入模拟模式（mock mode），不发起任何真实网络请求
- ``test_connection`` / ``health_check`` 在模拟模式下直接返回 ``status: ok``
- 全局单例 :data:`integration_registry` 在模块导入时自动注册内置三件套
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.core.config import settings
from app.core.integrations.base import BaseIntegrationAdapter
from app.core.integrations.bank import BankIntegrationAdapter
from app.core.integrations.esign import ESignIntegrationAdapter
from app.core.integrations.exceptions import IntegrationConfigError
from app.core.integrations.tax import TaxIntegrationAdapter

logger = structlog.get_logger()

#: 占位符默认值
_PLACEHOLDER_API_KEY = "sk-placeholder"
_PLACEHOLDER_BASE_URL = "https://api.example.com"


def _resolve_credential(settings_field: str, env_var: str, placeholder: str) -> tuple[str, bool]:
    """
    按 settings 字段 → 环境变量 → 占位符 顺序解析凭据。

    :return: ``(value, configured)``；无真实配置时 ``configured=False``
    """
    value = getattr(settings, settings_field, None)
    if value not in (None, ""):
        return str(value), True
    value = os.environ.get(env_var)
    if value:
        return str(value), True
    return placeholder, False


def _resolve_timeout() -> float:
    """解析集成请求超时（秒），默认 10.0"""
    value = getattr(settings, "integration_timeout", None) or os.environ.get("ERP_INTEGRATION_TIMEOUT")
    try:
        return float(value) if value not in (None, "") else 10.0
    except (TypeError, ValueError):
        return 10.0


class IntegrationRegistry:
    """外部系统集成注册中心"""

    def __init__(self, auto_register: bool = False):
        #: name -> 适配器实例
        self._adapters: dict[str, BaseIntegrationAdapter] = {}
        #: name -> 注册时携带的配置元数据
        self._configs: dict[str, dict] = {}
        if auto_register:
            self.register_defaults()

    # ------------------------------------------------------------------
    # 注册 / 查询 / 列表
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        adapter: BaseIntegrationAdapter,
        config: Optional[dict] = None,
    ) -> None:
        """
        注册外部集成连接。

        :param name: 集成名称（如 ``bank`` / ``tax`` / ``esign``）
        :param adapter: 适配器实例（必须是 :class:`BaseIntegrationAdapter` 子类）
        :param config: 附加配置元数据（仅用于展示，如 base_url / env）
        """
        if not name or not str(name).strip():
            raise IntegrationConfigError("集成名称不能为空", provider="registry")
        if not isinstance(adapter, BaseIntegrationAdapter):
            raise IntegrationConfigError(
                f"adapter 必须是 BaseIntegrationAdapter 实例，实际为 {type(adapter).__name__}",
                provider="registry",
                detail={"name": name},
            )
        name = str(name).strip()
        self._adapters[name] = adapter
        self._configs[name] = dict(config or {})
        logger.info(
            "integration.registry.register",
            name=name,
            provider=adapter.provider,
            configured=adapter.configured,
            mock_mode=adapter.mock_mode,
        )

    def unregister(self, name: str) -> None:
        """注销集成连接（不存在时静默忽略）"""
        self._adapters.pop(name, None)
        self._configs.pop(name, None)
        logger.info("integration.registry.unregister", name=name)

    def get(self, name: str) -> BaseIntegrationAdapter:
        """获取已注册的适配器；未注册时抛出 :class:`IntegrationConfigError`"""
        adapter = self._adapters.get(name)
        if adapter is None:
            raise IntegrationConfigError(
                f"未注册的外部集成: {name}",
                provider="registry",
                detail={"available": sorted(self._adapters)},
            )
        return adapter

    def list(self) -> list[dict]:
        """列出所有已注册集成及其配置状态"""
        return [
            {
                "name": name,
                "provider": adapter.provider,
                "configured": adapter.configured,
                "mock_mode": adapter.mock_mode,
                "config": self._configs.get(name, {}),
            }
            for name, adapter in self._adapters.items()
        ]

    # ------------------------------------------------------------------
    # 连接测试 / 健康检查
    # ------------------------------------------------------------------

    async def test_connection(self, name: str) -> dict:
        """
        测试单个集成连接。

        模拟模式：直接返回 ``{"status": "ok", "mode": "mock"}``（不发起网络请求）。
        真实模式：调用适配器 ``health_check``（GET /health）。
        """
        adapter = self.get(name)
        result = await adapter.health_check()
        logger.info(
            "integration.registry.test_connection",
            name=name,
            status=result.get("status"),
        )
        return result

    async def health_check(self) -> dict:
        """
        整体健康检查：遍历所有已注册集成，汇总连接状态。

        返回 ``overall_status``：``healthy``（全部正常）/ ``degraded``（部分异常）/
        ``unhealthy``（全部异常）/ ``no_connections``（未注册任何集成）。
        """
        connections: dict[str, dict] = {}
        for name, adapter in self._adapters.items():
            try:
                connections[name] = await adapter.health_check()
            except Exception as exc:  # noqa: BLE001 - 单点失败不影响整体汇总
                connections[name] = {
                    "status": "error",
                    "provider": adapter.provider,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }

        statuses = [c.get("status") for c in connections.values()]
        if not statuses:
            overall = "no_connections"
        elif all(s == "ok" for s in statuses):
            overall = "healthy"
        elif any(s == "ok" for s in statuses):
            overall = "degraded"
        else:
            overall = "unhealthy"

        result = {
            "overall_status": overall,
            "total": len(connections),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "connections": connections,
        }
        logger.info(
            "integration.registry.health_check",
            overall=overall,
            total=len(connections),
        )
        return result

    # ------------------------------------------------------------------
    # 内置集成工厂（从配置 / 环境变量构建）
    # ------------------------------------------------------------------

    def register_defaults(self) -> None:
        """
        注册内置集成三件套（银行 / 税务 / 电子签章）。

        凭据解析：settings 字段 → 环境变量（``ERP_*``）→ 占位符。
        无真实凭据时对应适配器自动进入模拟模式（``configured: False``）。
        """
        timeout = _resolve_timeout()

        # ---- 银行 ----
        bank_base, bank_base_ok = _resolve_credential(
            "bank_base_url", "ERP_BANK_BASE_URL", "https://bank.example.com/api"
        )
        bank_key, bank_key_ok = _resolve_credential(
            "bank_api_key", "ERP_BANK_API_KEY", _PLACEHOLDER_API_KEY
        )
        bank_secret, _ = _resolve_credential("bank_api_secret", "ERP_BANK_API_SECRET", "")
        bank_configured = bank_base_ok and bank_key_ok
        self.register(
            "bank",
            BankIntegrationAdapter(
                base_url=bank_base,
                api_key=bank_key,
                api_secret=bank_secret,
                timeout=timeout,
                configured=bank_configured,
            ),
            config={"base_url": bank_base, "configured": bank_configured},
        )

        # ---- 税务 ----
        tax_base, tax_base_ok = _resolve_credential(
            "tax_base_url", "ERP_TAX_BASE_URL", "https://etax.example.gov.cn/api"
        )
        tax_key, tax_key_ok = _resolve_credential(
            "tax_api_key", "ERP_TAX_API_KEY", _PLACEHOLDER_API_KEY
        )
        tax_org, _ = _resolve_credential("tax_org_code", "ERP_TAX_ORG_CODE", "")
        tax_configured = tax_base_ok and tax_key_ok
        self.register(
            "tax",
            TaxIntegrationAdapter(
                base_url=tax_base,
                api_key=tax_key,
                org_code=tax_org,
                timeout=timeout,
                configured=tax_configured,
            ),
            config={"base_url": tax_base, "org_code": tax_org, "configured": tax_configured},
        )

        # ---- 电子签章 ----
        esign_base, esign_base_ok = _resolve_credential(
            "esign_base_url", "ERP_ESIGN_BASE_URL", "https://esign.example.com/api"
        )
        esign_key, esign_key_ok = _resolve_credential(
            "esign_api_key", "ERP_ESIGN_API_KEY", _PLACEHOLDER_API_KEY
        )
        esign_app, _ = _resolve_credential("esign_app_id", "ERP_ESIGN_APP_ID", "")
        esign_configured = esign_base_ok and esign_key_ok
        self.register(
            "esign",
            ESignIntegrationAdapter(
                base_url=esign_base,
                api_key=esign_key,
                app_id=esign_app,
                timeout=timeout,
                configured=esign_configured,
            ),
            config={"base_url": esign_base, "app_id": esign_app, "configured": esign_configured},
        )

    # ------------------------------------------------------------------
    # 测试辅助
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        """清空所有注册（仅用于测试夹具 / 重新初始化）"""
        self._adapters.clear()
        self._configs.clear()


# 全局单例：模块导入时自动注册内置集成（无凭据时进入模拟模式）
integration_registry = IntegrationRegistry(auto_register=True)
