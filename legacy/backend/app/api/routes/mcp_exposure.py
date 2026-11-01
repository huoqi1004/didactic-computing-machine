"""
MCP 出口暴露管理 API（路线图 Phase F4.3）
=========================================

端点列表：
    GET /api/v1/agent-ops/mcp-exposure            候选域 × 暴露状态 × 工具计数
    PUT /api/v1/agent-ops/mcp-exposure/{domain}   开/关某个域的 MCP 出口

口径：
- 这里是**出口**（本系统把工具导出给外部系统调用），与 mcp-servers（外部 MCP
  **入口**接入）是相反方向，两件事不要混在一个页面里；
- 关闭域 = 收紧，即时生效；开放域 = 放宽（对外多开一条调用通道），转人审（红线 2）；
- 工具级启停不在这里做：沿用 ``tool`` 类定制（定制中心"工具"分区），
  避免同一个工具两处可停、两处真相。
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.middleware.auth import require_permission

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops/mcp-exposure", tags=["MCP出口治理"])


class ExposureRequest(BaseModel):
    exposed: bool = Field(..., description="true=对外开放该域出口；false=关闭")
    comment: str = Field("", max_length=512)


@router.get("", summary="MCP 出口域暴露状态")
async def list_exposure(current_user: dict = Depends(require_permission("customization", "read"))):
    from app.core.agent import mcp_export

    return {"domains": await mcp_export.resolve()}


@router.put("/{domain}", summary="开/关 MCP 出口域")
async def set_exposure(
    domain: str,
    req: ExposureRequest,
    idempotency_key: str = Header("", alias="Idempotency-Key"),
    current_user: dict = Depends(require_permission("customization", "update")),
):
    """关闭即时生效（收紧）；开放需人工审批（放宽），批准后 server 端过滤放行。"""
    from app.core.agent import mcp_export
    from app.core.agent.customization import CustomizationError

    try:
        result = await mcp_export.set_domain_exposed(
            domain, req.exposed, by=current_user.get("user_id", "unknown"),
            comment=req.comment, idempotency_key=idempotency_key)
    except CustomizationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if result.get("status") == "duplicate":
        raise HTTPException(status_code=409, detail=result.get("reason", "重复请求"))
    logger.info("mcp_exposure.changed", domain=domain, exposed=req.exposed,
                status=result.get("status"),
                by=current_user.get("user_id", "unknown"))
    return result
