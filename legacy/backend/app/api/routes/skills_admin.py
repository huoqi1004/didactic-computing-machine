"""
Skill 编辑器与版本管理 API（路线图 Phase F / F4.4-F4.5）
========================================================

端点列表（前缀 ``/api/v1/agent-ops/skills``，静态路径先声明，避免与 ``/{name}`` 争配）：
    GET    /meta                     编辑器元数据（按域工具清单 + 模板 + 发布目录）
    GET    /versions                 全部技能的版本历史（可按 name 过滤）
    POST   /validate                 校验定义（编辑器"检查"按钮，不落库）
    GET    /export                   导出 JSON 定制包（customization:export）
    POST   /import                   导入 JSON 定制包 → 落草稿（customization:update）
    GET    /{name}                   单技能：版本历史 + 生效版本 + 启停 + 运行时视图
    GET    /{name}/{version}         取完整定义
    PUT    /{name}/{version}/draft   保存/覆盖草稿（customization:update）
    POST   /{name}/{version}/publish 申请发布 → 定制层人审（customization:publish）
    PUT    /{name}/enabled           启停：停用即时 / 启用转人审（customization:update）
    POST   /{name}/rollback          回滚到历史已批版本（customization:publish）

红线：
- **发布必经人审**：技能指令进 LLM 系统提示，影响面等同 DSL；
- 已发布版本内容不可变，只能另存新版本；
- 导入只落草稿，绝不自动发布（跨环境搬运不得绕过本环境审批）；
- 全量审计（草稿/发布/驳回/启停/回滚/导入）入审计哈希链。
"""
from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.middleware.auth import require_permission

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops/skills", tags=["Agent技能定制"])


class DefinitionRequest(BaseModel):
    definition: dict = Field(..., description="技能定义（name/domain/tools/instructions…）")
    comment: str = Field("", max_length=512, description="变更说明")


class PublishRequest(BaseModel):
    comment: str = Field("", max_length=512, description="发布说明")


class RollbackRequest(BaseModel):
    version: str = Field(..., max_length=32, description="回滚目标版本")


class EnabledRequest(BaseModel):
    enabled: bool = Field(..., description="true=启用（放宽，须人审）/ false=停用（收紧，即时）")
    comment: str = Field("", max_length=512)


class ImportRequest(BaseModel):
    bundle: dict = Field(..., description="export 产出的定制包")


def _bad_request(err: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(err))


# ===========================================================================
# 静态路径
# ===========================================================================

@router.get("/meta", summary="编辑器元数据")
async def get_meta(current_user: dict = Depends(require_permission("customization", "read"))):
    from app.core.agent.skill_versions import form_metadata

    return form_metadata()


@router.get("/versions", summary="技能版本历史列表")
async def list_versions(
    name: Optional[str] = Query(None, description="按技能过滤"),
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent.skill_versions import list_versions as _list

    return {"versions": await _list(name)}


@router.post("/validate", summary="校验定义（不落库）")
async def validate_definition(
    req: DefinitionRequest,
    current_user: dict = Depends(require_permission("customization", "read")),
):
    """errors=结构问题（草稿都不能存）；publish_errors=发布额外要求（工具须已注册）。"""
    from app.core.agent.skill_versions import validate_definition as validate
    from app.core.agent.skill_versions import validate_publishable

    errors = validate(req.definition)
    return {"valid": not errors, "errors": errors,
            "publish_errors": [] if errors else validate_publishable(req.definition)}


@router.get("/export", summary="导出技能定制包（JSON）")
async def export_bundle(
    name: Optional[str] = Query(None, description="只导出某个技能"),
    include_draft: bool = Query(False, description="连带草稿（默认只导已发布版本）"),
    include_runtime: bool = Query(True, description="连带出厂技能（没有版本流行的运行时项）"),
    current_user: dict = Depends(require_permission("customization", "export")),
):
    from app.core.agent.skill_versions import export_bundle as _export

    return await _export(name, include_draft, include_runtime)


@router.post("/import", summary="导入技能定制包（落草稿）")
async def import_bundle(
    req: ImportRequest,
    idempotency_key: str = Header("", max_length=128,
                                  description="防重放幂等键（§2.4，5 分钟窗口）"),
    current_user: dict = Depends(require_permission("customization", "update")),
):
    """
    导入不会改变运行时：条目一律落 **草稿**，仍需逐条申请发布并过人审。
    幂等键命中 5 分钟窗口时拒绝重复导入（与放宽类变更同一防重放口径）。
    """
    from app.core.agent.customization import customization_service
    from app.core.agent.skill_versions import SkillVersionError, import_bundle as _import

    if idempotency_key and customization_service.seen_idempotency_key(idempotency_key):
        raise HTTPException(status_code=409, detail="重复请求（幂等键已存在）")
    try:
        return await _import(req.bundle, current_user.get("user_id", "unknown"))
    except SkillVersionError as e:
        raise _bad_request(e)


# ===========================================================================
# 参数路径
# ===========================================================================

@router.get("/{name}", summary="单技能：版本历史 + 生效状态 + 运行时视图")
async def get_skill_status(
    name: str,
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry

    rows = await skill_versions.list_versions(name)
    stable = next((r for r in rows if r["status"] == "stable"), None)
    entry = customization_service.get_override("skill", name)
    live = skill_registry.get(name)          # 已过滤停用项：None = 运行时不生效
    return {
        "name": name,
        "versions": rows,
        "stable": stable,
        "drafts": [r for r in rows if r["status"] == "draft"],
        "enabled": True if entry is None else bool(entry.get("enabled", True)),
        "pending": await skill_versions.pending_proposal(name),
        "runtime": None if live is None else {
            "version": live.version, "domain": live.domain, "tools": live.tools,
            "valid": live.valid, "invalid_reason": live.invalid_reason,
            "published": live.published,
        },
    }


@router.get("/{name}/{version}", summary="取完整定义")
async def get_definition(
    name: str, version: str,
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent.skill_versions import get_definition as _get

    row = await _get(name, version)
    if row is None:
        raise HTTPException(status_code=404, detail=f"版本不存在: {name}@{version}")
    return {"name": name, "version": version, **row}


@router.put("/{name}/{version}/draft", summary="保存草稿")
async def save_draft(
    name: str,
    version: str,
    req: DefinitionRequest,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    from app.core.agent.skill_versions import SkillVersionError, save_draft as _save

    try:
        return await _save(name, version, req.definition,
                           current_user.get("user_id", "unknown"), req.comment)
    except SkillVersionError as e:
        raise _bad_request(e)


@router.post("/{name}/{version}/publish", summary="申请发布（转人审）")
async def request_publish(
    name: str,
    version: str,
    req: PublishRequest,
    idempotency_key: str = Header("", max_length=128,
                                  description="防重放幂等键（§2.4，5 分钟窗口）"),
    current_user: dict = Depends(require_permission("customization", "publish")),
):
    from app.core.agent import skill_versions
    from app.core.agent.customization import CustomizationError, customization_service

    try:
        result = await skill_versions.request_publish(
            name, version, current_user.get("user_id", "unknown"), req.comment,
            idempotency_key=idempotency_key)
    except (skill_versions.SkillVersionError, CustomizationError) as e:
        raise _bad_request(e)
    if result.get("status") == "duplicate":
        raise HTTPException(status_code=409, detail=result.get("reason", "重复请求"))
    return result


@router.put("/{name}/enabled", summary="技能启停")
async def set_enabled(
    name: str,
    req: EnabledRequest,
    idempotency_key: str = Header("", max_length=128,
                                  description="防重放幂等键（§2.4，5 分钟窗口）"),
    current_user: dict = Depends(require_permission("customization", "update")),
):
    """停用即时生效（收紧）；启用属放宽，返回 pending_approval 与审批 thread_id。"""
    from app.core.agent import skill_versions
    from app.core.agent.customization import CustomizationError

    try:
        result = await skill_versions.set_enabled(
            name, req.enabled, current_user.get("user_id", "unknown"), req.comment,
            idempotency_key=idempotency_key)
    except (skill_versions.SkillVersionError, CustomizationError) as e:
        raise _bad_request(e)
    if result.get("status") == "duplicate":
        raise HTTPException(status_code=409, detail=result.get("reason", "重复请求"))
    return result


@router.post("/{name}/rollback", summary="回滚到历史已批版本")
async def rollback(
    name: str,
    req: RollbackRequest,
    current_user: dict = Depends(require_permission("customization", "publish")),
):
    from app.core.agent.skill_versions import SkillVersionError, rollback as _back

    try:
        return await _back(name, req.version, current_user.get("user_id", "unknown"))
    except SkillVersionError as e:
        raise _bad_request(e)
