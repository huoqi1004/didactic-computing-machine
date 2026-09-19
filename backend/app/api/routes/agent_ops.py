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


class WorkflowStartRequest(BaseModel):
    """工作流启动请求"""
    input: dict = Field(..., description="工作流输入参数（见各工作流 input_hint）")
    thread_id: str = Field("", max_length=150, description="可选：指定线程ID（默认自动生成）")


@router.get("/tools", summary="工具目录")
async def list_tools(current_user: dict = Depends(get_current_user)):
    """列出治理面注册的全部工具（含风险级/权限要求/调用统计）。"""
    from app.core.agent.governor import agent_governor

    return agent_governor.get_stats()


@router.get("/workflows", summary="可用工作流目录")
async def list_workflows_route(current_user: dict = Depends(get_current_user)):
    """列出全部跨域工作流（名称/描述/输入参数说明）。"""
    from app.core.agent.workflows import list_workflows

    return {"workflows": list_workflows()}


@router.post("/workflows/{name}/start", summary="启动跨域工作流")
async def start_workflow_route(
    name: str,
    req: WorkflowStartRequest,
    current_user: dict = Depends(require_permission("agent", "execute")),
):
    """
    启动跨域业务工作流（报销审批流/采购到支付/合同审批流）。

    遇人审节点时中断并落审批行（审批中心可见），
    经 approvals/{thread_id}/decide 批准后从断点恢复。
    """
    from app.core.agent.workflows import WORKFLOW_CATALOG, start_workflow
    from app.core.agent.tools_v2 import ToolContext
    from app.db.repositories.base import has_db

    if name not in WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"未知工作流: {name}")

    user_id = current_user.get("user_id", "unknown")
    ctx = ToolContext(user_id=user_id, username=current_user.get("username", ""),
                      agent_key=f"workflow:{name}")
    result = await start_workflow(name, req.input, ctx, thread_id=req.thread_id)

    # awaiting → 落审批行（复用审批面板与决定路由）
    if result["status"] == "awaiting_approval" and has_db():
        from app.core.agent.orchestration_service import _create_approval_record

        await _create_approval_record(result.get("interrupt") or {}, ctx)

    await _audit_workflow_start(name, user_id, result["status"])
    return result


async def _audit_workflow_start(name: str, user_id: str, status: str) -> None:
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(
            actor_did=user_id, action=f"workflow.{name}.started",
            resource=f"workflow:{name}", result=status,
            details={"status": status},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.start_audit_failed", error=str(e))


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
