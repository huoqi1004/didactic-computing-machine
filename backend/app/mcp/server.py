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

# MCP 桥接工具的归属域 → FastMCP 子服务器名
_MCP_DOMAINS = ("reimbursement", "treasury", "payroll", "hr", "crm", "sales", "contract")


def _build_generic_handler(domain: str, tool_name: str):
    """MCP 工具 → 治理面桥接处理器（身份从 HTTP 头透传）。"""

    async def handler(tool_name: str, tool_args: dict) -> dict:
        from fastmcp.server.dependencies import get_http_request

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

        ctx = ToolContext(user_id=user_id, username=username,
                          agent_key=f"mcp:{domain}")
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


def _export_domain_tools(mcp: FastMCP, domain: str) -> int:
    """把治理面注册的某域工具动态导出为 MCP 工具（显式签名，schema 正确）。"""
    from app.core.agent.tools_v2 import tool_registry_v2

    specs = tool_registry_v2.list_specs(domain=domain)
    count = 0
    for spec in specs:
        fields = spec.args_model.model_fields
        ns: dict[str, Any] = {
            "_H": _build_generic_handler(domain, spec.name),
            "_NAME": spec.name,
            "_ANN": {k: f.annotation for k, f in fields.items()},
            "_DEF": {k: f.default for k, f in fields.items() if not f.is_required()},
        }
        param_lines, call_parts = [], []
        for fname, f in fields.items():
            ann = f"_ANN[{fname!r}]"
            if f.is_required():
                param_lines.append(f"    {fname}: {ann},")
            else:
                param_lines.append(f"    {fname}: {ann} = _DEF[{fname!r}],")
            call_parts.append(f"{fname!r}: {fname}")
        src = (
            f"@_MCP.tool(name={spec.name!r}, "
            f"description={ (spec.description or '')[:400]!r})\n"
            f"async def {spec.name}(\n" + "\n".join(param_lines) +
            f"\n) -> dict:\n    return await _H(_NAME, {{{', '.join(call_parts)}}})"
        )
        exec(src, ns)
        count += 1
    logger.info("mcp.domain_exported", domain=domain, tools=count)
    return count


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
    构建总 ASGI 应用：5+2 个域子服务器挂载到 ``/{domain}/mcp``。

    供 compose（uvicorn app.mcp.server:app）与测试（ASGITransport）使用。
    """
    from app.core.config import settings

    from contextlib import AsyncExitStack, asynccontextmanager

    routes = []
    sub_lifespans = []
    for domain in _MCP_DOMAINS:
        mcp = FastMCP(f"erp-{domain}")
        ns: dict[str, Any] = {"_MCP": mcp}
        _export_domain_tools_into(ns, mcp, domain)
        sub_app = mcp.http_app(path="/mcp")
        sub_lifespans.append(sub_app.router.lifespan_context)
        routes.append(Mount(f"/{domain}", app=sub_app))

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # 每个 fastmcp 子应用的 lifespan 必须进入父应用生命周期，
        # 否则 StreamableHTTPSessionManager 未初始化（initialize 请求 500）
        async with AsyncExitStack() as stack:
            for sub_lifespan in sub_lifespans:
                await stack.enter_async_context(sub_lifespan(app))
            yield

    app = Starlette(routes=routes, lifespan=lifespan,
                    middleware=[Middleware(TokenAuthMiddleware)])
    logger.info("mcp.server_built", domains=list(_MCP_DOMAINS))
    return app


def _export_domain_tools_into(ns: dict, mcp: FastMCP, domain: str) -> None:
    """在给定命名空间内为某域生成并注册全部工具（装饰在 def 处完成）。"""
    from app.core.agent.tools_v2 import tool_registry_v2

    ns["_MCP"] = mcp
    for spec in tool_registry_v2.list_specs(domain=domain):
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
