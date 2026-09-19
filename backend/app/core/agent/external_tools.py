"""
外部 MCP 工具白名单接入（路线图 Phase D / D3）
================================================

把**外部** MCP 服务器的工具（如汇率/天气等第三方能力）按白名单桥接为
治理面只读工具：

- 配置清单：``ERP_EXTERNAL_MCP_SERVERS``（JSON 数组：
  ``[{"name": "fx", "url": "https://host/mcp", "tools": ["get_rate"]}]``）；
- **治理面强制过桥**：外部工具包装为 ToolSpecV2（readonly，
  ``resource="external"``），调用经 AgentGovernor（RBAC/schema/审计），
  外部服务故障降级为错误结果，不影响主流程；
- 外部服务不可达时跳过注册（显式告警），不阻塞启动。

白名单语义：不在清单 ``tools`` 里的外部工具一律不接入。
"""
from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger()


def _load_config() -> list[dict]:
    from app.core.config import settings

    try:
        data = json.loads(settings.external_mcp_servers or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        logger.warning("external_mcp.config_invalid")
        return []


async def _fetch_tools(url: str) -> dict[str, dict]:
    """经官方 MCP 客户端列出外部服务器工具（name → {description, schema}）。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {
                t.name: {
                    "description": t.description or "",
                    "input_schema": t.inputSchema or {},
                }
                for t in tools.tools
            }


def _build_args_model(server_name: str, tool_name: str, schema: dict) -> type:
    """MCP inputSchema → 动态 pydantic 模型（供治理面 schema 校验）。"""
    from pydantic import Field, create_model

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    field_defs: dict[str, Any] = {}
    for prop_name, prop in properties.items():
        ptype = {"string": str, "number": float, "integer": int,
                 "boolean": bool}.get(prop.get("type"), str)
        field_defs[prop_name] = (
            ptype,
            Field(... if prop_name in required else prop.get("default"),
                  description=prop.get("description", "")),
        )
    model_name = f"{server_name}_{tool_name}_Args"
    return create_model(model_name, **field_defs)


async def register_external_tools() -> int:
    """
    按配置清单桥接外部 MCP 工具（lifespan 调用）。

    Returns:
        成功桥接的工具数量。
    """
    from app.core.agent.tools_v2 import ToolSpecV2, ToolRisk, tool_registry_v2

    registered = 0
    for server in _load_config():
        name = server.get("name", "external")
        url = server.get("url", "")
        whitelist = set(server.get("tools", []))
        if not url or not whitelist:
            continue
        try:
            remote_tools = await _fetch_tools(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("external_mcp.unreachable", server=name, error=str(e))
            continue

        for tool_name in whitelist:
            meta = remote_tools.get(tool_name)
            if meta is None:
                logger.warning("external_mcp.tool_missing",
                               server=name, tool=tool_name)
                continue
            spec_name = f"external_{name}_{tool_name}"
            if tool_registry_v2.get(spec_name) is not None:
                continue  # 幂等

            async def handler(ctx, _url=url, _tn=tool_name, **kwargs):
                from mcp import ClientSession
                from mcp.client.streamable_http import streamable_http_client

                async with streamable_http_client(_url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(_tn, kwargs)
                        text = result.content[0].text if result.content else "{}"
                        try:
                            return json.loads(text)
                        except (ValueError, TypeError):
                            return {"raw": text}

            args_model = _build_args_model(name, tool_name, meta["input_schema"])
            tool_registry_v2.register(ToolSpecV2(
                name=spec_name,
                description=f"[外部:{name}] {meta['description']}",
                handler=handler,
                args_model=args_model,
                risk_level=ToolRisk.READONLY,
                resource="external",
                action="read",
                agent_domain="external",
                tags=["mcp", name],
            ))
            registered += 1

    if registered:
        logger.info("external_mcp.registered", count=registered)
    return registered
