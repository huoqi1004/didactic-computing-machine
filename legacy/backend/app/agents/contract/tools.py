"""
合同管家场景工具（Phase B 批量推广）
====================================

3 查询 + 2 写（创建 write_low；审批 write_high 强制人审）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.db.repositories.base import schedule
from app.db.repositories.treasury_contract_repository import contract_repository


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("合同Agent")
    if agent is None:
        raise RuntimeError("合同Agent 未启动")
    return agent


class QueryContractsArgs(BaseModel):
    status: str | None = Field(None, description="按状态过滤（draft/approved/executing/completed）")
    contract_type: str | None = Field(None, description="按类型过滤（如 采购/销售/服务）")
    department: str | None = Field(None, description="按归属部门过滤")
    limit: int = Field(20, ge=1, le=100)


@erp_tool(
    name="contract_query_list",
    description="查询合同列表，可按状态/类型/部门过滤",
    args_model=QueryContractsArgs,
    risk_level=ToolRisk.READONLY, resource="contract", action="read",
    agent_domain="contract",
)
async def contract_query_list(ctx: ToolContext, status: str | None = None,
                              contract_type: str | None = None,
                              department: str | None = None, limit: int = 20):
    contracts = _get_agent().get_contracts(status=status, type=contract_type,
                                           department=department)
    return {"total": len(contracts), "contracts": contracts[:limit]}


class QueryContractArgs(BaseModel):
    contract_id: str = Field(..., min_length=1, description="合同编号（如 CTR-xxxxxxxx）")


@erp_tool(
    name="contract_query_detail",
    description="按编号查询合同详情",
    args_model=QueryContractArgs,
    risk_level=ToolRisk.READONLY, resource="contract", action="read",
    agent_domain="contract",
)
async def contract_query_detail(ctx: ToolContext, contract_id: str):
    contract = _get_agent().get_contract(contract_id)
    if contract is None:
        return {"error": f"合同不存在: {contract_id}"}
    return contract


class QueryExpiringArgs(BaseModel):
    days: int = Field(30, ge=1, le=365, description="未来天数窗口")


@erp_tool(
    name="contract_query_expiring",
    description="查询即将到期的合同（默认未来30天）",
    args_model=QueryExpiringArgs,
    risk_level=ToolRisk.READONLY, resource="contract", action="read",
    agent_domain="contract",
)
async def contract_query_expiring(ctx: ToolContext, days: int = 30):
    contracts = _get_agent().get_expiring_contracts(days=days)
    return {"total": len(contracts), "contracts": contracts[:50]}


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------

class CreateContractArgs(BaseModel):
    title: str = Field(..., min_length=1, description="合同标题")
    type: str = Field(..., min_length=1, description="合同类型（采购/销售/服务/租赁等）")
    counterparty: str = Field(..., min_length=1, description="对方当事人")
    amount: float = Field(..., gt=0, description="合同金额（元）")
    department: str = Field(..., min_length=1, description="归属部门")
    start_date: str = Field(..., min_length=8, description="开始日期（YYYY-MM-DD）")
    end_date: str = Field(..., min_length=8, description="结束日期（YYYY-MM-DD）")
    terms: str = Field("", description="主要条款")


async def _create_contract_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：按 contract_id 删库 + 清 Agent 内存。"""
    if not result or not result.get("contract_id"):
        return
    contract_id = result["contract_id"]
    agent = _get_agent()
    agent._contracts.pop(contract_id, None)
    await contract_repository.delete_by_id(contract_id)


@erp_tool(
    name="contract_create",
    description="创建合同草稿（状态 draft，需走审批流程后生效）",
    args_model=CreateContractArgs,
    risk_level=ToolRisk.WRITE_LOW, resource="contract", action="create",
    agent_domain="contract",
    compensation=_create_contract_compensation,
    idempotent=True,  # G1.4 红线2：补偿幂等（已核阅）
    effects_fn=lambda a: (
        f"将创建合同草稿[{a.get('title')}]：对方[{a.get('counterparty')}] "
        f"金额 ¥{a.get('amount')}，部门[{a.get('department')}]"
    ),
)
async def contract_create(ctx: ToolContext, title: str, type: str,
                          counterparty: str, amount: float, department: str,
                          start_date: str, end_date: str, terms: str = ""):
    contract = await _get_agent().create_contract(
        title=title, type=type, counterparty=counterparty,
        amount=amount, department=department,
        start_date=start_date, end_date=end_date, terms=terms,
    )
    await contract_repository.upsert(contract, "contract_id")
    return contract


class ApproveContractArgs(BaseModel):
    contract_id: str = Field(..., min_length=1, description="合同编号")
    comment: str = Field("", max_length=300, description="审批意见")


async def _approve_contract_compensation(ctx: ToolContext, args: dict, result) -> None:
    """回滚：合同状态退回 draft。"""
    if not result or not result.get("contract_id"):
        return
    contract_id = result["contract_id"]
    agent = _get_agent()
    if contract_id in agent._contracts:
        agent._contracts[contract_id]["status"] = "draft"
    await contract_repository.update_status(contract_id, "draft")


@erp_tool(
    name="contract_approve",
    description="审批通过一份草稿合同（draft → approved）。合同属高危变更，需人工审批后执行",
    args_model=ApproveContractArgs,
    risk_level=ToolRisk.WRITE_HIGH, resource="contract", action="approve",
    agent_domain="contract",
    compensation=_approve_contract_compensation,
    idempotent=True,  # G1.4 红线2：补偿幂等（已核阅）
    effects_fn=lambda a: f"将审批通过合同[{a.get('contract_id')}]，状态从草稿变为已批准",
)
async def contract_approve(ctx: ToolContext, contract_id: str, comment: str = ""):
    contract = await _get_agent().approve_contract(
        contract_id=contract_id,
        approved_by=ctx.username or ctx.user_id,
        comment=comment or "Agent 编排审批通过",
    )
    if "error" in contract:
        return {"error": contract.get("error", "审批失败"),
                "contract_id": contract_id}
    await contract_repository.update_status(
        contract_id, "approved",
        extra={
            "approved_by": contract.get("approved_by") or None,
            "approval_comment": contract.get("approval_comment") or None,
            "approved_at": contract.get("approved_at"),
        },
    )
    return contract
