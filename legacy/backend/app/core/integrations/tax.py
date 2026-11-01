"""
税务接口适配器 - 电子税务局（P3-2 企业就绪阶段）
================================================

对接电子税务局 / 金税系统的网上办税接口。

能力：
- ``submit_tax_return``  申报缴税（支持增值税 / 企业所得税等税种）
- ``query_tax_status``   查询申报状态
- ``get_tax_rates``      获取税率表

生产环境真实对接（供实施参考）：
- 电子税务局通常要求：实名办税（法人/财务负责人身份）、UKey / CA 证书、
  申报表模板（表样版本号），报文按税局接口规范（XML/JSON）组装
- 本适配器以 REST 风格封装，需经税局开放平台 / 服务商网关适配：
  * ``base_url``：电子税务局 API 地址（环境变量 ``ERP_TAX_BASE_URL``）
  * ``api_key``：办税应用令牌
  * ``org_code``：纳税人识别号（统一社会信用代码），用于身份标识
- ``submit_tax_return`` 成功后应轮询 ``query_tax_status`` 直至最终状态
  （受理 → 入库 → 扣款成功/失败）

模拟模式：
- API key 为占位符（``sk-placeholder``）或未配置时进入模拟模式
- 不发起任何网络请求，返回模拟申报记录与内置税率表（``mock: True``）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import structlog

from app.core.integrations.base import BaseIntegrationAdapter
from app.core.integrations.exceptions import IntegrationConfigError

logger = structlog.get_logger()

#: 支持的税种类型：key 为接口标识，value 为中文名称
TAX_TYPES = {
    "vat": "增值税",
    "corporate_income": "企业所得税",
    "individual_income": "个人所得税",
    "stamp": "印花税",
}

#: 内置税率表（模拟模式使用；真实模式以税局返回为准）
_TAX_RATES: dict[str, list[dict]] = {
    "vat": [
        {"label": "销售货物、加工修理修配劳务、有形动产租赁（一般纳税人）", "rate": 0.13},
        {"label": "交通运输、邮政、基础电信、建筑、不动产租赁销售", "rate": 0.09},
        {"label": "现代服务、增值电信、金融、生活服务、销售无形资产", "rate": 0.06},
        {"label": "小规模纳税人征收率", "rate": 0.03},
    ],
    "corporate_income": [
        {"label": "标准税率", "rate": 0.25},
        {"label": "高新技术企业优惠税率", "rate": 0.15},
        {"label": "小型微利企业实际税负", "rate": 0.20},
    ],
    "individual_income": [
        {"label": "综合所得 3%-45% 超额累进（简化示例）", "rate": 0.03},
        {"label": "经营所得 5%-35% 超额累进（简化示例）", "rate": 0.05},
    ],
    "stamp": [
        {"label": "购销合同", "rate": 0.0003},
        {"label": "借款合同", "rate": 0.00005},
    ],
}


class TaxIntegrationAdapter(BaseIntegrationAdapter):
    """电子税务局接口适配器"""

    provider = "tax"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        org_code: str = "",
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
        #: 纳税人识别号（统一社会信用代码），真实申报时必填
        self.org_code = org_code or ""
        # ---- 模拟模式内部状态（真实模式不使用） ----
        self._mock_submissions: dict[str, dict] = {}

    def _validate_tax_type(self, tax_type: str) -> None:
        if tax_type not in TAX_TYPES:
            raise IntegrationConfigError(
                f"不支持的税种类型: {tax_type}",
                provider=self.provider,
                detail={"supported": sorted(TAX_TYPES)},
            )

    # ------------------------------------------------------------------
    # 申报缴税
    # ------------------------------------------------------------------

    async def submit_tax_return(
        self,
        period: str,
        amount: float,
        tax_type: str = "vat",
    ) -> dict:
        """
        申报缴税。

        :param period: 所属期（如 ``2026-08``）
        :param amount: 应缴税额（元）
        :param tax_type: 税种类型，支持：``vat`` 增值税 / ``corporate_income``
                         企业所得税 / ``individual_income`` 个人所得税 / ``stamp`` 印花税

        生产环境对接：``POST {base_url}/api/v1/tax/returns``
        请求体：``{"period", "amount", "tax_type", "org_code"}``
        响应：``{"submission_id", "status", "tax_type", "period", "amount", "submitted_at"}``

        成功后应轮询 :meth:`query_tax_status` 直至终态（入库 / 扣款结果）。
        模拟模式：返回 ``submission_id``（``mock-tax-`` 前缀）与状态 ``submitted``。
        """
        self._validate_tax_type(tax_type)

        if self.mock_mode:
            return self._mock_submit(period, float(amount), tax_type)

        data = await self._request(
            "POST",
            "/api/v1/tax/returns",
            json={
                "period": period,
                "amount": float(amount),
                "tax_type": tax_type,
                "org_code": self.org_code,
            },
        )
        if isinstance(data, dict):
            return data
        return {
            "submission_id": str(data),
            "status": "submitted",
            "tax_type": tax_type,
            "period": period,
            "amount": float(amount),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def _mock_submit(self, period: str, amount: float, tax_type: str) -> dict:
        submission_id = f"mock-tax-{uuid4().hex[:12]}"
        record = {
            "submission_id": submission_id,
            "period": period,
            "amount": amount,
            "tax_type": tax_type,
            "tax_type_name": TAX_TYPES.get(tax_type, tax_type),
            "status": "submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }
        self._mock_submissions[submission_id] = record
        logger.info(
            "integration.tax.submit_tax_return",
            mode="mock",
            submission_id=submission_id,
            period=period,
            tax_type=tax_type,
            amount=amount,
        )
        return dict(record)

    # ------------------------------------------------------------------
    # 查询申报状态
    # ------------------------------------------------------------------

    async def query_tax_status(self, submission_id: str) -> dict:
        """
        查询申报状态。

        生产环境对接：``GET {base_url}/api/v1/tax/submissions/{submission_id}``
        状态取值示例：``submitted``（已受理）/ ``processing``（处理中）/
        ``completed``（入库完成）/ ``failed``（失败，含失败原因）。

        模拟模式：返回与申报时一致的状态；未知提交号返回 ``status: not_found``。
        """
        if self.mock_mode:
            record = self._mock_submissions.get(submission_id)
            if record is None:
                return {
                    "submission_id": submission_id,
                    "status": "not_found",
                    "mock": True,
                }
            return dict(record)

        data = await self._request("GET", f"/api/v1/tax/submissions/{submission_id}")
        if isinstance(data, dict):
            return data
        return {"submission_id": submission_id, "status": str(data)}

    # ------------------------------------------------------------------
    # 税率表
    # ------------------------------------------------------------------

    async def get_tax_rates(self, tax_type: Optional[str] = None) -> dict:
        """
        获取税率表。

        :param tax_type: 税种类型（None 返回全部税种税率）

        生产环境对接：``GET {base_url}/api/v1/tax/rates?tax_type=vat``
        模拟模式：返回内置税率表（``mock: True``）。
        """
        if tax_type is not None:
            self._validate_tax_type(tax_type)

        if self.mock_mode:
            return self._mock_get_tax_rates(tax_type)

        data = await self._request(
            "GET", "/api/v1/tax/rates", params={"tax_type": tax_type or ""}
        )
        if isinstance(data, dict):
            return data
        return {"rates": data}

    def _mock_get_tax_rates(self, tax_type: Optional[str]) -> dict:
        if tax_type is not None:
            result = {"rates": _TAX_RATES[tax_type], "tax_type": tax_type, "mock": True}
        else:
            result = {"rates": {k: list(v) for k, v in _TAX_RATES.items()}, "mock": True}
        logger.info(
            "integration.tax.get_tax_rates",
            mode="mock",
            tax_type=tax_type or "all",
            count=len(result["rates"]),
        )
        return result
