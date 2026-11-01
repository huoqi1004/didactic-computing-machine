"""
ERP MCP Server（路线图 Phase D / D1-D2）
==========================================

把治理面注册的业务工具（app/core/agent/tools_v2）按域导出为标准
MCP（Model Context Protocol）服务器——外部系统/Agent 可经
streamable HTTP 调用，与本地 LangGraph 调用**行为一致**（同一治理面）。

架构：
- 5 个域子服务器（finance/hr/crm/sales/contract）+ 2 个试点域
  （reimbursement/treasury），FastMCP ``http_app()`` 挂载为
  ``/{domain}/mcp``；
- 工具导出：ToolSpecV2 → 动态生成显式签名函数（fastmcp 4 的 schema
  由签名推导，``**kwargs`` 包装不受支持）→ 装饰注册；
- **治理面桥接**：MCP 调用最终走 ``AgentGovernor.execute``（RBAC/schema/
  审计哈希链/补偿），与本地行为完全一致；调用者身份经 ``X-ERP-User``
  头透传（缺省 ``mcp:external``，治理面按其权限校验）；
- 令牌鉴权：``Authorization: Bearer <ERP_MCP_AUTH_TOKEN>``（未配置
  token 时为开发模式放行，生产必须配置）。

运行：
- 开发：``python -m app.mcp.server``（uvicorn，端口 8100）
- compose：``mcp`` profile 下 ``erp-mcp`` 服务
"""
from __future__ import annotations

import inspect
from typing import Any

import structlog
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

logger = structlog.get_logger()

# MCP 桥接工具的归属域 → FastMCP 子服务器名。
# 这是**候选清单**（新增业务域涉及工具归属与鉴权映射，必须过代码评审），
# 实际对外暴露哪些域由定制层决定：见 app/core/agent/mcp_export.py（F4.3）。
from app.core.agent.mcp_export import DEFAULT_EXPORT_DOMAINS  # noqa: E402

_MCP_DOMAINS = DEFAULT_EXPORT_DOMAINS


def _build_generic_handler(domain: str, tool_name: str):
    """MCP 工具 → 治理面桥接处理器（身份从 HTTP 头透传）。"""

    async def handler(tool_name: str, tool_args: dict) -> dict:
        from fastmcp.server.dependencies import get_http_request

        from app.core.agent import mcp_export
        from app.core.agent.customization import customization_service
        from app.core.agent.governor import agent_governor
        from app.core.agent.tools_v2 import ToolContext

        user_id = "mcp:external"
        username = "mcp:external"
        try:
            request = get_http_request()
            if request is not None:
                user_id = request.headers.get("X-ERP-User", user_id)
                username = request.headers.get("X-ERP-User", username)
        except Exception:  # noqa: BLE001
            pass  # 非 HTTP 传输（stdio 测试）时用默认身份

        # server 端过滤（F4.3）：不能只在构建期筛一次——ASGI 应用是启动时装配的，
        # 管理端关掉某域/某工具须在不重启的前提下立刻拒绝，故每次调用前刷定制缓存。
        await customization_service.ensure_fresh()
        if not mcp_export.is_tool_exportable(tool_name, domain):
            return {"status": "error",
                    "error": f"工具 {tool_name} 未开放 MCP 出口（域 {domain} 已关闭或工具已停用）"}

        from app.core.observability.trace import get_business_key, get_trace_id

        ctx = ToolContext(user_id=user_id, username=username,
                          agent_key=f"mcp:{domain}",
                          trace_id=get_trace_id(), business_key=get_business_key())
        result = await agent_governor.execute(tool_name, tool_args or {}, ctx)
        if result.approval is not None:
            return {"status": "approval_required",
                    "detail": ("该工具为高危及人工审批类，MCP 直连通道不执行；"
                               "请经 ERP 审批流程发起。"),
                    "tool": tool_name}
        if not result.ok:
            return {"status": "error", "error": result.error}
        return {"status": "success", "data": result.data}

    return handler


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Bearer 令牌鉴权：配置 ERP_MCP_AUTH_TOKEN 后强制校验，未配置为开发模式。"""

    async def dispatch(self, request: Request, call_next):
        from app.core.config import settings

        token = settings.mcp_auth_token
        if token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"},
                                    status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)


def create_mcp_asgi_app() -> Starlette:
    """
    构建总 ASGI 应用：按生效定制把已暴露的域子服务器挂载到 ``/{domain}/mcp``。

    供 compose（uvicorn app.mcp.server:app）与测试（ASGITransport）使用。

    构建期只做"少挂载"（收窄面的快路径）；真正的强制在请求期的
    ``mcp_export.is_tool_exportable``（见 ``_build_generic_handler``），
    因此管理端改开关后无需重启即可生效，且构建时定制缓存尚未加载也不会误放——
    那时 ``_domain_exposed`` 回退内置默认清单，暴露面不放大。
    """
    from contextlib import AsyncExitStack, asynccontextmanager

    from app.core.agent import mcp_export

    routes = []
    sub_lifespans = []
    mounted = []
    for domain in _MCP_DOMAINS:
        if not mcp_export.is_domain_exposed(domain):
            logger.info("mcp.domain_skipped", domain=domain)
            continue
        mcp = FastMCP(f"erp-{domain}")
        ns: dict[str, Any] = {"_MCP": mcp}
        _export_domain_tools_into(ns, mcp, domain)
        sub_app = mcp.http_app(path="/mcp")
        sub_lifespans.append(sub_app.router.lifespan_context)
        routes.append(Mount(f"/{domain}", app=sub_app))
        mounted.append(domain)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # 每个 fastmcp 子应用的 lifespan 必须进入父应用生命周期，
        # 否则 StreamableHTTPSessionManager 未初始化（initialize 请求 500）
        from app.core.agent.customization import customization_service

        async with AsyncExitStack() as stack:
            # 启动即加载定制层（出口开关/工具启停都读它）；失败则保持默认清单
            try:
                await customization_service.ensure_fresh()
            except Exception as e:  # noqa: BLE001
                logger.warning("mcp.customization_load_failed", error=str(e))
            for sub_lifespan in sub_lifespans:
                await stack.enter_async_context(sub_lifespan(app))
            yield

    app = Starlette(routes=routes, lifespan=lifespan,
                    middleware=[Middleware(TokenAuthMiddleware)])
    logger.info("mcp.server_built", domains=mounted,
                candidates=list(_MCP_DOMAINS))
    return app


def _export_domain_tools_into(ns: dict, mcp: FastMCP, domain: str) -> None:
    """在给定命名空间内为某域生成并注册当前可导出的工具（装饰在 def 处完成）。"""
    from app.core.agent import mcp_export
    from app.core.agent.tools_v2 import tool_registry_v2

    ns["_MCP"] = mcp
    for spec in tool_registry_v2.list_specs(domain=domain):
        # 工具级开关（F4.3）：被 tool 类定制停用的工具不出现在出口清单里，
        # 省得外部系统在 list_tools 里看到调不动的工具。
        if not mcp_export.tool_enabled(spec.name):
            continue
        fields = spec.args_model.model_fields
        ns["_ANN"] = ns.get("_ANN", {})
        ns["_DEF"] = ns.get("_DEF", {})
        ns["_ANN"].update({k: f.annotation for k, f in fields.items()})
        ns["_DEF"].update({k: f.default for k, f in fields.items() if not f.is_required()})
        ns["_H"] = _build_generic_handler(domain, spec.name)
        # 必填在前可选在后（Python 语法要求），调用映射按名字不受影响
        ordered = sorted(fields.items(), key=lambda kv: not kv[1].is_required())
        param_lines, call_parts = [], []
        for fname, f in ordered:
            ann = f"_ANN[{fname!r}]"
            if f.is_required():
                param_lines.append(f"    {fname}: {ann},")
            else:
                param_lines.append(f"    {fname}: {ann} = _DEF[{fname!r}],")
            call_parts.append(f"{fname!r}: {fname}")
        desc = (spec.description or "")[:400]
        src = (
            f"@_MCP.tool(name={spec.name!r}, description={desc!r})\n"
            f"async def {spec.name}(\n" + "\n".join(param_lines) +
            f"\n) -> dict:\n    return await _H({spec.name!r}, {{{', '.join(call_parts)}}})"
        )
        exec(src, ns)


app = None


def get_asgi_app() -> Starlette:
    """懒加载（工具需先注册——compose 中 uvicorn 导入前需触发工具模块导入）。"""
    global app
    if app is None:
        import app.agents.contract.tools  # noqa: F401
        import app.agents.crm.tools  # noqa: F401
        import app.agents.hr.tools  # noqa: F401
        import app.agents.payroll.tools  # noqa: F401
        import app.agents.reimbursement.tools  # noqa: F401
        import app.agents.sales.tools  # noqa: F401
        import app.agents.treasury.tools  # noqa: F401

        app = create_mcp_asgi_app()
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.mcp.server:get_asgi_app", host="0.0.0.0", port=8100, factory=True)
