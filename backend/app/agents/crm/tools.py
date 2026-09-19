"""
客户关系助手场景工具（Phase B 批量推广）
========================================

3 查询 + 1 写（新增客户 write_low，精确补偿）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.db.repositories.sales_repository import customer_repository


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("客户关系Agent")
    if agent is None:
        raise RuntimeError("客户关系Agent 未启动")
    return agent


class QueryCustomersArgs(BaseModel):
    segment: str | None = Field(None, description="按分级过滤（A/B/C）")
    limit: int = Field(20, ge=1, le=100)


@erp_tool(
    name="crm_query_customers",
    description="查询客户列表，可按分级过滤",
    args_model=QueryCustomersArgs,
    risk_level=ToolRisk.READONLY, resource="crm", action="read",
    agent_domain="crm",
)
async def crm_query_customers(ctx: ToolContext, segment: str | None = None,
                              limit: int = 20):
    customers = _get_agent().get_customers(segment=segment)
    return {"total": len(customers), "customers": customers[:limit]}


class QueryCustomerArgs(BaseModel):
    customer_id: str = Field(..., min_length=1, description="客户编号")


@erp_tool(
    name="crm_query_customer_detail",
    description="按编号查询客户详情（含联系人与商机）",
    args_model=QueryCustomerArgs,
    risk_level=ToolRisk.READONLY, resource="crm", action="read",
    agent_domain="crm",
)
async def crm_query_customer_detail(ctx: ToolContext, customer_id: str):
    customer = _get_agent().get_customer(customer_id)
    if customer is None:
        return {"error": f"客户不存在: {customer_id}"}
    return customer


class QueryOpportunitiesArgs(BaseModel):
    customer_id: str | None = Field(None, description="按客户编号过滤")
    limit: int = Field(20, ge=1, le=100)


@erp_tool(
    name="crm_query_opportunities",
    description="查询销售商机列表，可按客户过滤",
    args_model=QueryOpportunitiesArgs,
    risk_level=ToolRisk.READONLY, resource="crm", action="read",
    agent_domain="crm",
)
async def crm_query_opportunities(ctx: ToolContext, customer_id: str | None = None,
                                  limit: int = 20):
    opportunities = _get_agent().get_opportunities(customer_id=customer_id)
    return {"total": len(opportunities), "opportunities": opportunities[:limit]}


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------

class CreateCustomerArgs(BaseModel):
    name: str = Field(..., min_length=1, description="客户名称")
    industry: str = Field("", description="所属行业")
    segment: str = Field("C", description="客户分级（A/B/C）")
    contact_name: str = Field(..., min_length=1, description="主联系人姓名")
    contact_phone: str = Field(..., min_length=1, description="主联系人电话")


async def _create_customer_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：按 customer_id 删库 + 清 Agent 内存。"""
    if not result or not result.get("customer_id"):
        return
    customer_id = result["customer_id"]
    agent = _get_agent()
    agent._customers.pop(customer_id, None)
    agent._segments.pop(customer_id, None)
    await customer_repository.delete_by_id(customer_id)


@erp_tool(
    name="crm_create_customer",
    description="新增客户档案（自动创建主联系人）",
    args_model=CreateCustomerArgs,
    risk_level=ToolRisk.WRITE_LOW, resource="crm", action="create",
    agent_domain="crm",
    compensation=_create_customer_compensation,
    effects_fn=lambda a: (
        f"将新增客户[{a.get('name')}]（{a.get('industry') or '行业未填'}/分级{a.get('segment')}），"
        f"主联系人[{a.get('contact_name')}]"
    ),
)
async def crm_create_customer(ctx: ToolContext, name: str, contact_name: str,
                              contact_phone: str, industry: str = "",
                              segment: str = "C"):
    customer = _get_agent().create_customer(
        name=name, industry=industry, segment=segment,
        contact_name=contact_name, contact_phone=contact_phone,
    )
    await customer_repository.upsert(customer, "customer_id")
    return customer
