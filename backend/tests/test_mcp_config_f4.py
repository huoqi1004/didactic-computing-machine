"""
外部 MCP 接入测试（路线图 Phase F4.1 / F4.2）
==============================================

分两层，避免"用桩验桩"：

1. **SSRF 规则本身用真实代码测**（``test_url_guard_*`` / ``test_save_or_bridge_rejects_*``）：
   url_guard 不打桩，逐条覆盖 §2.4——协议、IP 直连、内网字面量、保留主机名、
   出站域名白名单（含子域与后缀伪装）、DNS 解析到内网、白名单外工具不注册。
2. **F4.2 接线用桩化 guard 测**（``f4_env.stub_guard``）：只放行 resolve_public /
   check_url_static 两步，让 demo server 的 127.0.0.1 地址能走完整链路，
   同时断言"保存路径确实调用过 guard"（见 stub_guard.urls）。

本机环境限制（已实测）：无外网，且任何可解析的主机名都指向内网/链路本地地址，
因此"公网域名解析成功 → 放行"这一正向分支无法在本机真跑，桩化覆盖；
上到出网环境后需补一条真实公网用例（见报告"待补测"）。
"""
import asyncio
import ipaddress
import socket
import threading

import pytest
import pytest_asyncio
from fastmcp import FastMCP

from app.core.config import settings
from app.core.security import url_guard


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ===========================================================================
# 1. url_guard 真实规则（无 DB、无网络）
# ===========================================================================

@pytest.mark.parametrize("url,expect", [
    ("ftp://mcp.example.com/mcp", "http/https"),
    ("mcp://mcp.example.com:7777", "http/https"),
    ("http://127.0.0.1:8080/mcp", "禁止 IP 直连"),
    ("http://169.254.169.254/latest/meta-data/", "禁止 IP 直连"),
    ("http://[::1]:8080/mcp", "禁止 IP 直连"),
    ("http://0.0.0.0:8080/mcp", "禁止 IP 直连"),
    ("http://localhost:8080/mcp", "保留主机名"),
    ("http://metadata/mcp", "保留主机名"),
    ("http://db.internal:9000/mcp", "保留主机名"),
    ("http://printer.local/mcp", "保留主机名"),
    ("http://svc.k8s.localhost/mcp", "保留主机名"),
    ("http:///mcp", "缺少主机名"),
])
def test_url_guard_static_rejects(url, expect):
    """§2.4 静态规则逐条拒绝（内网、元数据、IP 直连、非 http(s)）。"""
    with pytest.raises(url_guard.UnsafeURL) as e:
        url_guard.check_url_static(url)
    assert expect in str(e.value), f"{url} 的拒绝原因不符：{e.value}"


def test_url_guard_blocks_private_ip_ranges():
    """解析后二次校验的分类函数：内网/回环/链路本地/组播/保留/CGNAT 全部判危。"""
    for addr in ("10.1.2.3", "172.16.0.1", "192.168.10.15", "127.0.0.1",
                 "169.254.169.254", "224.0.0.1", "0.0.0.0", "::1", "fe80::1",
                 "100.64.0.1",        # RFC6598 CGNAT：Python 的 is_private 不含它
                 "255.255.255.255",    # 受限广播：靠 is_reserved（地址对象没有 is_broadcast）
                 "203.0.113.7"):       # TEST-NET：Python 亦判非公网
        assert url_guard._blocked_ip(ipaddress.ip_address(addr)), addr
    for addr in ("8.8.8.8", "151.101.3.164", "2606:4700:4700::1111"):
        assert not url_guard._blocked_ip(ipaddress.ip_address(addr)), addr


async def test_validate_outbound_public_allow_and_rebinding_reject(monkeypatch):
    """
    真实 validate_outbound_url 的两个分支（本机无外网，故只桩 DNS 解析这一步）。

    同时是 is_broadcast 崩溃的回归：修复前解析到公网 IP 会抛
    AttributeError('IPv4Address' object has no attribute 'is_broadcast')，
    即"越合法越报错"——管理端保存任何公网地址都会 500。
    """
    loop = asyncio.get_running_loop()

    def resolve(addrs):
        monkeypatch.setattr(
            loop, "getaddrinfo",
            lambda *a, **k: asyncio.sleep(0, result=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (x, 0)) for x in addrs]))

    resolve(["8.8.8.8"])
    assert await url_guard.validate_outbound_url(
        "https://mcp-partner.example.com/mcp") == "https://mcp-partner.example.com/mcp"

    # rebinding 型：一个公网 + 一个内网（DNS 轮询/重绑定）→ 任一内网即拒
    resolve(["8.8.8.8", "169.254.169.254"])
    with pytest.raises(url_guard.UnsafeURL) as e:
        await url_guard.validate_outbound_url("https://mcp-partner.example.com/mcp")
    assert "内网" in str(e.value)


def test_url_guard_rejects_hostname_resolving_to_internal():
    """真实 DNS 二次校验：主机名解析到内网地址必须拒绝（不桩化）。"""
    host = socket.gethostname()
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except OSError:  # 无 DNS/hosts 条目，环境不具备该分支
        pytest.skip(f"主机名 {host} 无法解析，跳过内网解析用例")
    if not any(url_guard._blocked_ip(ipaddress.ip_address(a)) for a in addrs):
        pytest.skip(f"主机名 {host} 未解析到内网地址，跳过")

    with pytest.raises(url_guard.UnsafeURL) as e:
        asyncio.run(url_guard.validate_outbound_url(f"http://{host}:8080/mcp"))
    assert "内网" in str(e.value) or "无法解析" in str(e.value)


def test_url_guard_rejects_unresolvable_host():
    """解析失败按不安全处理：无法证明公网可达就不放行。"""
    with pytest.raises(url_guard.UnsafeURL) as e:
        asyncio.run(url_guard.validate_outbound_url("http://f4-nonexistent.invalid/mcp"))
    assert "无法解析" in str(e.value)


def test_url_guard_allowlist_is_master_control(monkeypatch):
    """复审备注 3：白名单是主控手段——含子域放行、后缀伪装拒绝。"""
    monkeypatch.setattr(settings, "mcp_outbound_allowed_domains", "api.example.com")
    assert url_guard.check_url_static("https://api.example.com/mcp")
    assert url_guard.check_url_static("https://cn.api.example.com/mcp")
    for bad in ("https://evil.com/mcp", "https://api.example.com.evil.com/mcp",
                "https://notexample.com/mcp",
                "https://sh-api.example.com/mcp"):  # 看着像子域，其实父域是 example.com
        with pytest.raises(url_guard.UnsafeURL) as e:
            url_guard.check_url_static(bad)
        assert "白名单" in str(e.value), bad


def test_url_guard_empty_allowlist_defers_to_dns(monkeypatch):
    """白名单为空时退回"公网可达即可"，但仍由 DNS 二次校验兜底。"""
    monkeypatch.setattr(settings, "mcp_outbound_allowed_domains", "")
    assert url_guard.check_url_static("https://anyone.example.org/mcp")


# ===========================================================================
# 2. fixtures
# ===========================================================================

class _GuardStub:
    """记录被校验过的 URL，用于断言"保存/同步路径确实过 guard"。"""

    def __init__(self):
        self.urls: list[str] = []

    async def validate(self, url: str) -> str:
        self.urls.append(url)
        return url

    def check_static(self, url: str) -> str:
        return url


@pytest_asyncio.fixture
async def f4_env(tmp_path, monkeypatch):
    """sqlite 库 + fastmcp demo server（汇率）+ guard 桩（仅接线用例使用）。"""
    import app.core.database as database
    from app.core.agent.tools_v2 import tool_registry_v2
    from app.core.rbac import rbac_engine

    db_file = tmp_path / "mcp_f4.db"
    monkeypatch.setattr(settings, "mysql_url",
                        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info, info
    # mcp_servers 是本轮新增表：确认 bootstrap 的列差集真的把它建出来了
    async with engine.begin() as conn:
        ok = (await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_servers'"
        )).fetchall()
    assert ok, "bootstrap_schema 未建出 mcp_servers 表"

    mcp = FastMCP("fx-demo")

    @mcp.tool(name="get_rate", description="查询汇率")
    async def get_rate(currency: str) -> dict:
        return {"currency": currency, "rate": 7.21}

    @mcp.tool(name="get_quote", description="查询牌价")
    async def get_quote(currency: str) -> dict:
        return {"currency": currency, "bid": 7.20, "ask": 7.22}

    @mcp.tool(name="get_secret", description="白名单外的工具")
    async def get_secret(key: str) -> dict:
        return {"key": key, "value": "should-not-leak"}

    port = _free_port()
    import uvicorn

    config = uvicorn.Config(mcp.http_app(path="/mcp"),
                            host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(80):
        await asyncio.sleep(0.1)
        if server.started:
            break
    assert server.started, "demo MCP server 未启动"

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True)

    stub = _GuardStub()
    before = {s.name for s in tool_registry_v2.list_specs()}

    def use_stub_guard():
        """放行 demo server 的 127.0.0.1 地址，只用于验证接线（规则见第 1 层）。"""
        monkeypatch.setattr(url_guard, "validate_outbound_url", stub.validate)
        monkeypatch.setattr(url_guard, "check_url_static", stub.check_static)

    yield {"engine": engine, "port": port, "stub": stub,
           "use_stub_guard": use_stub_guard,
           "url": f"http://127.0.0.1:{port}/mcp"}

    server.should_exit = True
    thread.join(timeout=5)
    # tool_registry_v2 是 append-only 全局注册表：清掉本用例新增的工具，避免污染后续用例
    for s in list(tool_registry_v2.list_specs()):
        if s.name not in before:
            tool_registry_v2._tools.pop(s.name, None)
    await database.close_db()


async def _register(f4_env, name: str) -> str:
    from app.core.agent import mcp_config

    f4_env["use_stub_guard"]()
    await mcp_config.create_server(name, f4_env["url"], "汇率源", by="user_admin")
    return name


# ===========================================================================
# 3. 服务层：登记 / 校验 / 暴露审批 / 增量注册
# ===========================================================================

async def test_save_rejects_internal_url(f4_env):
    """UAT-F4-1 负向用例：内网 URL 保存被 SSRF 校验拒绝（guard 不打桩）。"""
    from sqlalchemy import func, select

    from app.core.agent import mcp_config
    from app.db.models import mcp_servers
    from app.db.repositories.base import session_scope

    for bad in ("http://127.0.0.1:9999/mcp", "http://169.254.169.254/mcp",
                "http://localhost:8080/mcp", "ftp://mcp.example.com/mcp"):
        with pytest.raises(url_guard.UnsafeURL):
            await mcp_config.create_server("fxsafer", bad, "", by="user_admin")

    async with session_scope() as session:
        n = (await session.execute(
            select(func.count()).select_from(mcp_servers))).scalar()
    assert n == 0, "被 SSRF 拒绝的地址不得落库"


async def test_bridge_refuses_unregistered_internal_server(f4_env):
    """桥接侧同样把门：check_url=True 时内网地址直接拒绝，不发出站请求。"""
    from app.core.agent.external_tools import bridge_server

    res = await bridge_server("fxinline", "http://127.0.0.1:9/mcp", ["get_rate"])
    assert res["ok"] is False and res["registered"] == []
    assert res["error"].startswith("SSRF 校验未通过"), res["error"]


async def test_discover_reports_remote_tools(f4_env):
    """连接测试 = 探测 + 工具发现（只读，不注册）。"""
    from app.core.agent import mcp_config

    f4_env["use_stub_guard"]()
    res = await mcp_config.probe_url(f4_env["url"])
    assert res["ok"] is True, res["error"]
    assert {t["name"] for t in res["tools"]} == {"get_quote", "get_rate", "get_secret"}
    from app.core.agent.tools_v2 import tool_registry_v2

    assert tool_registry_v2.get("external_probe_get_rate") is None, "探测不得注册"


async def test_exposure_requires_approval_then_registers(f4_env):
    """红线 2：新外部工具接入=放宽 → 待批时不注册，批准后才进治理面且为只读。"""
    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc
    from app.core.agent.tools_v2 import tool_registry_v2

    name = await _register(f4_env, "fxa")
    assert f4_env["url"] in f4_env["stub"].urls, "保存路径未调用 SSRF 校验"

    r = await mcp_config.set_exposed_tools(name, ["get_rate"], by="user_admin")
    assert r["status"] == "pending_approval", r
    assert svc.get_override("mcp", name) is None or not svc.get_override("mcp", name).get("enabled")
    assert tool_registry_v2.get("external_fxa_get_rate") is None, (
        "未批准的放宽变更就把外部工具接进了治理面"
    )
    assert r["thread_id"], "放宽提案须给出可批的 thread_id"

    done = await svc.apply_pending("mcp", name, approved=True,
                                   decided_by="user_chairman", comment="同意接入")
    assert done["status"] == "executed", done
    assert done["mcp"]["registered"] == ["get_rate"], done

    spec = tool_registry_v2.get("external_fxa_get_rate")
    assert spec is not None
    assert spec.risk_level.value == "readonly" and spec.agent_domain == "external"
    assert spec.resource == "external"

    # 批准后的工具真的可调用（UAT-F4-1：返回汇率）
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext

    gov = AgentGovernor()
    result = await gov.execute("external_fxa_get_rate", {"currency": "USD"},
                               ToolContext(user_id="user_admin", thread_id="f4-call"))
    assert result.ok and result.data == {"currency": "USD", "rate": 7.21}, result.error


async def test_rejected_proposal_registers_nothing(f4_env):
    """驳回：外部工具一个都不进治理面，生效配置不动。"""
    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc
    from app.core.agent.tools_v2 import tool_registry_v2

    name = await _register(f4_env, "fxb")
    await mcp_config.set_exposed_tools(name, ["get_rate"], by="user_admin")
    out = await svc.apply_pending("mcp", name, approved=False,
                                  decided_by="user_chairman", comment="不同意")
    assert out["status"] == "rejected"
    assert tool_registry_v2.get("external_fxb_get_rate") is None
    assert svc.get_override("mcp", name) is None, "驳回不得留下生效暴露行"


async def test_shrinking_exposure_is_immediate(f4_env):
    """收缩白名单 = 收紧，即时生效；撤销暴露同样即时。"""
    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc

    name = await _register(f4_env, "fxc")
    await mcp_config.set_exposed_tools(name, ["get_rate", "get_quote"], by="admin")
    await svc.apply_pending("mcp", name, approved=True, decided_by="chairman")

    r = await mcp_config.set_exposed_tools(name, ["get_rate"], by="admin")
    assert r["status"] == "applied" and r["registered"] == ["get_rate"]
    ov = svc.get_override("mcp", name)
    assert ov["overrides"]["tools"] == ["get_rate"]

    gone = await mcp_config.set_exposed_tools(name, [], by="admin")
    assert gone["status"] == "applied", "撤销暴露属收紧，须即时"
    assert svc.get_override("mcp", name)["enabled"] is False


async def test_registered_tool_survives_until_disabled_by_tool_gate(f4_env):
    """
    注册表 append-only：撤销 mcp 暴露不会摘掉已注册的工具，
    因此"停用一个已经接进来的外部工具"必须走 tool 类定制停用（收紧即时）。
    """
    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext, tool_registry_v2

    name = await _register(f4_env, "fxd")
    await mcp_config.set_exposed_tools(name, ["get_rate"], by="admin")
    await svc.apply_pending("mcp", name, approved=True, decided_by="chairman")
    assert tool_registry_v2.get("external_fxd_get_rate") is not None

    await mcp_config.set_exposed_tools(name, [], by="admin")
    assert tool_registry_v2.get("external_fxd_get_rate") is not None, (
        "注册表无 unregister，此断言记录的是既有约束"
    )

    r = await svc.apply_change("tool", "external_fxd_get_rate", {"reason": "下线"},
                               enabled=False, changed_by="admin")
    assert r["status"] == "applied", "外部工具可按 tool 类收紧停用"
    result = await AgentGovernor().execute(
        "external_fxd_get_rate", {"currency": "USD"},
        ToolContext(user_id="user_admin", thread_id="f4-off"))
    assert not result.ok and "停用" in result.error


async def test_unknown_target_and_unregistered_server_rejected(f4_env):
    """红线：未登记的 server 不能被定制（防错别字指向任意地址）。"""
    from app.core.agent.customization import CustomizationError, customization_service

    with pytest.raises(CustomizationError) as e:
        await customization_service.apply_change(
            "mcp", "no_such_server", {"tools": ["x"]}, changed_by="admin")
    assert "未登记的 MCP Server" in str(e.value)


async def test_url_change_blocked_while_exposed(f4_env):
    """已有生效工具时禁改地址（注册表 append-only，改了不生效 → 宁可拒绝）。"""
    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc

    name = await _register(f4_env, "fxe")
    await mcp_config.set_exposed_tools(name, ["get_rate"], by="admin")
    await svc.apply_pending("mcp", name, approved=True, decided_by="chairman")

    with pytest.raises(mcp_config.McpConfigError) as e:
        await mcp_config.update_server(name, url="https://api.example.com/mcp",
                                       by="admin")
    assert "撤销暴露" in str(e.value)

    assert await mcp_config.update_server(name, description="改描述可以", by="admin")
    assert (await mcp_config.update_server(name, url=f4_env["url"],
                                           by="admin"))["status"] == "noop"


async def test_name_rules_and_delete_revokes(f4_env):
    """名称约束（决定工具名前缀）；删除登记会撤销暴露并删行。"""
    from sqlalchemy import func, select

    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc
    from app.db.models import mcp_servers
    from app.db.repositories.base import session_scope

    for bad in ("Fx", "1fx", "fx-name", ""):
        with pytest.raises(mcp_config.McpConfigError):
            await mcp_config.create_server(bad, f4_env["url"], "", by="admin")

    f4_env["use_stub_guard"]()
    await mcp_config.create_server("fxf", f4_env["url"], "", by="admin")
    await mcp_config.set_exposed_tools("fxf", ["get_rate"], by="admin")
    await svc.apply_pending("mcp", "fxf", approved=True, decided_by="chairman")

    await mcp_config.delete_server("fxf", by="admin")
    assert svc.get_override("mcp", "fxf") is None or (
        svc.get_override("mcp", "fxf")["enabled"] is False)
    async with session_scope() as session:
        n = (await session.execute(
            select(func.count()).select_from(mcp_servers)
            .where(mcp_servers.c.name == "fxf"))).scalar()
    assert n == 0


async def test_sync_from_db_registers_only_approved(f4_env):
    """lifespan 同步：只有带生效暴露行的 server 才桥接（未登记定制=不暴露，失败安全）。"""
    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc
    from app.core.agent.tools_v2 import tool_registry_v2

    f4_env["use_stub_guard"]()
    for nm in ("fxg", "fxh"):
        await mcp_config.create_server(nm, f4_env["url"], "", by="admin")
    await mcp_config.set_exposed_tools("fxg", ["get_rate"], by="admin")
    await svc.apply_pending("mcp", "fxg", approved=True, decided_by="chairman")
    # 摘掉缓存模拟新进程，再跑启动同步
    svc._cache.clear()
    svc._loaded_from_db = False

    # 换一批不会撞幂等的工具名：先确认启动前不存在
    tool_registry_v2._tools.pop("external_fxg_get_rate", None)
    res = await mcp_config.sync_from_db()
    assert res["servers"] == 1, res
    assert res["registered"] == 1, res
    assert tool_registry_v2.get("external_fxg_get_rate") is not None
    assert tool_registry_v2.get("external_fxh_get_rate") is None, "未批准的 server 不得注册"
    assert res["errors"] == []


async def test_route_layer_passes_decider_not_comment(f4_env):
    """
    decide 路由回归：POST /customizations/{type}/{target}/decide 曾把 comment 放在
    第 4 个位置参数上（签名第 4 位是 decided_by）→ 每次调用 TypeError(500)，
    修对顺序前还会污染审批人（红线 3）。此处直接调用端点函数，绕过鉴权依赖。
    """
    from sqlalchemy import select

    from app.api.routes.customizations import DecideRequest, decide_customization
    from app.core.agent import mcp_config
    from app.core.agent.customization import customization_service as svc
    from app.db.models import customization_versions
    from app.db.repositories.base import session_scope

    name = await _register(f4_env, "fxi")
    r = await mcp_config.set_exposed_tools(name, ["get_rate"], by="user_admin")
    assert r["status"] == "pending_approval"

    out = await decide_customization(
        "mcp", name, DecideRequest(approved=True, comment="同意接入，注意额度"),
        current_user={"user_id": "user_chairman"})
    assert out["status"] == "executed", out

    async with session_scope() as session:
        row = (await session.execute(
            select(customization_versions).where(
                customization_versions.c.type == "mcp",
                customization_versions.c.target == name,
                customization_versions.c.status == "approved_executed")
        )).first()
    assert row.approved_by == "user_chairman", (
        f"approved_by 被写成了审批意见：{row.approved_by!r}")
    assert row.comment == "同意接入，注意额度"


async def test_idempotency_key_blocks_replay_on_exposure(f4_env):
    """防重放（§2.4）：同一幂等键的重复提交不再二次变更。"""
    from app.core.agent import mcp_config

    name = await _register(f4_env, "fxj")
    r1 = await mcp_config.set_exposed_tools(name, ["get_rate"], by="admin",
                                            idempotency_key="f4-replay")
    r2 = await mcp_config.set_exposed_tools(name, ["get_rate", "get_quote"], by="admin",
                                            idempotency_key="f4-replay")
    assert r1["status"] == "pending_approval"
    assert r2["status"] == "duplicate", r2


async def test_mcp_server_routes_are_mounted(f4_env):
    """路由接线：/api/v1/agent-ops/mcp-servers 全套端点已在 app 上可见。"""
    from app.main import app

    paths = {r.path for r in app.routes}
    base = "/api/v1/agent-ops/mcp-servers"
    for p in (base, f"{base}/{{name}}", f"{base}/probe",
              f"{base}/{{name}}/probe", f"{base}/{{name}}/tools"):
        assert p in paths, f"缺少路由 {p}（现有 {sorted(x for x in paths if 'mcp' in x)}）"
