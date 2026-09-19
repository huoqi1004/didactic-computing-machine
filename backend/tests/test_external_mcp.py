"""
外部 MCP 工具白名单桥接测试（Phase D / D3）+ Phase D 汇总验证
==============================================================
1. 起 fastmcp demo 外部 server（汇率 mock，进程内 ASGI 端口）；
2. 白名单桥接：外部工具注册为治理面 readonly 工具；
3. 白名单外工具不接入；
4. 治理面调用外部工具（经 RBAC/schema/审计）。
"""
import asyncio
import socket
import threading

import pytest
import pytest_asyncio
from fastmcp import FastMCP
from httpx import ASGITransport

from app.core.config import settings


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest_asyncio.fixture
async def external_env(tmp_path, monkeypatch):
    """sqlite 库 + fastmcp demo 外部 server（fx 汇率）+ 白名单配置。"""
    import app.core.database as database

    db_file = tmp_path / "external_mcp.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info

    # demo 外部 server：汇率工具 ×2（get_rate 白名单内，get_secret 白名单外）
    mcp = FastMCP("fx-demo")

    @mcp.tool(name="get_rate", description="查询汇率")
    async def get_rate(currency: str) -> dict:
        return {"currency": currency, "rate": 7.21}

    @mcp.tool(name="get_secret", description="不应被接入的工具")
    async def get_secret(key: str) -> dict:
        return {"key": key, "value": "should-not-leak"}

    port = _free_port()
    app = mcp.http_app(path="/mcp")
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        await asyncio.sleep(0.1)
        if server.started:
            break

    # 白名单配置：仅 get_rate 接入
    import json as _json

    monkeypatch.setattr(settings, "external_mcp_servers", _json.dumps([
        {"name": "fx", "url": f"http://127.0.0.1:{port}/mcp",
         "tools": ["get_rate", "get_secret_missing"]},
    ]))

    yield {"engine": engine, "port": port}

    server.should_exit = True
    thread.join(timeout=5)
    await database.close_db()


async def test_external_tool_whitelist_bridging(external_env, monkeypatch):
    """白名单桥接：get_rate 注册为治理面工具；白名单外/缺失工具不接入。"""
    from app.core.agent.external_tools import register_external_tools
    from app.core.agent.tools_v2 import tool_registry_v2
    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    count = await register_external_tools()
    assert count == 1, "仅白名单内的 get_rate 应接入（get_secret 被排除、missing 跳过）"

    spec = tool_registry_v2.get("external_fx_get_rate")
    assert spec is not None and spec.risk_level.value == "readonly"
    assert spec.agent_domain == "external"
    assert tool_registry_v2.get("external_fx_get_secret") is None

    # 治理面调用外部工具（经 RBAC/schema/审计）
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext

    gov = AgentGovernor()
    result = await gov.execute(
        "external_fx_get_rate", {"currency": "USD"},
        ToolContext(user_id="user_admin", thread_id="ext-1"),
    )
    assert result.ok and result.data == {"currency": "USD", "rate": 7.21}


async def test_external_tool_schema_validation(external_env, monkeypatch):
    """外部工具参数经治理面 schema 校验（缺必填参数报错）。"""
    from app.core.agent.external_tools import register_external_tools
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext
    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)
    await register_external_tools()

    gov = AgentGovernor()
    result = await gov.execute(
        "external_fx_get_rate", {}, ToolContext(user_id="u", thread_id="ext-2"),
    )
    assert not result.ok and "currency" in result.error


async def test_phase_d_composite_summary(external_env, monkeypatch):
    """Phase D 汇总：MCP 出口（7域）+ 外部接入（1）+ Skills（6）+ 目录 API。"""
    import app.agents.contract.tools  # noqa: F401
    import app.agents.crm.tools  # noqa: F401
    import app.agents.hr.tools  # noqa: F401
    import app.agents.payroll.tools  # noqa: F401
    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401
    from app.core.agent.external_tools import register_external_tools
    from app.core.agent.skills import skill_registry
    from app.core.agent.tools_v2 import tool_registry_v2
    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)
    await register_external_tools()

    specs = tool_registry_v2.list_specs()
    by_risk = {}
    for s in specs:
        by_risk[s.risk_level.value] = by_risk.get(s.risk_level.value, 0) + 1
    assert len(specs) >= 25, f"治理面工具应≥25，实际 {len(specs)}"
    assert by_risk.get("readonly", 0) >= 16
    assert by_risk.get("write_low", 0) >= 7
    assert by_risk.get("write_high", 0) >= 2

    skills = skill_registry.to_catalog()
    assert len(skills) == 6 and all(s["valid"] for s in skills)
