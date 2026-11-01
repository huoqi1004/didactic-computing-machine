"""
审批工作流路由 (Workflow Routes)
================================

提供审批请求的创建、审批、查询等接口。

端点列表：
    POST   /api/v1/workflow/request          创建审批请求
    GET    /api/v1/workflow/requests         查询审批请求列表
    GET    /api/v1/workflow/requests/{id}    获取审批请求详情
    POST   /api/v1/workflow/requests/{id}/approve  审批通过
    POST   /api/v1/workflow/requests/{id}/reject   审批拒绝
    POST   /api/v1/workflow/requests/{id}/cancel   撤回审批请求
    GET    /api/v1/workflow/pending          获取待我审批的列表
    GET    /api/v1/workflow/stats            工作流统计
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.core.workflow import workflow_engine
from app.api.middleware.auth import get_current_user

import structlog

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/workflow", tags=["审批工作流"])


# ===================================================================
# 请求模型
# ===================================================================

class CreateWorkflowRequest(BaseModel):
    """创建审批请求"""
    request_type: str = Field(..., description="请求类型: permission_grant/permission_revoke/reimbursement/procurement/budget/contract/custom")
    title: str = Field(..., min_length=1, max_length=200, description="申请标题")
    risk_level: str = Field("ordinary", description="风险等级: ordinary/sensitive/high_risk")
    details: dict = Field(default_factory=dict, description="申请详情")
    approver_chain: Optional[list[str]] = Field(None, description="审批人ID列表（不指定则自动生成）")
    timeout_days: Optional[int] = Field(None, description="超时天数（不指定则按风险等级默认）")


class ApproveRequest(BaseModel):
    """审批通过请求"""
    comment: str = Field("", description="审批意见")


class RejectRequest(BaseModel):
    """审批拒绝请求"""
    comment: str = Field(..., min_length=1, description="拒绝理由")


class CancelRequest(BaseModel):
    """撤回请求"""
    reason: str = Field("", description="撤回原因")


# ===================================================================
# 路由
# ===================================================================

@router.post("/request", summary="创建审批请求")
async def create_request(
    req: CreateWorkflowRequest,
    current_user: dict = Depends(get_current_user),
):
    """创建新的审批请求。"""
    # 验证风险等级
    valid_risk_levels = ["ordinary", "sensitive", "high_risk"]
    if req.risk_level not in valid_risk_levels:
        raise HTTPException(
            status_code=400,
            detail=f"无效的风险等级: {req.risk_level}，可选: {', '.join(valid_risk_levels)}"
        )

    try:
        request = workflow_engine.create_request(
            requester_id=current_user["user_id"],
            request_type=req.request_type,
            title=req.title,
            risk_level=req.risk_level,
            details=req.details,
            approver_chain=req.approver_chain,
            timeout_days=req.timeout_days,
        )
        logger.info(
            "workflow.request_created",
            request_id=request["request_id"],
            requester_id=current_user["user_id"],
            request_type=req.request_type,
        )
        return {"message": "审批请求已创建", "request": request}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/requests", summary="查询审批请求列表")
async def list_requests(
    current_user: dict = Depends(get_current_user),
    status: Optional[str] = Query(None, description="按状态过滤: pending/in_review/approved/rejected/expired/cancelled"),
    request_type: Optional[str] = Query(None, description="按类型过滤"),
    mine: bool = Query(False, description="只看我发起的请求"),
):
    """查询审批请求列表，支持多条件过滤。"""
    requester_id = current_user["user_id"] if mine else None
    requests = workflow_engine.list_requests(
        requester_id=requester_id,
        status=status,
        request_type=request_type,
    )
    return {"requests": requests, "count": len(requests)}


@router.get("/requests/{request_id}", summary="获取审批请求详情")
async def get_request(
    request_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取审批请求详情，包含审批记录。"""
    request = workflow_engine.get_request(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    return request


@router.post("/requests/{request_id}/approve", summary="审批通过")
async def approve_request(
    request_id: str,
    req: ApproveRequest,
    current_user: dict = Depends(get_current_user),
):
    """审批通过当前步骤。"""
    try:
        request = workflow_engine.approve(
            request_id=request_id,
            approver_id=current_user["user_id"],
            comment=req.comment,
        )
        logger.info(
            "workflow.approved",
            request_id=request_id,
            approver_id=current_user["user_id"],
            status=request["status"],
        )
        return {"message": "审批通过", "request": request}
    except KeyError:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/requests/{request_id}/reject", summary="审批拒绝")
async def reject_request(
    request_id: str,
    req: RejectRequest,
    current_user: dict = Depends(get_current_user),
):
    """审批拒绝。"""
    try:
        request = workflow_engine.reject(
            request_id=request_id,
            approver_id=current_user["user_id"],
            comment=req.comment,
        )
        logger.info(
            "workflow.rejected",
            request_id=request_id,
            approver_id=current_user["user_id"],
        )
        return {"message": "审批已拒绝", "request": request}
    except KeyError:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/requests/{request_id}/cancel", summary="撤回审批请求")
async def cancel_request(
    request_id: str,
    req: CancelRequest,
    current_user: dict = Depends(get_current_user),
):
    """申请人撤回审批请求。"""
    try:
        request = workflow_engine.cancel(
            request_id=request_id,
            requester_id=current_user["user_id"],
            reason=req.reason,
        )
        logger.info(
            "workflow.cancelled",
            request_id=request_id,
            requester_id=current_user["user_id"],
        )
        return {"message": "审批请求已撤回", "request": request}
    except KeyError:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/pending", summary="获取待我审批的列表")
async def get_pending_approvals(current_user: dict = Depends(get_current_user)):
    """获取当前需要我审批的请求列表。"""
    pending = workflow_engine.get_pending_approvals(current_user["user_id"])
    return {"pending": pending, "count": len(pending)}


@router.get("/stats", summary="工作流统计信息")
async def get_workflow_stats(current_user: dict = Depends(get_current_user)):
    """获取审批工作流引擎的统计信息。"""
    return workflow_engine.get_stats()
