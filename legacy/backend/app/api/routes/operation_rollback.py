"""
操作回滚 API（roadmap-logging-sandbox G4.3/G4.4）
==================================================

- GET  /operation-rollback/ledger            账本查询（operation_rollback:read）
- POST /operation-rollback/{id}/precheck     前置检查试运行（read；下游引用清单预览）
- POST /operation-rollback/{id}/request      发起回滚（execute；前置检查默认拒绝
                                              + 落 write_high 审批行，D4 裁决一律人审）
- 决定经既有 approvals/{thread_id}/decide 通道（rollback 分支），
  批准 → 执行补偿 → 全量审计；失败 → compensate_failed + 告警待办，可重放。

红线：账本不可删改；回滚必审批；存在下游引用默认拒绝（红线 6）。
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.middleware.auth import require_permission

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops/operation-rollback", tags=["操作回滚"])


class RollbackRequest(BaseModel):
    """发起回滚请求"""
    reason: str = Field("", max_length=500, description="发起理由（进审批单与审计）")


@router.get("/ledger", summary="回滚账本查询（双键/状态/工具过滤）")
async def query_ledger(
    business_key: str = "",
    trace_id: str = "",
    tool: str = "",
    status: str = "",
    actor: str = "",
    limit: int = 100,
    current_user: dict = Depends(require_permission("operation_rollback", "read")),
):
    from app.core.agent.operation_ledger import ledger_query

    items = await ledger_query(business_key=business_key, trace_id=trace_id,
                               tool=tool, status=status, actor=actor, limit=limit)
    return {"items": items, "total": len(items)}


@router.post("/{ledger_id}/precheck", summary="前置检查试运行（下游引用清单预览）")
async def run_precheck(
    ledger_id: str,
    current_user: dict = Depends(require_permission("operation_rollback", "read")),
):
    from app.core.agent.operation_ledger import ledger_get
    from app.core.agent.operation_ledger import _load_json
    from app.core.agent.tools_v2 import ToolContext, tool_registry_v2

    ledger = await ledger_get(ledger_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail="账本行不存在")
    spec = tool_registry_v2.get(ledger["tool"])
    if spec is None:
        return {"blocked": True, "dependencies": [],
                "message": f"工具 {ledger['tool']} 已不在注册表"}
    if spec.rollback_precheck is None:
        return {"blocked": False, "dependencies": [], "message": "无前置检查登记"}
    ctx = ToolContext(user_id=current_user.get("user_id", "unknown"),
                      agent_key=f"rollback:{ledger['tool']}")
    out = await spec.rollback_precheck(ctx, _load_json(ledger.get("forward_args")) or {},
                                       _load_json(ledger.get("forward_result")))
    return out


@router.post("/{ledger_id}/request", summary="发起回滚（前置检查→write_high 审批）")
async def request_rollback_api(
    ledger_id: str,
    req: RollbackRequest,
    current_user: dict = Depends(require_permission("operation_rollback", "execute")),
):
    from app.core.agent.operation_ledger import request_rollback

    result = await request_rollback(
        ledger_id, requested_by=current_user.get("user_id", "unknown"),
        reason=req.reason,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="账本行不存在")
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result.get("reason", "数据库不可用"))
    # rejected / rejected_precheck / pending_approval 均 200 返回（语义结果）
    return result
