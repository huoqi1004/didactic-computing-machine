"""
报销助手场景工具（路线图 Phase A / A6）
========================================

把报销 Agent 的业务方法注册为治理面工具（LangGraph 编排可调用）。

- 3 个只读查询 + 1 个低危写（write_low：免审批、dry-run 留痕、事后抽样审计）；
- 提交工具的补偿函数可精确回滚（按 handler 返回的 reimbursement_id 删库+清内存）；
- Agent 实例经 orchestrator 运行时解析（避免循环导入）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.db.repositories.base import schedule
from app.db.repositories.reimbursement_repository import reimbursement_request_repository


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("报销Agent")
    if agent is None:
        raise RuntimeError("报销Agent 未启动")
    return agent


# ---------------------------------------------------------------------------
# 只读查询
# ---------------------------------------------------------------------------

class QueryListArgs(BaseModel):
    status: str | None = Field(None, description="按状态过滤：pending/verified/approved/rejected/paid")
    employee_id: str | None = Field(None, description="按员工ID过滤")
    limit: int = Field(20, ge=1, le=100, description="返回条数上限")


@erp_tool(
    name="reimbursement_query_list",
    description="查询报销单列表，可按状态与员工过滤，返回单据号/金额/类别/状态",
    args_model=QueryListArgs,
    risk_level=ToolRisk.READONLY,
    resource="reimbursement", action="read",
    agent_domain="reimbursement",
)
async def reimbursement_query_list(ctx: ToolContext, status: str | None = None,
                                   employee_id: str | None = None, limit: int = 20):
    agent = _get_agent()
    records = agent.get_reimbursements(status=status, employee_id=employee_id)
    return {"total": len(records), "reimbursements": records[:limit]}


class QueryDetailArgs(BaseModel):
    reimbursement_id: str = Field(..., min_length=1, description="报销单ID")


@erp_tool(
    name="reimbursement_query_detail",
    description="按ID查询单条报销单详情（含票据与项目信息）",
    args_model=QueryDetailArgs,
    risk_level=ToolRisk.READONLY,
    resource="reimbursement", action="read",
    agent_domain="reimbursement",
)
async def reimbursement_query_detail(ctx: ToolContext, reimbursement_id: str):
    record = _get_agent().get_reimbursement(reimbursement_id)
    if record is None:
        return {"error": f"报销单不存在: {reimbursement_id}"}
    return record


class StatsArgs(BaseModel):
    pass


@erp_tool(
    name="reimbursement_query_stats",
    description="查询报销统计（待审数量/金额、按类别与项目分布），无需参数",
    args_model=StatsArgs,
    risk_level=ToolRisk.READONLY,
    resource="reimbursement", action="read",
    agent_domain="reimbursement",
)
async def reimbursement_query_stats(ctx: ToolContext):
    return _get_agent().get_reimbursement_stats()


# ---------------------------------------------------------------------------
# 写操作（write_low：免审批，dry-run 留痕，事后抽样审计）
# ---------------------------------------------------------------------------

class SubmitArgs(BaseModel):
    employee_name: str = Field(..., min_length=1, description="员工姓名")
    employee_id: str = Field("", description="员工ID（选填）")
    department: str = Field("", description="部门（选填）")
    category: str = Field("other", description="类别：travel/meal/office/transport/communication/training/entertainment/project/other")
    amount: float = Field(..., gt=0, description="报销金额（元）")
    description: str = Field(..., min_length=1, description="报销事由")
    has_receipt: bool = Field(True, description="是否已附发票")
    receipt_amount: float = Field(0, ge=0, description="票据金额")
    project_id: str = Field("", description="项目ID（选填）")
    project_name: str = Field("", description="项目名称（选填）")


async def _submit_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：按 handler 返回的 reimbursement_id 删库 + 清 Agent 内存。"""
    if not result:
        return
    reimb_id = result.get("reimbursement_id")
    if not reimb_id:
        return
    agent = _get_agent()
    agent._requests.pop(reimb_id, None)
    await reimbursement_request_repository.delete_by_id(reimb_id)


@erp_tool(
    name="reimbursement_submit",
    description="替员工提交一笔报销申请（生成报销单号，进入待核验流程）",
    args_model=SubmitArgs,
    risk_level=ToolRisk.WRITE_LOW,
    resource="reimbursement", action="create",
    agent_domain="reimbursement",
    compensation=_submit_compensation,
    effects_fn=lambda a: (
        f"将创建一笔报销单：员工[{a.get('employee_name')}] 类别[{a.get('category')}] "
        f"金额[¥{a.get('amount')}]，事由[{a.get('description')}]，状态置为待核验"
    ),
)
async def reimbursement_submit(ctx: ToolContext, employee_name: str, amount: float,
                               description: str, employee_id: str = "",
                               department: str = "", category: str = "other",
                               has_receipt: bool = True, receipt_amount: float = 0,
                               project_id: str = "", project_name: str = ""):
    agent = _get_agent()
    record = await agent.submit_reimbursement({
        "employee_id": employee_id,
        "employee_name": employee_name,
        "department": department,
        "category": category,
        "amount": amount,
        "description": description,
        "has_receipt": has_receipt,
        "receipt_amount": receipt_amount,
        "project_id": project_id,
        "project_name": project_name,
    })
    if "error" in record:
        return {"error": record["error"]}
    # 立即同步落库（不走 fire-and-forget：写工具要求执行后 DB 即真）
    await reimbursement_request_repository.upsert(record, key_field="reimbursement_id")
    return record
