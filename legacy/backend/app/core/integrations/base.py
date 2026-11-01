"""
外部系统集成适配器基类（P3-2 企业就绪阶段）
===========================================

提供所有外部系统适配器（银行 / 税务 / 电子签章）共享的基础能力：

- 凭据与配置管理（``base_url`` / ``api_key`` / ``timeout``）
- **模拟模式（mock mode）判定**：``configured=False`` 或 API key 为空 /
  包含占位符（如 ``sk-placeholder``）时自动进入模拟模式，
  不发起任何真实网络请求，返回合理模拟数据并记录日志
- 异步 HTTP 客户端管理（``httpx.AsyncClient``，懒加载，可注入自定义客户端）
- 统一请求封装与异常翻译（网络错误 / 认证失败 / 超时）
- 健康检查（``health_check``）

约束：
- 所有网络请求均为异步（httpx.AsyncClient）
- 超时使用 httpx timeout 参数（同时兼容 Python 3.10，不使用 3.11+ 的 ``asyncio.timeout``）
- 日志统一使用 structlog，事件名以 ``integration.`` 前缀

模拟模式约定：
- 模拟模式下的所有返回值均携带 ``"mock": True`` 标识，便于调用方识别
- 模拟模式绝不调用 ``_request`` / ``_request_bytes``（测试可据此断言无网络请求）
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx
import structlog

from app.core.integrations.exceptions import (
    IntegrationAuthError,
    IntegrationError,
    IntegrationTimeoutError,
)

logger = structlog.get_logger()

# 占位符 API Key 特征（不区分大小写）
_PLACEHOLDER_MARKERS = (
    "placeholder",
    "sk-placeholder",
    "replace-me",
    "changeme",
    "your-api-key",
)


class BaseIntegrationAdapter:
    """外部系统集成适配器基类（所有适配器必须继承）"""

    #: 提供商标识，子类必须覆盖（如 "bank" / "tax" / "esign"）
    provider: str = "generic"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        api_secret: str = "",
        timeout: float = 10.0,
        configured: bool = True,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        :param base_url: 外部系统 API 根地址（可配置，如 ``ERP_BANK_BASE_URL``）
        :param api_key:  API Key / 令牌（占位符或为空时进入模拟模式）
        :param api_secret: 附加密钥（银行证书密钥 / 签名密钥等，可为空）
        :param timeout:  请求超时秒数（httpx timeout 参数）
        :param configured: 显式标记是否具备真实对接凭据（False 强制进入模拟模式）
        :param http_client: 注入的 ``httpx.AsyncClient``（测试 / 高级用法）；
                            None 时懒加载自建客户端（默认使用 ``base_url``）
        """
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.timeout = float(timeout)
        self._configured = bool(configured)
        self._http_client = http_client
        self._owns_client = http_client is None

    # ==================================================================
    # 配置与模式判定
    # ==================================================================

    @property
    def name(self) -> str:
        """适配器名称（默认等于 provider 标识）"""
        return self.provider

    @property
    def configured(self) -> bool:
        """是否具备真实对接凭据（configured 标记 + 非占位符 API Key）"""
        return self._configured and self._has_real_key()

    def _has_real_key(self) -> bool:
        key = (self.api_key or "").strip().lower()
        if not key:
            return False
        return not any(marker in key for marker in _PLACEHOLDER_MARKERS)

    @property
    def mock_mode(self) -> bool:
        """是否处于模拟模式（未配置或占位符凭据 → 不发起真实网络请求）"""
        return not self.configured

    # ==================================================================
    # HTTP 客户端管理
    # ==================================================================

    def _build_headers(self) -> dict[str, str]:
        """构建默认请求头（子类可覆盖以适配厂商鉴权方式）"""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "erp-integration/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.api_secret:
            headers["X-Api-Secret"] = self.api_secret
        return headers

    def _get_client(self) -> httpx.AsyncClient:
        """获取（懒加载）httpx.AsyncClient；已注入客户端则直接复用"""
        if self._http_client is not None:
            return self._http_client
        self._http_client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=self._build_headers(),
            follow_redirects=True,
        )
        return self._http_client

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        """
        发起真实 HTTP 请求，并翻译异常为集成异常。

        - ``httpx.TimeoutException`` / ``asyncio.TimeoutError`` → ``IntegrationTimeoutError``
        - ``httpx.TransportError``（连接失败等）→ ``IntegrationError``

        仅应在非模拟模式下调用；模拟模式不会走到这里。
        """
        client = self._get_client()
        request_timeout = timeout if timeout is not None else self.timeout
        try:
            return await client.request(
                method,
                path,
                params=params,
                json=json,
                timeout=request_timeout,
            )
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise IntegrationTimeoutError(
                f"{self.provider} 请求超时: {method} {path}",
                provider=self.provider,
                detail={
                    "method": method,
                    "path": path,
                    "timeout": request_timeout,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            ) from exc
        except httpx.TransportError as exc:
            raise IntegrationError(
                f"{self.provider} 网络请求失败: {method} {path}",
                provider=self.provider,
                detail={
                    "method": method,
                    "path": path,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            ) from exc

    def _raise_for_status(self, response: httpx.Response, method: str, path: str) -> None:
        """HTTP 状态码 → 集成异常（401/403 → 认证失败，其余 ≥400 → 通用错误）"""
        if response.status_code in (401, 403):
            raise IntegrationAuthError(
                f"{self.provider} 认证失败（HTTP {response.status_code}）",
                provider=self.provider,
                detail={
                    "status_code": response.status_code,
                    "method": method,
                    "path": path,
                },
            )
        if response.status_code >= 400:
            raise IntegrationError(
                f"{self.provider} 请求失败（HTTP {response.status_code}）",
                provider=self.provider,
                detail={
                    "status_code": response.status_code,
                    "method": method,
                    "path": path,
                    "body": (response.text or "")[:500],
                },
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """
        发起真实 HTTP 请求并返回解析后的 JSON / 文本（仅在非模拟模式使用）。
        """
        response = await self._send(method, path, params=params, json=json, timeout=timeout)
        self._raise_for_status(response, method, path)
        try:
            return response.json()
        except Exception:  # noqa: BLE001 - 非 JSON 响应降级返回文本
            return response.text

    async def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> bytes:
        """
        发起真实 HTTP 请求并返回原始字节流（如下载 PDF 文档，仅在非模拟模式使用）。
        """
        response = await self._send(method, path, params=params, json=json, timeout=timeout)
        self._raise_for_status(response, method, path)
        return response.content

    # ==================================================================
    # 健康检查 / 生命周期
    # ==================================================================

    async def health_check(self) -> dict:
        """
        连接健康检查。

        模拟模式：不发起网络请求，直接返回 ``{"status": "ok", "mode": "mock"}``。
        真实模式：调用 ``GET /health``（子类可覆盖），失败时返回
        ``{"status": "error", ...}`` 而不抛异常（便于注册中心汇总）。
        """
        if self.mock_mode:
            logger.info(
                "integration.health_check",
                provider=self.provider,
                mode="mock",
                configured=False,
            )
            return {
                "status": "ok",
                "provider": self.provider,
                "mode": "mock",
                "configured": False,
                "latency_ms": 0,
            }

        try:
            start = time.monotonic()
            await self._request("GET", "/health")
            latency_ms = int((time.monotonic() - start) * 1000)
        except IntegrationError as exc:
            logger.warning(
                "integration.health_check_failed",
                provider=self.provider,
                mode="live",
                error=str(exc),
            )
            return {
                "status": "error",
                "provider": self.provider,
                "mode": "live",
                "configured": True,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

        logger.info(
            "integration.health_check",
            provider=self.provider,
            mode="live",
            configured=True,
            latency_ms=latency_ms,
        )
        return {
            "status": "ok",
            "provider": self.provider,
            "mode": "live",
            "configured": True,
            "latency_ms": latency_ms,
        }

    async def close(self) -> None:
        """关闭自建的 httpx 客户端（注入的客户端由调用方负责关闭）"""
        if self._owns_client and self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> "BaseIntegrationAdapter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
