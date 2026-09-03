"""
银行接口适配器 - 银企直连（P3-2 企业就绪阶段）
==============================================

对接企业网上银行 / 银企直连平台（如工商银行、建设银行银企直连等）。

能力：
- ``get_balance``         查询账户余额
- ``transfer``            发起转账（记录审计日志）
- ``get_transactions``    查询交易流水

生产环境真实对接（供实施参考）：
- 银企直连通常基于行方提供的 SDK / Web Service，使用企业网银证书（U盾 / USBKey）
  做双向 TLS 与报文签名，本适配器以 REST 风格封装，需经行方 API 网关适配：
  * ``base_url``：行方 API 网关地址（环境变量 ``ERP_BANK_BASE_URL``）
  * ``api_key``：应用标识 / 访问令牌
  * ``api_secret``：证书密钥或签名密钥，用于报文签名（此处作为 ``X-Api-Secret`` 头传递）
- ``transfer`` 属于资金操作，**必须**先走双人复核 / 审批流再调用本方法，
  并核对返回的 ``transfer_id`` 与 ``status``，以银行回执为准
- 所有操作均记录审计日志（structlog，事件名 ``integration.bank.audit_*``）

模拟模式：
- API key 为占位符（``sk-placeholder``）或未配置时进入模拟模式
- 不发起任何网络请求，在内存中维护简易台账（余额随转账增减），并返回 ``mock: True``
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4

import structlog

from app.core.integrations.base import BaseIntegrationAdapter
from app.core.integrations.exceptions import IntegrationError

logger = structlog.get_logger()


class BankIntegrationAdapter(BaseIntegrationAdapter):
    """银企直连适配器"""

    provider = "bank"

    #: 模拟模式默认余额（每个账户）
    _DEFAULT_BALANCE = Decimal("1000000.00")

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        api_secret: str = "",
        timeout: float = 10.0,
        configured: bool = True,
        http_client=None,
    ):
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            timeout=timeout,
            configured=configured,
            http_client=http_client,
        )
        # ---- 模拟模式内部状态（真实模式不使用） ----
        self._mock_ledger: dict[str, Decimal] = {}
        self._mock_transactions: list[dict] = []

    # ------------------------------------------------------------------
    # 查询余额
    # ------------------------------------------------------------------

    async def get_balance(self, account: str) -> dict:
        """
        查询账户余额。

        生产环境对接：``GET {base_url}/api/v1/accounts/{account}/balance``
        返回 ``{"account", "balance", "currency", "updated_at"}``。

        模拟模式：返回默认余额（随转账联动变化）。
        """
        if self.mock_mode:
            return self._mock_get_balance(account)

        data = await self._request("GET", f"/api/v1/accounts/{account}/balance")
        if isinstance(data, dict):
            return {
                "account": account,
                "balance": data.get("balance"),
                "currency": data.get("currency", "CNY"),
                "updated_at": data.get("updated_at", datetime.now(timezone.utc).isoformat()),
            }
        return {
            "account": account,
            "balance": data,
            "currency": "CNY",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _mock_get_balance(self, account: str) -> dict:
        balance = self._mock_ledger.get(account, self._DEFAULT_BALANCE)
        result = {
            "account": account,
            "balance": float(balance),
            "currency": "CNY",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }
        logger.info(
            "integration.bank.get_balance",
            mode="mock",
            account=account,
            balance=float(balance),
        )
        return result

    # ------------------------------------------------------------------
    # 转账
    # ------------------------------------------------------------------

    async def transfer(
        self,
        from_acct: str,
        to_acct: str,
        amount: float,
        reference: str,
    ) -> dict:
        """
        发起转账（银企直连）。

        生产环境对接：``POST {base_url}/api/v1/transfers``
        请求体：``{"from_account", "to_account", "amount", "reference"}``
        响应：``{"transaction_id", "from_account", "to_account", "amount",
        "currency", "reference", "status", "timestamp"}``

        注意事项（生产）：
        - 资金操作必须经审批流后方可调用；以银行回执 ``transaction_id`` 为准
        - 使用 ``api_secret``（证书密钥）签名报文，失败时抛出 ``IntegrationAuthError``
        - 每次转账均记录审计日志（事件 ``integration.bank.audit_transfer``）

        模拟模式：更新内存台账并返回模拟回执（``transaction_id`` 以 ``mock-txn-`` 开头）。
        """
        try:
            amount_dec = Decimal(str(amount))
        except Exception as exc:  # noqa: BLE001 - 非法金额
            raise IntegrationError(
                f"转账金额非法: {amount!r}",
                provider=self.provider,
                detail={"from_acct": from_acct, "to_acct": to_acct},
            ) from exc
        if amount_dec <= 0:
            raise IntegrationError(
                "转账金额必须大于 0",
                provider=self.provider,
                detail={"from_acct": from_acct, "to_acct": to_acct, "amount": float(amount_dec)},
            )

        if self.mock_mode:
            return self._mock_transfer(from_acct, to_acct, amount_dec, reference)

        data = await self._request(
            "POST",
            "/api/v1/transfers",
            json={
                "from_account": from_acct,
                "to_account": to_acct,
                "amount": float(amount_dec),
                "reference": reference,
            },
        )
        # 审计日志：资金操作必须留痕
        logger.info(
            "integration.bank.audit_transfer",
            action="transfer",
            from_acct=from_acct,
            to_acct=to_acct,
            amount=float(amount_dec),
            reference=reference,
            transaction_id=data.get("transaction_id") if isinstance(data, dict) else None,
        )
        if isinstance(data, dict):
            return data
        return {
            "transaction_id": str(data),
            "from_account": from_acct,
            "to_account": to_acct,
            "amount": float(amount_dec),
            "reference": reference,
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _mock_transfer(self, from_acct: str, to_acct: str, amount: Decimal, reference: str) -> dict:
        # 更新模拟台账
        self._mock_ledger[from_acct] = self._mock_ledger.get(from_acct, self._DEFAULT_BALANCE) - amount
        self._mock_ledger[to_acct] = self._mock_ledger.get(to_acct, self._DEFAULT_BALANCE) + amount

        now = datetime.now(timezone.utc).isoformat()
        transaction = {
            "transaction_id": f"mock-txn-{uuid4().hex[:12]}",
            "from_account": from_acct,
            "to_account": to_acct,
            "amount": float(amount),
            "currency": "CNY",
            "reference": reference,
            "status": "success",
            "timestamp": now,
            "mock": True,
        }
        self._mock_transactions.append(transaction)
        # 审计日志：资金操作必须留痕
        logger.info(
            "integration.bank.audit_transfer",
            action="transfer",
            mode="mock",
            from_acct=from_acct,
            to_acct=to_acct,
            amount=float(amount),
            reference=reference,
            transaction_id=transaction["transaction_id"],
        )
        return transaction

    # ------------------------------------------------------------------
    # 交易流水
    # ------------------------------------------------------------------

    async def get_transactions(
        self,
        account: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        """
        查询账户交易流水。

        生产环境对接：``GET {base_url}/api/v1/accounts/{account}/transactions``
        查询参数：``date_from`` / ``date_to``（ISO 日期，如 ``2026-08-01``）。

        模拟模式：返回预置示例流水 + 本次会话内通过 ``transfer`` 产生的记录
        （按账户与日期范围过滤）。
        """
        if self.mock_mode:
            return self._mock_get_transactions(account, date_from, date_to)

        data = await self._request(
            "GET",
            f"/api/v1/accounts/{account}/transactions",
            params={"date_from": date_from, "date_to": date_to},
        )
        if isinstance(data, dict):
            return data.get("transactions") or data.get("items") or []
        if isinstance(data, list):
            return data
        return []

    def _mock_get_transactions(
        self,
        account: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        # 预置示例流水（演示用）
        samples = [
            {
                "transaction_id": "txn-0001",
                "account": account,
                "direction": "in",
                "amount": 50000.0,
                "currency": "CNY",
                "counterparty": "客户A",
                "reference": "货款-2026-08",
                "status": "settled",
                "timestamp": "2026-08-01T10:00:00+08:00",
                "mock": True,
            },
            {
                "transaction_id": "txn-0002",
                "account": account,
                "direction": "out",
                "amount": 8000.0,
                "currency": "CNY",
                "counterparty": "供应商B",
                "reference": "采购付款",
                "status": "settled",
                "timestamp": "2026-08-03T14:30:00+08:00",
                "mock": True,
            },
        ]
        transferred = [
            t for t in self._mock_transactions
            if t["from_account"] == account or t["to_account"] == account
        ]
        transactions = samples + transferred

        if date_from:
            transactions = [t for t in transactions if t["timestamp"] >= date_from]
        if date_to:
            transactions = [t for t in transactions if t["timestamp"] <= date_to]

        logger.info(
            "integration.bank.get_transactions",
            mode="mock",
            account=account,
            count=len(transactions),
        )
        return transactions
