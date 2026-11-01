"""
定制层故障降级（路线图 Phase F5 / F5.4）
==========================================

F5.4 的退出标准是「读取失败自动回退内置默认（显式告警，**0 不可用**）」。
"回退默认"这件事本身在消费点早就有 try/except（governor/runtime/workflows），
所以真正要钉住的是四条容易被漏掉的不变量：

1. **入口不许抛**：`ensure_fresh()` 被治理闸口、MCP 出口、工作流阈值在热路径上
   await，它一旦抛异常，"降级"就变成"不可用"——这是本阶段唯一能把业务打停的路径；
2. **失败要分层**：从没加载成功过（回退内置默认）和加载过再失败（吃旧缓存）
   对运维是两个完全不同的结论，必须用 mode 区分；
3. **降级必须看得见**：状态跳变一条告警 + 一个可查的面（端点 / Prometheus），
   否则管理端改完定制没生效，运维只能翻日志猜；
4. **告警不许刷屏**：重载每 5 秒重试一次，每次一条 ERROR 会把故障埋进噪音里。

DB 用一次性 sqlite、版本信号用 fakeredis（与 F5.3 同一套装置）。
"""
import contextlib

import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def f54_env(tmp_path, monkeypatch):
    """临时 sqlite + fakeredis + RBAC 放行；返回"新实例"构造器（避开单例污染）。"""
    import fakeredis.aioredis

    import app.core.database as database

    db_file = tmp_path / "degrade_f54.db"
    monkeypatch.setattr(settings, "mysql_url",
                        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    monkeypatch.setattr(database, "_redis_client",
                        fakeredis.aioredis.FakeRedis(decode_responses=True),
                        raising=False)

    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    from app.core.agent.customization import CustomizationService

    def new_svc() -> CustomizationService:
        return CustomizationService()

    yield new_svc

    client = database._redis_client
    if client is not None and hasattr(client, "aclose"):
        await client.aclose()
    monkeypatch.setattr(database, "_redis_client", None, raising=False)
    await database.close_db()
    from app.core.metrics import CUSTOMIZATION_DEGRADED

    CUSTOMIZATION_DEGRADED.set(0)


@contextlib.asynccontextmanager
async def _broken_scope():
    """把"读定制层"变成一次真实失败（等价于 MySQL 连接断了 / 库不可达）。"""
    raise OSError("db down")
    yield  # pragma: no cover


async def _write(svc, target: str, value: float) -> dict:
    """agent 类型恒为收紧（即时生效），是这里最干净的写入原语。"""
    return await svc.apply_change("agent", target, {"temperature": value},
                                  changed_by="user_admin", comment="F5.4 降级演练")


async def _force_reload(svc) -> None:
    """越过 5 秒节流并制造版本漂移，保证这次 ensure_fresh 真去读库。"""
    from app.core import redis_store

    await redis_store.bump_key_value("erp:cust:version")
    svc._checked_at = 0.0


# ===========================================================================
# 1. 回退语义：读不到定制层 = 各执行点用内置默认
# ===========================================================================

async def test_cold_start_read_failure_serves_built_in_defaults(f54_env, monkeypatch):
    """冷启动就读不到：不许抛、不许空缓存被当成"定制生效"，并标记降级。"""
    import app.db.repositories.base as repo_base
    from app.core.metrics import CUSTOMIZATION_DEGRADED

    svc = f54_env()
    monkeypatch.setattr(repo_base, "session_scope", _broken_scope, raising=True)

    await svc.ensure_fresh()  # 抛异常就是 F5.4 失败

    st = svc.degradation_status()
    assert st["degraded"] is True
    assert st["mode"] == "defaults", st
    assert st["serving"] == "built_in_defaults", st
    # 回退语义：没有覆盖 = 用代码里的内置默认
    assert svc.get_override("agent", "reimbursement") is None
    assert svc.is_tool_enabled("some_tool") is True
    assert svc.get_tool_risk_override("some_tool") is None
    assert svc.get_threshold("reimbursement_auto_approve_limit") is None
    assert svc.get_domain_param("reimbursement", "require_receipt", default=False) is False
    assert st["cache_entries"] == 0
    assert CUSTOMIZATION_DEGRADED._value.get() == 1


async def test_db_not_configured_is_not_silent(f54_env, monkeypatch):
    """没建会话工厂同样读不到定制层——静默跑默认值会让运维以为管理端改动生效了。"""
    import app.db.repositories.base as repo_base

    monkeypatch.setattr(repo_base, "has_db", lambda: False, raising=True)
    svc = f54_env()
    await svc.ensure_fresh()
    st = svc.degradation_status()
    assert st["degraded"] is True and st["mode"] == "defaults", st
    assert "mysql_unavailable" in st["reason"]


# ===========================================================================
# 2. 失败分层 + 入口不许抛
# ===========================================================================

async def test_warm_failure_keeps_last_known_cache_and_flags_stale(f54_env, monkeypatch):
    """加载成功过一次再失败：吃旧缓存（定制仍生效但可能过期）→ mode=stale_cache。"""
    import app.db.repositories.base as repo_base

    svc = f54_env()
    r = await _write(svc, "warm_agent", 0.5)
    assert r["status"] == "applied"
    await _force_reload(svc)
    await svc.ensure_fresh()
    assert svc.degradation_status()["degraded"] is False

    monkeypatch.setattr(repo_base, "session_scope", _broken_scope, raising=True)
    await _force_reload(svc)
    await svc.ensure_fresh()

    st = svc.degradation_status()
    assert st["degraded"] is True and st["mode"] == "stale_cache", st
    assert st["serving"] == "last_known_cache", st
    got = svc.get_override("agent", "warm_agent")
    assert got and got["overrides"] == {"temperature": 0.5}, "旧缓存不能被失败清掉"
    assert st["cache_entries"] == 1


async def test_ensure_fresh_swallows_any_unexpected_error(f54_env, monkeypatch):
    """
    本阶段唯一能把业务打停的路径：ensure_fresh 抛异常。

    治理闸口（governor）、MCP 出口（mcp_config/mcp_export）、MCP server、管理端视图
    都在热路径上 await 它，所以即便错误发生在预期之外（读信号本身炸了），
    也只能记成降级，不能把异常递出去。
    """
    svc = f54_env()
    await _write(svc, "boom_agent", 0.3)

    async def boom():
        raise RuntimeError("signal reader exploded")

    monkeypatch.setattr(svc, "_read_version_signal", boom, raising=True)
    await svc.ensure_fresh()          # 不许抛

    st = svc.degradation_status()
    assert st["degraded"] is True
    assert "signal reader exploded" in st["reason"]
    got = svc.get_override("agent", "boom_agent")
    assert got and got["overrides"] == {"temperature": 0.3}, "异常后缓存必须照常可读"


# ===========================================================================
# 3. 告警口径：一次故障一条，恢复要看得见
# ===========================================================================

async def test_alert_fires_once_per_incident_not_per_retry(f54_env, monkeypatch):
    """告警只在跳变时发一次：5 秒一次的重试若每次一条 ERROR，故障就淹在噪音里。"""
    import app.db.repositories.base as repo_base
    from app.core.agent import customization as cust_mod

    real_scope = repo_base.session_scope
    seen: list[tuple] = []

    class Spy:
        def __getattr__(self, level):
            def _log(event, **kw):
                seen.append((level, event, kw.get("mode")))
                return None
            return _log

    monkeypatch.setattr(cust_mod, "logger", Spy(), raising=True)
    monkeypatch.setattr(repo_base, "session_scope", _broken_scope, raising=True)

    svc = f54_env()
    for _ in range(5):                       # 模拟 5 轮 5 秒节流窗口后的重试
        await _force_reload(svc)
        await svc.ensure_fresh()

    alerts = [x for x in seen if x[1] == "customization.degraded"]
    assert len(alerts) == 1, f"降级告警刷屏了：{alerts}"
    st = svc.degradation_status()
    assert st["degrade_events"] == 1
    assert st["reload_failures"] == 5, "失败次数仍要累加，便于判断持续了多久"

    # 恢复：库回来了 → 一条 recovered + 降级位清零（下一次故障才会再告一次）
    monkeypatch.setattr(repo_base, "session_scope", real_scope, raising=True)
    seen.clear()
    await _force_reload(svc)
    await svc.ensure_fresh()
    st = svc.degradation_status()
    assert st["degraded"] is False, st
    assert any(x[1] == "customization.degraded_recovered" for x in seen), seen
    assert st["degrade_events"] == 1


# ===========================================================================
# 4. 演练腿：清空生效表 = 回出厂，但审计不跟着丢（红线 3）
# ===========================================================================

async def test_clearing_customization_table_returns_to_factory_defaults(f54_env):
    """风险表里「定制表损坏可清空回出厂」那条腿的 CI 版。"""
    from sqlalchemy import delete, select

    from app.core.agent.customization import THRESHOLD_DEFAULTS, customization_service
    from app.core.agent.workflows import _threshold
    from app.db.models import agent_customizations, customization_versions
    from app.db.repositories.base import session_scope

    # 消费点（workflows._threshold）读的是进程内单例，所以断言走单例；
    # 写入用本用例的实例——顺带把"另一个实例改完，本实例经信号跟上"这条也压到了。
    snapshot = dict(customization_service.__dict__)
    try:
        svc = f54_env()
        g = await svc.apply_change("gate", "reimbursement_auto_approve_limit",
                                   {"value": 1200.0}, changed_by="user_admin",
                                   comment="F5.4 出厂回退演练")
        assert g["status"] == "applied"
        await _force_reload(customization_service)
        await customization_service.ensure_fresh()
        assert _threshold("reimbursement_auto_approve_limit", 5000.0) == 1200.0, \
            "管理端降额后工作流应立刻按新免审线跑"

        async with session_scope() as s:
            await s.execute(delete(agent_customizations))
        await _force_reload(customization_service)
        await customization_service.ensure_fresh()

        assert customization_service.list_active() == [], "清空后生效视图必须为空"
        assert _threshold("reimbursement_auto_approve_limit", 5000.0) == 5000.0, \
            "清空生效表 = 回出厂默认，不需要重启"
        assert THRESHOLD_DEFAULTS["reimbursement_auto_approve_limit"] == 5000.0
        st = customization_service.degradation_status()
        assert st["degraded"] is False and st["cache_entries"] == 0, \
            "清空是正常运营动作，不该报故障"

        async with session_scope() as s:
            rows = (await s.execute(select(customization_versions).where(
                customization_versions.c.target == "reimbursement_auto_approve_limit"))).all()
        assert len(rows) >= 1, "清空生效表不得连带抹掉变更历史（红线 3）"
    finally:
        customization_service.__dict__.clear()
        customization_service.__dict__.update(snapshot)


async def test_cold_failures_never_claim_to_be_serving_old_cache(f54_env, monkeypatch):
    """
    真机演练抓到的误报：坏库实例连读三轮都失败，状态却报 mode=stale_cache
    （「在吃最后一次成功的缓存」），而它从没成功读过——手里那份空缓存是失败留下的。

    根因是拿「试过读」（_loaded_from_db，节流闸门）当「读到过」用了。
    """
    import app.db.repositories.base as repo_base

    monkeypatch.setattr(repo_base, "session_scope", _broken_scope, raising=True)
    svc = f54_env()
    for _ in range(3):
        await _force_reload(svc)
        await svc.ensure_fresh()

    st = svc.degradation_status()
    assert st["mode"] == "defaults", f"从没读到过却报成吃旧缓存：{st}"
    assert st["serving"] == "built_in_defaults", st
    assert st["loaded_from_db"] is False, 'loaded_from_db 要报「成功读到过」，不是「试过读」'
    assert st["degrade_events"] == 1


async def test_disabled_entry_stops_being_an_override(f54_env):
    """
    真机演练第二条腿踩到的语义洞：PUT enabled=false（管理端的「停用」）之后，
    运行时读取器仍把这份覆盖照用——停用等于没停用。

    拆开两种读法：get_override 给「看启停态」的消费点（原始条目），
    get_effective_override / 阈值 / 域参数 给「要生效值」的消费点（停用 = 无覆盖）。
    """
    from app.core.agent.customization import THRESHOLD_DEFAULTS

    svc = f54_env()
    g = await svc.apply_change("gate", "reimbursement_auto_approve_limit",
                               {"value": 1200.0}, changed_by="user_admin", comment="F5.4")
    assert g["status"] == "applied"
    assert svc.get_threshold("reimbursement_auto_approve_limit") == 1200.0

    a = await svc.apply_change("agent", "reimbursement", {"params": {"require_receipt": True}},
                               changed_by="user_admin", comment="F5.4")
    assert a["status"] == "applied"
    assert svc.get_domain_param("reimbursement", "require_receipt", default=False) is True

    # 停用两条定制（覆盖内容不变，只把 enabled 置假）
    # 阈值那份不一样：停用 1200 的覆盖 = 生效值升回出厂 5000 = 放宽，红线 2 须人审。
    # 旧断言写的是 applied，那是 _classify_change 取错新值路径（从 new["value"] 而非
    # new["overrides"]["value"]）导致的"上调绕过审批"，不是设计意图。
    off = await svc.apply_change("gate", "reimbursement_auto_approve_limit",
                                 {"value": 1200.0}, enabled=False,
                                 changed_by="user_admin", comment="F5.4 停用")
    assert off["status"] == "pending_approval", off
    assert svc.get_threshold("reimbursement_auto_approve_limit") == 1200.0, \
        "停用提案批准前不得提前改掉生效阈值（未批先松）"
    assert (await svc.apply_pending("gate", "reimbursement_auto_approve_limit",
                                    approved=True, decided_by="user_chairman")
            )["status"] == "executed"
    assert (await svc.apply_change("agent", "reimbursement",
                                   {"params": {"require_receipt": True}}, enabled=False,
                                   changed_by="user_admin", comment="F5.4 停用")
            )["status"] == "applied"

    assert svc.get_threshold("reimbursement_auto_approve_limit") is None, "停用后应回内置默认"
    assert THRESHOLD_DEFAULTS["reimbursement_auto_approve_limit"] == 5000.0
    assert svc.get_domain_param("reimbursement", "require_receipt", default=False) is False,         "停用后域参数不能再注入"
    assert svc.get_effective_override("agent", "reimbursement") is None
    # 但启停态本身还得读得到（技能/MCP 管理页要显示「已停用」，不是「没配过」）
    raw = svc.get_override("agent", "reimbursement")
    assert raw is not None and raw["enabled"] is False, raw


async def test_disabled_entry_survives_reload_as_disabled(f54_env):
    """
    停用必须是"存下来的状态"，不是热缓存里的临时印象。

    生效表按 status=active + enabled=0 存停用条目（F1 的既定语义），
    所以重载（等价于重启）之后：启停态还在、覆盖仍不生效。
    tool 类型走的是同一条持久化路径（这里用 agent 类型，
    因为 tool 的 target 受工具注册表校验，那是 test_customization 的装置）。
    """
    svc = f54_env()
    r = await svc.apply_change("agent", "reload_off_agent", {"temperature": 0.9},
                               changed_by="user_admin", comment="F5.4")
    assert r["status"] == "applied"
    assert (await svc.apply_change("agent", "reload_off_agent", {"temperature": 0.9},
                                   enabled=False, changed_by="user_admin",
                                   comment="F5.4 停用"))["status"] == "applied"

    await _force_reload(svc)
    await svc.ensure_fresh()

    raw = svc.get_override("agent", "reload_off_agent")
    assert raw is not None and raw["enabled"] is False,         f"停用条目重载后消失了，会被当成\"没配过\"重新生效：{raw}"
    assert svc.get_effective_override("agent", "reload_off_agent") is None
    assert svc.get_domain_param("reload_off_agent", "temperature", default=None) is None


# ===========================================================================
# 5. 可查面：GET /degradation
# ===========================================================================

async def test_degradation_endpoint_exposes_the_state(f54_env):
    """显式告警的第二个出口：巡检脚本不必翻日志，读这个端点就知道现在跑在哪一层。"""
    import time as _time

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.api.routes import customizations as cust_routes
    from app.core.agent import customization as cust_mod

    svc = f54_env()
    await _write(svc, "probe_agent", 0.6)
    await _force_reload(svc)
    await svc.ensure_fresh()
    assert svc.degradation_status()["degraded"] is False

    # 端点读的是进程内单例：把本用例实例的状态搬过去，测完原样还原
    singleton = cust_mod.customization_service
    snapshot = dict(singleton.__dict__)

    class FakeAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = {"user_id": "user_admin", "username": "admin"}
            return await call_next(request)

    app = FastAPI()
    app.include_router(cust_routes.router)
    app.add_middleware(FakeAuth)

    try:
        for k, v in vars(svc).items():
            if k != "_write_locks":
                setattr(singleton, k, v)
        singleton._checked_at = _time.monotonic()   # 端点里的 ensure_fresh 走节流
        base = "/api/v1/agent-ops/customizations/degradation"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            ok = (await client.get(base)).json()
            assert ok["degraded"] is False, ok
            assert ok["serving"] == "customization_cache", ok
            assert ok["cache_entries"] == 1

            singleton._degraded = True
            singleton._degraded_mode = "defaults"
            singleton._degraded_reason = "reload:OSError: db down"
            singleton._degraded_at_wall = "2026-10-01T04:00:00+00:00"
            singleton._cache = {}
            bad = (await client.get(base)).json()
            assert bad["mode"] == "defaults", bad
            assert bad["serving"] == "built_in_defaults", bad
            assert "db down" in bad["reason"]
            assert bad["since"] == "2026-10-01T04:00:00+00:00"
    finally:
        singleton.__dict__.clear()
        singleton.__dict__.update(snapshot)
