"""
人力资源 API 路由
==================

提供人力资源全流程的 HTTP API 端点，覆盖四大子域：

- ``/hr/*``:          员工档案、考勤、请假、组织架构
- ``/recruitment/*``:  职位、候选人、面试、Offer
- ``/performance/*``:  KPI 模板、绩效周期、评审、目标
- ``/payroll/*``:      薪资结构、薪酬计算、社保配置

所有路由均通过全局 Agent 引用（``_agent_refs``）访问对应 Agent 实例。
Agent 实例由 ``main.py`` 的 lifespan 在应用启动时注入。
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.api.middleware.auth import get_current_user, require_permission
from app.api.routes._agent_refs import (
    get_hr_agent,
    get_recruitment_agent,
    get_performance_agent,
    get_payroll_agent,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["人力资源"])


# =============================================================================
# 请求模型 - HR
# =============================================================================

class AddEmployeeRequest(BaseModel):
    """新增员工请求"""
    name: str = Field(..., description="员工姓名", min_length=1)
    department: str = Field(..., description="所属部门")
    position: str = Field(..., description="职位")
    email: str = Field("", description="邮箱")
    phone: str = Field("", description="电话")
    hire_date: str = Field("", description="入职日期 (YYYY-MM-DD)")
    base_salary: float = Field(0, description="基本工资")
    allowances: float = Field(0, description="津贴")
    deductions: float = Field(0, description="扣款")
    manager_id: str = Field("", description="直属上级员工编号")


class UpdateEmployeeRequest(BaseModel):
    """更新员工请求"""
    name: Optional[str] = Field(None, description="员工姓名")
    department: Optional[str] = Field(None, description="所属部门")
    position: Optional[str] = Field(None, description="职位")
    email: Optional[str] = Field(None, description="邮箱")
    phone: Optional[str] = Field(None, description="电话")
    status: Optional[str] = Field(None, description="状态 (active/inactive)")
    manager_id: Optional[str] = Field(None, description="直属上级员工编号")


class RecordAttendanceRequest(BaseModel):
    """记录考勤请求"""
    employee_id: str = Field(..., description="员工编号")
    date: str = Field(..., description="日期 (YYYY-MM-DD)")
    check_in: str = Field(..., description="签到时间")
    check_out: str = Field(..., description="签退时间")
    status: str = Field("normal", description="考勤状态 (normal/late/absent/leave)")


class LeaveRequest(BaseModel):
    """请假申请请求"""
    employee_id: str = Field(..., description="员工编号")
    leave_type: str = Field(..., description="请假类型 (annual/sick/personal/other)")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    reason: str = Field("", description="请假原因")


# =============================================================================
# 请求模型 - 招聘
# =============================================================================

class CreatePositionRequest(BaseModel):
    """创建职位请求"""
    title: str = Field(..., description="职位名称")
    department: str = Field(..., description="所属部门")
    requirements: list[str] = Field(default_factory=list, description="任职要求")
    headcount: int = Field(1, description="招聘人数", ge=1)
    salary_range: dict = Field(default_factory=dict, description="薪资范围")
    description: str = Field("", description="职位描述")


class AddCandidateRequest(BaseModel):
    """添加候选人请求"""
    name: str = Field(..., description="候选人姓名")
    position_id: str = Field(..., description="应聘职位编号")
    source: str = Field("external", description="来源")
    resume_summary: str = Field("", description="简历摘要")
    phone: str = Field("", description="电话")
    email: str = Field("", description="邮箱")


class UpdateCandidateStatusRequest(BaseModel):
    """更新候选人状态请求"""
    status: str = Field(..., description="新状态 (new/screening/interviewing/offered/accepted/rejected/onboarded)")


class ScheduleInterviewRequest(BaseModel):
    """安排面试请求"""
    candidate_id: str = Field(..., description="候选人编号")
    interview_date: str = Field(..., description="面试时间")
    interviewer: str = Field(..., description="面试官")
    round_num: int = Field(1, description="面试轮次", ge=1)


class ExtendOfferRequest(BaseModel):
    """发放 Offer 请求"""
    candidate_id: str = Field(..., description="候选人编号")
    salary: float = Field(..., description="Offer 薪资")
    position: str = Field(..., description="职位名称")
    start_date: str = Field(..., description="入职日期")


# =============================================================================
# 请求模型 - 绩效
# =============================================================================

class CreateKpiTemplateRequest(BaseModel):
    """创建 KPI 模板请求"""
    name: str = Field(..., description="KPI 名称")
    department: str = Field(..., description="所属部门")
    metric: str = Field(..., description="度量指标")
    target: float = Field(..., description="目标值")
    weight: float = Field(..., description="权重")
    unit: str = Field("", description="单位")
    description: str = Field("", description="描述")


class CreateReviewCycleRequest(BaseModel):
    """创建绩效周期请求"""
    name: str = Field(..., description="周期名称")
    period: str = Field(..., description="周期标识 (如 2026Q3)")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    description: str = Field("", description="描述")


class SubmitReviewRequest(BaseModel):
    """提交绩效评审请求"""
    employee_id: str = Field(..., description="员工编号")
    cycle_id: str = Field(..., description="绩效周期编号")
    scores: dict = Field(default_factory=dict, description="各 KPI 评分 {kpi_id: score}")
    comments: str = Field("", description="评审意见")


class SetGoalRequest(BaseModel):
    """设定目标请求"""
    employee_id: str = Field(..., description="员工编号")
    goal_type: str = Field(..., description="目标类型")
    description: str = Field(..., description="目标描述")
    target_date: str = Field(..., description="目标完成日期")
    cycle_id: str = Field("", description="关联绩效周期")


# =============================================================================
# 请求模型 - 薪酬
# =============================================================================

class SetSalaryStructureRequest(BaseModel):
    """设置薪资结构请求"""
    employee_id: str = Field(..., description="员工编号")
    base_salary: float = Field(..., description="基本工资")
    allowances: float = Field(0, description="津贴")
    deductions: float = Field(0, description="扣款")
    bank_account: str = Field("", description="银行账号")
    payment_method: str = Field("bank_transfer", description="支付方式")
    effective_date: str = Field("", description="生效日期")


class CalculatePayrollRequest(BaseModel):
    """计算单个员工薪酬请求"""
    employee_id: str = Field(..., description="员工编号")
    period: str = Field(..., description="薪酬周期 (如 2026-07)")


class BatchCalculatePayrollRequest(BaseModel):
    """批量计算薪酬请求"""
    period: str = Field(..., description="薪酬周期 (如 2026-07)")


class UpdateSocialInsuranceRequest(BaseModel):
    """更新社保配置请求"""
    rates: dict = Field(..., description="社保费率配置")


# =============================================================================
# HR Agent 路由
# =============================================================================

@router.get("/hr/employees", summary="获取员工列表")
async def get_employees(
    department: Optional[str] = Query(None, description="按部门过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取员工列表，可按部门过滤"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    employees = agent.get_employees(department=department)
    return {"total": len(employees), "employees": employees}


@router.get("/hr/employees/{employee_id}", summary="获取员工详情")
async def get_employee(
    employee_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个员工档案"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    employee = agent.get_employee(employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail=f"员工不存在: {employee_id}")
    return employee


@router.post("/hr/employees", summary="添加员工")
async def add_employee(
    req: AddEmployeeRequest,
    current_user: dict = Depends(require_permission("hr", "create")),
):
    """新增员工档案"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    employee = agent.add_employee(
        name=req.name,
        department=req.department,
        position=req.position,
        email=req.email,
        phone=req.phone,
        hire_date=req.hire_date,
        base_salary=req.base_salary,
        allowances=req.allowances,
        deductions=req.deductions,
        manager_id=req.manager_id,
    )
    return {"message": "员工添加成功", "employee": employee}


@router.put("/hr/employees/{employee_id}", summary="更新员工")
async def update_employee(
    employee_id: str,
    req: UpdateEmployeeRequest,
    current_user: dict = Depends(require_permission("hr", "update")),
):
    """更新员工档案"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    update_data = req.model_dump(exclude_none=True)
    result = agent.update_employee(employee_id, **update_data)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"message": "员工更新成功", "employee": result}


@router.post("/hr/attendance", summary="记录考勤")
async def record_attendance(
    req: RecordAttendanceRequest,
    current_user: dict = Depends(get_current_user),
):
    """记录员工考勤"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    record = agent.record_attendance(
        employee_id=req.employee_id,
        date=req.date,
        check_in=req.check_in,
        check_out=req.check_out,
        status=req.status,
    )
    if "error" in record:
        raise HTTPException(status_code=400, detail=record["error"])
    return {"message": "考勤记录成功", "record": record}


@router.get("/hr/attendance", summary="获取考勤记录")
async def get_attendance(
    employee_id: Optional[str] = Query(None, description="按员工编号过滤"),
    date: Optional[str] = Query(None, description="按日期过滤 (YYYY-MM-DD)"),
    current_user: dict = Depends(get_current_user),
):
    """获取考勤记录，可按员工和日期过滤"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    records = agent.get_attendance(employee_id=employee_id, date=date)
    return {"total": len(records), "records": records}


@router.post("/hr/leave", summary="请假申请")
async def request_leave(
    req: LeaveRequest,
    current_user: dict = Depends(get_current_user),
):
    """提交请假申请"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    result = agent.request_leave(
        employee_id=req.employee_id,
        leave_type=req.leave_type,
        start_date=req.start_date,
        end_date=req.end_date,
        reason=req.reason,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": "请假申请已提交", "request": result}


@router.get("/hr/leave", summary="获取请假记录")
async def get_leave_requests(
    employee_id: Optional[str] = Query(None, description="按员工编号过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取请假记录，可按员工和状态过滤"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    requests = agent.get_leave_requests(employee_id=employee_id, status=status)
    return {"total": len(requests), "requests": requests}


@router.get("/hr/org-chart", summary="获取组织架构")
async def get_org_chart(
    current_user: dict = Depends(get_current_user),
):
    """获取组织架构"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    return agent.get_org_chart()


@router.get("/hr/stats", summary="HR 统计")
async def get_hr_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取人力资源统计信息"""
    agent = get_hr_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="HR Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 招聘路由
# =============================================================================

@router.get("/recruitment/positions", summary="获取职位列表")
async def get_positions(
    status: Optional[str] = Query(None, description="按状态过滤 (open/closed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取职位列表，可按状态过滤"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    positions = agent.get_positions(status=status)
    return {"total": len(positions), "positions": positions}


@router.post("/recruitment/positions", summary="创建职位")
async def create_position(
    req: CreatePositionRequest,
    current_user: dict = Depends(require_permission("recruitment", "create")),
):
    """创建招聘职位"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    position = agent.create_position(
        title=req.title,
        department=req.department,
        requirements=req.requirements,
        headcount=req.headcount,
        salary_range=req.salary_range,
        description=req.description,
    )
    return {"message": "职位创建成功", "position": position}


@router.get("/recruitment/candidates", summary="获取候选人列表")
async def get_candidates(
    position_id: Optional[str] = Query(None, description="按职位编号过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取候选人列表，可按职位和状态过滤"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    candidates = agent.get_candidates(position_id=position_id, status=status)
    return {"total": len(candidates), "candidates": candidates}


@router.post("/recruitment/candidates", summary="添加候选人")
async def add_candidate(
    req: AddCandidateRequest,
    current_user: dict = Depends(require_permission("recruitment", "create")),
):
    """添加候选人"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    candidate = agent.add_candidate(
        name=req.name,
        position_id=req.position_id,
        source=req.source,
        resume_summary=req.resume_summary,
        phone=req.phone,
        email=req.email,
    )
    return {"message": "候选人添加成功", "candidate": candidate}


@router.put("/recruitment/candidates/{candidate_id}/status", summary="更新候选人状态")
async def update_candidate_status(
    candidate_id: str,
    req: UpdateCandidateStatusRequest,
    current_user: dict = Depends(require_permission("recruitment", "update")),
):
    """更新候选人状态"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    result = agent.update_candidate_status(candidate_id, req.status)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"message": "候选人状态已更新", "candidate": result}


@router.post("/recruitment/interviews", summary="安排面试")
async def schedule_interview(
    req: ScheduleInterviewRequest,
    current_user: dict = Depends(require_permission("recruitment", "create")),
):
    """安排面试"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    result = agent.schedule_interview(
        candidate_id=req.candidate_id,
        interview_date=req.interview_date,
        interviewer=req.interviewer,
        round_num=req.round_num,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": "面试已安排", "interview": result}


@router.get("/recruitment/interviews", summary="获取面试列表")
async def get_interviews(
    candidate_id: Optional[str] = Query(None, description="按候选人编号过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取面试记录，可按候选人和状态过滤"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    interviews = agent.get_interviews(candidate_id=candidate_id, status=status)
    return {"total": len(interviews), "interviews": interviews}


@router.post("/recruitment/offers", summary="发放 Offer")
async def extend_offer(
    req: ExtendOfferRequest,
    current_user: dict = Depends(require_permission("recruitment", "create")),
):
    """向候选人发放 Offer"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    result = agent.extend_offer(
        candidate_id=req.candidate_id,
        salary=req.salary,
        position=req.position,
        start_date=req.start_date,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": "Offer 已发放", "offer": result}


@router.get("/recruitment/offers", summary="获取 Offer 列表")
async def get_offers(
    status: Optional[str] = Query(None, description="按状态过滤 (pending/accepted/declined/expired)"),
    current_user: dict = Depends(get_current_user),
):
    """获取 Offer 列表，可按状态过滤"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    offers = agent.get_offers(status=status)
    return {"total": len(offers), "offers": offers}


@router.get("/recruitment/stats", summary="招聘统计")
async def get_recruitment_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取招聘统计信息"""
    agent = get_recruitment_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Recruitment Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 绩效路由
# =============================================================================

@router.get("/performance/kpi-templates", summary="获取 KPI 模板")
async def get_kpi_templates(
    department: Optional[str] = Query(None, description="按部门过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取 KPI 模板列表，可按部门过滤"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    templates = agent.get_kpi_templates(department=department)
    return {"total": len(templates), "templates": templates}


@router.post("/performance/kpi-templates", summary="创建 KPI 模板")
async def create_kpi_template(
    req: CreateKpiTemplateRequest,
    current_user: dict = Depends(require_permission("performance", "create")),
):
    """创建 KPI 模板"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    template = agent.create_kpi_template(
        name=req.name,
        department=req.department,
        metric=req.metric,
        target=req.target,
        weight=req.weight,
        unit=req.unit,
        description=req.description,
    )
    return {"message": "KPI 模板创建成功", "template": template}


@router.get("/performance/cycles", summary="获取绩效周期")
async def get_review_cycles(
    status: Optional[str] = Query(None, description="按状态过滤 (draft/active/completed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取绩效周期列表，可按状态过滤"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    cycles = agent.get_review_cycles(status=status)
    return {"total": len(cycles), "cycles": cycles}


@router.post("/performance/cycles", summary="创建绩效周期")
async def create_review_cycle(
    req: CreateReviewCycleRequest,
    current_user: dict = Depends(require_permission("performance", "create")),
):
    """创建绩效周期"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    cycle = agent.create_review_cycle(
        name=req.name,
        period=req.period,
        start_date=req.start_date,
        end_date=req.end_date,
        description=req.description,
    )
    return {"message": "绩效周期创建成功", "cycle": cycle}


@router.post("/performance/reviews", summary="提交绩效评审")
async def submit_review(
    req: SubmitReviewRequest,
    current_user: dict = Depends(require_permission("performance", "create")),
):
    """提交绩效评审"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    result = agent.submit_review(
        employee_id=req.employee_id,
        cycle_id=req.cycle_id,
        scores=req.scores,
        comments=req.comments,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": "绩效评审已提交", "review": result}


@router.get("/performance/reviews", summary="获取评审记录")
async def get_reviews(
    employee_id: Optional[str] = Query(None, description="按员工编号过滤"),
    cycle_id: Optional[str] = Query(None, description="按绩效周期过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取评审记录，可按员工和周期过滤"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    reviews = agent.get_reviews(employee_id=employee_id, cycle_id=cycle_id)
    return {"total": len(reviews), "reviews": reviews}


@router.post("/performance/goals", summary="设定目标")
async def set_goal(
    req: SetGoalRequest,
    current_user: dict = Depends(require_permission("performance", "create")),
):
    """为员工设定目标"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    goal = agent.set_goal(
        employee_id=req.employee_id,
        goal_type=req.goal_type,
        description=req.description,
        target_date=req.target_date,
        cycle_id=req.cycle_id,
    )
    return {"message": "目标设定成功", "goal": goal}


@router.get("/performance/goals", summary="获取目标列表")
async def get_goals(
    employee_id: Optional[str] = Query(None, description="按员工编号过滤"),
    status: Optional[str] = Query(None, description="按状态过滤 (active/completed/overdue)"),
    current_user: dict = Depends(get_current_user),
):
    """获取目标列表，可按员工和状态过滤"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    goals = agent.get_goals(employee_id=employee_id, status=status)
    return {"total": len(goals), "goals": goals}


@router.get("/performance/stats", summary="绩效统计")
async def get_performance_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取绩效统计信息"""
    agent = get_performance_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Performance Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 薪酬路由
# =============================================================================

def _mask_bank_account(account: str) -> str:
    """银行卡号脱敏：仅保留尾4位（评估报告 D-01 止血措施）"""
    if not account:
        return ""
    return f"************{account[-4:]}" if len(account) >= 4 else "************"


@router.get("/payroll/structure/{employee_id}", summary="获取薪资结构")
async def get_salary_structure(
    employee_id: str,
    current_user: dict = Depends(require_permission("payroll", "read")),
):
    """获取员工薪资结构（需 payroll:read 权限；银行卡号脱敏返回）"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    structure = agent.get_salary_structure(employee_id)
    if structure is None:
        raise HTTPException(status_code=404, detail=f"薪资结构不存在: {employee_id}")
    # 脱敏返回：不泄露完整银行账号（写接口 /payroll/structure 的原始数据不受影响）
    masked = dict(structure)
    if masked.get("bank_account"):
        masked["bank_account"] = _mask_bank_account(str(masked["bank_account"]))
    return masked


@router.post("/payroll/structure", summary="设置薪资结构")
async def set_salary_structure(
    req: SetSalaryStructureRequest,
    current_user: dict = Depends(require_permission("payroll", "create")),
):
    """设置员工薪资结构"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    structure = agent.set_salary_structure(
        employee_id=req.employee_id,
        base_salary=req.base_salary,
        allowances=req.allowances,
        deductions=req.deductions,
        bank_account=req.bank_account,
        payment_method=req.payment_method,
        effective_date=req.effective_date,
    )
    return {"message": "薪资结构设置成功", "structure": structure}


@router.post("/payroll/calculate", summary="计算单个员工薪酬")
async def calculate_payroll(
    req: CalculatePayrollRequest,
    current_user: dict = Depends(require_permission("payroll", "create")),
):
    """计算单个员工薪酬（含社保、公积金、个税）"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    record = agent.calculate_payroll(
        employee_id=req.employee_id,
        period=req.period,
    )
    if "error" in record:
        raise HTTPException(status_code=400, detail=record["error"])
    return {"message": "薪酬计算完成", "record": record}


@router.post("/payroll/batch-calculate", summary="批量计算薪酬")
async def batch_calculate_payroll(
    req: BatchCalculatePayrollRequest,
    current_user: dict = Depends(require_permission("payroll", "create")),
):
    """批量计算所有员工薪酬"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    records = agent.batch_calculate_payroll(period=req.period)
    return {"total": len(records), "period": req.period, "records": records}


@router.get("/payroll/records", summary="获取薪酬记录")
async def get_payroll_records(
    employee_id: Optional[str] = Query(None, description="按员工编号过滤"),
    period: Optional[str] = Query(None, description="按周期过滤 (如 2026-07)"),
    current_user: dict = Depends(require_permission("payroll", "read")),
):
    """获取薪酬记录，可按员工和周期过滤（需 payroll:read 权限）"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    records = agent.get_payroll_records(employee_id=employee_id, period=period)
    return {"total": len(records), "records": records}


@router.get("/payroll/social-insurance", summary="获取社保配置")
async def get_social_insurance_config(
    current_user: dict = Depends(require_permission("payroll", "read")),
):
    """获取社保公积金费率配置（需 payroll:read 权限）"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    return agent.get_social_insurance_config()


@router.put("/payroll/social-insurance", summary="更新社保配置")
async def update_social_insurance_config(
    req: UpdateSocialInsuranceRequest,
    current_user: dict = Depends(require_permission("payroll", "update")),
):
    """更新社保公积金费率配置"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    config = agent.update_social_insurance_config(rates=req.rates)
    return {"message": "社保配置已更新", "config": config}


@router.get("/payroll/stats", summary="薪酬统计")
async def get_payroll_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取薪酬统计信息"""
    agent = get_payroll_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Payroll Agent 未启动")
    return agent.get_stats()
