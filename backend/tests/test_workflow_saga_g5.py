"""
G5 工作流 Saga 补偿 + 影子执行测试（roadmap-logging-sandbox-2026-10-01 G5）
============================================================================

覆盖（M-G5 出口 / UAT-G5-1 预演）：
1. E2E 中断补偿：自定义 DSL（reimb.submit → 演练注入失败的 verify）→
   工作流 error 终止 → 已执行创建步骤逆序自动补偿（单据删除 + 账面清理）；
2. needs_manual：非自动补偿步骤（资金/状态终态类）登记为 auto=false，
   补偿报告点名"已发生且未撤销"的步骤 + error 级告警；
3. 重启恢复（G5.2）：executing 状态的 saga 行 → saga_recover_pending 续跑补偿；
4. 影子执行（G5.3/S2）：write_high 审批单携带影响面快照（impact 字段 +
   preview 人类可读摘要）；资金调拨 impact_fn 实例输出。
"""
import json

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "saga_g5.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    # sagas 表由 migrations 原生 DDL 创建（MySQL），sqlite 测试补同构表
    from sqlalchemy import text as _text

    async with engine.begin() as conn:
        await conn.execute(_text("""
            CREATE TABLE IF NOT EXISTS sagas (
                saga_id VARCHAR(150) PRIMARY KEY,
                saga_type VARCHAR(64),
                status VARCHAR(32),
                steps TEXT,
                current_step INTEGER,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
    yield engine
    await database.close_db()


@pytest.fixture(autouse=True)
def _clean():
    from app.core.observability.alerts import alert_channel
    from app.core.security.audit import audit_chain

    alert_channel._cooldowns.clear()
    alert_channel.stats.update({"raised": 0, "webhook_ok": 0,
                                "webhook_failed": 0, "dead_lettered": 0})
    audit_chain._entries.clear()
    yield
    alert_channel._cooldowns.clear()


@pytest_asyncio.fixture
async def reimb_agent(sqlite_env):
    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.agents.reimbursement.agent import ReimbursementAgent
    import app.agents.reimbursement.tools  # noqa: F401

    agent = ReimbursementAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await orchestrator.register_agent(agent)
    yield agent
    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain == agent.domain:
            del orchestrator._agents[key]


# ===========================================================================
# 1. E2E 中断补偿（UAT-G5-1 核心）
# ===========================================================================

async def test_workflow_failure_triggers_reverse_compensation(
    sqlite_env, reimb_agent, monkeypatch,
):
    from app.core.agent.workflows import start_workflow
    from app.core.agent.tools_v2 import ToolContext
    from app.core.agent import workflow_versions as wfv
    from app.core.security.audit import audit_chain
    from sqlalchemy import select as _select
    from app.db.repositories.base import session_scope

    # 发布演练 DSL：submit（创建类，可补偿）→ verify（演练注入失败）
    defn = {
        "name": "saga_drill_wf",
        "entry": "submit",
        "nodes": [
            {"id": "submit", "type": "task", "handler": "reimb.submit",
             "next": "gate"},
            {"id": "gate", "type": "gate",
             "threshold": {"key": "reimbursement_auto_approve_limit",
                           "default": 5000},
             "amount_from": "result.amount",
             "preview": "报销单 {reimbursement_no} 演练",
             "next_approved": "verify", "next_rejected": "reject"},
            {"id": "verify", "type": "task", "handler": "reimb.verify_approve",
             "next": "END"},
            {"id": "reject", "type": "task", "handler": "reimb.reject_mark",
             "next": "END"},
        ],
    }
    await wfv.save_draft("saga_drill_wf", "1.0.0", defn, created_by="user_admin")
    await wfv.publish("saga_drill_wf", "1.0.0")

    # 注入失败：verify 阶段返回错误（真实创建的报销单已存在 → 触发 error 终止）
    async def _boom(reimb_id):
        return {"error": "演练注入失败：核验服务不可用"}

    monkeypatch.setattr(reimb_agent, "verify_reimbursement", _boom)

    result = await start_workflow(
        "saga_drill_wf",
        {"employee_name": " Saga 演练员", "amount": 300, "category": "office",
         "description": "G5 中断补偿演练", "has_receipt": True},
        ToolContext(user_id="user_admin", username="admin",
                    agent_key="workflow:saga_drill_wf"),
        document_key="PO-DRILL-SAGA",
    )
    assert result["status"] == "error", result

    # 补偿闭环：创建的报销单已被逆序撤销（内存 + DB 双清）
    assert len(reimb_agent._requests) == 0, " Saga 补偿应清空 Agent 内存单据"
    async with session_scope() as session:
        from app.db.models import Reimbursement as reimbursements

        n = (await session.execute(
            _select(text("COUNT(*)")).select_from(reimbursements).where(
                text("employee_name = ' Saga 演练员'")))).scalar()
    assert n == 0, " Saga 补偿应删除已创建的报销单行"

    # 审计与账面
    saga_audits = [e for e in audit_chain._entries
                   if e.action == "workflow.saga_compensated"]
    assert saga_audits and saga_audits[-1].details.get("compensated") == ["reimb.submit"]
    from app.core import database as db_module

    engine = db_module.get_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, steps FROM sagas WHERE saga_type='workflow_saga' "
            "AND saga_id = 'wf:' || (SELECT saga_id FROM sagas WHERE saga_type='workflow_saga' LIMIT 1)"
        ))).first()
    # 直接按 saga_id 精确查（thread_id 未知 → 用步骤内容验证）
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT saga_id, status, steps FROM sagas WHERE saga_type='workflow_saga'"
        ))).mappings().all()
    assert rows and rows[0]["status"] == "compensated"
    steps = json.loads(rows[0]["steps"]) if isinstance(rows[0]["steps"], str) else rows[0]["steps"]
    assert steps and steps[0]["handler"] == "reimb.submit"


async def test_needs_manual_lists_non_auto_steps(sqlite_env, monkeypatch):
    """资金/状态终态类步骤（auto=false）失败时被点名 + error 告警（红线 2 语义）。"""
    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.agents.treasury.agent import TreasuryAgent

    _agent = TreasuryAgent()
    _agent.identity = did_registry.register_agent(
        name=_agent.name, domain=_agent.domain,
        capabilities=_agent.capabilities,
        security_clearance=_agent.security_clearance)
    await orchestrator.register_agent(_agent)

    from app.core.agent.workflow_saga import saga_record, saga_compensate
    from app.core.observability.alerts import alert_channel
    from app.core.security.audit import audit_chain

    # 模拟：p2p.schedule（可补偿）已执行，p2p.pay（资金终态，不可自动补偿）已执行，
    # 随后工作流失败
    await saga_record("wf-thread-manual", "schedule", "p2p.schedule",
                      {"payment_id": "PAY-DRILL-1", "amount": 500})
    await saga_record("wf-thread-manual", "pay", "p2p.pay",
                      {"payment_id": "PAY-DRILL-1", "status": "paid"})

    out = await saga_compensate("wf-thread-manual", reason="演练失败")
    assert out["status"] == "needs_manual"
    # 非自动补偿步骤被点名（钱已动、未撤销，必须让人看见）
    manual_handlers = [m["handler"] for m in out["manual"]]
    assert "p2p.pay" in manual_handlers
    # 可补偿步骤已自动撤销
    assert "p2p.schedule" in out["compensated"]
    assert alert_channel.stats["raised"] >= 1
    saga_audits = [e for e in audit_chain._entries
                   if e.action == "workflow.saga_compensated"]
    assert saga_audits[-1].result == "partial"

    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain == _agent.domain:
            del orchestrator._agents[key]


# ===========================================================================
# 3. 重启恢复（G5.2）
# ===========================================================================

async def test_saga_recover_pending_after_crash(sqlite_env, monkeypatch):
    """kill -9 语义：executing 状态遗留 → 启动恢复续跑补偿。"""
    from app.core.agent.workflow_saga import saga_record, saga_recover_pending
    from app.core import database as db_module

    # 步骤已登记（补偿未跑）——模拟补偿执行前进程死亡
    await saga_record("wf-thread-crash", "submit", "reimb.submit",
                      {"reimbursement_id": "RE-CRASH-1", "amount": 100})
    engine = db_module.get_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status FROM sagas WHERE saga_id='wf:wf-thread-crash'"
        ))).first()
    assert row[0] == "executing"

    # 注册补偿目标（reimb.submit 的补偿按 id 删内存/库——内存无此单即 no-op，
    # 幂等语义）
    out = await saga_recover_pending()
    assert out["recovered"] >= 1
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status FROM sagas WHERE saga_id='wf:wf-thread-crash'"
        ))).first()
    assert row[0] in ("compensated", "needs_manual")


# ===========================================================================
# 4. 影子执行（G5.3/S2）
# ===========================================================================

async def test_write_high_approval_carries_impact(sqlite_env, monkeypatch):
    from pydantic import BaseModel

    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext, ToolRisk, ToolSpecV2, \
        tool_registry_v2
    from app.core.rbac import rbac_engine

    class HiArgs(BaseModel):
        amount: float = 0

    async def handler(ctx, amount=0):
        return {"done": True}

    async def comp(ctx, args, result):
        return None

    async def impact(args):
        return {"调拨金额": args.get("amount"), "调拨后余额": 1000 - args.get("amount", 0),
                "余额是否转负": args.get("amount", 0) > 1000}

    spec = ToolSpecV2(
        name="impact_demo_tool", description="t", handler=handler,
        args_model=HiArgs, risk_level=ToolRisk.WRITE_HIGH,
        resource="treasury", action="create",
        compensation=comp, effects_fn=lambda a: "调拨预演",
        agent_domain="test", idempotent=True, impact_fn=impact,
    )
    monkeypatch.setattr(tool_registry_v2, "_tools",
                        {**tool_registry_v2._tools, "impact_demo_tool": spec},
                        raising=False)
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True,
                        raising=True)

    gov = AgentGovernor()
    r = await gov.execute("impact_demo_tool", {"amount": 500},
                          ToolContext(user_id="user_admin", agent_key="t"))
    assert r.approval is not None
    assert r.approval["impact"]["调拨金额"] == 500
    assert "影响面" in r.approval["preview"], "影响面应并入人类可读预览"
    assert r.approval["impact"]["余额是否转负"] is False


async def test_transfer_impact_fn_shape(sqlite_env):
    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.agents.treasury.agent import TreasuryAgent
    from app.agents.treasury.tools import _transfer_impact

    agent = TreasuryAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance)
    await orchestrator.register_agent(agent)
    try:
        out = await _transfer_impact({"amount": 3000, "from_account": "主账户",
                                      "to_account": "备用金"})
    finally:
        for key in list(orchestrator._agents.keys()):
            if orchestrator._agents[key].domain == agent.domain:
                del orchestrator._agents[key]
    assert out["调拨金额"] == 3000 and "调拨后余额" in out
    assert isinstance(out["余额是否转负"], bool)
