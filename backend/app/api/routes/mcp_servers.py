"""
外部 MCP Server 管理端 API（路线图 Phase F4.1 / F4.2）
======================================================

端点列表：
    GET    /api/v1/agent-ops/mcp-servers              连接清单 + 各自已生效的暴露工具
    POST   /api/v1/agent-ops/mcp-servers              登记 server（URL 过 §2.4 SSRF 校验）
    PUT    /api/v1/agent-ops/mcp-servers/{name}       改连接信息
    DELETE /api/v1/agent-ops/mcp-servers/{name}       删除登记并撤销暴露（收紧即时）
    POST   /api/v1/agent-ops/mcp-servers/probe        对未保存的地址做连接测试 + 工具发现
    POST   /api/v1/agent-ops/mcp-servers/{name}/probe 对已登记 server 连接测试（结果写回）
    GET    /api/v1/agent-ops/mcp-servers/{name}/tools 读取最近一次探测到的工具清单
    PUT    /api/v1/agent-ops/mcp-servers/{name}/tools 勾选要暴露的工具（扩面=放宽→人审）

安全边界：
- 权限：customization:read（看）/ customization:update（登记、探测、勾选）。
  **探测也要求 update**：它会让后端向调用者提供的地址发出出站请求，
  属于 operator 动作而非只读查询，不能靠 read 权限敞开。
- §2.4 SSRF：登记、改地址、探测、每次工具调用前都过 url_guard（协议/内网/IP 直连/
  域名白名单 + DNS 解析后二次校验）。白名单由 `mcp_outbound_allowed_domains` 配置。
- 红线 2：新增外部工具暴露 = 放宽，服务层落提案行 + 审批行，本路由直接回
  pending_approval；批准后才由 customization 审批钩子增量注册进工具治理面。
"""
from __future__ import annotations

from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.middleware.auth import require_permission

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops/mcp-servers", tags=["外部MCP接入"])


class ServerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64,
                      description="小写字母开头的 a-z0-9_，决定外部工具名前缀")
    url: str = Field(..., min_length=8, max_length=512,
                     description="MCP streamable-http 端点（须公网可达且在出站域名白名单内）")
    description: str = Field("", max_length=255)


class ServerUpdateRequest(BaseModel):
    url: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = Field(None, max_length=255)


class ProbeRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=512)


class ExposeToolsRequest(BaseModel):
    tools: List[str] = Field(default_factory=list,
                             description="要暴露给 Agent 的外部工具名（全量覆盖，空清单=撤销暴露）")
    comment: str = Field("", max_length=512)


def _bad_request(err: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(err))


@router.get("", summary="外部 MCP Server 清单")
async def list_mcp_servers(
    current_user: dict = Depends(require_permission("customization", "read")),
):
    from app.core.agent import mcp_config

    return {"servers": await mcp_config.list_servers()}


@router.post("", summary="登记外部 MCP Server")
async def create_mcp_server(
    req: ServerCreateRequest,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    """
    只登记连接信息，不暴露任何工具（因此即时可建，不属于放宽）。
    真正接入 Agent 需要随后 PUT /{name}/tools 勾选，那一步走人审。
    """
    from app.core.agent import mcp_config
    from app.core.security.url_guard import UnsafeURL

    try:
        return await mcp_config.create_server(
            req.name, req.url, req.description,
            by=current_user.get("user_id", "unknown"))
    except (mcp_config.McpConfigError, UnsafeURL) as e:
        raise _bad_request(e)


@router.put("/{name}", summary="修改连接信息")
async def update_mcp_server(
    name: str,
    req: ServerUpdateRequest,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    from app.core.agent import mcp_config
    from app.core.security.url_guard import UnsafeURL

    try:
        return await mcp_config.update_server(
            name, url=req.url, description=req.description,
            by=current_user.get("user_id", "unknown"))
    except (mcp_config.McpConfigError, UnsafeURL) as e:
        raise _bad_request(e)


@router.delete("/{name}", summary="删除登记并撤销暴露")
async def delete_mcp_server(
    name: str,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    from app.core.agent import mcp_config

    try:
        return await mcp_config.delete_server(
            name, by=current_user.get("user_id", "unknown"))
    except mcp_config.McpConfigError as e:
        raise _bad_request(e)


@router.post("/probe", summary="连接测试 + 工具发现（未保存的地址）")
async def probe_mcp_url(
    req: ProbeRequest,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    """保存前预检。SSRF 不通过时返回 ok=false 与原因，不当作 HTTP 错误。"""
    from app.core.agent import mcp_config

    return await mcp_config.probe_url(req.url)


@router.post("/{name}/probe", summary="连接测试（已登记 server）")
async def probe_mcp_server(
    name: str,
    current_user: dict = Depends(require_permission("customization", "update")),
):
    from app.core.agent import mcp_config

    try:
        return await mcp_config.probe_server(
            name, by=current_user.get("user_id", "unknown"))
    except mcp_config.McpConfigError as e:
        raise _bad_request(e)


@router.get("/{name}/tools", summary="最近一次探测到的外部工具清单")
async def list_mcp_server_tools(
    name: str,
    current_user: dict = Depends(require_permission("customization", "read")),
):
    """
    只读 last_probe，不在此处发出站请求：GET 语义下触发网络调用会让"刷新页面"
    变成对任意外部端点的打点，也更难做 §2.4 的审计口径。要更新清单请 POST /{name}/probe。
    """
    from sqlalchemy import select

    from app.db.models import mcp_servers
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise HTTPException(status_code=503, detail="存储不可用")
    async with session_scope() as session:
        row = (await session.execute(
            select(mcp_servers).where(mcp_servers.c.name == name)
        )).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未登记的 MCP Server: {name}")

    probe = row.last_probe or {}
    return {
        "name": name,
        "tools": probe.get("tools") or [],
        "probed_at": probe.get("at"),
        "probe_ok": bool(probe.get("ok")),
        "note": "" if probe else "尚未连接测试，先 POST /{name}/probe",
    }


@router.put("/{name}/tools", summary="勾选要暴露的外部工具")
async def set_mcp_server_tools(
    name: str,
    req: ExposeToolsRequest,
    idempotency_key: str = Header("", alias="Idempotency-Key"),
    current_user: dict = Depends(require_permission("customization", "update")),
):
    """
    全量覆盖语义：tools 即最终白名单，空清单=撤销暴露。

    扩面/首次接入 → pending_approval（红线 2，批准后才注册进治理面）；
    收缩/撤销 → applied（收紧即时）。
    """
    from app.core.agent import mcp_config

    try:
        result = await mcp_config.set_exposed_tools(
            name, req.tools, by=current_user.get("user_id", "unknown"),
            comment=req.comment, idempotency_key=idempotency_key)
    except mcp_config.McpConfigError as e:
        raise _bad_request(e)
    if result.get("status") == "duplicate":
        raise HTTPException(status_code=409, detail=result.get("reason", "重复请求"))
    return result
