"""
MCP 出口暴露开关测试（路线图 Phase F4.3）
==========================================

覆盖"管理端可配 + server 端过滤"两半：

1. 默认层：无定制时按 ``DEFAULT_EXPORT_DOMAINS`` 全开，非候选域拒绝（不能凭空造域）；
2. 分级：关闭出口=收紧即时生效；重新开放=放宽（对外多开一条通道），须人审（红线 2）；
3. 构建侧：``create_mcp_asgi_app`` 只挂载暴露中的域，工具级停用的工具不出现在清单；
4. **请求侧（本阶段的真正防线）**：ASGI 应用装配完毕后改开关，无需重启即被拒——
   挂载列表是启动时算的，所以强制必须落在每次调用的 ``is_tool_exportable`` 上。
"""
import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def f43_env(tmp_path, monkeypatch):
    """sqlite 库 + sales/reimbursement/treasury 工具注册 + 空的定制缓存。"""
    import app.core.agent as agent_pkg
    import app.core.database as database
    from app.core.agent.tools_v2 import tool_registry_v2
    from app.core.rbac import rbac_engine

    db_file = tmp_path / "mcp_export.db"
    monkeypatch.setattr(settings, "mysql_url",
                        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info

    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401

    assert tool_registry_v2.list_specs(domain="sales"), "sales 域应有工具"
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True)

    svc = agent_pkg.customization.customization_service
    svc._cache.clear()
    svc._loaded_from_db = False
    yield {"engine": engine}
    svc._cache.clear()
    svc._loaded_from_db = False
    await database.close_db()


def _export():
    from app.core.agent import mcp_export

    return mcp_export


async def test_defaults_and_candidate_guard(f43_env):
    """无定制 = 内置默认全开；非候选域拒绝（管理端只有开关权，没有造域权）。"""
    from app.core.agent.customization import CustomizationError

    m = _export()
    assert m.is_domain_exposed("sales") is True
    assert m.is_domain_exposed("external") is False  # 不在候选清单里
    with pytest.raises(CustomizationError) as e:
        await m.set_domain_exposed("inventory", True, by="admin")
    assert "非候选出口域" in str(e.value)

    rows = await m.resolve()
    assert [r["domain"] for r in rows] == list(m.DEFAULT_EXPORT_DOMAINS)
    assert all(r["exposed"] and not r["customized"] for r in rows)
    sales = next(r for r in rows if r["domain"] == "sales")
    assert sales["tool_total"] >= 5 and sales["tool_exposed"] == sales["tool_total"]


async def test_close_immediate_open_requires_approval(f43_env):
    """关闭即时；重新开放属放宽，批准前出口仍处于关闭态（红线 2）。"""
    from app.core.agent.customization import customization_service as svc

    m = _export()
    r = await m.set_domain_exposed("sales", False, by="user_admin")
    assert r["status"] == "applied", r
    assert m.is_domain_exposed("sales") is False

    again = await m.set_domain_exposed("sales", True, by="user_admin")
    assert again["status"] == "pending_approval", again
    assert m.is_domain_exposed("sales") is False, "未批准的重新开放不得先放行"

    done = await svc.apply_pending("mcp_export", "sales", approved=True,
                                   decided_by="user_chairman")
    assert done["status"] == "executed", done
    assert m.is_domain_exposed("sales") is True

    rows = await m.resolve()
    sales = next(r for r in rows if r["domain"] == "sales")
    assert sales["customized"] is True and sales["exposed"] is True


async def test_rejected_reopen_keeps_domain_closed(f43_env):
    """驳回重新开放：出口维持关闭。"""
    from app.core.agent.customization import customization_service as svc

    m = _export()
    await m.set_domain_exposed("crm", False, by="user_admin")
    await m.set_domain_exposed("crm", True, by="user_admin")
    out = await svc.apply_pending("mcp_export", "crm", approved=False,
                                  decided_by="user_chairman")
    assert out["status"] == "rejected"
    assert m.is_domain_exposed("crm") is False


async def test_asgi_mounts_and_tool_list_follow_switches(f43_env):
    """构建侧过滤：关掉的域不挂载；被停用的工具不进 list_tools。"""
    from fastmcp import FastMCP

    from app.core.agent.customization import customization_service as svc
    from app.mcp.server import _export_domain_tools_into, create_mcp_asgi_app

    m = _export()
    await m.set_domain_exposed("payroll", False, by="admin")
    app = create_mcp_asgi_app()
    mounted = {getattr(r, "path", "") for r in app.routes}
    assert "/payroll" not in mounted, mounted
    for keep in ("sales", "hr", "crm"):
        assert f"/{keep}" in mounted, mounted

    await svc.apply_change("tool", "sales_create_order", {"reason": "出口演练下线"},
                           enabled=False, changed_by="admin")
    ns: dict = {}
    sub = FastMCP("erp-sales")
    _export_domain_tools_into(ns, sub, "sales")
    names = {t.name for t in await sub.list_tools()}
    assert "sales_create_order" not in names, names
    assert "sales_confirm_order" in names, names
    assert m.is_tool_exportable("sales_create_order", "sales") is False


async def test_runtime_gate_blocks_without_restart(f43_env):
    """
    请求侧过滤：先按"全开"装配处理器，再关掉域——调用必须立刻被拒。

    这是 F4.3 的实际语义（挂载表启动时算好，改开关不重启），
    只测构建期过滤会给出虚假的安全感。
    """
    from app.mcp.server import _build_generic_handler

    m = _export()
    handler = _build_generic_handler("sales", "sales_confirm_order")

    ok = await handler("sales_confirm_order", {})
    assert ok["status"] != "error" or "未开放 MCP 出口" not in ok.get("error", ""), ok

    await m.set_domain_exposed("sales", False, by="admin")
    blocked = await handler("sales_confirm_order", {})
    assert blocked["status"] == "error" and "未开放 MCP 出口" in blocked["error"], blocked

    # 工具级同理：域仍开放，单个工具停用后出口拒绝
    from app.core.agent.customization import customization_service as svc

    await m.set_domain_exposed("sales", True, by="admin")
    await svc.apply_pending("mcp_export", "sales", approved=True, decided_by="chairman")
    assert m.is_domain_exposed("sales") is True
    await svc.apply_change("tool", "sales_confirm_order", {"reason": "单点下线"},
                           enabled=False, changed_by="admin")
    one = await handler("sales_confirm_order", {})
    assert one["status"] == "error" and "未开放 MCP 出口" in one["error"], one


async def test_exposure_routes_are_mounted(f43_env):
    """路由接线。"""
    from app.main import app

    paths = {r.path for r in app.routes}
    assert "/api/v1/agent-ops/mcp-exposure" in paths
    assert "/api/v1/agent-ops/mcp-exposure/{domain}" in paths
