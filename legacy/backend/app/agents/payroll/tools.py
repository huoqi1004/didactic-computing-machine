"""
薪酬助手场景工具（Phase B 批量推广）
====================================

2 查询 + 2 写（薪酬结构为 write_high 强制人审，决策 D6）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.db.repositories.base import has_db, schedule, session_scope
from app.db.repositories.payroll_repository import (
    payroll_record_repository,
    salary_structure_repository,
)


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("薪酬Agent")
    if agent is None:
        raise RuntimeError("薪酬Agent 未启动")
    return agent


class QueryRecordsArgs(BaseModel):
    employee_id: str | None = Field(None, description="按员工编号过滤")
    period: str | None = Field(None, description="按周期过滤（如 2026-09）")
    limit: int = Field(20, ge=1, le=100)


@erp_tool(
    name="payroll_query_records",
    description="查询薪酬记录列表，可按员工与周期过滤（含基本工资/津贴/个税/实发）",
    args_model=QueryRecordsArgs,
    risk_level=ToolRisk.READONLY, resource="payroll", action="read",
    agent_domain="payroll",
)
async def payroll_query_records(ctx: ToolContext, employee_id: str | None = None,
                                period: str | None = None, limit: int = 20):
    records = _get_agent().get_payroll_records(employee_id=employee_id, period=period)
    return {"total": len(records), "records": records[:limit]}


class QueryStructureArgs(BaseModel):
    employee_id: str = Field(..., min_length=1, description="员工编号（如 EMP0001）")


@erp_tool(
    name="payroll_query_structure",
    description="查询员工薪资结构（基本工资/津贴/扣款）",
    args_model=QueryStructureArgs,
    risk_level=ToolRisk.READONLY, resource="payroll", action="read",
    agent_domain="payroll",
)
async def payroll_query_structure(ctx: ToolContext, employee_id: str):
    structure = _get_agent().get_salary_structure(employee_id)
    if structure is None:
        return {"error": f"员工薪资结构不存在: {employee_id}"}
    return structure


@erp_tool(
    name="payroll_query_stats",
    description="查询薪酬统计（总发薪/总个税/按周期分布）",
    args_model=QueryRecordsArgs,
    risk_level=ToolRisk.READONLY, resource="payroll", action="read",
    agent_domain="payroll",
)
async def payroll_query_stats(ctx: ToolContext):
    return _get_agent().get_stats()


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------

class CalculateArgs(BaseModel):
    employee_id: str = Field(..., min_length=1, description="员工编号")
    period: str = Field(..., min_length=4, description="薪酬周期（如 2026-09）")


async def _calculate_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：按 record_id 删库 + 清 Agent 内存记录。"""
    if not result or not result.get("record_id"):
        return
    record_id = result["record_id"]
    agent = _get_agent()
    agent._payroll_records = [
        r for r in agent._payroll_records if r.get("record_id") != record_id
    ]
    await payroll_record_repository.delete_by_record_id(record_id)


@erp_tool(
    name="payroll_calculate",
    description="计算单个员工指定周期的薪酬（含社保公积金个税，生成薪酬记录）",
    args_model=CalculateArgs,
    risk_level=ToolRisk.WRITE_LOW, resource="payroll", action="create",
    agent_domain="payroll",
    compensation=_calculate_compensation,
    idempotent=True,  # G1.4 红线2：补偿幂等（已核阅）
    effects_fn=lambda a: f"将为员工[{a.get('employee_id')}]计算 {a.get('period')} 周期薪酬并生成薪酬记录",
)
async def payroll_calculate(ctx: ToolContext, employee_id: str, period: str):
    record = _get_agent().calculate_payroll(employee_id=employee_id, period=period)
    if "error" in record:
        return {"error": record["error"]}
    await payroll_record_repository.upsert_by_record_id(record)
    return record


class SetStructureArgs(BaseModel):
    employee_id: str = Field(..., min_length=1, description="员工编号")
    base_salary: float = Field(..., gt=0, description="基本工资（元/月）")
    allowances: float = Field(0, ge=0, description="津贴")
    deductions: float = Field(0, ge=0, description="扣款")


async def _set_structure_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：恢复 handler 暂存在 ctx.extra 里的旧薪资结构。"""
    old = ctx.extra.get("_prev_structure")
    employee_id = args.get("employee_id")
    agent = _get_agent()
    if old:
        agent._salary_structures[employee_id] = dict(old)
        await salary_structure_repository.upsert(old, "employee_id")
    else:
        agent._salary_structures.pop(employee_id, None)
        if has_db():
            await salary_structure_repository.delete_by_id(employee_id)


@erp_tool(
    name="payroll_set_salary_structure",
    description="设置员工薪资结构（基本工资/津贴/扣款）。薪酬属高敏数据，需人工审批后执行",
    args_model=SetStructureArgs,
    risk_level=ToolRisk.WRITE_HIGH, resource="payroll", action="create",
    agent_domain="payroll",
    compensation=_set_structure_compensation,
    idempotent=True,  # G1.4 红线2：补偿幂等（已核阅）
    effects_fn=lambda a: (
        f"将设置员工[{a.get('employee_id')}]的薪资结构："
        f"基本工资 ¥{a.get('base_salary')}/月，津贴 ¥{a.get('allowances')}，扣款 ¥{a.get('deductions')}"
    ),
)
async def payroll_set_salary_structure(ctx: ToolContext, employee_id: str,
                                       base_salary: float, allowances: float = 0,
                                       deductions: float = 0):
    agent = _get_agent()
    # 暂存旧结构供补偿精确回滚
    ctx.extra["_prev_structure"] = agent.get_salary_structure(employee_id)
    structure = agent.set_salary_structure(
        employee_id=employee_id, base_salary=base_salary,
        allowances=allowances, deductions=deductions,
    )
    await salary_structure_repository.upsert(structure, "employee_id")
    return structure
