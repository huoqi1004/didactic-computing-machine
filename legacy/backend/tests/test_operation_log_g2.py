"""
G2 操作日志底座测试（roadmap-logging-sandbox-2026-10-01 G2.5）
==============================================================

覆盖：
1. log_operation 统一通道：入队 → 批量落库 → 行内容（含追踪键）；
2. 红线 3：落库失败不丢（内存重试），恢复后续写；
3. 审计事件订阅：审计条目自动流入操作日志（Agent 写操作零改造可查）；
4. G2.3 留存分层：热表 30 天搬运归档、归档 180 天清除（确定性 id 水位）；
5. G2.4 LLM 全文开关：默认摘要 / 按域开启 / 过期失效 / 双人确认提案；
6. LLM 交互日志：摘要模式 vs 全文模式行内容。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + bootstrap 建表（含 operation_logs/archive）+ 引擎重置。"""
    import app.core.database as database
    from app.core.config import settings

    db_file = tmp_path / "oplog_g2.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


@pytest.fixture(autouse=True)
def _reset_oplog_buffer():
    """用例间清空缓冲与统计，保证隔离。"""
    from app.core.observability.operation_log import operation_log_buffer

    operation_log_buffer._buf.clear()
    operation_log_buffer.stats.update({"flushed": 0, "failed": 0, "peak_buffer": 0})
    yield
    operation_log_buffer._buf.clear()


async def _count(engine, table: str, where: str = "", params: dict = None) -> int:
    async with engine.connect() as conn:
        sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
        return (await conn.execute(text(sql), params or {})).scalar()


# ===========================================================================
# 1. 统一通道：入队 → 批量落库
# ===========================================================================

async def test_log_operation_enqueues_and_flushes(sqlite_env):
    from app.core.observability.operation_log import log_operation
    from app.core.observability.trace import bind_trace, get_trace_id

    bind_trace(trace_id="c" * 32, business_key="RE-G2-1")
    await log_operation(actor="user_admin", action="user.login", resource="auth",
                        result="success", details={"username": "admin"},
                        source="http", to_audit_chain=False)
    await log_operation(actor="user_admin", action="rbac.user.create",
                        resource="user:u1", details={}, risk_level="medium")

    engine = sqlite_env
    from app.core.observability.operation_log import operation_log_buffer

    assert operation_log_buffer.pending() == 2
    flushed = await operation_log_buffer.flush_once()
    assert flushed == 2
    assert operation_log_buffer.pending() == 0
    assert await _count(engine, "operation_logs") == 2

    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT actor, action, trace_id, business_key, source "
            "FROM operation_logs WHERE action='user.login'"
        ))).first()
    assert row[0] == "user_admin" and row[1] == "user.login"
    assert row[2] == "c" * 32 and row[3] == "RE-G2-1"  # 追踪键自动继承（G1 上下文）
    assert row[4] == "http"


# ===========================================================================
# 2. 红线 3：落库失败不丢，恢复后续写
# ===========================================================================

async def test_flush_failure_keeps_buffer_then_recovers(sqlite_env, monkeypatch):
    from app.core.observability.operation_log import log_operation, operation_log_buffer

    await log_operation(actor="u1", action="op.a", details={})
    await log_operation(actor="u2", action="op.b", details={})

    # 故障注入：flush 期间引擎执行报错（模拟 MySQL 不可用）
    real_flush = operation_log_buffer.flush_once

    async def broken_flush():
        operation_log_buffer._buf  # noqa: B018
        raise RuntimeError("mysql down")

    monkeypatch.setattr(operation_log_buffer, "flush_once", broken_flush)
    try:
        await operation_log_buffer.flush_once()
    except RuntimeError:
        pass
    monkeypatch.setattr(operation_log_buffer, "flush_once", real_flush)

    # 直接调底层真实 flush——此时缓冲仍应保有 2 行（未被丢弃）
    assert operation_log_buffer.pending() == 2

    flushed = await operation_log_buffer.flush_once()
    assert flushed == 2  # 恢复后全部补写（不丢）
    assert await _count(sqlite_env, "operation_logs") == 2


async def test_real_flush_failure_requeues(sqlite_env, monkeypatch):
    """真实 flush 路径失败 → 行回灌队首（保序），统计显式计数。"""
    from app.core import database as db_module
    from app.core.observability.operation_log import log_operation, operation_log_buffer

    await log_operation(actor="u1", action="op.x", details={})

    class _Boom:
        def begin(self):
            raise RuntimeError("db gone")

    monkeypatch.setattr(db_module, "get_engine", lambda: _Boom())
    flushed = await operation_log_buffer.flush_once()
    assert flushed == 0
    assert operation_log_buffer.stats["failed"] == 1
    assert operation_log_buffer.pending() == 1  # 回灌，未丢弃
    monkeypatch.undo()
    assert await operation_log_buffer.flush_once() == 1


# ===========================================================================
# 3. 审计事件订阅：Agent 写操作零改造流入
# ===========================================================================

async def test_audit_feed_subscription_flows_to_buffer(sqlite_env):
    from app.core.event.bus import event_bus
    from app.core.observability.operation_log import operation_log_buffer
    from app.core.security.audit import audit_chain
    from app.core.observability.trace import bind_trace

    await event_bus.start()
    await operation_log_buffer.start()  # 幂等订阅挂载
    bind_trace(trace_id="d" * 32, business_key="TF-G2-3")

    await audit_chain.log(actor_did="user_cfo", action="tool.treasury_fund_transfer_request.executed",
                          resource="treasury:treasury", result="success",
                          details={"amount": 5000}, risk_level="high",
                          trace_id="d" * 32, business_key="TF-G2-3")
    await asyncio.sleep(0.3)  # 等待总线分发
    rows = [r for r in operation_log_buffer._buf if r["action"].startswith("tool.treasury")]
    assert rows, "审计条目应经订阅流入操作日志缓冲"
    assert rows[-1]["business_key"] == "TF-G2-3"
    assert rows[-1]["source"] == "agent"  # tool.* 归类 agent 来源


# ===========================================================================
# 4. G2.3 留存分层：热→归档→清除（确定性 id 水位分段）
# ===========================================================================

async def test_retention_archive_moves_and_purges(sqlite_env, monkeypatch):
    from app.core.config import settings
    from app.core.maintenance.retention import archive_operation_logs

    engine = sqlite_env
    now = datetime.now(timezone.utc)
    rows = []
    for i, age_days in enumerate([40, 35, 10, 1]):  # 40/35 天过期；10/1 天保留
        rows.append({
            "ts": (now - timedelta(days=age_days)).isoformat(sep=" "),
            "actor": f"u{i}", "source": "http", "action": f"op.{i}",
            "resource": "", "result": "success", "risk_level": "low",
            "trace_id": None, "business_key": None, "request_id": None,
            "details": "{}",
        })
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO operation_logs (ts, actor, source, action, resource, result,"
            " risk_level, trace_id, business_key, request_id, details) "
            "VALUES (:ts,:actor,:source,:action,:resource,:result,:risk_level,"
            ":trace_id,:business_key,:request_id,:details)"), rows)
        # 归档表中已有一条 200 天前的旧归档（应被清除）。
        # 显式高位 id：生产路径归档表只经 INSERT SELECT 接收热表显式 id，
        # 不存在自撞；测试用高位 id 模拟"更早世代的存量归档"。
        await conn.execute(text(
            "INSERT INTO operation_logs_archive (id, ts, actor, source, action, resource,"
            " result, risk_level, trace_id, business_key, request_id, details) "
            "VALUES (999901, :ts,'old','system','op.old','', 'success','low',NULL,NULL,NULL,'{}')"),
            {"ts": (now - timedelta(days=200)).isoformat(sep=" ")})

    monkeypatch.setattr(settings, "operation_log_hot_days", 30)
    monkeypatch.setattr(settings, "operation_log_archive_days", 180)
    out = await archive_operation_logs(engine)
    assert out["archived"] == 2  # 40/35 天两行入档
    assert out["purged"] == 1    # 200 天旧归档清除

    assert await _count(engine, "operation_logs",
                        "action IN ('op.0','op.1')") == 0      # 过期行已搬出
    assert await _count(engine, "operation_logs",
                        "action IN ('op.2','op.3')") == 2      # 热行保留
    assert await _count(engine, "operation_logs_archive",
                        "action IN ('op.0','op.1')") == 2      # 归档在案
    assert await _count(engine, "operation_logs_archive", "action='op.old'") == 0


# ===========================================================================
# 5. G2.4 LLM 全文开关
# ===========================================================================

def _patch_domain_param(monkeypatch, value):
    from app.core.agent.customization import customization_service

    monkeypatch.setattr(customization_service, "get_domain_param",
                        lambda domain, key, default=None: value)


def test_llm_fulltext_default_disabled():
    from app.core.observability.operation_log import llm_fulltext_enabled

    assert llm_fulltext_enabled("finance") is False  # 默认摘要模式


def test_llm_fulltext_enabled_by_domain_and_expiry(monkeypatch):
    from app.core.observability.operation_log import llm_fulltext_enabled, llm_fulltext_state

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    _patch_domain_param(monkeypatch, {"domains": ["finance"], "expires_at": future})
    assert llm_fulltext_enabled("finance") is True
    assert llm_fulltext_enabled("hr") is False
    assert llm_fulltext_state()["expires_at"] == future

    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _patch_domain_param(monkeypatch, {"domains": ["finance"], "expires_at": expired})
    state = llm_fulltext_state()
    assert state["enabled"] is False and state["expired"] is True  # 7 天自动关闭


async def test_llm_fulltext_request_uses_relax_approval(monkeypatch):
    """开启申请走定制层 apply_change（agent 类型=放宽→人审，双人确认复用）。"""
    from app.core.agent.customization import customization_service
    from app.core.observability.operation_log import request_llm_fulltext

    captured = {}

    async def fake_apply(ctype, target, overrides, **kw):
        captured.update(ctype=ctype, target=target, overrides=overrides, **kw)
        return {"status": "pending_approval", "reason": "放宽类变更须人审", "thread_id": "t1"}

    monkeypatch.setattr(customization_service, "apply_change", fake_apply)
    r = await request_llm_fulltext(["finance"], changed_by="user_cio")
    assert r["status"] == "pending_approval"
    assert captured["ctype"] == "agent" and captured["target"] == "observability"
    value = captured["overrides"]["params"]["llm_fulltext_logging"]
    assert value["domains"] == ["finance"] and value["expires_at"]  # ≤7 天自动过期

    with pytest.raises(ValueError, match="不能为空"):
        await request_llm_fulltext([], changed_by="user_cio")


# ===========================================================================
# 6. LLM 交互日志：摘要 vs 全文
# ===========================================================================

class _Ctx:
    user_id = "user_admin"
    thread_id = "th-1"
    agent_key = "reimbursement"


async def test_llm_interaction_digest_by_default(sqlite_env, monkeypatch):
    from app.core.observability.operation_log import (
        log_llm_interaction, operation_log_buffer,
    )

    await log_llm_interaction("reimbursement", _Ctx(), messages_in=5, tokens=800,
                              latency_ms=120.5, reply_text="已提交报销",
                              user_message="帮我提交报销")
    row = operation_log_buffer._buf[-1]
    assert row["action"] == "llm.interaction"
    assert row["details"]["tokens"] == 800 and row["details"]["domain"] == "reimbursement"
    assert "user_message" not in row["details"]  # 默认摘要：全文不落库
    assert "reply_text" not in row["details"]


async def test_llm_interaction_fulltext_when_enabled(sqlite_env, monkeypatch):
    from app.core.observability.operation_log import (
        log_llm_interaction, operation_log_buffer,
    )

    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    _patch_domain_param(monkeypatch, {"domains": ["reimbursement"], "expires_at": future})

    await log_llm_interaction("reimbursement", _Ctx(), messages_in=3, tokens=100,
                              latency_ms=50.0, reply_text="已提交",
                              user_message="帮我提交报销")
    row = operation_log_buffer._buf[-1]
    d = row["details"]
    assert d["fulltext"] is True and d["expires_at"] == future  # 带过期标记（清理依据）
    assert d["user_message"] == "帮我提交报销" and d["reply_text"] == "已提交"
