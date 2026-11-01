"""
MCP Server 双跑一致性测试（路线图 Phase D / D1-D3）
====================================================

退出标准验证：LangGraph（经官方 MCPAdapter / MCP 客户端）调用与本地
治理面调用**行为一致**：

1. 同真实端口起 MCP server（streamable HTTP）；
2. MCP 客户端列出工具——与治理面注册表按域一致；
3. MCP 调用 reimbursement_query_list——返回与本地 governor.execute 同构
   （status/success + 数据一致）；
4. write_high 工具经 MCP 调用 → 返回 approval_required（不执行，人审
   只在 ERP 审批流程）；
5. 无效参数 → error 结构一致。
"""
import asyncio
import socket
import threading

import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.core.config import settings


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest_asyncio.fixture
async def mcp_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + 7 域 Agent 注册 + MCP server 真实端口起跑。"""
    import app.core.database as database

    db_file = tmp_path / "mcp_test.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    monkeypatch.setattr(settings, "mcp_auth_token", "")  # 开发模式
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info

    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.core.rbac import rbac_engine
    from app.agents.reimbursement.agent import ReimbursementAgent
    from app.agents.treasury.agent import TreasuryAgent
    from app.agents.payroll.agent import PayrollAgent
    from app.agents.hr.agent import HRAgent
    from app.agents.crm.agent import CRMAgent
    from app.agents.sales.agent import SalesAgent
    from app.agents.contract.agent import ContractAgent

    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401
    import app.agents.payroll.tools  # noqa: F401
    import app.agents.hr.tools  # noqa: F401
    import app.agents.crm.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.contract.tools  # noqa: F401

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    classes = (ReimbursementAgent, TreasuryAgent, PayrollAgent,
               HRAgent, CRMAgent, SalesAgent, ContractAgent)
    created = []
    for cls in classes:
        agent = cls()
        agent.identity = did_registry.register_agent(
            name=agent.name, domain=agent.domain,
            capabilities=agent.capabilities, security_clearance=agent.security_clearance,
        )
        await orchestrator.register_agent(agent)
        created.append(agent)

    # 起真实 uvicorn（随机端口，后台线程）
    import uvicorn

    from app.mcp.server import create_mcp_asgi_app

    port = _free_port()
    app = create_mcp_asgi_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        await asyncio.sleep(0.1)
        if server.started:
            break

    yield {"engine": engine, "port": port, "base": f"http://127.0.0.1:{port}"}

    server.should_exit = True
    thread.join(timeout=5)
    domains = {a.domain for a in created}
    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain in domains:
            del orchestrator._agents[key]
    await database.close_db()


async def _mcp_call_tools(base: str, domain: str):
    """官方 MCP 客户端（streamable HTTP）列出该域可调用工具。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = f"{base}/{domain}/mcp"
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {t.name: t for t in tools.tools}, session


async def _mcp_call(base: str, domain: str, tool_name: str, args: dict) -> dict:
    import json as _json

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = f"{base}/{domain}/mcp"
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            text = result.content[0].text if result.content else "{}"
            try:
                return _json.loads(text)
            except Exception:  # noqa: BLE001
                return {"raw": text}


async def test_mcp_tool_catalog_matches_registry(mcp_env):
    """MCP 列出的工具与治理面注册表（该域）一致。"""
    from app.core.agent.tools_v2 import tool_registry_v2

    catalog, _ = await _mcp_call_tools(mcp_env["base"], "reimbursement")
    expected = {s.name for s in tool_registry_v2.list_specs(domain="reimbursement")}
    assert expected <= set(catalog.keys()), (
        f"MCP 缺工具: {expected - set(catalog.keys())}"
    )


async def test_mcp_local_dual_run_consistency(mcp_env):
    """双跑一致性：MCP 调用与本地治理面调用返回同构数据。"""
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext

    args = {"limit": 5}
    # 本地跑
    gov = AgentGovernor()
    local = await gov.execute("reimbursement_query_list", dict(args),
                              ToolContext(user_id="local-user"))
    assert local.ok

    # MCP 跑
    remote = await _mcp_call(mcp_env["base"], "reimbursement",
                             "reimbursement_query_list", args)
    assert remote.get("status") == "success"
    assert remote["data"]["total"] == local.data["total"], (
        "双跑不一致：MCP 与本地返回的报销总数不同"
    )


async def test_mcp_write_high_blocked_from_direct_channel(mcp_env):
    """write_high 经 MCP 直连不执行（返回 approval_required 引导走审批流程）。"""
    remote = await _mcp_call(mcp_env["base"], "treasury",
                             "treasury_fund_transfer_request",
                             {"amount": 10000, "from_account": "A",
                              "to_account": "B", "reason": "mcp test"})
    assert remote.get("status") == "approval_required"
    assert remote["tool"] == "treasury_fund_transfer_request"


async def test_mcp_invalid_args_error_shape(mcp_env):
    """无效参数：MCP 与本地同样返回错误结构（不 500）。"""
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext

    gov = AgentGovernor()
    local = await gov.execute("reimbursement_submit", {"amount": -5},
                              ToolContext(user_id="u", thread_id="t"))
    assert not local.ok

    remote = await _mcp_call(mcp_env["base"], "reimbursement",
                             "reimbursement_submit", {"amount": -5})
    # fastmcp 层参数校验先拦（validation 文本）或治理面返回 error 结构——两者皆可
    assert (remote.get("status") == "error"
            or "validation" in str(remote.get("raw", "")).lower()), remote
