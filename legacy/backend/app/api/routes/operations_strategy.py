"""
运营战略 API 路由
==================

提供企业运营与战略管理全流程的 HTTP API 端点，覆盖九大子域：

- ``/legal/*``:     法务（法律咨询/诉讼/法规/合同审查/统计）
- ``/risk/*``:      风险（风险识别/评估/缓解/预警/矩阵/统计）
- ``/contracts/*``: 合同（合同全生命周期/模板/到期提醒/统计）
- ``/quality/*``:   质量（检验/标准/改进/统计）
- ``/assets/*``:    资产（资产登记/折旧/盘点/处置/统计）
- ``/projects/*``:  项目（项目/里程碑/成本/进度/统计）
- ``/budget/*``:    预算（预算/审批/执行/差异/调整/统计）
- ``/bi/*``:        商业智能（报告/仪表盘/KPI/趋势分析/统计）
- ``/esg/*``:       ESG（指标/报告/碳排放/社会责任/评分/统计）

所有路由均通过全局 Agent 引用（``_agent_refs``）访问对应 Agent 实例。
Agent 实例由 ``main.py`` 的 lifespan 在应用启动时注入。

注意：为避免 FastAPI 路径匹配冲突，各 Agent 下静态子路径
（如 ``/risk/stats``、``/contracts/templates``）均先于动态路径
（如 ``/risk/{risk_id}``、``/contracts/{contract_id}``）声明。
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.api.middleware.auth import get_current_user, require_permission
from app.api.routes._agent_refs import (
    get_legal_agent,
    get_risk_agent,
    get_contract_agent,
    get_quality_agent,
    get_asset_agent,
    get_project_agent,
    get_budget_agent,
    get_bi_agent,
    get_esg_agent,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["运营战略"])


# =============================================================================
# 请求模型 - 法务
# =============================================================================

class CreateConsultationRequest(BaseModel):
    """创建法律咨询请求"""
    title: str = Field(..., description="咨询标题")
    question: str = Field(..., description="咨询问题")
    department: str = Field(..., description="提出部门")
    priority: str = Field("normal", description="优先级 (low/normal/high/urgent)")


class AnswerConsultationRequest(BaseModel):
    """答复法律咨询请求"""
    answer: str = Field(..., description="答复内容")
    legal_basis: str = Field(..., description="法律依据")


class CreateLawsuitRequest(BaseModel):
    """创建诉讼请求"""
    title: str = Field(..., description="案件标题")
    defendant: str = Field(..., description="被告")
    cause: str = Field(..., description="案由")
    amount: float = Field(..., description="涉案金额")
    court: str = Field(..., description="受理法院")
    filing_date: str = Field(..., description="立案日期")


class UpdateLawsuitStatusRequest(BaseModel):
    """更新诉讼状态请求"""
    status: str = Field(..., description="新状态 (filed/in_progress/ended/closed)")
    notes: str = Field("", description="备注")


class AddRegulationRequest(BaseModel):
    """新增法规请求"""
    title: str = Field(..., description="法规标题")
    category: str = Field(..., description="法规分类")
    content: str = Field(..., description="法规内容")
    effective_date: str = Field(..., description="生效日期")


class ReviewContractRequest(BaseModel):
    """合同审查请求"""
    contract_text: str = Field(..., description="合同文本")
    contract_type: str = Field(..., description="合同类型")
    amount: float = Field(..., description="合同金额")


# =============================================================================
# 请求模型 - 风险
# =============================================================================

class IdentifyRiskRequest(BaseModel):
    """识别风险请求"""
    title: str = Field(..., description="风险标题")
    category: str = Field(..., description="风险类别")
    description: str = Field(..., description="风险描述")
    source: str = Field(..., description="风险来源")


class AssessRiskRequest(BaseModel):
    """评估风险请求"""
    probability: float = Field(..., description="发生概率 (0-1)", ge=0, le=1)
    impact: float = Field(..., description="影响程度 (0-1)", ge=0, le=1)
    owner: str = Field(..., description="风险责任人")


class MitigateRiskRequest(BaseModel):
    """缓解风险请求"""
    mitigation_plan: str = Field(..., description="缓解计划")
    owner: str = Field(..., description="负责人")
    due_date: str = Field(..., description="截止日期")


# =============================================================================
# 请求模型 - 合同
# =============================================================================

class CreateContractRequest(BaseModel):
    """创建合同请求"""
    title: str = Field(..., description="合同标题")
    type: str = Field(..., description="合同类型")
    counterparty: str = Field(..., description="相对方")
    amount: float = Field(..., description="合同金额")
    department: str = Field(..., description="归属部门")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    terms: list[dict] = Field(default_factory=list, description="合同条款")


class ApproveContractRequest(BaseModel):
    """审批合同请求"""
    approved_by: str = Field(..., description="审批人")


class CompleteContractRequest(BaseModel):
    """完成合同请求"""
    completion_notes: str = Field("", description="完成备注")


class CreateTemplateRequest(BaseModel):
    """创建合同模板请求"""
    name: str = Field(..., description="模板名称")
    type: str = Field(..., description="合同类型")
    content: str = Field(..., description="模板内容")


# =============================================================================
# 请求模型 - 质量
# =============================================================================

class CreateInspectionRequest(BaseModel):
    """创建检验请求"""
    product: str = Field(..., description="产品名称")
    batch: str = Field(..., description="批次号")
    inspector: str = Field(..., description="检验员")
    standard_id: Optional[str] = Field(None, description="质量标准编号")


class RecordResultRequest(BaseModel):
    """记录检验结果请求"""
    result: str = Field(..., description="检验结果 (pass/fail)")
    defects: Optional[list[dict]] = Field(None, description="缺陷列表")
    notes: str = Field("", description="备注")


class CreateStandardRequest(BaseModel):
    """创建质量标准请求"""
    name: str = Field(..., description="标准名称")
    category: str = Field(..., description="标准分类")
    criteria: list[dict] = Field(default_factory=list, description="判定条件")
    threshold: float = Field(95.0, description="合格阈值")


class CreateImprovementRequest(BaseModel):
    """创建改进请求"""
    title: str = Field(..., description="改进标题")
    description: str = Field(..., description="改进描述")
    root_cause: str = Field(..., description="根本原因")
    action_plan: str = Field(..., description="行动计划")
    owner: str = Field(..., description="负责人")
    due_date: str = Field(..., description="截止日期")


class CompleteImprovementRequest(BaseModel):
    """完成改进请求"""
    result: str = Field(..., description="改进结果")
    notes: str = Field("", description="备注")


# =============================================================================
# 请求模型 - 资产
# =============================================================================

class RegisterAssetRequest(BaseModel):
    """登记资产请求"""
    name: str = Field(..., description="资产名称")
    category: str = Field(..., description="资产类别")
    original_value: float = Field(..., description="原值")
    purchase_date: str = Field(..., description="购置日期")
    location: str = Field(..., description="存放位置")
    useful_life: int = Field(..., description="使用年限 (年)")
    salvage_rate: float = Field(5.0, description="残值率 (%)")


class CalculateDepreciationRequest(BaseModel):
    """计算折旧请求"""
    period: str = Field(..., description="折旧期间 (如 2026-07)")


class BatchDepreciationRequest(BaseModel):
    """批量折旧请求"""
    period: str = Field(..., description="折旧期间 (如 2026-07)")


class CreateInventoryRequest(BaseModel):
    """创建盘点请求"""
    name: str = Field(..., description="盘点名称")
    scope: str = Field(..., description="盘点范围")
    date: str = Field(..., description="盘点日期")


class RecordInventoryResultRequest(BaseModel):
    """记录盘点结果请求"""
    asset_id: str = Field(..., description="资产编号")
    found: bool = Field(True, description="是否盘点到")
    condition: str = Field("good", description="资产状况 (good/fair/poor)")
    notes: str = Field("", description="备注")


class DisposeAssetRequest(BaseModel):
    """处置资产请求"""
    disposal_method: str = Field(..., description="处置方式 (sale/scrapping/donation)")
    reason: str = Field(..., description="处置原因")
    disposal_value: float = Field(0, description="处置残值")


# =============================================================================
# 请求模型 - 项目
# =============================================================================

class CreateProjectRequest(BaseModel):
    """创建项目请求"""
    name: str = Field(..., description="项目名称")
    description: str = Field(..., description="项目描述")
    manager: str = Field(..., description="项目经理")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    budget: float = Field(..., description="预算")
    department: str = Field(..., description="归属部门")


class UpdateProjectStatusRequest(BaseModel):
    """更新项目状态请求"""
    status: str = Field(..., description="新状态 (planning/in_progress/on_hold/completed/cancelled)")


class CreateMilestoneRequest(BaseModel):
    """创建里程碑请求"""
    name: str = Field(..., description="里程碑名称")
    planned_date: str = Field(..., description="计划日期")
    description: str = Field("", description="描述")


class CompleteMilestoneRequest(BaseModel):
    """完成里程碑请求"""
    actual_date: str = Field(..., description="实际完成日期")
    notes: str = Field("", description="备注")


class RecordCostRequest(BaseModel):
    """记录项目成本请求"""
    category: str = Field(..., description="成本类别")
    amount: float = Field(..., description="金额")
    description: str = Field(..., description="成本描述")
    date: str = Field(..., description="发生日期")


# =============================================================================
# 请求模型 - 预算
# =============================================================================

class CreateBudgetRequest(BaseModel):
    """创建预算请求"""
    period: str = Field(..., description="预算周期 (如 2026Q3)")
    department: str = Field(..., description="部门")
    title: str = Field(..., description="预算标题")
    amount: float = Field(..., description="预算总额")
    items: Optional[list[dict]] = Field(None, description="预算明细")


class ApproveBudgetRequest(BaseModel):
    """审批预算请求"""
    approved_by: str = Field(..., description="审批人")


class RecordExecutionRequest(BaseModel):
    """记录预算执行请求"""
    actual_amount: float = Field(..., description="实际金额")
    description: str = Field(..., description="执行描述")
    date: str = Field(..., description="发生日期")
    reference_id: Optional[str] = Field(None, description="关联单据编号")
    reference_type: Optional[str] = Field(None, description="关联单据类型")


class AdjustBudgetRequest(BaseModel):
    """调整预算请求"""
    new_amount: float = Field(..., description="调整后金额")
    reason: str = Field(..., description="调整原因")
    adjusted_by: str = Field(..., description="调整人")


# =============================================================================
# 请求模型 - 商业智能
# =============================================================================

class GenerateReportRequest(BaseModel):
    """生成报告请求"""
    type: str = Field(..., description="报告类型")
    period: str = Field(..., description="报告周期")
    title: str = Field(..., description="报告标题")
    description: str = Field("", description="报告描述")


class UpdateDashboardRequest(BaseModel):
    """更新仪表盘请求"""
    kpi_ids: Optional[list[str]] = Field(None, description="KPI 编号列表")
    layout: Optional[dict] = Field(None, description="布局配置")


class AddKpiRequest(BaseModel):
    """添加 KPI 请求"""
    name: str = Field(..., description="KPI 名称")
    target: float = Field(..., description="目标值")
    current: float = Field(..., description="当前值")
    unit: str = Field("", description="单位")
    direction: str = Field("higher", description="方向 (higher/lower)")
    weight: float = Field(1.0, description="权重")
    dashboard_id: Optional[str] = Field(None, description="所属仪表盘编号")


class AnalyzeTrendRequest(BaseModel):
    """趋势分析请求"""
    metric: str = Field(..., description="分析指标")
    period_range: str = Field(..., description="周期范围 (如 2026-01:2026-07)")
    method: str = Field("linear", description="分析方法 (linear/moving_average/exponential)")


# =============================================================================
# 请求模型 - ESG
# =============================================================================

class UpdateIndicatorRequest(BaseModel):
    """更新 ESG 指标请求"""
    value: float = Field(..., description="指标值")
    updated_by: str = Field("system", description="更新人")


class GenerateEsgReportRequest(BaseModel):
    """生成 ESG 报告请求"""
    period: str = Field(..., description="报告周期")
    title: str = Field("ESG Report", description="报告标题")
    format: str = Field("standard", description="报告格式 (standard/detailed/summary)")


class RecordCarbonRequest(BaseModel):
    """记录碳排放请求"""
    scope: str = Field(..., description="排放范围 (scope1/scope2/scope3)")
    amount: float = Field(..., description="排放量")
    unit: str = Field(..., description="单位 (如 tCO2e)")
    source: str = Field(..., description="排放来源")
    period: str = Field(..., description="周期 (如 2026Q2)")


class CreateSocialActivityRequest(BaseModel):
    """创建社会责任活动请求"""
    name: str = Field(..., description="活动名称")
    type: str = Field(..., description="活动类型")
    date: str = Field(..., description="活动日期")
    description: str = Field(..., description="活动描述")
    budget: float = Field(0, description="活动预算")


# =============================================================================
# 法务路由
# =============================================================================

@router.get("/legal/consultations", summary="获取法律咨询列表")
async def get_consultations(
    status: Optional[str] = Query(None, description="按状态过滤 (open/answered/closed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取法律咨询列表，可按状态过滤"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    consultations = agent.get_consultations(status=status)
    return {"total": len(consultations), "consultations": consultations}


@router.post("/legal/consultations", summary="创建法律咨询")
async def create_consultation(
    req: CreateConsultationRequest,
    current_user: dict = Depends(require_permission("legal", "create")),
):
    """创建法律咨询"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    logger.info("legal.consultation.create", title=req.title, department=req.department)
    consultation = await agent.create_consultation(
        title=req.title,
        question=req.question,
        department=req.department,
        priority=req.priority,
    )
    return {"message": "法律咨询创建成功", "consultation": consultation}


@router.post("/legal/consultations/{consultation_id}/answer", summary="答复法律咨询")
async def answer_consultation(
    consultation_id: str,
    req: AnswerConsultationRequest,
    current_user: dict = Depends(require_permission("legal", "update")),
):
    """答复法律咨询"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    logger.info("legal.consultation.answer", consultation_id=consultation_id)
    try:
        consultation = await agent.answer_consultation(
            consultation_id=consultation_id,
            answer=req.answer,
            legal_basis=req.legal_basis,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "法律咨询已答复", "consultation": consultation}


@router.get("/legal/lawsuits", summary="获取诉讼列表")
async def get_lawsuits(
    status: Optional[str] = Query(None, description="按状态过滤 (filed/in_progress/ended/closed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取诉讼列表，可按状态过滤"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    lawsuits = agent.get_lawsuits(status=status)
    return {"total": len(lawsuits), "lawsuits": lawsuits}


@router.post("/legal/lawsuits", summary="创建诉讼")
async def create_lawsuit(
    req: CreateLawsuitRequest,
    current_user: dict = Depends(require_permission("legal", "create")),
):
    """创建诉讼记录"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    logger.info("legal.lawsuit.create", title=req.title, defendant=req.defendant)
    lawsuit = await agent.create_lawsuit(
        title=req.title,
        defendant=req.defendant,
        cause=req.cause,
        amount=req.amount,
        court=req.court,
        filing_date=req.filing_date,
    )
    return {"message": "诉讼创建成功", "lawsuit": lawsuit}


@router.put("/legal/lawsuits/{lawsuit_id}/status", summary="更新诉讼状态")
async def update_lawsuit_status(
    lawsuit_id: str,
    req: UpdateLawsuitStatusRequest,
    current_user: dict = Depends(require_permission("legal", "update")),
):
    """更新诉讼状态"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    logger.info("legal.lawsuit.update_status", lawsuit_id=lawsuit_id, status=req.status)
    try:
        lawsuit = await agent.update_lawsuit_status(
            lawsuit_id=lawsuit_id,
            status=req.status,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "诉讼状态已更新", "lawsuit": lawsuit}


@router.get("/legal/regulations", summary="获取法规列表")
async def get_regulations(
    category: Optional[str] = Query(None, description="按分类过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取法规列表，可按分类过滤"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    regulations = agent.get_regulations(category=category)
    return {"total": len(regulations), "regulations": regulations}


@router.post("/legal/regulations", summary="新增法规")
async def add_regulation(
    req: AddRegulationRequest,
    current_user: dict = Depends(require_permission("legal", "create")),
):
    """新增法规条目"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    logger.info("legal.regulation.add", title=req.title, category=req.category)
    regulation = await agent.add_regulation(
        title=req.title,
        category=req.category,
        content=req.content,
        effective_date=req.effective_date,
    )
    return {"message": "法规新增成功", "regulation": regulation}


@router.post("/legal/contracts/{contract_id}/review", summary="审查合同")
async def review_contract(
    contract_id: str,
    req: ReviewContractRequest,
    current_user: dict = Depends(require_permission("legal", "update")),
):
    """法务审查合同"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    logger.info("legal.contract.review", contract_id=contract_id, contract_type=req.contract_type)
    try:
        result = await agent.review_contract(
            contract_id=contract_id,
            contract_text=req.contract_text,
            contract_type=req.contract_type,
            amount=req.amount,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "合同审查完成", "review": result}


@router.get("/legal/stats", summary="法务统计")
async def get_legal_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取法务统计信息"""
    agent = get_legal_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Legal Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 风险路由
# =============================================================================

@router.get("/risk", summary="获取风险列表")
async def get_risks(
    category: Optional[str] = Query(None, description="按类别过滤"),
    status: Optional[str] = Query(None, description="按状态过滤 (identified/assessed/mitigated/closed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取风险列表，可按类别和状态过滤"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    risks = agent.get_risks(category=category, status=status)
    return {"total": len(risks), "risks": risks}


@router.get("/risk/alerts", summary="获取风险预警")
async def get_risk_alerts(
    severity: Optional[str] = Query(None, description="按严重程度过滤 (low/medium/high/critical)"),
    current_user: dict = Depends(get_current_user),
):
    """获取风险预警列表，可按严重程度过滤"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    alerts = agent.get_alerts(severity=severity)
    return {"total": len(alerts), "alerts": alerts}


@router.get("/risk/matrix", summary="获取风险矩阵")
async def get_risk_matrix(
    current_user: dict = Depends(get_current_user),
):
    """获取风险矩阵（概率-影响分布）"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    return agent.get_risk_matrix()


@router.get("/risk/stats", summary="风险统计")
async def get_risk_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取风险统计信息"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    return agent.get_stats()


@router.get("/risk/{risk_id}", summary="获取风险详情")
async def get_risk(
    risk_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个风险详情"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    risk = agent.get_risk(risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"风险不存在: {risk_id}")
    return risk


@router.post("/risk/identify", summary="识别风险")
async def identify_risk(
    req: IdentifyRiskRequest,
    current_user: dict = Depends(require_permission("risk", "create")),
):
    """识别新风险"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    logger.info("risk.identify", title=req.title, category=req.category)
    risk = await agent.identify_risk(
        title=req.title,
        category=req.category,
        description=req.description,
        source=req.source,
    )
    return {"message": "风险识别成功", "risk": risk}


@router.post("/risk/{risk_id}/assess", summary="评估风险")
async def assess_risk(
    risk_id: str,
    req: AssessRiskRequest,
    current_user: dict = Depends(require_permission("risk", "update")),
):
    """评估风险概率与影响"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    logger.info("risk.assess", risk_id=risk_id, probability=req.probability, impact=req.impact)
    try:
        risk = await agent.assess_risk(
            risk_id=risk_id,
            probability=req.probability,
            impact=req.impact,
            owner=req.owner,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "风险评估完成", "risk": risk}


@router.post("/risk/{risk_id}/mitigate", summary="缓解风险")
async def mitigate_risk(
    risk_id: str,
    req: MitigateRiskRequest,
    current_user: dict = Depends(require_permission("risk", "update")),
):
    """制定并执行风险缓解计划"""
    agent = get_risk_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Risk Agent 未启动")
    logger.info("risk.mitigate", risk_id=risk_id, owner=req.owner)
    try:
        risk = await agent.mitigate_risk(
            risk_id=risk_id,
            mitigation_plan=req.mitigation_plan,
            owner=req.owner,
            due_date=req.due_date,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "风险缓解计划已制定", "risk": risk}


# =============================================================================
# 合同路由
# =============================================================================

@router.get("/contracts", summary="获取合同列表")
async def get_contracts(
    status: Optional[str] = Query(None, description="按状态过滤 (draft/approved/active/completed/terminated)"),
    type: Optional[str] = Query(None, description="按类型过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取合同列表，可按状态和类型过滤"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    contracts = agent.get_contracts(status=status, type=type)
    return {"total": len(contracts), "contracts": contracts}


@router.get("/contracts/templates", summary="获取合同模板列表")
async def get_contract_templates(
    type: Optional[str] = Query(None, description="按类型过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取合同模板列表，可按类型过滤"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    templates = agent.get_templates(type=type)
    return {"total": len(templates), "templates": templates}


@router.get("/contracts/expiring", summary="获取即将到期合同")
async def get_expiring_contracts(
    days: int = Query(30, description="未来天数 (默认 30 天)", ge=1),
    current_user: dict = Depends(get_current_user),
):
    """获取指定天数内即将到期的合同"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    contracts = agent.get_expiring_contracts(days=days)
    return {"total": len(contracts), "contracts": contracts}


@router.get("/contracts/stats", summary="合同统计")
async def get_contract_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取合同统计信息"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    return agent.get_stats()


@router.get("/contracts/{contract_id}", summary="获取合同详情")
async def get_contract(
    contract_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个合同详情"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    contract = agent.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"合同不存在: {contract_id}")
    return contract


@router.post("/contracts", summary="创建合同")
async def create_contract(
    req: CreateContractRequest,
    current_user: dict = Depends(require_permission("contract", "create")),
):
    """创建合同"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    logger.info("contract.create", title=req.title, type=req.type, counterparty=req.counterparty)
    contract = await agent.create_contract(
        title=req.title,
        type=req.type,
        counterparty=req.counterparty,
        amount=req.amount,
        department=req.department,
        start_date=req.start_date,
        end_date=req.end_date,
        terms=req.terms,
    )
    return {"message": "合同创建成功", "contract": contract}


@router.post("/contracts/{contract_id}/approve", summary="审批合同")
async def approve_contract(
    contract_id: str,
    req: ApproveContractRequest,
    current_user: dict = Depends(require_permission("contract", "update")),
):
    """审批合同"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    logger.info("contract.approve", contract_id=contract_id, approved_by=req.approved_by)
    try:
        contract = await agent.approve_contract(
            contract_id=contract_id,
            approved_by=req.approved_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "合同已审批", "contract": contract}


@router.post("/contracts/{contract_id}/execute", summary="执行合同")
async def execute_contract(
    contract_id: str,
    current_user: dict = Depends(require_permission("contract", "update")),
):
    """执行合同"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    logger.info("contract.execute", contract_id=contract_id)
    try:
        contract = await agent.execute_contract(contract_id=contract_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "合同已执行", "contract": contract}


@router.post("/contracts/{contract_id}/complete", summary="完成合同")
async def complete_contract(
    contract_id: str,
    req: CompleteContractRequest,
    current_user: dict = Depends(require_permission("contract", "update")),
):
    """完成合同"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    logger.info("contract.complete", contract_id=contract_id)
    try:
        contract = await agent.complete_contract(
            contract_id=contract_id,
            completion_notes=req.completion_notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "合同已完成", "contract": contract}


@router.post("/contracts/templates", summary="创建合同模板")
async def create_contract_template(
    req: CreateTemplateRequest,
    current_user: dict = Depends(require_permission("contract", "create")),
):
    """创建合同模板"""
    agent = get_contract_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Contract Agent 未启动")
    logger.info("contract.template.create", name=req.name, type=req.type)
    template = await agent.create_template(
        name=req.name,
        type=req.type,
        content=req.content,
    )
    return {"message": "合同模板创建成功", "template": template}


# =============================================================================
# 质量路由
# =============================================================================

@router.get("/quality/inspections", summary="获取检验列表")
async def get_inspections(
    status: Optional[str] = Query(None, description="按状态过滤 (pending/completed)"),
    product: Optional[str] = Query(None, description="按产品过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取检验列表，可按状态和产品过滤"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    inspections = agent.get_inspections(status=status, product=product)
    return {"total": len(inspections), "inspections": inspections}


@router.get("/quality/inspections/{inspection_id}", summary="获取检验详情")
async def get_inspection(
    inspection_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个检验详情"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    inspection = agent.get_inspection(inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail=f"检验不存在: {inspection_id}")
    return inspection


@router.post("/quality/inspections", summary="创建检验")
async def create_inspection(
    req: CreateInspectionRequest,
    current_user: dict = Depends(require_permission("quality", "create")),
):
    """创建检验任务"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    logger.info("quality.inspection.create", product=req.product, batch=req.batch)
    inspection = agent.create_inspection(
        product=req.product,
        batch=req.batch,
        inspector=req.inspector,
        standard_id=req.standard_id,
    )
    return {"message": "检验创建成功", "inspection": inspection}


@router.post("/quality/inspections/{inspection_id}/result", summary="记录检验结果")
async def record_inspection_result(
    inspection_id: str,
    req: RecordResultRequest,
    current_user: dict = Depends(require_permission("quality", "update")),
):
    """记录检验结果"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    logger.info("quality.inspection.record_result", inspection_id=inspection_id, result=req.result)
    try:
        inspection = await agent.record_result(
            inspection_id=inspection_id,
            result=req.result,
            defects=req.defects,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "检验结果已记录", "inspection": inspection}


@router.get("/quality/standards", summary="获取质量标准列表")
async def get_standards(
    category: Optional[str] = Query(None, description="按分类过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取质量标准列表，可按分类过滤"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    standards = agent.get_standards(category=category)
    return {"total": len(standards), "standards": standards}


@router.post("/quality/standards", summary="创建质量标准")
async def create_standard(
    req: CreateStandardRequest,
    current_user: dict = Depends(require_permission("quality", "create")),
):
    """创建质量标准"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    logger.info("quality.standard.create", name=req.name, category=req.category)
    standard = agent.create_standard(
        name=req.name,
        category=req.category,
        criteria=req.criteria,
        threshold=req.threshold,
    )
    return {"message": "质量标准创建成功", "standard": standard}


@router.get("/quality/improvements", summary="获取改进列表")
async def get_improvements(
    status: Optional[str] = Query(None, description="按状态过滤 (open/in_progress/completed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取质量改进列表，可按状态过滤"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    improvements = agent.get_improvements(status=status)
    return {"total": len(improvements), "improvements": improvements}


@router.post("/quality/improvements", summary="创建改进")
async def create_improvement(
    req: CreateImprovementRequest,
    current_user: dict = Depends(require_permission("quality", "create")),
):
    """创建质量改进项"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    logger.info("quality.improvement.create", title=req.title, owner=req.owner)
    improvement = agent.create_improvement(
        title=req.title,
        description=req.description,
        root_cause=req.root_cause,
        action_plan=req.action_plan,
        owner=req.owner,
        due_date=req.due_date,
    )
    return {"message": "质量改进创建成功", "improvement": improvement}


@router.post("/quality/improvements/{improvement_id}/complete", summary="完成改进")
async def complete_improvement(
    improvement_id: str,
    req: CompleteImprovementRequest,
    current_user: dict = Depends(require_permission("quality", "update")),
):
    """完成质量改进"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    logger.info("quality.improvement.complete", improvement_id=improvement_id)
    try:
        improvement = await agent.complete_improvement(
            improvement_id=improvement_id,
            result=req.result,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "质量改进已完成", "improvement": improvement}


@router.get("/quality/stats", summary="质量统计")
async def get_quality_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取质量统计信息"""
    agent = get_quality_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Quality Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 资产路由
# =============================================================================

@router.get("/assets", summary="获取资产列表")
async def get_assets(
    category: Optional[str] = Query(None, description="按类别过滤"),
    status: Optional[str] = Query(None, description="按状态过滤 (in_use/idle/disposed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取资产列表，可按类别和状态过滤"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    assets = agent.get_assets(category=category, status=status)
    return {"total": len(assets), "assets": assets}


@router.get("/assets/depreciation/records", summary="获取折旧记录")
async def get_depreciation_records(
    asset_id: Optional[str] = Query(None, description="按资产编号过滤"),
    period: Optional[str] = Query(None, description="按期间过滤 (如 2026-07)"),
    current_user: dict = Depends(get_current_user),
):
    """获取折旧记录列表，可按资产和期间过滤"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    records = agent.get_depreciation_records(asset_id=asset_id, period=period)
    return {"total": len(records), "records": records}


@router.get("/assets/inventories", summary="获取盘点列表")
async def get_inventories(
    current_user: dict = Depends(get_current_user),
):
    """获取资产盘点列表"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    inventories = agent.get_inventories()
    return {"total": len(inventories), "inventories": inventories}


@router.get("/assets/stats", summary="资产统计")
async def get_asset_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取资产统计信息"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    return agent.get_stats()


@router.get("/assets/{asset_id}", summary="获取资产详情")
async def get_asset(
    asset_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个资产详情"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    asset = agent.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"资产不存在: {asset_id}")
    return asset


@router.post("/assets", summary="登记资产")
async def register_asset(
    req: RegisterAssetRequest,
    current_user: dict = Depends(require_permission("asset", "create")),
):
    """登记新资产"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    logger.info("asset.register", name=req.name, category=req.category)
    asset = agent.register_asset(
        name=req.name,
        category=req.category,
        original_value=req.original_value,
        purchase_date=req.purchase_date,
        location=req.location,
        useful_life=req.useful_life,
        salvage_rate=req.salvage_rate,
    )
    return {"message": "资产登记成功", "asset": asset}


@router.post("/assets/{asset_id}/depreciation", summary="计算资产折旧")
async def calculate_depreciation(
    asset_id: str,
    req: CalculateDepreciationRequest,
    current_user: dict = Depends(require_permission("asset", "update")),
):
    """计算单个资产折旧"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    logger.info("asset.depreciation.calculate", asset_id=asset_id, period=req.period)
    try:
        result = await agent.calculate_depreciation(asset_id=asset_id, period=req.period)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "折旧计算完成", "depreciation": result}


@router.post("/assets/depreciation/batch", summary="批量计算折旧")
async def batch_depreciation(
    req: BatchDepreciationRequest,
    current_user: dict = Depends(require_permission("asset", "update")),
):
    """批量计算所有资产折旧"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    logger.info("asset.depreciation.batch", period=req.period)
    results = await agent.batch_depreciation(period=req.period)
    return {"message": "批量折旧计算完成", "total": len(results), "depreciations": results}


@router.post("/assets/inventories", summary="创建盘点")
async def create_inventory(
    req: CreateInventoryRequest,
    current_user: dict = Depends(require_permission("asset", "create")),
):
    """创建资产盘点任务"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    logger.info("asset.inventory.create", name=req.name, scope=req.scope)
    inventory = agent.create_inventory(
        name=req.name,
        scope=req.scope,
        date=req.date,
    )
    return {"message": "盘点创建成功", "inventory": inventory}


@router.post("/assets/inventories/{inventory_id}/result", summary="记录盘点结果")
async def record_inventory_result(
    inventory_id: str,
    req: RecordInventoryResultRequest,
    current_user: dict = Depends(require_permission("asset", "update")),
):
    """记录单项资产盘点结果"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    logger.info("asset.inventory.record_result", inventory_id=inventory_id, asset_id=req.asset_id)
    try:
        result = agent.record_inventory_result(
            inventory_id=inventory_id,
            asset_id=req.asset_id,
            found=req.found,
            condition=req.condition,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "盘点结果已记录", "result": result}


@router.post("/assets/{asset_id}/dispose", summary="处置资产")
async def dispose_asset(
    asset_id: str,
    req: DisposeAssetRequest,
    current_user: dict = Depends(require_permission("asset", "update")),
):
    """处置资产"""
    agent = get_asset_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Asset Agent 未启动")
    logger.info("asset.dispose", asset_id=asset_id, disposal_method=req.disposal_method)
    try:
        result = await agent.dispose_asset(
            asset_id=asset_id,
            disposal_method=req.disposal_method,
            reason=req.reason,
            disposal_value=req.disposal_value,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "资产处置完成", "disposal": result}


# =============================================================================
# 项目路由
# =============================================================================

@router.get("/projects", summary="获取项目列表")
async def get_projects(
    status: Optional[str] = Query(None, description="按状态过滤 (planning/in_progress/on_hold/completed/cancelled)"),
    department: Optional[str] = Query(None, description="按部门过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取项目列表，可按状态和部门过滤"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    projects = agent.get_projects(status=status, department=department)
    return {"total": len(projects), "projects": projects}


@router.get("/projects/stats", summary="项目统计")
async def get_project_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取项目统计信息"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    return agent.get_stats()


@router.get("/projects/{project_id}", summary="获取项目详情")
async def get_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个项目详情"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    project = agent.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    return project


@router.post("/projects", summary="创建项目")
async def create_project(
    req: CreateProjectRequest,
    current_user: dict = Depends(require_permission("project", "create")),
):
    """创建项目"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    logger.info("project.create", name=req.name, manager=req.manager, department=req.department)
    project = agent.create_project(
        name=req.name,
        description=req.description,
        manager=req.manager,
        start_date=req.start_date,
        end_date=req.end_date,
        budget=req.budget,
        department=req.department,
    )
    return {"message": "项目创建成功", "project": project}


@router.put("/projects/{project_id}/status", summary="更新项目状态")
async def update_project_status(
    project_id: str,
    req: UpdateProjectStatusRequest,
    current_user: dict = Depends(require_permission("project", "update")),
):
    """更新项目状态"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    logger.info("project.update_status", project_id=project_id, status=req.status)
    try:
        project = await agent.update_project_status(project_id=project_id, status=req.status)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "项目状态已更新", "project": project}


@router.get("/projects/{project_id}/milestones", summary="获取项目里程碑")
async def get_milestones(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取项目的里程碑列表"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    milestones = agent.get_milestones(project_id=project_id)
    return {"total": len(milestones), "milestones": milestones}


@router.post("/projects/{project_id}/milestones", summary="创建里程碑")
async def create_milestone(
    project_id: str,
    req: CreateMilestoneRequest,
    current_user: dict = Depends(require_permission("project", "create")),
):
    """为项目创建里程碑"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    logger.info("project.milestone.create", project_id=project_id, name=req.name)
    try:
        milestone = agent.create_milestone(
            project_id=project_id,
            name=req.name,
            planned_date=req.planned_date,
            description=req.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "里程碑创建成功", "milestone": milestone}


@router.post("/projects/milestones/{milestone_id}/complete", summary="完成里程碑")
async def complete_milestone(
    milestone_id: str,
    req: CompleteMilestoneRequest,
    current_user: dict = Depends(require_permission("project", "update")),
):
    """完成项目里程碑"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    logger.info("project.milestone.complete", milestone_id=milestone_id)
    try:
        milestone = await agent.complete_milestone(
            milestone_id=milestone_id,
            actual_date=req.actual_date,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "里程碑已完成", "milestone": milestone}


@router.post("/projects/{project_id}/costs", summary="记录项目成本")
async def record_project_cost(
    project_id: str,
    req: RecordCostRequest,
    current_user: dict = Depends(require_permission("project", "update")),
):
    """记录项目成本"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    logger.info("project.cost.record", project_id=project_id, category=req.category, amount=req.amount)
    try:
        cost = await agent.record_cost(
            project_id=project_id,
            category=req.category,
            amount=req.amount,
            description=req.description,
            date=req.date,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "项目成本已记录", "cost": cost}


@router.get("/projects/{project_id}/progress", summary="获取项目进度")
async def get_project_progress(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取项目进度（含里程碑与成本消耗）"""
    agent = get_project_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Project Agent 未启动")
    try:
        progress = agent.get_progress(project_id=project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return progress


# =============================================================================
# 预算路由
# =============================================================================

@router.get("/budget", summary="获取预算列表")
async def get_budgets(
    period: Optional[str] = Query(None, description="按周期过滤 (如 2026Q3)"),
    department: Optional[str] = Query(None, description="按部门过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取预算列表，可按周期和部门过滤"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    budgets = agent.get_budgets(period=period, department=department)
    return {"total": len(budgets), "budgets": budgets}


@router.get("/budget/variances", summary="获取预算差异列表")
async def get_variances(
    period: Optional[str] = Query(None, description="按周期过滤"),
    department: Optional[str] = Query(None, description="按部门过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取预算差异列表，可按周期和部门过滤"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    variances = agent.get_variances(period=period, department=department)
    return {"total": len(variances), "variances": variances}


@router.get("/budget/stats", summary="预算统计")
async def get_budget_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取预算统计信息"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    return agent.get_stats()


@router.get("/budget/summary/{period}", summary="获取预算汇总")
async def get_budget_summary(
    period: str,
    current_user: dict = Depends(get_current_user),
):
    """获取指定周期的预算汇总"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    return agent.get_budget_summary(period=period)


@router.get("/budget/{budget_id}", summary="获取预算详情")
async def get_budget(
    budget_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个预算详情"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    budget = agent.get_budget(budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail=f"预算不存在: {budget_id}")
    return budget


@router.post("/budget", summary="创建预算")
async def create_budget(
    req: CreateBudgetRequest,
    current_user: dict = Depends(require_permission("budget", "create")),
):
    """创建预算"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    logger.info("budget.create", period=req.period, department=req.department, title=req.title)
    budget = agent.create_budget(
        period=req.period,
        department=req.department,
        title=req.title,
        amount=req.amount,
        items=req.items,
    )
    return {"message": "预算创建成功", "budget": budget}


@router.post("/budget/{budget_id}/approve", summary="审批预算")
async def approve_budget(
    budget_id: str,
    req: ApproveBudgetRequest,
    current_user: dict = Depends(require_permission("budget", "update")),
):
    """审批预算"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    logger.info("budget.approve", budget_id=budget_id, approved_by=req.approved_by)
    try:
        budget = await agent.approve_budget(budget_id=budget_id, approved_by=req.approved_by)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "预算已审批", "budget": budget}


@router.post("/budget/{budget_id}/execution", summary="记录预算执行")
async def record_budget_execution(
    budget_id: str,
    req: RecordExecutionRequest,
    current_user: dict = Depends(require_permission("budget", "update")),
):
    """记录预算执行情况"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    logger.info("budget.execution.record", budget_id=budget_id, actual_amount=req.actual_amount)
    try:
        execution = await agent.record_execution(
            budget_id=budget_id,
            actual_amount=req.actual_amount,
            description=req.description,
            date=req.date,
            reference_id=req.reference_id,
            reference_type=req.reference_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "预算执行已记录", "execution": execution}


@router.get("/budget/{budget_id}/variance", summary="获取预算差异")
async def get_budget_variance(
    budget_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个预算的差异分析"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    try:
        variance = agent.get_variance(budget_id=budget_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return variance


@router.post("/budget/{budget_id}/adjust", summary="调整预算")
async def adjust_budget(
    budget_id: str,
    req: AdjustBudgetRequest,
    current_user: dict = Depends(require_permission("budget", "update")),
):
    """调整预算"""
    agent = get_budget_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Budget Agent 未启动")
    logger.info("budget.adjust", budget_id=budget_id, new_amount=req.new_amount)
    try:
        budget = await agent.adjust_budget(
            budget_id=budget_id,
            new_amount=req.new_amount,
            reason=req.reason,
            adjusted_by=req.adjusted_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "预算已调整", "budget": budget}


# =============================================================================
# 商业智能路由
# =============================================================================

@router.get("/bi/reports", summary="获取报告列表")
async def get_bi_reports(
    type: Optional[str] = Query(None, description="按类型过滤"),
    period: Optional[str] = Query(None, description="按周期过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取报告列表，可按类型和周期过滤"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    reports = agent.get_reports(type=type, period=period)
    return {"total": len(reports), "reports": reports}


@router.get("/bi/reports/{report_id}", summary="获取报告详情")
async def get_bi_report(
    report_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个报告详情"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    report = agent.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"报告不存在: {report_id}")
    return report


@router.post("/bi/reports/generate", summary="生成报告")
async def generate_bi_report(
    req: GenerateReportRequest,
    current_user: dict = Depends(require_permission("bi", "create")),
):
    """生成分析报告"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    logger.info("bi.report.generate", type=req.type, period=req.period, title=req.title)
    report = await agent.generate_report(
        type=req.type,
        period=req.period,
        title=req.title,
        description=req.description,
    )
    return {"message": "报告生成成功", "report": report}


@router.get("/bi/dashboards", summary="获取仪表盘列表")
async def get_dashboards(
    current_user: dict = Depends(get_current_user),
):
    """获取仪表盘列表"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    dashboards = agent.get_dashboards()
    return {"total": len(dashboards), "dashboards": dashboards}


@router.get("/bi/dashboards/{dashboard_id}", summary="获取仪表盘详情")
async def get_dashboard(
    dashboard_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个仪表盘详情"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    dashboard = agent.get_dashboard(dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail=f"仪表盘不存在: {dashboard_id}")
    return dashboard


@router.put("/bi/dashboards/{dashboard_id}", summary="更新仪表盘")
async def update_dashboard(
    dashboard_id: str,
    req: UpdateDashboardRequest,
    current_user: dict = Depends(require_permission("bi", "update")),
):
    """更新仪表盘（KPI 与布局）"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    logger.info("bi.dashboard.update", dashboard_id=dashboard_id)
    try:
        dashboard = await agent.update_dashboard(
            dashboard_id=dashboard_id,
            kpi_ids=req.kpi_ids,
            layout=req.layout,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "仪表盘已更新", "dashboard": dashboard}


@router.get("/bi/kpis", summary="获取 KPI 列表")
async def get_kpis(
    dashboard_id: Optional[str] = Query(None, description="按仪表盘编号过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取 KPI 列表，可按仪表盘过滤"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    kpis = agent.get_kpis(dashboard_id=dashboard_id)
    return {"total": len(kpis), "kpis": kpis}


@router.post("/bi/kpis", summary="添加 KPI")
async def add_kpi(
    req: AddKpiRequest,
    current_user: dict = Depends(require_permission("bi", "create")),
):
    """添加 KPI 指标"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    logger.info("bi.kpi.add", name=req.name, target=req.target, current=req.current)
    kpi = agent.add_kpi(
        name=req.name,
        target=req.target,
        current=req.current,
        unit=req.unit,
        direction=req.direction,
        weight=req.weight,
        dashboard_id=req.dashboard_id,
    )
    return {"message": "KPI 添加成功", "kpi": kpi}


@router.post("/bi/analyze/trend", summary="趋势分析")
async def analyze_trend(
    req: AnalyzeTrendRequest,
    current_user: dict = Depends(get_current_user),
):
    """分析指标趋势"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    logger.info("bi.analyze.trend", metric=req.metric, period_range=req.period_range, method=req.method)
    return agent.analyze_trend(
        metric=req.metric,
        period_range=req.period_range,
        method=req.method,
    )


@router.get("/bi/stats", summary="商业智能统计")
async def get_bi_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取商业智能统计信息"""
    agent = get_bi_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BI Agent 未启动")
    return agent.get_stats()


# =============================================================================
# ESG 路由
# =============================================================================

@router.get("/esg/indicators", summary="获取 ESG 指标列表")
async def get_esg_indicators(
    category: Optional[str] = Query(None, description="按分类过滤 (environmental/social/governance)"),
    current_user: dict = Depends(get_current_user),
):
    """获取 ESG 指标列表，可按分类过滤"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    indicators = agent.get_indicators(category=category)
    return {"total": len(indicators), "indicators": indicators}


@router.get("/esg/indicators/{indicator_id}", summary="获取 ESG 指标详情")
async def get_esg_indicator(
    indicator_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个 ESG 指标详情"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    indicator = agent.get_indicator(indicator_id)
    if indicator is None:
        raise HTTPException(status_code=404, detail=f"ESG 指标不存在: {indicator_id}")
    return indicator


@router.put("/esg/indicators/{indicator_id}", summary="更新 ESG 指标")
async def update_esg_indicator(
    indicator_id: str,
    req: UpdateIndicatorRequest,
    current_user: dict = Depends(require_permission("esg", "update")),
):
    """更新 ESG 指标值"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    logger.info("esg.indicator.update", indicator_id=indicator_id, value=req.value, updated_by=req.updated_by)
    try:
        indicator = agent.update_indicator(
            indicator_id=indicator_id,
            value=req.value,
            updated_by=req.updated_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "ESG 指标已更新", "indicator": indicator}


@router.get("/esg/reports", summary="获取 ESG 报告列表")
async def get_esg_reports(
    period: Optional[str] = Query(None, description="按周期过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取 ESG 报告列表，可按周期过滤"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    reports = agent.get_reports(period=period)
    return {"total": len(reports), "reports": reports}


@router.post("/esg/reports/generate", summary="生成 ESG 报告")
async def generate_esg_report(
    req: GenerateEsgReportRequest,
    current_user: dict = Depends(require_permission("esg", "create")),
):
    """生成 ESG 报告"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    logger.info("esg.report.generate", period=req.period, title=req.title, format=req.format)
    report = await agent.generate_report(
        period=req.period,
        title=req.title,
        format=req.format,
    )
    return {"message": "ESG 报告生成成功", "report": report}


@router.post("/esg/carbon", summary="记录碳排放")
async def record_carbon(
    req: RecordCarbonRequest,
    current_user: dict = Depends(require_permission("esg", "create")),
):
    """记录碳排放数据"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    logger.info("esg.carbon.record", scope=req.scope, amount=req.amount, period=req.period)
    record = agent.record_carbon(
        scope=req.scope,
        amount=req.amount,
        unit=req.unit,
        source=req.source,
        period=req.period,
    )
    return {"message": "碳排放记录成功", "record": record}


@router.get("/esg/carbon", summary="获取碳排放记录")
async def get_carbon_records(
    scope: Optional[str] = Query(None, description="按排放范围过滤 (scope1/scope2/scope3)"),
    period: Optional[str] = Query(None, description="按周期过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取碳排放记录列表，可按范围和周期过滤"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    records = agent.get_carbon_records(scope=scope, period=period)
    return {"total": len(records), "records": records}


@router.get("/esg/social", summary="获取社会责任活动列表")
async def get_social_activities(
    status: Optional[str] = Query(None, description="按状态过滤 (planned/active/completed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取社会责任活动列表，可按状态过滤"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    activities = agent.get_social_activities(status=status)
    return {"total": len(activities), "activities": activities}


@router.post("/esg/social", summary="创建社会责任活动")
async def create_social_activity(
    req: CreateSocialActivityRequest,
    current_user: dict = Depends(require_permission("esg", "create")),
):
    """创建社会责任活动"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    logger.info("esg.social.create", name=req.name, type=req.type, date=req.date)
    activity = agent.create_social_activity(
        name=req.name,
        type=req.type,
        date=req.date,
        description=req.description,
        budget=req.budget,
    )
    return {"message": "社会责任活动创建成功", "activity": activity}


@router.get("/esg/score", summary="获取 ESG 评分")
async def get_esg_score(
    current_user: dict = Depends(get_current_user),
):
    """获取综合 ESG 评分"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    return agent.get_esg_score()


@router.get("/esg/stats", summary="ESG 统计")
async def get_esg_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取 ESG 统计信息"""
    agent = get_esg_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="ESG Agent 未启动")
    return agent.get_stats()
