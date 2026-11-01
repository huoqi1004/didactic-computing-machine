"""
事件模型定义
所有业务变更以不可变事件记录，构成事件溯源的基础
"""
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Any, Optional
from enum import Enum
import orjson


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, Enum):
    """事件类型枚举"""
    # 库存事件
    INVENTORY_ADJUSTED = "inventory.adjusted"
    STOCK_LOW_WARNING = "inventory.stock_low_warning"
    REPLENISHMENT_PROPOSED = "inventory.replenishment_proposed"
    REPLENISHMENT_APPROVED = "inventory.replenishment_approved"
    REPLENISHMENT_REJECTED = "inventory.replenishment_rejected"
    # 会计事件
    JOURNAL_ENTRY_CREATED = "accounting.journal_entry_created"
    VOUCHER_GENERATED = "accounting.voucher_generated"
    VOUCHER_APPROVED = "accounting.voucher_approved"
    VOUCHER_POSTED = "accounting.voucher_posted"
    # 采购事件
    PURCHASE_REQUESTED = "procurement.purchase_requested"
    PURCHASE_ORDERED = "procurement.purchase_ordered"
    PURCHASE_ORDER_CREATED = "purchase_order_created"
    PURCHASE_ORDER_APPROVED = "purchase_order_approved"
    PURCHASE_ORDER_REJECTED = "purchase_order_rejected"
    PURCHASE_ORDER_UPDATED = "purchase_order_updated"
    SUPPLIER_EVALUATED = "supplier_evaluated"
    DELIVERY_DELAYED = "delivery_delayed"
    # 资金事件
    CASH_FLOW_FORECAST_COMPLETED = "treasury.cash_flow_forecast_completed"
    PAYMENT_SCHEDULED = "treasury.payment_scheduled"
    FUND_TRANSFER_REQUESTED = "treasury.fund_transfer_requested"
    # 内审事件
    AUDIT_ALERT_RAISED = "audit.alert_raised"
    PROCESS_FROZEN = "audit.process_frozen"
    AUDIT_COMPLETED = "audit.completed"
    # 税务事件
    TAX_CALCULATED = "tax.calculated"
    TAX_DECLARATION_GENERATED = "tax.declaration_generated"
    TAX_RISK_WARNING = "tax.risk_warning"
    # 合规事件
    COMPLIANCE_CHECK_PASSED = "compliance.check_passed"
    COMPLIANCE_VIOLATION_FOUND = "compliance.violation_found"
    CONTROL_POINT_UPDATED = "compliance.control_point_updated"
    # Agent事件
    AGENT_REGISTERED = "agent.registered"
    AGENT_STATE_CHANGED = "agent.state_changed"
    AGENT_DECISION_MADE = "agent.decision_made"
    AGENT_CONSENSUS_REACHED = "agent.consensus_reached"
    # 安全事件
    AUTHORIZATION_GRANTED = "security.authorization_granted"
    AUTHORIZATION_REVOKED = "security.authorization_revoked"
    SECURITY_ALERT = "security.alert"
    # 审计事件
    AUDIT_LOG_CREATED = "audit.log_created"
    # 需求事件
    DEMAND_FORECAST_GENERATED = "demand.forecast_generated"
    DEMAND_ANOMALY_DETECTED = "demand.anomaly_detected"
    # 供应事件
    SUPPLY_RISK_WARNING = "supply.risk_warning"
    SUPPLIER_PERFORMANCE_UPDATED = "supply.supplier_performance_updated"
    # 生产事件
    PRODUCTION_ORDER_CREATED = "production.order_created"
    PRODUCTION_COMPLETED = "production.completed"
    PRODUCTION_DELAYED = "production.delayed"
    # 物流事件
    SHIPMENT_DISPATCHED = "logistics.shipment_dispatched"
    DELIVERY_CONFIRMED = "logistics.delivery_confirmed"
    DELIVERY_EXCEPTION = "logistics.delivery_exception"
    # 调度事件
    SCHEDULING_DECISION = "scheduler.decision_made"
    PRIORITY_UPDATED = "scheduler.priority_updated"
    # 发票事件
    INVOICE_CREATED = "invoice.created"
    INVOICE_VERIFIED = "invoice.verified"
    INVOICE_REJECTED = "invoice.rejected"
    INVOICE_CANCELLED = "invoice.cancelled"
    # 报销事件
    REIMBURSEMENT_REQUESTED = "reimbursement.requested"
    REIMBURSEMENT_VERIFIED = "reimbursement.verified"
    REIMBURSEMENT_APPROVED = "reimbursement.approved"
    REIMBURSEMENT_REJECTED = "reimbursement.rejected"
    REIMBURSEMENT_PAID = "reimbursement.paid"
    # HR 事件
    EMPLOYEE_ONBOARDED = "hr.employee_onboarded"
    EMPLOYEE_OFFBOARDED = "hr.employee_offboarded"
    ATTENDANCE_RECORDED = "hr.attendance_recorded"
    LEAVE_REQUESTED = "hr.leave_requested"
    # 招聘事件
    CANDIDATE_CREATED = "recruitment.candidate_created"
    INTERVIEW_SCHEDULED = "recruitment.interview_scheduled"
    OFFER_EXTENDED = "recruitment.offer_extended"
    # 绩效事件
    PERFORMANCE_REVIEWED = "performance.reviewed"
    KPI_UPDATED = "performance.kpi_updated"
    # 薪酬事件
    PAYROLL_CALCULATED = "payroll.calculated"
    SALARY_ADJUSTED = "payroll.salary_adjusted"
    # CRM 事件
    CUSTOMER_CREATED = "crm.customer_created"
    CUSTOMER_UPDATED = "crm.customer_updated"
    OPPORTUNITY_CREATED = "crm.opportunity_created"
    OPPORTUNITY_WON = "crm.opportunity_won"
    OPPORTUNITY_LOST = "crm.opportunity_lost"
    # 销售事件
    SALES_ORDER_CREATED = "sales.order_created"
    SALES_ORDER_CONFIRMED = "sales.order_confirmed"
    SALES_TARGET_UPDATED = "sales.target_updated"
    # 市场事件
    CAMPAIGN_LAUNCHED = "marketing.campaign_launched"
    CAMPAIGN_COMPLETED = "marketing.campaign_completed"
    LEAD_GENERATED = "marketing.lead_generated"
    # 客服事件
    TICKET_CREATED = "cs.ticket_created"
    TICKET_RESOLVED = "cs.ticket_resolved"
    COMPLAINT_RECEIVED = "cs.complaint_received"
    # 法务事件
    CONTRACT_CREATED = "legal.contract_created"
    CONTRACT_APPROVED = "legal.contract_approved"
    CONTRACT_EXPIRED = "legal.contract_expired"
    LAWSUIT_FILED = "legal.lawsuit_filed"
    # 风险事件
    RISK_IDENTIFIED = "risk.identified"
    RISK_ASSESSED = "risk.assessed"
    RISK_MITIGATED = "risk.mitigated"
    RISK_ALERT = "risk.alert"
    # 质量事件
    QUALITY_INSPECTION_PASSED = "quality.inspection_passed"
    QUALITY_INSPECTION_FAILED = "quality.inspection_failed"
    QUALITY_IMPROVEMENT_PROPOSED = "quality.improvement_proposed"
    # 资产事件
    ASSET_REGISTERED = "asset.registered"
    ASSET_DEPRECIATED = "asset.depreciated"
    ASSET_DISPOSED = "asset.disposed"
    # 项目事件
    PROJECT_CREATED = "project.created"
    PROJECT_MILESTONE_REACHED = "project.milestone_reached"
    PROJECT_COMPLETED = "project.completed"
    # 预算事件
    BUDGET_CREATED = "budget.created"
    BUDGET_APPROVED = "budget.approved"
    BUDGET_EXCEEDED = "budget.exceeded"
    # BI 事件
    REPORT_GENERATED = "bi.report_generated"
    DASHBOARD_UPDATED = "bi.dashboard_updated"
    # ESG 事件
    ESG_REPORT_GENERATED = "esg.report_generated"
    ESG_TARGET_UPDATED = "esg.target_updated"


class Event(BaseModel):
    """
    不可变事件基类
    所有业务变更的基础单元，包含完整的上下文信息
    """
    event_id: UUID = Field(default_factory=uuid4, description="事件唯一ID")
    event_type: EventType = Field(description="事件类型")
    aggregate_id: str = Field(description="聚合根ID")
    aggregate_type: str = Field(description="聚合根类型")
    event_data: dict[str, Any] = Field(default_factory=dict, description="事件负载数据")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: datetime = Field(default_factory=utc_now, description="事件发生时间")
    version: int = Field(default=1, description="事件版本号")
    agent_id: Optional[str] = Field(None, description="触发事件的Agent ID")
    security_level: str = Field(default="internal", description="数据安全等级")
    hash_chain: Optional[str] = Field(None, description="前一个事件的哈希（审计链）")

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "aggregate_id": self.aggregate_id,
            "aggregate_type": self.aggregate_type,
            "event_data": self.event_data,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
            "version": self.version,
            "agent_id": self.agent_id,
            "security_level": self.security_level,
            "hash_chain": self.hash_chain,
        }

    def to_json(self) -> bytes:
        """序列化为JSON bytes"""
        return orjson.dumps(self.to_dict())

    def compute_hash(self, prev_hash: Optional[str] = None) -> str:
        """计算事件哈希（用于审计链防篡改）"""
        import hashlib
        content = f"{self.event_id}{self.event_type.value}{self.aggregate_id}"
        content += f"{self.timestamp.isoformat()}{self.version}"
        content += orjson.dumps(
            self.event_data, option=orjson.OPT_SORT_KEYS
        ).decode()
        if prev_hash:
            content += prev_hash
        return hashlib.sha256(content.encode()).hexdigest()


class EventSubscription:
    """事件订阅信息"""
    def __init__(self, subscriber_id: str, event_types: list[EventType],
                 handler: callable, filter_expr: Optional[dict] = None):
        self.subscriber_id = subscriber_id
        self.event_types = event_types
        self.handler = handler
        self.filter_expr = filter_expr or {}

    def matches(self, event: Event) -> bool:
        """检查事件是否匹配订阅条件"""
        if event.event_type not in self.event_types:
            return False
        for key, value in self.filter_expr.items():
            if event.event_data.get(key) != value:
                return False
        return True
