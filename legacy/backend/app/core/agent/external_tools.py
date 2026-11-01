"""
外部 MCP 工具白名单接入（路线图 Phase D/D3 + F4.1/F4.2）
========================================================

把**外部** MCP 服务器的工具（如汇率/天气等第三方能力）按白名单桥接为
治理面只读工具：

- 配置来源有两处，F4.1 起以管理端为准：
  1. ``ERP_EXTERNAL_MCP_SERVERS``（环境变量 JSON，向后兼容的出厂清单）；
  2. ``mcp_servers`` 表（管理端 CRUD）+ ``agent_customizations`` 里
     ``type="mcp"`` 的生效行（overrides.tools = 已批准接入的工具白名单）；
- **治理面强制过桥**：外部工具包装为 ToolSpecV2（readonly，
  ``resource="external"``），调用经 AgentGovernor（RBAC/schema/审计），
  外部服务故障降级为错误结果，不影响主流程；
- 外部服务不可达时跳过注册（显式告警），不阻塞启动；
- 每次出站连接前都过 §2.4 SSRF 校验（``url_guard.validate_outbound_url``）——
  保存时校验过一次不够，DNS 可以在此之后被改写。

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


async def discover_tools(url: str, *, check_url: bool = True) -> dict:
    """只做连接测试 + 工具发现（不注册任何工具）。"""
    from app.core.security import url_guard

    if check_url:
        try:
            await url_guard.validate_outbound_url(url)
        except url_guard.UnsafeURL as e:
            return {"ok": False, "error": f"SSRF 校验未通过：{e}", "tools": []}
    try:
        remote = await _fetch_tools(url)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"连接失败：{e}", "tools": []}
    return {
        "ok": True, "error": "",
        "tools": [{"name": n, "description": m["description"]} for n, m in remote.items()],
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


async def bridge_server(name: str, url: str, tools: list[str],
                        *, check_url: bool = True) -> dict:
    """
    桥接一个外部 MCP Server 的白名单工具（增量注册，幂等）。

    Args:
        check_url: 是否按 §2.4 做 SSRF 校验（桥接时 + 每次出站调用时）。
            管理端登记的 server 必须 True；环境变量清单是部署方自有配置
            （与代码同信任级，可指向内网 sidecar），由调用方显式传 False。

    Returns:
        {"ok", "registered": [...], "skipped": [...], "remote": [...], "error": str}
    """
    from app.core.agent.tools_v2 import ToolSpecV2, ToolRisk, tool_registry_v2
    from app.core.security import url_guard

    result: dict[str, Any] = {"ok": False, "registered": [], "skipped": [],
                              "remote": [], "error": ""}
    if not url or not tools:
        result["error"] = "地址或工具白名单为空"
        return result
    if check_url:
        try:
            await url_guard.validate_outbound_url(url)
        except url_guard.UnsafeURL as e:
            result["error"] = f"SSRF 校验未通过：{e}"
            logger.warning("external_mcp.blocked", server=name, error=str(e))
            return result

    try:
        remote_tools = await _fetch_tools(url)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"连接失败：{e}"
        logger.warning("external_mcp.unreachable", server=name, error=str(e))
        return result

    result["ok"] = True
    result["remote"] = sorted(remote_tools)

    for tool_name in tools:
        meta = remote_tools.get(tool_name)
        if meta is None:
            result["skipped"].append(tool_name)
            logger.warning("external_mcp.tool_missing", server=name, tool=tool_name)
            continue
        spec_name = f"external_{name}_{tool_name}"
        if tool_registry_v2.get(spec_name) is not None:
            result["registered"].append(tool_name)
            continue  # 幂等

        async def handler(ctx, _url=url, _tn=tool_name,
                          _guard=check_url, **kwargs):
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            from app.core.security import url_guard

            # 保存时校验过一次不等于永远安全：每次出站前重过静态规则
            if _guard:
                try:
                    url_guard.check_url_static(_url)
                except url_guard.UnsafeURL as e:
                    return {"error": f"外部工具出站被 SSRF 校验拦截：{e}"}

            async with streamable_http_client(_url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    call = await session.call_tool(_tn, kwargs)
                    text = call.content[0].text if call.content else "{}"
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
        result["registered"].append(tool_name)

    if result["registered"]:
        logger.info("external_mcp.registered", server=name,
                    count=len(result["registered"]))
    return result


async def register_external_tools() -> int:
    """
    按环境变量清单桥接外部 MCP 工具（出厂配置，lifespan 调用）。

    环境变量由部署方掌握、与代码同信任级（可指向内网 sidecar），因此不做 SSRF；
    管理端登记的 server 走 ``mcp_config.sync_from_db()``，那条路径强制校验。
    """
    registered = 0
    for server in _load_config():
        name = server.get("name", "external")
        res = await bridge_server(name, server.get("url", ""),
                                  list(server.get("tools", [])), check_url=False)
        registered += len(res["registered"])
    return registered
