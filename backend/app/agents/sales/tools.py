"""
销售助手场景工具（Phase B 批量推广）
====================================

3 查询 + 2 写（下单/确认 write_low，精确补偿）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.db.repositories.base import schedule
from app.db.repositories.sales_repository import sales_order_repository


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("销售Agent")
    if agent is None:
        raise RuntimeError("销售Agent 未启动")
    return agent


class QueryOrdersArgs(BaseModel):
    status: str | None = Field(None, description="按状态过滤（pending/confirmed）")
    customer_id: str | None = Field(None, description="按客户编号过滤")
    limit: int = Field(20, ge=1, le=100)


@erp_tool(
    name="sales_query_orders",
    description="查询销售订单列表，可按状态与客户过滤",
    args_model=QueryOrdersArgs,
    risk_level=ToolRisk.READONLY, resource="sales", action="read",
    agent_domain="sales",
)
async def sales_query_orders(ctx: ToolContext, status: str | None = None,
                             customer_id: str | None = None, limit: int = 20):
    orders = _get_agent().get_orders(status=status, customer_id=customer_id)
    return {"total": len(orders), "orders": orders[:limit]}


class QueryOrderArgs(BaseModel):
    order_id: str = Field(..., min_length=1, description="订单编号（如 SO-xxxxxxxx）")


@erp_tool(
    name="sales_query_order_detail",
    description="按编号查询单条销售订单详情",
    args_model=QueryOrderArgs,
    risk_level=ToolRisk.READONLY, resource="sales", action="read",
    agent_domain="sales",
)
async def sales_query_order_detail(ctx: ToolContext, order_id: str):
    order = _get_agent().get_order(order_id)
    if order is None:
        return {"error": f"订单不存在: {order_id}"}
    return order


class QueryTargetArgs(BaseModel):
    salesperson_id: str = Field(..., min_length=1, description="销售员编号")
    period: str = Field(..., min_length=4, description="周期（如 2026-Q3）")


@erp_tool(
    name="sales_query_target_progress",
    description="查询销售员的目标完成率",
    args_model=QueryTargetArgs,
    risk_level=ToolRisk.READONLY, resource="sales", action="read",
    agent_domain="sales",
)
async def sales_query_target_progress(ctx: ToolContext, salesperson_id: str,
                                      period: str):
    return _get_agent().get_target_progress(salesperson_id, period)


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------

class CreateOrderArgs(BaseModel):
    customer_id: str = Field(..., min_length=1, description="客户编号（如 CUST-SEED-001）")
    total_amount: float = Field(..., gt=0, description="订单总金额（元）")
    items: list[dict] = Field(..., min_length=1,
                              description="订单行项：[{product_id, product_name, qty, unit_price}]")
    delivery_date: str = Field("", description="交付日期（YYYY-MM-DD，选填）")


async def _create_order_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：按 order_id 删库 + 清 Agent 内存。"""
    if not result or not result.get("order_id"):
        return
    order_id = result["order_id"]
    agent = _get_agent()
    agent._orders.pop(order_id, None)
    await sales_order_repository.delete_by_id(order_id)


@erp_tool(
    name="sales_create_order",
    description="创建销售订单（关联客户，生成订单号，状态待确认）",
    args_model=CreateOrderArgs,
    risk_level=ToolRisk.WRITE_LOW, resource="sales", action="create",
    agent_domain="sales",
    compensation=_create_order_compensation,
    effects_fn=lambda a: (
        f"将为客户[{a.get('customer_id')}]创建销售订单，"
        f"总金额 ¥{a.get('total_amount')}，行项 {len(a.get('items') or [])} 条"
    ),
)
async def sales_create_order(ctx: ToolContext, customer_id: str, total_amount: float,
                             items: list[dict], delivery_date: str = ""):
    order = await _get_agent().create_order(
        customer_id=customer_id, items=items,
        total_amount=total_amount, delivery_date=delivery_date,
    )
    await sales_order_repository.upsert(order, "order_id")
    return order


class ConfirmOrderArgs(BaseModel):
    order_id: str = Field(..., min_length=1, description="订单编号")


async def _confirm_order_compensation(ctx: ToolContext, args: dict, result) -> None:
    """回滚：状态退回 pending。"""
    if not result or not result.get("order_id"):
        return
    order_id = result["order_id"]
    agent = _get_agent()
    if order_id in agent._orders:
        agent._orders[order_id]["status"] = "pending"
        agent._orders[order_id]["confirmed_at"] = None
    await sales_order_repository.update_status(order_id, "pending", confirmed_at=None)


@erp_tool(
    name="sales_confirm_order",
    description="确认销售订单（pending → confirmed，触发后续履约流程）",
    args_model=ConfirmOrderArgs,
    risk_level=ToolRisk.WRITE_LOW, resource="sales", action="update",
    agent_domain="sales",
    compensation=_confirm_order_compensation,
    effects_fn=lambda a: f"将确认订单[{a.get('order_id')}]，状态从待确认变为已确认",
)
async def sales_confirm_order(ctx: ToolContext, order_id: str):
    try:
        order = await _get_agent().confirm_order(order_id)
    except ValueError as e:
        return {"error": str(e)}
    from datetime import datetime, timezone

    await sales_order_repository.update_status(
        order_id, "confirmed", confirmed_at=datetime.now(timezone.utc),
    )
    return order
