"""
外部系统集成适配器模块（P3-2 企业就绪阶段）
============================================

提供与外部系统的集成能力，当前包含：

- 银行接口适配器（银企直连）：余额查询 / 转账 / 流水查询
- 税务接口适配器（电子税务局）：申报缴税 / 状态查询 / 税率表
- 电子签章适配器（e签宝 / 法大大）：创建签署 / 状态查询 / 下载签署文档
- 集成注册中心：统一管理连接、连接测试与健康检查

设计要点：
- 所有网络请求均为异步（httpx.AsyncClient）
- 未配置真实凭据（API key 为占位符 / 缺失）时自动进入**模拟模式**，
  不发起网络请求并返回合理模拟数据，保证开发环境与 CI 离线可运行
- 日志统一使用 structlog，事件名以 ``integration.`` 前缀
- 异常统一使用 :mod:`app.core.integrations.exceptions`

快速开始：:

    from app.core.integrations import integration_registry

    bank = integration_registry.get("bank")          # 模拟模式（开发环境）
    balance = await bank.get_balance("622200001234") # -> {"mock": True, ...}
    await integration_registry.health_check()        # -> {"overall_status": "healthy", ...}
"""
from app.core.integrations.base import BaseIntegrationAdapter
from app.core.integrations.bank import BankIntegrationAdapter
from app.core.integrations.esign import ESignIntegrationAdapter
from app.core.integrations.exceptions import (
    IntegrationAuthError,
    IntegrationConfigError,
    IntegrationError,
    IntegrationTimeoutError,
)
from app.core.integrations.registry import IntegrationRegistry, integration_registry
from app.core.integrations.tax import TaxIntegrationAdapter

__all__ = [
    "BaseIntegrationAdapter",
    "BankIntegrationAdapter",
    "TaxIntegrationAdapter",
    "ESignIntegrationAdapter",
    "IntegrationRegistry",
    "integration_registry",
    "IntegrationError",
    "IntegrationAuthError",
    "IntegrationTimeoutError",
    "IntegrationConfigError",
]
