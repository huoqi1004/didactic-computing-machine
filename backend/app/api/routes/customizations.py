"""
Agent 定制化管理 API（路线图 Phase F / F1.5）
==============================================

端点列表：
    GET  /api/v1/agent-ops/customizations                       生效中的定制列表（合成视图）
    GET  /api/v1/agent-ops/customizations/meta                  表单元数据（域/参数/角色/单据类型）
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


@router.get("/degradation", summary="定制层降级状态（F5.4 显式告警的可查面）")
async def get_degradation_status(
    current_user: dict = Depends(require_permission("customization", "read")),
):
    """
    读不到定制层时业务不会停（各执行点回退内置默认），所以这件事必须**看得见**：
    本端点 + Prometheus `erp_customization_degraded` 是同一个状态的两个出口。

    mode=defaults 表示当前按内置默认跑（管理端改的定制没生效）；
    mode=stale_cache 表示吃上一次成功加载的缓存（可能已过期）。
    """
    from app.core.agent.customization import customization_service

    await customization_service.ensure_fresh()
    return customization_service.degradation_status()


@router.get("/versions", summary="变更历史（定制审计视图的数据源，F5.1）")
async def list_versions(
    type: Optional[str] = Query(None, description="按类型过滤"),
    target: Optional[str] = Query(None, description="按目标过滤"),
    status: Optional[str] = Query(None, description="按状态过滤（pending_approval/approved_executed/…）"),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_permission("customization", "read")),
):
    """
    一条变更记录要能回答"谁、何时、改了什么、旧值→新值、谁批的"（红线 3 的可见面）。
    old_summary/new_summary 是变更前后快照（{enabled, overrides}），前端按键做 diff。
    """
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
            if status:
                stmt = stmt.where(customization_versions.c.status == status)
            rows = (await session.execute(stmt)).all()
        return {"versions": [
            {"id": r.id, "type": r.type, "target": r.target,
             "environment": r.environment, "overrides": r.overrides,
             "old_summary": r.old_summary, "new_summary": r.new_summary,
             "status": r.status, "comment": r.comment,
             "changed_by": r.changed_by, "approved_by": r.approved_by,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ]}
    except Exception as e:  # noqa: BLE001
        logger.error("customizations.versions_failed", error=str(e))
        raise HTTPException(status_code=500, detail="变更历史查询失败")


@router.get("/meta", summary="定制表单元数据（域/参数清单/角色/单据类型）")
async def customization_meta(
    current_user: dict = Depends(require_permission("customization", "read")),
):
    """
    管理端表单自动生成所需的最小元数据（F2.3 声明式参数清单 + F2.7 二期 UI）。

    只回结构不回数据：当前生效覆盖由 GET /customizations 提供，避免两处口径。
    未在 DOMAIN_PARAM_SPECS 登记的键不会出现在表单里——防止"能改但运行期不生效"。
    阈值同理只回 THRESHOLD_DEFAULTS 注册表内的键（gate 的白名单，后端 apply_change 校验）。
    """
    from app.core.agent.customization import THRESHOLD_DEFAULTS, THRESHOLD_LABELS
    from app.core.agent.domain_params import DOMAIN_PARAM_SPECS
    from app.core.agent.governor import agent_governor
    from app.core.rbac import rbac_engine
    from app.core.rbac.seed import RESOURCES

    by_domain: dict[str, list[str]] = {}
    for spec in agent_governor.registry.list_specs():
        by_domain.setdefault(spec.agent_domain or "未分域", []).append(spec.name)

    return {
        "agents": [
            {
                "domain": d,
                "label": RESOURCES.get(d, d),
                "tools": sorted(by_domain[d]),
                "params": DOMAIN_PARAM_SPECS.get(d, []),
            }
            for d in sorted(by_domain)
        ],
        "roles": [
            {"code": r["role_code"], "name": r.get("role_name", ""), "level": r.get("level")}
            for r in rbac_engine.list_roles()
        ],
        # 阈值清单（gate 类型可配 target 的白名单）：前端表单由此生成，不硬编码键名
        "thresholds": [
            {"key": k, "label": THRESHOLD_LABELS.get(k, k), "builtin_default": v}
            for k, v in sorted(THRESHOLD_DEFAULTS.items())
        ],
        # 审批矩阵按 document_type 匹配：值即中断载荷里的类型（工具名后缀 tool:xxx → xxx）
        "document_types": sorted({
            name.split(":")[-1] for names in by_domain.values() for name in names
        }),
    }


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
    """
    撤销某次变更（恢复到该版本"变更前"的快照）。

    回滚目标若比现状更松，服务层会落提案行 + 审批行并返回 pending_approval
    （与放宽类同一闭环：/approvals/{thread_id}/decide）。
    """
    from app.core.agent.customization import CustomizationError, customization_service

    try:
        result = await customization_service.rollback_to_version(
            ctype, target, req.version_id,
            rolled_by=current_user.get("user_id", "unknown"),
        )
    except CustomizationError as e:
        raise HTTPException(status_code=400, detail=str(e))
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

    # 必须关键字传参：签名是 (…, approved, decided_by, comment)，
    # 按位置把 comment 放在第 4 位会撞上 decided_by（TypeError 或与审批人串号）。
    result = await customization_service.apply_pending(
        ctype, target, req.approved,
        decided_by=current_user.get("user_id", "unknown"),
        comment=req.comment,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["reply"])
    return result
