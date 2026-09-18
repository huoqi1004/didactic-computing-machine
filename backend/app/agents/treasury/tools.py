"""
资金管家场景工具（路线图 Phase A / A7）
========================================

3 个只读查询 + 1 个高危写：
- ``treasury_fund_transfer_request`` 为 **write_high**：治理面强制人审门
  （LangGraph interrupt → 审批决定 → approved=True 重放），补偿函数按
  handler 返回的 transfer_id 做精确软撤销（status=cancelled）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.db.repositories.base import schedule
from app.db.repositories.treasury_contract_repository import fund_transfer_repository


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("资金Agent")
    if agent is None:
        raise RuntimeError("资金Agent 未启动")
    return agent


class EmptyArgs(BaseModel):
    pass


class OptionalStatusArgs(BaseModel):
    status: str | None = Field(None, description="按状态过滤（如 scheduled/approved/pending_approval）")


@erp_tool(
    name="treasury_cash_balance",
    description="查询当前现金账户余额",
    args_model=EmptyArgs,
    risk_level=ToolRisk.READONLY,
    resource="treasury", action="read",
    agent_domain="treasury",
)
async def treasury_cash_balance(ctx: ToolContext):
    return {"cash_balance": _get_agent().get_cash_balance()}


@erp_tool(
    name="treasury_payment_schedule",
    description="查询应付支付排程列表（采购订单对应的付款计划）",
    args_model=OptionalStatusArgs,
    risk_level=ToolRisk.READONLY,
    resource="treasury", action="read",
    agent_domain="treasury",
)
async def treasury_payment_schedule(ctx: ToolContext, status: str | None = None):
    payments = _get_agent().get_payment_schedule(status=status)
    return {"total": len(payments), "payments": payments[:50]}


@erp_tool(
    name="treasury_fund_transfers",
    description="查询资金调拨记录列表",
    args_model=OptionalStatusArgs,
    risk_level=ToolRisk.READONLY,
    resource="treasury", action="read",
    agent_domain="treasury",
)
async def treasury_fund_transfers(ctx: ToolContext, status: str | None = None):
    transfers = _get_agent().get_fund_transfers(status=status)
    return {"total": len(transfers), "transfers": transfers[:50]}


# ---------------------------------------------------------------------------
# write_high：强制人审门（决策 D6）
# ---------------------------------------------------------------------------

class TransferRequestArgs(BaseModel):
    amount: float = Field(..., gt=0, description="调拨金额（元）")
    from_account: str = Field(..., min_length=1, description="来源账户")
    to_account: str = Field(..., min_length=1, description="目标账户")
    reason: str = Field("", description="调拨原因")


async def _transfer_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确软撤销：按 transfer_id 将调拨置为 cancelled（内存 + DB）。"""
    if not result:
        return
    transfer_id = result.get("transfer_id")
    if not transfer_id:
        return
    agent = _get_agent()
    transfer = agent._fund_transfers.get(transfer_id)
    if transfer is not None:
        transfer["status"] = "cancelled"
        schedule(
            fund_transfer_repository.upsert(transfer, "transfer_id"),
            "fund_transfer_repo",
        )


@erp_tool(
    name="treasury_fund_transfer_request",
    description="发起一笔资金调拨申请（高危操作，需人工审批后才会执行）",
    args_model=TransferRequestArgs,
    risk_level=ToolRisk.WRITE_HIGH,
    resource="treasury", action="create",
    agent_domain="treasury",
    compensation=_transfer_compensation,
    effects_fn=lambda a: (
        f"将发起资金调拨 ¥{a.get('amount')}：{a.get('from_account')} → "
        f"{a.get('to_account')}（{a.get('reason') or '未填写原因'}），"
        "审批通过后创建调拨单并扣减现金余额"
    ),
)
async def treasury_fund_transfer_request(ctx: ToolContext, amount: float,
                                         from_account: str, to_account: str,
                                         reason: str = ""):
    transfer = await _get_agent().request_fund_transfer(
        amount=amount, from_account=from_account, to_account=to_account, reason=reason,
    )
    if transfer is None:
        return {"error": "调拨金额必须大于0"}
    # 同步落库（写工具执行后 DB 即真）
    await fund_transfer_repository.upsert(transfer, key_field="transfer_id")
    return transfer
