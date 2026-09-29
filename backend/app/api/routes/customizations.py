"""
Agent 定制化管理 API（路线图 Phase F / F1.5）
==============================================

端点列表：
    GET  /api/v1/agent-ops/customizations                       生效中的定制列表（合成视图）
    GET  /api/v1/agent-ops/customizations/versions              变更历史
    PUT  /api/v1/agent-ops/customizations/{type}/{target}       应用定制变更（收紧即时/放宽转人审）
    POST /api/v1/agent-ops/customizations/{type}/{target}/rollback   回滚到历史版本
    POST /api/v1/agent-ops/customizations/{type}/{target}/decide  审批决定（放宽类）

安全：
- 权限：customization:update（变更）/ customization:approve（决定）/ customization:read（查看）
- 防重放：Idempotency-Key 头（5 分钟窗口去重）
- 放宽类变更自动转人审（红线 2）；全部变更审计
"""
from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.middleware.auth import get_current_user, require_permission

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops/customizations", tags=["Agent定制"])


class CustomizationRequest(BaseModel):
    """定制变更请求"""
    overrides: dict = Field(..., description="覆盖内容（按类型 schema）")
    enabled: bool = Field(True, description="是否启用")
    comment: str = Field("", max_length=512, description="变更说明")


class RollbackRequest(BaseModel):
    """回滚请求"""
    version_id: int = Field(..., description="历史版本ID")


class DecideRequest(BaseModel):
    """定制审批决定"""
    approved: bool = Field(..., description="是否批准")
    comment: str = Field("", max_length=512, description="审批意见")


@router.get("", summary="生效中的定制列表（合成视图）")
async def list_customizations(current_user: dict = Depends(require_permission("customization", "read"))):
    from app.core.agent.customization import customization_service

    await customization_service.ensure_fresh()
    return {"customizations": customization_service.list_active()}


@router.get("/versions", summary="变更历史")
async def list_versions(
    type: Optional[str] = Query(None, description="按类型过滤"),
    target: Optional[str] = Query(None, description="按目标过滤"),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return {"versions": []}
    try:
        from sqlalchemy import select

        from app.db.models import customization_versions

        async with session_scope() as session:
            stmt = select(customization_versions).order_by(
                customization_versions.c.id.desc()).limit(limit)
            if type:
                stmt = stmt.where(customization_versions.c.type == type)
            if target:
                stmt = stmt.where(customization_versions.c.target == target)
            rows = (await session.execute(stmt)).all()
        return {"versions": [
            {"id": r.id, "type": r.type, "target": r.target,
             "overrides": r.overrides, "status": r.status,
             "changed_by": r.changed_by, "approved_by": r.approved_by,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ]}
    except Exception as e:  # noqa: BLE001
        logger.error("customizations.versions_failed", error=str(e))
        raise HTTPException(status_code=500, detail="变更历史查询失败")


@router.put("/{ctype}/{target}", summary="应用定制变更")
async def apply_customization(
    ctype: str,
    target: str,
    req: CustomizationRequest,
    idempotency_key: str = Header("", alias="Idempotency-Key"),
    current_user: dict = Depends(require_permission("customization", "update")),
):
    """应用定制变更。收紧类即时生效；放宽类（风险降级/停用→启用/阈值上调）自动转人审。"""
    from app.core.agent.customization import CustomizationError, customization_service

    try:
        result = await customization_service.apply_change(
            ctype=ctype, target=target, overrides=req.overrides,
            enabled=req.enabled, changed_by=current_user.get("user_id", "unknown"),
            comment=req.comment, idempotency_key=idempotency_key,
        )
    except CustomizationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result["status"] == "duplicate":
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


@router.post("/{ctype}/{target}/rollback", summary="回滚到历史版本")
async def rollback_customization(
    ctype: str,
    target: str,
    req: RollbackRequest,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    from app.core.agent.customization import CustomizationError, customization_service

    try:
        result = await customization_service.rollback_to_version(
            ctype, target, req.version_id,
            rolled_by=current_user.get("user_id", "unknown"),
        )
    except CustomizationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result["status"] == "pending_approval":
        from app.core.agent.orchestration_service import _create_approval_record  # noqa: F401

        # 回滚触发的放宽场景：转人审（复用审批行机制）
        from app.core.agent.tools_v2 import ToolContext

        await _create_approval_record(
            {"tool": f"customization:{ctype}:{target}",
             "preview": f"回滚请求为放宽类：{result['reason']}", "risk": "high"},
            ToolContext(user_id=current_user.get("user_id", "unknown"),
                        thread_id=f"cust:{ctype}:{target}:rollback"),
        )
    return result


@router.post("/{ctype}/{target}/decide", summary="定制审批决定（放宽类）")
async def decide_customization(
    ctype: str,
    target: str,
    req: DecideRequest,
    current_user: dict = Depends(require_permission("customization", "approve")),
):
    """批准/驳回 pending 状态的放宽类定制变更。批准后立即生效并广播版本信号。"""
    from app.core.agent.customization import customization_service

    result = await customization_service.apply_pending(
        ctype, target, req.approved, req.comment,
        decided_by=current_user.get("user_id", "unknown"),
    )
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["reply"])
    return result
