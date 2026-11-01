"""
销售营销 API 路由
==================

提供销售与营销全流程的 HTTP API 端点，覆盖四大子域：

- ``/crm/*``:       客户档案、商机、联系人、分级统计
- ``/sales/*``:      销售订单、目标、预测
- ``/marketing/*``:  营销活动、线索、ROI
- ``/cs/*``:         工单、投诉、满意度、FAQ

所有路由均通过全局 Agent 引用（``_agent_refs``）访问对应 Agent 实例。
Agent 实例由 ``main.py`` 的 lifespan 在应用启动时注入。
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.api.middleware.auth import get_current_user, require_permission
from app.core.rbac import rbac_engine
from app.core.security.audit import audit_chain
from app.api.routes._agent_refs import (
    get_crm_agent,
    get_sales_agent,
    get_marketing_agent,
    get_customer_service_agent,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["销售营销"])


async def _audit_sales_write(
    current_user: dict,
    action: str,
    order_id: str,
    details: dict,
) -> None:
    """
    记录销售写操作审计（D-05：销售下单 100% 审计）。

    失败不抛异常，不影响主流程。
    """
    try:
        await audit_chain.log(
            actor_did=current_user.get("user_id", "unknown"),
            action=action,
            resource=f"sales_order:{order_id}",
            result="success",
            details={
                "username": current_user.get("username", ""),
                **details,
            },
        )
    except Exception as audit_err:  # noqa: BLE001
        logger.warning("sales.audit_failed", action=action, error=str(audit_err))


# =============================================================================
# 请求模型 - CRM
# =============================================================================

class CreateCustomerRequest(BaseModel):
    """创建客户请求"""
    name: str = Field(..., description="客户名称")
    industry: str = Field("", description="所属行业")
    segment: str = Field("C", description="客户分级 (A/B/C)")
    contact_name: str = Field(..., description="主联系人姓名")
    contact_phone: str = Field(..., description="主联系人电话")


class UpdateCustomerRequest(BaseModel):
    """更新客户请求"""
    name: Optional[str] = Field(None, description="客户名称")
    industry: Optional[str] = Field(None, description="所属行业")
    segment: Optional[str] = Field(None, description="客户分级 (A/B/C)")
    status: Optional[str] = Field(None, description="状态 (active/inactive)")


class CreateOpportunityRequest(BaseModel):
    """创建商机请求"""
    customer_id: str = Field(..., description="客户编号")
    name: str = Field(..., description="商机名称")
    amount: float = Field(..., description="商机金额")
    stage: str = Field("qualified", description="商机阶段 (qualified/proposal/negotiation/won/lost)")
    expected_close_date: str = Field(..., description="预计成交日期")


class UpdateOpportunityStageRequest(BaseModel):
    """更新商机阶段请求"""
    stage: str = Field(..., description="新阶段 (qualified/proposal/negotiation/won/lost)")


class AddContactRequest(BaseModel):
    """添加联系人请求"""
    customer_id: str = Field(..., description="客户编号")
    name: str = Field(..., description="联系人姓名")
    title: str = Field("", description="职位")
    phone: str = Field("", description="电话")
    email: str = Field("", description="邮箱")


# =============================================================================
# 请求模型 - 销售
# =============================================================================

class CreateOrderRequest(BaseModel):
    """创建销售订单请求"""
    customer_id: str = Field(..., description="客户编号")
    items: list[dict] = Field(default_factory=list, description="订单明细")
    total_amount: float = Field(..., description="订单总金额")
    delivery_date: str = Field(..., description="交付日期")


class SetTargetRequest(BaseModel):
    """设定销售目标请求"""
    salesperson_id: str = Field(..., description="销售人员编号")
    period: str = Field(..., description="周期 (如 2026Q3)")
    amount: float = Field(..., description="目标金额")
    product_line: str = Field("", description="产品线")


# =============================================================================
# 请求模型 - 营销
# =============================================================================

class CreateCampaignRequest(BaseModel):
    """创建营销活动请求"""
    name: str = Field(..., description="活动名称")
    budget: float = Field(..., description="预算")
    channels: list[str] = Field(default_factory=list, description="渠道列表")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    target_audience: str = Field("", description="目标受众")


class CompleteCampaignRequest(BaseModel):
    """完成营销活动请求"""
    results: dict = Field(default_factory=dict, description="活动结果 (含 revenue/actual_spend 等)")


class AddLeadRequest(BaseModel):
    """添加线索请求"""
    name: str = Field(..., description="线索姓名")
    source: str = Field(..., description="来源")
    contact_info: str = Field("", description="联系方式")
    interest: str = Field("", description="兴趣/需求")
    campaign_id: Optional[str] = Field(None, description="关联活动编号")


class QualifyLeadRequest(BaseModel):
    """线索资格判定请求"""
    qualification_score: float = Field(..., description="资格评分 (0-100)")
    notes: str = Field("", description="备注")


# =============================================================================
# 请求模型 - 客服
# =============================================================================

class CreateTicketRequest(BaseModel):
    """创建工单请求"""
    customer_id: str = Field(..., description="客户编号")
    subject: str = Field(..., description="工单主题")
    description: str = Field("", description="问题描述")
    priority: str = Field("medium", description="优先级 (low/medium/high/urgent)")
    category: str = Field("general", description="分类")


class UpdateTicketStatusRequest(BaseModel):
    """更新工单状态请求"""
    status: str = Field(..., description="新状态 (open/in_progress/resolved/closed)")
    resolution: Optional[str] = Field(None, description="解决方案")


class AssignTicketRequest(BaseModel):
    """分配工单请求"""
    agent_id: str = Field(..., description="客服人员编号")


class CreateComplaintRequest(BaseModel):
    """创建投诉请求"""
    customer_id: str = Field(..., description="客户编号")
    subject: str = Field(..., description="投诉主题")
    description: str = Field("", description="投诉详情")
    severity: str = Field("medium", description="严重程度 (low/medium/high/critical)")
    category: str = Field("general", description="分类")


class ResolveComplaintRequest(BaseModel):
    """解决投诉请求"""
    resolution: str = Field(..., description="解决方案")
    follow_up: bool = Field(False, description="是否需要后续跟进")


class RecordSatisfactionRequest(BaseModel):
    """记录满意度请求"""
    customer_id: str = Field(..., description="客户编号")
    ticket_id: str = Field(..., description="工单编号")
    score: int = Field(..., description="满意度评分 (1-5)", ge=1, le=5)
    feedback: str = Field("", description="反馈意见")


class AddFaqRequest(BaseModel):
    """添加 FAQ 请求"""
    question: str = Field(..., description="问题")
    answer: str = Field(..., description="答案")
    category: str = Field("general", description="分类")


# =============================================================================
# CRM 路由
# =============================================================================

@router.get("/crm/customers", summary="获取客户列表")
async def get_customers(
    segment: Optional[str] = Query(None, description="按分级过滤 (A/B/C)"),
    industry: Optional[str] = Query(None, description="按行业过滤"),
    skip: int = Query(0, ge=0, description="分页偏移（路线图 2.4）"),
    limit: int = Query(100, ge=1, le=500, description="每页数量上限"),
    current_user: dict = Depends(get_current_user),
):
    """
    获取客户列表，可按分级和行业过滤。

    行级隔离（路线图 2.9 首批，灰度）：无 ``crm:manage`` 权限的用户
    仅能看到归属本部门的客户（owner_department）；跨部门客户被过滤并
    记录观察日志。
    """
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    customers = agent.get_customers(segment=segment, industry=industry)

    # ---- 2.9 行级隔离首批（灰度：过滤 + 观察日志）----
    user_permissions = rbac_engine.get_user_permissions(current_user["user_id"])
    if "crm:manage" not in user_permissions:
        user_department = current_user.get("department", "")
        visible = [
            c for c in customers
            if not c.get("owner_department")
            or c.get("owner_department") == user_department
        ]
        filtered_out = len(customers) - len(visible)
        if filtered_out > 0:
            logger.info(
                "row_level_filter_applied",
                resource="customers",
                user_id=current_user["user_id"],
                department=user_department,
                filtered_out=filtered_out,
            )
        customers = visible

    total = len(customers)
    return {"total": total, "customers": customers[skip : skip + limit]}


@router.get("/crm/customers/{customer_id}", summary="获取客户详情")
async def get_customer(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个客户详情（含联系人和商机）"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    customer = agent.get_customer(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"客户不存在: {customer_id}")
    return customer


@router.post("/crm/customers", summary="创建客户")
async def create_customer(
    req: CreateCustomerRequest,
    current_user: dict = Depends(require_permission("crm", "create")),
):
    """创建客户档案（自动创建主联系人）"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    customer = agent.create_customer(
        name=req.name,
        industry=req.industry,
        segment=req.segment,
        contact_name=req.contact_name,
        contact_phone=req.contact_phone,
    )
    return {"message": "客户创建成功", "customer": customer}


@router.put("/crm/customers/{customer_id}", summary="更新客户")
async def update_customer(
    customer_id: str,
    req: UpdateCustomerRequest,
    current_user: dict = Depends(require_permission("crm", "update")),
):
    """更新客户信息"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    update_data = req.model_dump(exclude_none=True)
    try:
        customer = agent.update_customer(customer_id, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "客户更新成功", "customer": customer}


@router.get("/crm/opportunities", summary="获取商机列表")
async def get_opportunities(
    customer_id: Optional[str] = Query(None, description="按客户编号过滤"),
    status: Optional[str] = Query(None, description="按状态过滤 (open/won/lost)"),
    current_user: dict = Depends(get_current_user),
):
    """获取商机列表，可按客户和状态过滤"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    opportunities = agent.get_opportunities(customer_id=customer_id, status=status)
    return {"total": len(opportunities), "opportunities": opportunities}


@router.post("/crm/opportunities", summary="创建商机")
async def create_opportunity(
    req: CreateOpportunityRequest,
    current_user: dict = Depends(require_permission("crm", "create")),
):
    """创建商机"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    try:
        opportunity = agent.create_opportunity(
            customer_id=req.customer_id,
            name=req.name,
            amount=req.amount,
            stage=req.stage,
            expected_close_date=req.expected_close_date,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "商机创建成功", "opportunity": opportunity}


@router.put("/crm/opportunities/{opportunity_id}/stage", summary="更新商机阶段")
async def update_opportunity_stage(
    opportunity_id: str,
    req: UpdateOpportunityStageRequest,
    current_user: dict = Depends(require_permission("crm", "update")),
):
    """更新商机阶段"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    try:
        opportunity = agent.update_opportunity_stage(opportunity_id, req.stage)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "商机阶段已更新", "opportunity": opportunity}


@router.get("/crm/contacts", summary="获取联系人列表")
async def get_contacts(
    customer_id: Optional[str] = Query(None, description="按客户编号过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取联系人列表，可按客户过滤"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    contacts = agent.get_contacts(customer_id=customer_id)
    return {"total": len(contacts), "contacts": contacts}


@router.post("/crm/contacts", summary="添加联系人")
async def add_contact(
    req: AddContactRequest,
    current_user: dict = Depends(require_permission("crm", "create")),
):
    """添加联系人"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    try:
        contact = agent.add_contact(
            customer_id=req.customer_id,
            name=req.name,
            title=req.title,
            phone=req.phone,
            email=req.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "联系人添加成功", "contact": contact}


@router.get("/crm/segments", summary="获取客户分级统计")
async def get_customer_segments(
    current_user: dict = Depends(get_current_user),
):
    """获取各分级客户数统计"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    return agent.get_customer_segments()


@router.get("/crm/stats", summary="CRM 统计")
async def get_crm_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取 CRM 统计信息"""
    agent = get_crm_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="CRM Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 销售路由
# =============================================================================

@router.get("/sales/orders", summary="获取销售订单列表")
async def get_orders(
    status: Optional[str] = Query(None, description="按状态过滤 (pending/confirmed)"),
    customer_id: Optional[str] = Query(None, description="按客户编号过滤"),
    skip: int = Query(0, ge=0, description="分页偏移（路线图 2.4）"),
    limit: int = Query(100, ge=1, le=500, description="每页数量上限"),
    current_user: dict = Depends(get_current_user),
):
    """获取销售订单列表，可按状态和客户过滤（返回 total 为过滤后总数）"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    orders = agent.get_orders(status=status, customer_id=customer_id)
    total = len(orders)
    return {"total": total, "orders": orders[skip : skip + limit]}


@router.get("/sales/orders/{order_id}", summary="获取订单详情")
async def get_order(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个订单详情"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    order = agent.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"订单不存在: {order_id}")
    return order


@router.post("/sales/orders", summary="创建销售订单")
async def create_order(
    req: CreateOrderRequest,
    current_user: dict = Depends(require_permission("sales", "create")),
):
    """创建销售订单"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    order = await agent.create_order(
        customer_id=req.customer_id,
        items=req.items,
        total_amount=req.total_amount,
        delivery_date=req.delivery_date,
    )
    await _audit_sales_write(
        current_user, "sales.order.create", order.get("order_id", ""),
        {"customer_id": req.customer_id, "total_amount": req.total_amount},
    )
    return {"message": "销售订单创建成功", "order": order}


@router.post("/sales/orders/{order_id}/confirm", summary="确认订单")
async def confirm_order(
    order_id: str,
    current_user: dict = Depends(require_permission("sales", "update")),
):
    """确认销售订单"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    try:
        order = await agent.confirm_order(order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await _audit_sales_write(
        current_user, "sales.order.confirm", order_id,
        {"customer_id": order.get("customer_id"), "total_amount": order.get("total_amount")},
    )
    return {"message": "订单已确认", "order": order}


@router.get("/sales/targets", summary="获取销售目标")
async def get_targets(
    period: Optional[str] = Query(None, description="按周期过滤 (如 2026Q3)"),
    salesperson_id: Optional[str] = Query(None, description="按销售人员过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取销售目标列表，可按周期和销售人员过滤"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    targets = agent.get_targets(period=period, salesperson_id=salesperson_id)
    return {"total": len(targets), "targets": targets}


@router.post("/sales/targets", summary="设定销售目标")
async def set_target(
    req: SetTargetRequest,
    current_user: dict = Depends(require_permission("sales", "create")),
):
    """设定销售目标"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    target = agent.set_target(
        salesperson_id=req.salesperson_id,
        period=req.period,
        amount=req.amount,
        product_line=req.product_line,
    )
    return {"message": "销售目标设定成功", "target": target}


@router.get("/sales/target-progress", summary="获取目标完成情况")
async def get_target_progress(
    salesperson_id: str = Query(..., description="销售人员编号"),
    period: str = Query(..., description="周期 (如 2026Q3)"),
    current_user: dict = Depends(get_current_user),
):
    """获取销售目标完成率"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    return agent.get_target_progress(salesperson_id=salesperson_id, period=period)


@router.get("/sales/forecast", summary="销售预测")
async def forecast_sales(
    period: str = Query(..., description="预测周期 (如 2026Q4)"),
    method: str = Query("historical", description="预测方法 (historical/trend)"),
    current_user: dict = Depends(get_current_user),
):
    """基于历史数据生成销售预测"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    return agent.forecast_sales(period=period, method=method)


@router.get("/sales/stats", summary="销售统计")
async def get_sales_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取销售统计信息"""
    agent = get_sales_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Sales Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 营销路由
# =============================================================================

@router.get("/marketing/campaigns", summary="获取营销活动列表")
async def get_campaigns(
    status: Optional[str] = Query(None, description="按状态过滤 (planning/active/completed)"),
    current_user: dict = Depends(get_current_user),
):
    """获取营销活动列表，可按状态过滤"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    campaigns = agent.get_campaigns(status=status)
    return {"total": len(campaigns), "campaigns": campaigns}


@router.get("/marketing/campaigns/{campaign_id}", summary="获取活动详情")
async def get_campaign(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个营销活动详情"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    campaign = agent.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"营销活动不存在: {campaign_id}")
    return campaign


@router.post("/marketing/campaigns", summary="创建营销活动")
async def create_campaign(
    req: CreateCampaignRequest,
    current_user: dict = Depends(require_permission("marketing", "create")),
):
    """创建营销活动"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    campaign = agent.create_campaign(
        name=req.name,
        budget=req.budget,
        channels=req.channels,
        start_date=req.start_date,
        end_date=req.end_date,
        target_audience=req.target_audience,
    )
    return {"message": "营销活动创建成功", "campaign": campaign}


@router.post("/marketing/campaigns/{campaign_id}/launch", summary="启动活动")
async def launch_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_permission("marketing", "update")),
):
    """启动营销活动"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    try:
        campaign = await agent.launch_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "营销活动已启动", "campaign": campaign}


@router.post("/marketing/campaigns/{campaign_id}/complete", summary="完成活动")
async def complete_campaign(
    campaign_id: str,
    req: CompleteCampaignRequest,
    current_user: dict = Depends(require_permission("marketing", "update")),
):
    """完成营销活动并记录结果"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    try:
        campaign = await agent.complete_campaign(campaign_id, results=req.results)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "营销活动已完成", "campaign": campaign}


@router.get("/marketing/leads", summary="获取线索列表")
async def get_leads(
    source: Optional[str] = Query(None, description="按来源过滤"),
    status: Optional[str] = Query(None, description="按状态过滤 (new/qualified/hot/unqualified)"),
    current_user: dict = Depends(get_current_user),
):
    """获取线索列表，可按来源和状态过滤"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    leads = agent.get_leads(source=source, status=status)
    return {"total": len(leads), "leads": leads}


@router.post("/marketing/leads", summary="添加线索")
async def add_lead(
    req: AddLeadRequest,
    current_user: dict = Depends(require_permission("marketing", "create")),
):
    """添加线索"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    lead = agent.add_lead(
        name=req.name,
        source=req.source,
        contact_info=req.contact_info,
        interest=req.interest,
        campaign_id=req.campaign_id,
    )
    return {"message": "线索添加成功", "lead": lead}


@router.post("/marketing/leads/{lead_id}/qualify", summary="线索资格判定")
async def qualify_lead(
    lead_id: str,
    req: QualifyLeadRequest,
    current_user: dict = Depends(require_permission("marketing", "update")),
):
    """线索质量评估与资格判定"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    try:
        lead = agent.qualify_lead(
            lead_id=lead_id,
            qualification_score=req.qualification_score,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "线索资格判定完成", "lead": lead}


@router.get("/marketing/roi/{campaign_id}", summary="获取活动 ROI")
async def get_campaign_roi(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取营销活动 ROI 分析"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    roi = agent.get_campaign_roi(campaign_id)
    if not roi.get("found", True):
        raise HTTPException(status_code=404, detail=f"营销活动不存在: {campaign_id}")
    return roi


@router.get("/marketing/stats", summary="营销统计")
async def get_marketing_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取营销统计信息"""
    agent = get_marketing_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Marketing Agent 未启动")
    return agent.get_stats()


# =============================================================================
# 客服路由
# =============================================================================

@router.get("/cs/tickets", summary="获取工单列表")
async def get_tickets(
    status: Optional[str] = Query(None, description="按状态过滤 (open/in_progress/resolved/closed)"),
    priority: Optional[str] = Query(None, description="按优先级过滤 (low/medium/high/urgent)"),
    customer_id: Optional[str] = Query(None, description="按客户编号过滤"),
    current_user: dict = Depends(get_current_user),
):
    """获取工单列表，可按状态、优先级和客户过滤"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    tickets = agent.get_tickets(status=status, priority=priority, customer_id=customer_id)
    return {"total": len(tickets), "tickets": tickets}


@router.get("/cs/tickets/{ticket_id}", summary="获取工单详情")
async def get_ticket(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取单个工单详情"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    ticket = agent.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单不存在: {ticket_id}")
    return ticket


@router.post("/cs/tickets", summary="创建工单")
async def create_ticket(
    req: CreateTicketRequest,
    current_user: dict = Depends(get_current_user),
):
    """创建工单"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    ticket = agent.create_ticket(
        customer_id=req.customer_id,
        subject=req.subject,
        description=req.description,
        priority=req.priority,
        category=req.category,
    )
    return {"message": "工单创建成功", "ticket": ticket}


@router.put("/cs/tickets/{ticket_id}/status", summary="更新工单状态")
async def update_ticket_status(
    ticket_id: str,
    req: UpdateTicketStatusRequest,
    current_user: dict = Depends(require_permission("customer_service", "update")),
):
    """更新工单状态"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    try:
        ticket = agent.update_ticket_status(
            ticket_id=ticket_id,
            status=req.status,
            resolution=req.resolution,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "工单状态已更新", "ticket": ticket}


@router.post("/cs/tickets/{ticket_id}/assign", summary="分配工单")
async def assign_ticket(
    ticket_id: str,
    req: AssignTicketRequest,
    current_user: dict = Depends(require_permission("customer_service", "update")),
):
    """分配工单给客服人员"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    try:
        ticket = agent.assign_ticket(ticket_id=ticket_id, agent_id=req.agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "工单已分配", "ticket": ticket}


@router.get("/cs/complaints", summary="获取投诉列表")
async def get_complaints(
    status: Optional[str] = Query(None, description="按状态过滤 (open/resolved)"),
    severity: Optional[str] = Query(None, description="按严重程度过滤 (low/medium/high/critical)"),
    current_user: dict = Depends(get_current_user),
):
    """获取投诉列表，可按状态和严重程度过滤"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    complaints = agent.get_complaints(status=status, severity=severity)
    return {"total": len(complaints), "complaints": complaints}


@router.post("/cs/complaints", summary="创建投诉")
async def create_complaint(
    req: CreateComplaintRequest,
    current_user: dict = Depends(get_current_user),
):
    """创建投诉记录"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    complaint = agent.create_complaint(
        customer_id=req.customer_id,
        subject=req.subject,
        description=req.description,
        severity=req.severity,
        category=req.category,
    )
    return {"message": "投诉已创建", "complaint": complaint}


@router.post("/cs/complaints/{complaint_id}/resolve", summary="解决投诉")
async def resolve_complaint(
    complaint_id: str,
    req: ResolveComplaintRequest,
    current_user: dict = Depends(require_permission("customer_service", "update")),
):
    """解决投诉"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    try:
        complaint = agent.resolve_complaint(
            complaint_id=complaint_id,
            resolution=req.resolution,
            follow_up=req.follow_up,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "投诉已解决", "complaint": complaint}


@router.get("/cs/surveys", summary="获取满意度调查")
async def get_satisfaction_surveys(
    customer_id: Optional[str] = Query(None, description="按客户编号过滤"),
    period: Optional[str] = Query(None, description="按周期过滤 (如 2026-07)"),
    current_user: dict = Depends(get_current_user),
):
    """获取满意度调查列表，可按客户和周期过滤"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    surveys = agent.get_satisfaction_surveys(customer_id=customer_id, period=period)
    return {"total": len(surveys), "surveys": surveys}


@router.post("/cs/surveys", summary="记录满意度")
async def record_satisfaction(
    req: RecordSatisfactionRequest,
    current_user: dict = Depends(get_current_user),
):
    """记录客户满意度调查"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    survey = agent.record_satisfaction(
        customer_id=req.customer_id,
        ticket_id=req.ticket_id,
        score=req.score,
        feedback=req.feedback,
    )
    return {"message": "满意度已记录", "survey": survey}


@router.get("/cs/faq", summary="搜索 FAQ")
async def search_faq(
    query: str = Query(..., description="搜索关键词"),
    current_user: dict = Depends(get_current_user),
):
    """搜索 FAQ 知识库"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    results = agent.search_faq(query=query)
    return {"total": len(results), "results": results}


@router.post("/cs/faq", summary="添加 FAQ")
async def add_faq(
    req: AddFaqRequest,
    current_user: dict = Depends(require_permission("customer_service", "create")),
):
    """添加 FAQ 条目"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    faq = agent.add_faq(
        question=req.question,
        answer=req.answer,
        category=req.category,
    )
    return {"message": "FAQ 添加成功", "faq": faq}


@router.get("/cs/stats", summary="客服统计")
async def get_cs_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取客服统计信息"""
    agent = get_customer_service_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Customer Service Agent 未启动")
    return agent.get_stats()
