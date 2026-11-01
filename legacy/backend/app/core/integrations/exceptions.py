"""
外部系统集成异常定义（P3-2 企业就绪阶段）
=========================================

异常层次结构：
- IntegrationError                  （基类）
    - IntegrationAuthError          认证失败（HTTP 401/403、令牌过期、证书错误）
    - IntegrationTimeoutError       请求超时（连接 / 读取 / 总耗时超限）
    - IntegrationConfigError        配置错误（未注册、凭据缺失、参数非法、税种不支持）

设计原则（与 ``app.core.blockchain.exceptions`` 保持一致）：
- 所有异常均携带 ``provider``（触发方，如 bank/tax/esign/registry）与 ``detail`` 上下文
- 调用方应优先捕获具体异常，最后兜底捕获 ``IntegrationError``
- 模拟模式（mock mode）下不发起网络请求，因此不会抛出网络类异常；
  仅可能抛出参数校验类 ``IntegrationConfigError`` / 业务类 ``IntegrationError``
"""
from typing import Any, Optional


class IntegrationError(Exception):
    """外部系统集成错误基类（不应直接抛出，应抛出具体子类）"""

    def __init__(
        self,
        message: str = "",
        *,
        provider: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.detail = detail or {}

    def __str__(self) -> str:
        prefix = f"[{self.provider}] " if self.provider else ""
        if self.detail:
            return f"{prefix}{self.message} detail={self.detail}"
        return f"{prefix}{self.message}"


class IntegrationAuthError(IntegrationError):
    """
    认证失败。

    触发场景：
    - API Key / Secret 无效或被吊销
    - 请求返回 HTTP 401 / 403
    - 证书过期或签名校验失败（银企直连证书、电子税务局 UKey）
    """


class IntegrationTimeoutError(IntegrationError):
    """
    请求超时。

    触发场景：
    - 连接建立超时（ConnectTimeout）
    - 读取响应超时（ReadTimeout）
    - 超过配置的 ``timeout`` 秒仍未完成
    """


class IntegrationConfigError(IntegrationError):
    """
    配置错误。

    触发场景：
    - 向注册中心获取未注册的集成（``IntegrationRegistry.get``）
    - 适配器缺少必要凭据 / base_url
    - 参数非法（如转账金额 ≤ 0、签署人列表为空）
    - 不支持的税种类型
    """
