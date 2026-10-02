"""
G4 操作回滚账本测试（roadmap-logging-sandbox-2026-10-01 G4）
============================================================

覆盖（M-G4 出口 / UAT-G4-1、G4-2 预演）：
1. 账本登记：governor 写工具执行成功 → 同步落行（含 audit_entry_id/business_key）；
2. 状态机 CAS：合法推进成功，非法推进/并发重复推进被拒；
3. 对账：删除账本行模拟幽灵操作 → reconcile 补登记（backfilled=1）+ P0 告警；
4. 回滚 API：前置检查 blocked 默认拒绝并返回引用清单（红线 6）；
   通过 → 落审批行 → 批准执行 → 单据恢复 + 全量审计 + 账本闭环；
5. 回滚重放：已 compensated 再执行 → CAS 拒绝（P0-3 运营层防线）；
6. 补偿失败处置：compensate_failed + 告警；重新发起可重放（G4.4）；
7. 审批决定路由：resume_after_decision 的 rollback 分支端到端。
"""
import json

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "rollback_g4.db"
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
def _clean_state():
    from app.core.observability.alerts import alert_channel
    from app.core.security.audit import audit_chain

    alert_channel._cooldowns.clear()
    alert_channel.stats.update({"raised": 0, "webhook_ok": 0,
                                "webhook_failed": 0, "dead_lettered": 0})
    yield
    alert_channel._cooldowns.clear()


def _register_rollback_tool(monkeypatch, name="rollback_demo_tool",
                            precheck=None, comp_hook=None):
    """注册一个带内存副作用 + 补偿的临时写工具（低风险免审）。

    注册进全局 tool_registry_v2（monkeypatch 自动恢复）——request_rollback /
    execute_rollback 查的是全局注册表，与生产路径一致。
    """
    from pydantic import BaseModel

    from app.core.agent.tools_v2 import ToolRisk, ToolSpecV2, tool_registry_v2

    class DemoArgs(BaseModel):
        target: str = "demo-doc"

    store = {"docs": {}}

    async def handler(ctx, target="demo-doc"):
        store["docs"][target] = {"status": "created"}
        return {"reimbursement_id": target, "status": "created"}

    async def compensation(ctx, args, result):
        if comp_hook:
            await comp_hook(args, result)
        target = (args or {}).get("target") or (result or {}).get("reimbursement_id")
        store["docs"].pop(target, None)

    spec = ToolSpecV2(
        name=name, description="demo", handler=handler,
        args_model=DemoArgs, risk_level=ToolRisk.WRITE_LOW,
        resource="reimbursement", action="create",
        compensation=compensation, effects_fn=lambda a: f"创建 {a.get('target')}",
        agent_domain="test", idempotent=True,
        rollback_precheck=precheck,
    )
    monkeypatch.setattr(tool_registry_v2, "_tools",
                        {**tool_registry_v2._tools, name: spec}, raising=False)
    return tool_registry_v2, store


# ===========================================================================
# 1/2. 账本登记 + CAS 状态机
# ===========================================================================

async def test_governor_write_appends_ledger(sqlite_env, monkeypatch):
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.operation_ledger import ledger_get, ledger_query
    from app.core.rbac import rbac_engine
    from app.core.security.audit import audit_chain
    from app.core.agent.tools_v2 import ToolContext

    reg, store = _register_rollback_tool(monkeypatch)
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True,
                        raising=True)

    from app.core.observability.trace import bind_trace

    bind_trace(trace_id="b" * 32)
    gov = AgentGovernor()
    r = await gov.execute("rollback_demo_tool", {"target": "DOC-1"},
                          ToolContext(user_id="user_admin", agent_key="t"))
    assert r.ok and r.meta["business_key"] == "DOC-1"

    rows = await ledger_query(tool="rollback_demo_tool")
    assert len(rows) == 1
    row = rows[0]
    assert row["business_key"] == "DOC-1" and row["trace_id"] == "b" * 32
    assert row["status"] == "executed" and row["backfilled"] == 0
    assert row["audit_entry_id"], "应携带审计条目 ID（对账关联键）"
    # 审计条目真实存在且同链
    executed = [e for e in audit_chain._entries if e.action.endswith("executed")]
    assert any(e.entry_id == row["audit_entry_id"] for e in executed)
    full = await ledger_get(row["ledger_id"])
    assert json.loads(full["forward_args"])["target"] == "DOC-1"


async def test_ledger_cas_transitions(sqlite_env, monkeypatch):
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.operation_ledger import (ledger_get, ledger_query,
                                                 ledger_transition)
    from app.core.rbac import rbac_engine
    from app.core.agent.tools_v2 import ToolContext

    reg, _ = _register_rollback_tool(monkeypatch)
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True,
                        raising=True)
    gov = AgentGovernor()
    await gov.execute("rollback_demo_tool", {"target": "DOC-CAS"},
                      ToolContext(user_id="user_admin"))
    row = (await ledger_query(tool="rollback_demo_tool"))[0]
    lid = row["ledger_id"]

    # executed → compensating ✓（重复推进 executed → compensating ✗）
    assert await ledger_transition(lid, "compensating", ("executed",),
                                   rolled_by="user_cio") is True
    assert await ledger_transition(lid, "compensating", ("executed",)) is False
    # compensating → compensated ✓；compensated 后任何重放 ✗
    assert await ledger_transition(lid, "compensated", ("compensating",)) is True
    assert await ledger_transition(lid, "compensating",
                                   ("executed", "compensate_failed")) is False
    assert (await ledger_get(lid))["status"] == "compensated"


# ===========================================================================
# 3. 对账（幽灵操作补登记 + P0 告警）
# ===========================================================================

async def test_reconcile_backfills_ghost_operations(sqlite_env, monkeypatch):
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.operation_ledger import ledger_query, ledger_reconcile
    from app.core.rbac import rbac_engine
    from app.core.agent.tools_v2 import ToolContext
    from sqlalchemy import text as _text

    # 审计条目需真实落库（对账以库对库比对）：建 audit_logs + 打开持久化
    async with sqlite_env.begin() as conn:
        await conn.execute(_text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                entry_id VARCHAR(64) PRIMARY KEY,
                actor_did VARCHAR(128) NOT NULL,
                action VARCHAR(128) NOT NULL,
                resource VARCHAR(256) NOT NULL,
                result VARCHAR(32) NOT NULL,
                details TEXT NOT NULL,
                risk_level VARCHAR(16) NOT NULL DEFAULT 'low',
                prev_hash VARCHAR(128),
                entry_hash VARCHAR(128),
                trace_id VARCHAR(64),
                business_key VARCHAR(128),
                timestamp DATETIME NOT NULL
            )
        """))
    from app.core.security.audit import audit_chain
    monkeypatch.setattr(audit_chain, "_db_available", True, raising=False)
    monkeypatch.setattr(audit_chain, "_db_initialized", True, raising=False)

    reg, _ = _register_rollback_tool(monkeypatch)
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True,
                        raising=True)
    gov = AgentGovernor()
    await gov.execute("rollback_demo_tool", {"target": "DOC-GHOST"},
                      ToolContext(user_id="user_admin"))
    assert len(await ledger_query(tool="rollback_demo_tool")) == 1
    monkeypatch.setattr(audit_chain, "_db_available", False, raising=False)

    # 模拟幽灵窗口：账本行被删（如登记前崩溃且对账未跑的极端态）
    async with sqlite_env.begin() as conn:
        await conn.execute(_text("DELETE FROM operation_ledger"))
    # 审计条目仍在（哈希链），timestamp 为现在 → lookback 覆盖
    out = await ledger_reconcile(lookback_hours=24)
    assert out["audited"] >= 1 and out["backfilled"] >= 1, out

    rows = await ledger_query(tool="rollback_demo_tool")
    assert rows and rows[0]["backfilled"] == 1, "补登记行应带 backfilled 标记"
    # P0 告警已发
    from app.core.observability.alerts import alert_channel
    assert alert_channel.stats["raised"] >= 1


# ===========================================================================
# 4/5/6. 回滚：前置检查 / 审批执行 / 重放 / 失败处置
# ===========================================================================

async def _prepared_ledger(sqlite_env, monkeypatch, precheck=None, comp_hook=None):
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.operation_ledger import ledger_query
    from app.core.rbac import rbac_engine
    from app.core.agent.tools_v2 import ToolContext

    reg, store = _register_rollback_tool(monkeypatch, precheck=precheck,
                                         comp_hook=comp_hook)
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True,
                        raising=True)
    gov = AgentGovernor()
    await gov.execute("rollback_demo_tool", {"target": "DOC-RB"},
                      ToolContext(user_id="user_admin"))
    lid = (await ledger_query(tool="rollback_demo_tool"))[0]["ledger_id"]
    return lid, store


async def test_rollback_precheck_blocked_lists_dependencies(sqlite_env, monkeypatch):
    """红线 6：有下游引用 → 默认拒绝并返回引用清单（UAT-G4-1 反向用例）。"""
    from app.core.agent.operation_ledger import request_rollback

    async def precheck(ctx, args, result):
        return {"blocked": True,
                "dependencies": ["PAY-2026-001 已引用该调拨"],
                "message": "存在下游引用"}

    lid, _ = await _prepared_ledger(sqlite_env, monkeypatch, precheck=precheck)
    r = await request_rollback(lid, requested_by="user_cio", reason="金额错误")
    assert r["status"] == "rejected_precheck"
    assert "PAY-2026-001" in r["dependencies"][0]


async def test_rollback_full_flow_with_approval(sqlite_env, monkeypatch):
    """正向闭环：发起→审批行→批准→补偿执行→账本 compensated + 全量审计。"""
    from app.core.agent.operation_ledger import (execute_rollback, ledger_get,
                                                 request_rollback)
    from app.core.security.audit import audit_chain

    async def precheck(ctx, args, result):
        return {"blocked": False, "dependencies": [], "message": "无下游引用"}

    lid, store = await _prepared_ledger(sqlite_env, monkeypatch, precheck=precheck)

    r = await request_rollback(lid, requested_by="user_cio", reason="收款方错误")
    assert r["status"] == "pending_approval" and r["thread_id"] == f"rollback:{lid}"

    # 审批行存在且 pending
    from app.db.repositories.base import session_scope
    from sqlalchemy import select
    async with session_scope() as session:
        from app.db.models import agent_approval_requests
        row = (await session.execute(
            select(agent_approval_requests).where(
                agent_approval_requests.c.thread_id == r["thread_id"]))).first()
    assert row is not None and row.status == "pending"
    assert row.risk == "write_high" and row.domain == "rollback"

    # 批准 → 执行补偿
    out = await execute_rollback(lid, decided_by="user_cfo", approved=True,
                                 comment="同意回滚")
    assert out["status"] == "compensated", out
    assert store["docs"].get("DOC-RB") is None, "补偿应消除副作用"

    ledger = await ledger_get(lid)
    assert ledger["status"] == "compensated" and ledger["rolled_by"] == "user_cfo"

    # 全量审计链：requested → decided(approval.decided 由路由层记) → executed
    actions = [e.action for e in audit_chain._entries]
    assert "rollback.requested" in actions and "rollback.executed" in actions


async def test_rollback_replay_blocked_by_cas(sqlite_env, monkeypatch):
    """已 compensated 再执行 → CAS 拒绝（P0-3 运营层防线 / UAT-G4-2 重放）。"""
    from app.core.agent.operation_ledger import execute_rollback, request_rollback

    lid, _ = await _prepared_ledger(sqlite_env, monkeypatch)
    await request_rollback(lid, requested_by="user_cio")
    assert (await execute_rollback(lid, "user_cfo", approved=True))["status"] == "compensated"

    replay = await execute_rollback(lid, "user_cfo", approved=True)
    assert replay["status"] == "rejected" and "状态已变更" in replay["reason"]


async def test_rollback_failure_and_replay(sqlite_env, monkeypatch):
    """补偿失败 → compensate_failed + 告警待办；重放路径可重新执行（G4.4）。"""
    from app.core.agent.operation_ledger import (execute_rollback, ledger_get,
                                                 request_rollback)

    calls = {"n": 0}

    async def comp_hook(args, result):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db lock timeout")

    async def precheck(ctx, args, result):
        return {"blocked": False, "dependencies": [], "message": ""}

    lid, store = await _prepared_ledger(sqlite_env, monkeypatch,
                                        precheck=precheck, comp_hook=comp_hook)
    await request_rollback(lid, requested_by="user_cio")
    out = await execute_rollback(lid, "user_cfo", approved=True)
    assert out["status"] == "compensate_failed" and "db lock" in out["error"]
    ledger = await ledger_get(lid)
    assert ledger["status"] == "compensate_failed" and ledger["compensation_error"]

    # 告警（待办）已发
    from app.core.observability.alerts import alert_channel
    assert alert_channel.stats["raised"] >= 1

    # 重新发起 → 重放（CAS 允许 compensate_failed → compensating）→ 成功
    r2 = await request_rollback(lid, requested_by="user_cio", reason="已修复")
    assert r2["status"] == "pending_approval"
    out2 = await execute_rollback(lid, "user_cfo", approved=True)
    assert out2["status"] == "compensated"
    assert calls["n"] == 2  # 第二次补偿成功（协议幂等：第一次失败未破坏状态）


async def test_rollback_via_decision_route(sqlite_env, monkeypatch):
    """审批决定路由端到端：resume_after_decision 的 rollback 分支。"""
    from app.core.agent.operation_ledger import ledger_get, request_rollback
    from app.core.agent.orchestration_service import resume_after_decision

    lid, store = await _prepared_ledger(sqlite_env, monkeypatch)
    r = await request_rollback(lid, requested_by="user_cio", reason="测试路由")

    out = await resume_after_decision(
        thread_id=r["thread_id"], approved=True, comment="批准",
        decided_by="user_cfo")
    assert out["status"] == "compensated"
    assert store["docs"].get("DOC-RB") is None
    assert (await ledger_get(lid))["status"] == "compensated"

    # 驳回路径：另一笔 → 驳回 → 账本保持 executed
    await _prepared_ledger(sqlite_env, monkeypatch)
    from app.core.agent.operation_ledger import ledger_query as _lq
    lid2 = (await _lq(tool="rollback_demo_tool"))[0]["ledger_id"]
    r2 = await request_rollback(lid2, requested_by="user_cio")
    out2 = await resume_after_decision(
        thread_id=r2["thread_id"], approved=False, comment="证据不足",
        decided_by="user_cfo")
    assert out2["status"] == "rejected"
    assert (await ledger_get(lid2))["status"] == "executed"
