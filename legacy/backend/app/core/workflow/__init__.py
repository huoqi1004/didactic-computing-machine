"""
审批工作流引擎 (Approval Workflow Engine)
=========================================

三级审批工作流：
- 普通（ordinary）：部门经理审批，1个工作日
- 敏感（sensitive）：部门经理 → C-level，2个工作日
- 高危（high-risk）：部门经理 → C-level → 董事长，3个工作日

支持权限授予/收回、报销审批、采购审批等业务场景。
"""
from app.core.workflow.engine import (
    WorkflowEngine,
    workflow_engine,
    RiskLevel,
    RequestStatus,
    RequestType,
    RISK_LEVEL_TIMEOUT_DAYS,
    RISK_LEVEL_CHAIN_LENGTH,
)

__all__ = [
    "WorkflowEngine",
    "workflow_engine",
    "RiskLevel",
    "RequestStatus",
    "RequestType",
    "RISK_LEVEL_TIMEOUT_DAYS",
    "RISK_LEVEL_CHAIN_LENGTH",
]
