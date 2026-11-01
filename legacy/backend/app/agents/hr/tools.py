"""
人力资源助手场景工具（Phase B 批量推广）
========================================

3 查询 + 2 写（员工档案变更 write_low，精确补偿）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.db.repositories.base import has_db
from app.db.repositories.hr_repository import employee_repository


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("人力资源Agent")
    if agent is None:
        raise RuntimeError("人力资源Agent 未启动")
    return agent


class QueryEmployeesArgs(BaseModel):
    department: str | None = Field(None, description="按部门过滤")
    limit: int = Field(20, ge=1, le=100)


@erp_tool(
    name="hr_query_employees",
    description="查询员工列表，可按部门过滤",
    args_model=QueryEmployeesArgs,
    risk_level=ToolRisk.READONLY, resource="hr", action="read",
    agent_domain="hr",
)
async def hr_query_employees(ctx: ToolContext, department: str | None = None,
                             limit: int = 20):
    employees = _get_agent().get_employees(department=department)
    return {"total": len(employees), "employees": employees[:limit]}


class QueryEmployeeArgs(BaseModel):
    employee_id: str = Field(..., min_length=1, description="员工编号（如 EMP0001）")


@erp_tool(
    name="hr_query_employee_detail",
    description="按编号查询单个员工档案详情",
    args_model=QueryEmployeeArgs,
    risk_level=ToolRisk.READONLY, resource="hr", action="read",
    agent_domain="hr",
)
async def hr_query_employee_detail(ctx: ToolContext, employee_id: str):
    employee = _get_agent().get_employee(employee_id)
    if employee is None:
        return {"error": f"员工不存在: {employee_id}"}
    return employee


class QueryAttendanceArgs(BaseModel):
    employee_id: str | None = Field(None, description="按员工编号过滤")
    date: str | None = Field(None, description="按日期过滤（YYYY-MM-DD）")
    limit: int = Field(20, ge=1, le=100)


@erp_tool(
    name="hr_query_attendance",
    description="查询考勤记录，可按员工与日期过滤",
    args_model=QueryAttendanceArgs,
    risk_level=ToolRisk.READONLY, resource="hr", action="read",
    agent_domain="hr",
)
async def hr_query_attendance(ctx: ToolContext, employee_id: str | None = None,
                              date: str | None = None, limit: int = 20):
    records = _get_agent().get_attendance(employee_id=employee_id, date=date)
    return {"total": len(records), "records": records[:limit]}


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------

class AddEmployeeArgs(BaseModel):
    name: str = Field(..., min_length=1, description="员工姓名")
    department: str = Field(..., min_length=1, description="所属部门")
    position: str = Field(..., min_length=1, description="职位")
    email: str = Field("", description="邮箱（选填）")
    phone: str = Field("", description="电话（选填）")
    base_salary: float = Field(0, ge=0, description="基本工资（选填）")


async def _add_employee_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：按 employee_id 删库 + 清 Agent 内存。"""
    if not result or not result.get("employee_id"):
        return
    employee_id = result["employee_id"]
    agent = _get_agent()
    agent._employees.pop(employee_id, None)
    await employee_repository.delete_by_id(employee_id)


@erp_tool(
    name="hr_add_employee",
    description="新增员工档案（生成员工编号，写入组织）",
    args_model=AddEmployeeArgs,
    risk_level=ToolRisk.WRITE_LOW, resource="hr", action="create",
    agent_domain="hr",
    compensation=_add_employee_compensation,
    idempotent=True,  # G1.4 红线2：补偿幂等（已核阅）
    effects_fn=lambda a: (
        f"将新增员工[{a.get('name')}]：部门[{a.get('department')}] 职位[{a.get('position')}]"
    ),
)
async def hr_add_employee(ctx: ToolContext, name: str, department: str,
                          position: str, email: str = "", phone: str = "",
                          base_salary: float = 0):
    employee = _get_agent().add_employee(
        name=name, department=department, position=position,
        email=email, phone=phone, base_salary=base_salary,
    )
    await employee_repository.upsert(employee, "employee_id")
    return employee


class UpdateEmployeeArgs(BaseModel):
    employee_id: str = Field(..., min_length=1, description="员工编号")
    position: str | None = Field(None, description="新职位")
    department: str | None = Field(None, description="新部门")
    email: str | None = Field(None, description="新邮箱")
    phone: str | None = Field(None, description="新电话")
    status: str | None = Field(None, description="状态：active/inactive")


async def _update_employee_compensation(ctx: ToolContext, args: dict, result) -> None:
    """精确回滚：恢复 handler 暂存在 ctx.extra 里的旧档案。"""
    old = ctx.extra.get("_prev_employee")
    if not old:
        return
    employee_id = args.get("employee_id")
    agent = _get_agent()
    if employee_id in agent._employees:
        agent._employees[employee_id] = dict(old)
    await employee_repository.upsert(old, "employee_id")


@erp_tool(
    name="hr_update_employee",
    description="更新员工档案（职位/部门/联系方式/状态）",
    args_model=UpdateEmployeeArgs,
    risk_level=ToolRisk.WRITE_LOW, resource="hr", action="update",
    agent_domain="hr",
    compensation=_update_employee_compensation,
    idempotent=True,  # G1.4 红线2：补偿幂等（已核阅）
    effects_fn=lambda a: (
        f"将更新员工[{a.get('employee_id')}]档案："
        + "、".join(f"{k}={v}" for k, v in a.items() if k != "employee_id" and v is not None)
    ),
)
async def hr_update_employee(ctx: ToolContext, employee_id: str,
                             position: str | None = None,
                             department: str | None = None,
                             email: str | None = None, phone: str | None = None,
                             status: str | None = None):
    agent = _get_agent()
    old = agent.get_employee(employee_id)
    if old is None:
        return {"error": f"员工不存在: {employee_id}"}
    ctx.extra["_prev_employee"] = dict(old)
    updates = {k: v for k, v in dict(
        position=position, department=department,
        email=email, phone=phone, status=status,
    ).items() if v is not None}
    updated = agent.update_employee(employee_id, **updates)
    if "error" in updated:
        return {"error": updated["error"]}
    await employee_repository.upsert(updated, "employee_id")
    return updated
