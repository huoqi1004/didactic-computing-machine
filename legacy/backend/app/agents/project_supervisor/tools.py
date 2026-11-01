# -*- coding: utf-8 -*-
"""
项目监理场景工具（监理对话链路升级 1.2）
========================================

把 ProjectSupervisorAgent 的三项本体能力注册为治理面只读工具，供对话链路
（含 LLM 不可用时的降级骨架）经 agent_governor 调用——监理回答的数据源
由此收敛到 Agent 本体，不再是对话路由里另写的一份文案。

三项均 ToolRisk.READONLY：只读监理快照/报告（报告为内存态快照，
不落业务库），无数据变更，故不需要补偿函数。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("项目监理agent")
    if agent is None:
        raise RuntimeError("项目监理agent 未启动")
    return agent


# ---------------------------------------------------------------------------
# 进展查询
# ---------------------------------------------------------------------------

class SupervisionArgs(BaseModel):
    project_id: str | None = Field(
        None, min_length=1, max_length=64, description="按项目ID过滤，如 proj-001；留空返回全部在监项目")


@erp_tool(
    name="supervisor_query_progress",
    description="查询项目监理进展快照：阶段、里程碑完成数、预算使用率、上线就绪与风险清单",
    args_model=SupervisionArgs,
    risk_level=ToolRisk.READONLY,
    resource="project", action="read",
    agent_domain="project_supervisor",
)
async def supervisor_query_progress(ctx: ToolContext, project_id: str | None = None):
    records = _get_agent().get_supervision(project_id=project_id)
    return {"total": len(records), "projects": records}


# ---------------------------------------------------------------------------
# 合规检查
# ---------------------------------------------------------------------------

class ComplianceArgs(BaseModel):
    project_id: str | None = Field(
        None, min_length=1, max_length=64, description="按项目ID过滤；留空为全部在监项目合并核对")


@erp_tool(
    name="supervisor_check_compliance",
    description="按监理阶段清单（立项→需求→开发→UAT→上线→结项）核对合规状态，返回各阶段状态与风险分级",
    args_model=ComplianceArgs,
    risk_level=ToolRisk.READONLY,
    resource="project", action="read",
    agent_domain="project_supervisor",
)
async def supervisor_check_compliance(ctx: ToolContext, project_id: str | None = None):
    return _get_agent().check_compliance(project_id=project_id)


# ---------------------------------------------------------------------------
# 监理报告
# ---------------------------------------------------------------------------

class ReportArgs(BaseModel):
    pass


@erp_tool(
    name="supervisor_generate_report",
    description="生成项目监理报告（先从项目Agent同步实时进度），返回报告编号、整体合规判定、"
                "风险三级清单与各项目明细；无需参数",
    args_model=ReportArgs,
    risk_level=ToolRisk.READONLY,
    resource="project", action="read",
    agent_domain="project_supervisor",
)
async def supervisor_generate_report(ctx: ToolContext):
    return await _get_agent().generate_report()
