"""
电子签章适配器 - e签宝 / 法大大等签署服务（P3-2 企业就绪阶段）
==============================================================

对接第三方电子签章开放平台（e签宝、法大大、上上签等）。

能力：
- ``create_sign_request``        创建签署任务
- ``check_sign_status``          查询签署状态
- ``download_signed_document``   下载已签署文档（PDF 字节流）

生产环境真实对接（供实施参考）：
- 各签署平台开放平台 API 风格相近，通常基于 OAuth2 / AppKey+AppSecret 签名鉴权：
  * ``base_url``：签署平台 API 地址（环境变量 ``ERP_ESIGN_BASE_URL``）
  * ``api_key``：App Secret / AccessToken（占位符时进入模拟模式）
  * ``app_id``：应用标识（AppKey / ClientId），用于鉴权与回调校验
- ``create_sign_request`` 成功后应轮询 ``check_sign_status`` 直至 ``completed``，
  再调用 ``download_signed_document`` 获取签署后 PDF；文档需本地留存并归档
- 签署通知 / 结果回执通常还包含回调（webhook）机制，生产环境应配合验签

模拟模式：
- API key 为占位符（``sk-placeholder``）或未配置时进入模拟模式
- 首次查询签署状态即模拟完成签署，随后可下载模拟 PDF 字节流
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import structlog

from app.core.integrations.base import BaseIntegrationAdapter
from app.core.integrations.exceptions import IntegrationConfigError, IntegrationError

logger = structlog.get_logger()


class ESignIntegrationAdapter(BaseIntegrationAdapter):
    """电子签章服务适配器"""

    provider = "esign"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        app_id: str = "",
        timeout: float = 10.0,
        configured: bool = True,
        http_client=None,
    ):
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            api_secret="",
            timeout=timeout,
            configured=configured,
            http_client=http_client,
        )
        #: 签署平台应用标识（AppKey / ClientId）
        self.app_id = app_id or ""
        # ---- 模拟模式内部状态（真实模式不使用） ----
        self._mock_requests: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 创建签署任务
    # ------------------------------------------------------------------

    async def create_sign_request(self, document_id: str, signers: list) -> dict:
        """
        创建签署任务。

        :param document_id: 待签署文档标识（合同 / 单据）
        :param signers: 签署人列表，元素为 ``{"name": ..., "mobile": ...}``
                        或简单的姓名字符串

        生产环境对接：``POST {base_url}/api/v1/sign-requests``
        请求体：``{"document_id", "signers", "app_id"}``
        响应：``{"request_id", "document_id", "signers", "status", "created_at"}``

        模拟模式：返回 ``request_id``（``mock-sign-`` 前缀）与状态 ``pending``。
        """
        if not isinstance(signers, (list, tuple)) or len(signers) == 0:
            raise IntegrationConfigError(
                "签署人列表不能为空",
                provider=self.provider,
                detail={"document_id": document_id},
            )

        if self.mock_mode:
            return self._mock_create(document_id, list(signers))

        data = await self._request(
            "POST",
            "/api/v1/sign-requests",
            json={
                "document_id": document_id,
                "signers": list(signers),
                "app_id": self.app_id,
            },
        )
        if isinstance(data, dict):
            return data
        return {
            "request_id": str(data),
            "document_id": document_id,
            "signers": list(signers),
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _mock_create(self, document_id: str, signers: list) -> dict:
        request_id = f"mock-sign-{uuid4().hex[:12]}"
        record = {
            "request_id": request_id,
            "document_id": document_id,
            "signers": signers,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }
        self._mock_requests[request_id] = record
        logger.info(
            "integration.esign.create_sign_request",
            mode="mock",
            request_id=request_id,
            document_id=document_id,
            signer_count=len(signers),
        )
        return dict(record)

    # ------------------------------------------------------------------
    # 查询签署状态
    # ------------------------------------------------------------------

    async def check_sign_status(self, request_id: str) -> dict:
        """
        查询签署状态。

        生产环境对接：``GET {base_url}/api/v1/sign-requests/{request_id}``
        状态取值示例：``pending``（待签署）/ ``processing``（签署中）/
        ``completed``（已完成）/ ``cancelled``（已撤销）。

        模拟模式：首次查询即模拟完成签署（状态推进到 ``completed``），
        并附带 ``signed_at`` 与 ``signed_file_name``。
        """
        if self.mock_mode:
            record = self._mock_requests.get(request_id)
            if record is None:
                raise IntegrationError(
                    f"签署请求不存在: {request_id}",
                    provider=self.provider,
                    detail={"request_id": request_id},
                )
            if record["status"] == "pending":
                record["status"] = "completed"
                record["signed_at"] = datetime.now(timezone.utc).isoformat()
                record["signed_file_name"] = f"{record['document_id']}-signed.pdf"
            logger.info(
                "integration.esign.check_sign_status",
                mode="mock",
                request_id=request_id,
                status=record["status"],
            )
            return dict(record)

        data = await self._request("GET", f"/api/v1/sign-requests/{request_id}")
        if isinstance(data, dict):
            return data
        return {"request_id": request_id, "status": str(data)}

    # ------------------------------------------------------------------
    # 下载已签署文档
    # ------------------------------------------------------------------

    async def download_signed_document(self, request_id: str) -> bytes:
        """
        下载已签署文档（PDF 字节流）。

        生产环境对接：``GET {base_url}/api/v1/sign-requests/{request_id}/document``
        （二进制响应），应仅在 ``check_sign_status`` 返回 ``completed`` 后调用。

        模拟模式：签署完成后返回一段模拟 PDF 字节流；未完成或不存在时抛出
        :class:`IntegrationError`。
        """
        if self.mock_mode:
            return self._mock_download(request_id)

        return await self._request_bytes(
            "GET", f"/api/v1/sign-requests/{request_id}/document"
        )

    def _mock_download(self, request_id: str) -> bytes:
        record = self._mock_requests.get(request_id)
        if record is None:
            raise IntegrationError(
                f"签署请求不存在: {request_id}",
                provider=self.provider,
                detail={"request_id": request_id},
            )
        if record["status"] != "completed":
            raise IntegrationError(
                "签署尚未完成，无法下载文档",
                provider=self.provider,
                detail={"request_id": request_id, "status": record["status"]},
            )
        content = (
            f"%PDF-1.4\n% mock signed document\nrequest_id={request_id}\n"
            f"document_id={record['document_id']}\nstatus=completed\n%%EOF\n"
        ).encode("utf-8")
        logger.info(
            "integration.esign.download_signed_document",
            mode="mock",
            request_id=request_id,
            size_bytes=len(content),
        )
        return content
