"""
Agent 编排运维路由（路线图 Phase A / A8）
==========================================

端点列表：
    GET  /api/v1/agent-ops/tools                工具目录（治理面注册的全部工具+统计）
    GET  /api/v1/agent-ops/approvals            人审任务列表（审批卡片数据源）
    POST /api/v1/agent-ops/approvals/{thread_id}/decide   审批决定（恢复中断的图执行）
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.middleware.auth import get_current_user, require_permission
from app.api.middleware.ws_manager import ws_connection_manager
from app.core.agent.orchestration_service import (
    list_approvals,
    resume_after_decision,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops", tags=["Agent编排"])


class ApprovalDecisionRequest(BaseModel):
    """审批决定"""
    approved: bool = Field(..., description="是否批准")
    comment: str = Field("", max_length=500, description="审批意见")


@router.get("/tools", summary="工具目录")
async def list_tools(current_user: dict = Depends(get_current_user)):
    """列出治理面注册的全部工具（含风险级/权限要求/调用统计）。"""
    from app.core.agent.governor import agent_governor

    return agent_governor.get_stats()


@router.get("/approvals", summary="人审任务列表")
async def get_approvals(
    status: str = Query("pending", description="状态过滤：pending/approved/rejected/executed/all"),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_permission("agent", "read")),
):
    """查询 Agent 工具人审任务（write_high 中断产物）。"""
    return {"status": status, "approvals": await list_approvals(status=status, limit=limit)}


@router.post("/approvals/{thread_id}/decide", summary="审批决定（恢复/驳回）")
async def decide_approval(
    thread_id: str,
    req: ApprovalDecisionRequest,
    current_user: dict = Depends(require_permission("agent", "approve")),
):
    """
    对人审任务做出决定。

    - 批准：图从断点恢复（Command(resume)），write_high 工具以 approved=True
      真实执行，最终回复广播回原频道；
    - 驳回：图恢复后工具返回驳回文案，Agent 综合答复。
    """
    result = await resume_after_decision(
        thread_id=thread_id,
        approved=req.approved,
        comment=req.comment,
        decided_by=current_user.get("user_id", "unknown"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="审批任务不存在")

    # 最终回复广播回原频道（用户在聊天里直接看到执行结果）
    if result.get("channel_id"):
        try:
            await ws_connection_manager.broadcast_to_channel(result["channel_id"], {
                "type": "message",
                "channel_id": result["channel_id"],
                "message": {
                    "message_id": f"approval-{thread_id}",
                    "channel_id": result["channel_id"],
                    "sender_id": "approval_system",
                    "sender_type": "agent",
                    "sender_name": "审批执行结果",
                    "content": result["reply"],
                    "message_type": "text",
                    "created_at": None,
                },
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_ops.broadcast_failed", error=str(e))

    return {
        "thread_id": thread_id,
        "status": result["status"],
        "reply": result["reply"],
    }
