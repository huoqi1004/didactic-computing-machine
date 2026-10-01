"""
工作流定义管理 API（路线图 Phase F / F3.5-F3.6 管理端契约）
=============================================================

端点列表（前缀 ``/api/v1/agent-ops/workflow-definitions``）：
    GET  /                          全部版本列表（可按工作流过滤）
    GET  /meta                      设计器元数据（节点类型/算子/handler/阈值白名单）
    GET  /{name}                    单工作流版本历史 + 当前生效版本 + 发布评测门禁
    GET  /{name}/{version}          取完整 DSL 定义
    POST /validate                  校验 DSL（设计器"检查"按钮，不落库）
    PUT  /{name}/{version}/draft    保存/覆盖草稿（customization:update）
    POST /{name}/{version}/publish  申请发布 → 定制层人审（customization:publish）
    POST /{name}/rollback           回滚到历史版本（customization:publish）

红线：
- **发布必经人审**：DSL 影响面大，publish 只提交审批（批准后才切版本）；
- 已发布版本内容不可变，只能另存新版本；
- 全量审计（旧版本→新版本、灰度比例、操作人）；
- 发布后自动跑关联域评测，资金/薪酬/合同域低分强制回滚（D-4）。
"""
from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.middleware.auth import require_permission

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops/workflow-definitions",
                    tags=["Agent工作流定制"])


class DraftRequest(BaseModel):
    definition: dict = Field(..., description="DSL 定义（name/entry/nodes）")
    comment: str = Field("", max_length=512, description="变更说明")


class ValidateRequest(BaseModel):
    definition: dict = Field(..., description="待校验 DSL 定义")


class PublishRequest(BaseModel):
    traffic: int = Field(0, ge=0, le=100,
                         description="灰度百分比（0 或 100=全量 stable）")
    comment: str = Field("", max_length=512, description="发布说明")


class RollbackRequest(BaseModel):
    version: str = Field(..., max_length=32, description="回滚目标版本")


@router.get("", summary="工作流定义版本列表")
async def list_definitions(
    name: Optional[str] = Query(None, description="按工作流过滤"),
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent.workflow_versions import list_versions

    return {"definitions": await list_versions(name)}


@router.get("/meta", summary="设计器元数据")
async def get_meta(current_user: dict = Depends(require_permission("customization", "read"))):
    from app.core.agent.workflow_versions import form_metadata

    return form_metadata()


@router.get("/{name}", summary="单工作流版本历史与生效状态")
async def get_workflow_status(
    name: str,
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent.workflow_versions import list_versions, publish_eval_status

    rows = await list_versions(name)
    active = next((r for r in rows if r["status"] == "stable"), None)
    canary = next((r for r in rows if r["status"] == "canary"), None)
    return {
        "name": name,
        "versions": rows,
        "stable": active,
        "canary": canary,
        "publish_eval": publish_eval_status(name),
    }


@router.get("/{name}/{version}", summary="取完整 DSL 定义")
async def get_definition(
    name: str, version: str,
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent.workflow_versions import get_definition as get_def

    row = await get_def(name, version)
    if row is None:
        raise HTTPException(status_code=404, detail=f"版本不存在: {name}@{version}")
    return {"name": name, "version": version, **row}


@router.post("/validate", summary="校验 DSL（不落库）")
async def validate_definition(
    req: ValidateRequest,
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent.workflow_versions import validate_definition as validate

    errors = validate(req.definition)
    return {"valid": not errors, "errors": errors}


@router.put("/{name}/{version}/draft", summary="保存草稿")
async def save_draft(
    name: str,
    version: str,
    req: DraftRequest,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    from app.core.agent.workflow_versions import WorkflowVersionError, save_draft as save

    try:
        result = await save(name, version, req.definition,
                            current_user.get("user_id", "unknown"), req.comment)
    except WorkflowVersionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/{name}/{version}/publish", summary="申请发布（转人审）")
async def request_publish(
    name: str,
    version: str,
    req: PublishRequest,
    current_user: dict = Depends(require_permission("customization", "publish")),
):
    from app.core.agent.workflow_versions import (
        WorkflowVersionError,
        request_publish as request,
    )

    try:
        result = await request(name, version, req.traffic,
                               current_user.get("user_id", "unknown"), req.comment)
    except WorkflowVersionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result["status"] == "duplicate":
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


@router.post("/{name}/rollback", summary="回滚到历史版本")
async def rollback(
    name: str,
    req: RollbackRequest,
    current_user: dict = Depends(require_permission("customization", "publish")),
):
    from app.core.agent.workflow_versions import WorkflowVersionError, rollback as back

    try:
        result = await back(name, req.version,
                            current_user.get("user_id", "unknown"))
    except WorkflowVersionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result
