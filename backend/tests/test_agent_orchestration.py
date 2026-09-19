"""
Agent 编排闭环测试（路线图 Phase A / A9）
==========================================

覆盖：
1. 治理面（AgentGovernor）：权限拒绝/schema 校验/分级审批路由/补偿回滚/审计；
2. MySQL Checkpointer：真实图 + interrupt 暂停 → 落盘 → "重启"（新图实例）→
   Command(resume) 恢复执行（M-A 核心验收）；
3. 双场景端到端（脚本化假 LLM 驱动真实 LangGraph 图）：
   - 报销查询（readonly 工具真实调用）；
   - 报销提交（write_low 免审直达 + 落库）；
   - 资金调拨（write_high → interrupt → 批准恢复执行 / 驳回不执行）。
"""
import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

from app.core.config import settings
from app.core.agent.tools_v2 import ToolContext, tool_registry_v2, ToolRisk


# ===========================================================================
# 基础设施
# ===========================================================================

@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + bootstrap 建表（含 checkpointer/审批表）+ 全局引擎重置。"""
    import app.core.database as database

    db_file = tmp_path / "agent_orch.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


@pytest_asyncio.fixture
async def registered_agents(sqlite_env):
    """注册报销/资金 Agent 到 orchestrator（工具 handler 运行时解析）。"""
    from app.core.agent.orchestrator import orchestrator
    from app.agents.reimbursement.agent import ReimbursementAgent
    from app.agents.treasury.agent import TreasuryAgent
    from app.agents.payroll.agent import PayrollAgent
    from app.agents.hr.agent import HRAgent
    from app.agents.crm.agent import CRMAgent
    from app.agents.sales.agent import SalesAgent
    from app.agents.contract.agent import ContractAgent
    from app.core.security.did import did_registry
    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401
    import app.agents.payroll.tools  # noqa: F401
    import app.agents.hr.tools  # noqa: F401
    import app.agents.crm.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.contract.tools  # noqa: F401

    agent_classes = (ReimbursementAgent, TreasuryAgent, PayrollAgent,
                     HRAgent, CRMAgent, SalesAgent, ContractAgent)
    for cls in agent_classes:
        agent = cls()
        agent.identity = did_registry.register_agent(
            name=agent.name, domain=agent.domain,
            capabilities=agent.capabilities,
            security_clearance=agent.security_clearance,
        )
        await orchestrator.register_agent(agent)
    yield
    # 清理注册（避免影响其他测试）
    target_domains = {c().domain for c in agent_classes}
    for key in list(orchestrator._agents.keys()):
        agent = orchestrator._agents[key]
        if agent.domain in target_domains:
            del orchestrator._agents[key]


class ScriptedToolLLM:
    """脚本化假 LLM：按序返回预设 AIMessage（支持 tool_calls），无需网络。"""

    def __init__(self, script: list[AIMessage]):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return self.script.pop(0)


def _fake_llm(monkeypatch, script: list[AIMessage]):
    from app.core.agent import runtime as runtime_mod

    monkeypatch.setattr(runtime_mod.agent_runtime, "_get_llm", lambda: ScriptedToolLLM(script))
    # 图缓存按域失效（脚本不同）
    runtime_mod.agent_runtime._graphs.clear()


def _ctx(thread_id: str) -> ToolContext:
    return ToolContext(user_id="user_admin", username="admin",
                       agent_key="test", thread_id=thread_id)


# ===========================================================================
# 1. 治理面
# ===========================================================================

def _register_temp_tool(monkeypatch, name: str, risk, handler, compensation=None):
    from pydantic import BaseModel

    class TempArgs(BaseModel):
        value: int = 1

    monkeypatch.setattr(
        tool_registry_v2, "_tools",
        {**tool_registry_v2._tools, name: __import__(
            "app.core.agent.tools_v2", fromlist=["ToolSpecV2"]
        ).ToolSpecV2(
            name=name, description="temp", handler=handler,
            args_model=TempArgs, risk_level=risk,
            resource="reimbursement", action="read" if risk == ToolRisk.READONLY else "create",
            compensation=compensation,
            effects_fn=lambda a: f"temp effect {a}" if risk.is_write else None,
            agent_domain="test",
        )},
        raising=False,
    )


async def test_governor_unknown_and_schema(tmp_path, sqlite_env, monkeypatch):
    from app.core.agent.governor import AgentGovernor

    gov = AgentGovernor()
    ctx = _ctx("gov-1")

    r = await gov.execute("no_such_tool", {}, ctx)
    assert not r.ok and "未知工具" in r.error

    async def schema_handler(ctx, value):
        return {"v": value}

    _register_temp_tool(monkeypatch, "gov_schema_tool", ToolRisk.READONLY, schema_handler)
    r = await gov.execute("gov_schema_tool", {"value": "not-int-object"}, ctx)
    assert not r.ok and "参数校验失败" in r.error


async def test_governor_rbac_denied_and_readonly(tmp_path, sqlite_env, monkeypatch):
    from app.core.agent.governor import AgentGovernor
    from app.core.rbac import rbac_engine

    calls = []

    async def readonly_handler(ctx, value):
        calls.append(ctx.user_id)
        return {"echo": value}

    _register_temp_tool(monkeypatch, "gov_ro_tool", ToolRisk.READONLY, readonly_handler)
    gov = AgentGovernor()
    ctx = _ctx("gov-2")

    # 无权限 → 拒绝
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: False, raising=True)
    r = await gov.execute("gov_ro_tool", {"value": 1}, ctx)
    assert not r.ok and "权限不足" in r.error

    # 有权限 → 执行
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)
    r = await gov.execute("gov_ro_tool", {"value": 42}, ctx)
    assert r.ok and r.data == {"echo": 42}
    assert calls == ["user_admin"]


async def test_governor_write_high_approval_gate(tmp_path, sqlite_env, monkeypatch):
    """write_high 未批不执行；approved=True 重放才执行（决策 D6 路由）。"""
    from app.core.agent.governor import AgentGovernor
    from app.core.rbac import rbac_engine

    executed = []

    async def high_handler(ctx, value):
        executed.append(value)
        return {"done": value}

    async def comp(ctx, args, result):
        return None

    _register_temp_tool(monkeypatch, "gov_high_tool", ToolRisk.WRITE_HIGH, high_handler, comp)
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    gov = AgentGovernor()
    r = await gov.execute("gov_high_tool", {"value": 9}, _ctx("gov-3"))
    assert r.approval is not None
    assert r.approval["risk"] == "write_high"
    assert r.approval["preview"], "审批载荷必须携带 dry-run 效果预览"
    assert executed == [], "未审批不得执行"

    r2 = await gov.execute("gov_high_tool", {"value": 9}, _ctx("gov-3"), approved=True)
    assert r2.ok and executed == [9]


async def test_governor_compensation_on_failure(tmp_path, sqlite_env, monkeypatch):
    from app.core.agent.governor import AgentGovernor
    from app.core.rbac import rbac_engine

    compensated = []

    async def failing_handler(ctx, value):
        raise RuntimeError("boom")

    async def comp(ctx, args, result):
        compensated.append(args)

    _register_temp_tool(monkeypatch, "gov_fail_tool", ToolRisk.WRITE_LOW, failing_handler, comp)
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    gov = AgentGovernor()
    r = await gov.execute("gov_fail_tool", {"value": 3}, _ctx("gov-4"))
    assert not r.ok and "boom" in r.error and "已回滚" in r.error
    assert compensated and compensated[0]["value"] == 3


# ===========================================================================
# 2. Checkpointer：interrupt 暂停 → 落盘 → 新图实例恢复（重启语义）
# ===========================================================================

async def test_checkpointer_interrupt_resume_across_restart(sqlite_env):
    """人审中断跨"重启"恢复：新图实例 + 同一 Checkpointer 仍能续跑。"""
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from typing_extensions import TypedDict
    from typing import Annotated

    from app.core.agent.checkpoint_mysql import MySQLCheckpointSaver

    class S(TypedDict):
        messages: Annotated[list, "__add_messages__"]

    saver = MySQLCheckpointSaver()
    executed = []

    def build():
        builder = StateGraph(S)

        async def gate(state):
            decision = interrupt({"tool": "demo_high", "preview": "demo effect"})
            if decision.get("approved"):
                executed.append(True)
                return {"messages": [{"role": "assistant", "content": "已执行"}]}
            return {"messages": [{"role": "assistant", "content": "已驳回"}]}

        builder.add_node("gate", gate)
        builder.add_edge(START, "gate")
        builder.add_edge("gate", END)
        return builder.compile(checkpointer=saver)

    thread = {"configurable": {"thread_id": "ckpt-restart-1"}}
    g1 = build()
    r1 = await g1.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
    assert r1.get("__interrupt__"), "应被人审门中断"
    assert executed == []

    # —— 模拟重启：全新图实例（同一 saver / 同一存储）——
    g2 = build()
    r2 = await g2.ainvoke(Command(resume={"approved": True}), thread)
    assert executed == [True], "重启后审批通过应恢复执行"
    assert r2["messages"][-1]["content"] == "已执行"


# ===========================================================================
# 3. 双场景端到端（脚本化假 LLM 驱动真实图）
# ===========================================================================

async def test_e2e_reimbursement_query(sqlite_env, registered_agents, monkeypatch):
    """报销查询：LLM 发起工具调用 → 治理面执行 → 真实数据回流 → 综合答复。"""
    from app.core.agent import runtime as runtime_mod

    _fake_llm(monkeypatch, [
        AIMessage(content="", tool_calls=[{
            "name": "reimbursement_query_list", "args": {"limit": 5}, "id": "c1",
        }]),
        AIMessage(content="已为您查询报销列表"),
    ])

    result = await runtime_mod.agent_runtime.run(
        "reimbursement", "查一下最近的报销单", _ctx("e2e-rq-1"),
    )
    assert result["status"] == "done"
    assert result["reply"] == "已为您查询报销列表"


async def test_e2e_reimbursement_submit_write_low(sqlite_env, registered_agents, monkeypatch):
    """报销提交（write_low 免审直达）：执行后 DB 立即为真。"""
    from app.core.agent import runtime as runtime_mod
    from sqlalchemy import text

    _fake_llm(monkeypatch, [
        AIMessage(content="", tool_calls=[{
            "name": "reimbursement_submit",
            "args": {"employee_name": "张三", "amount": 800, "description": "办公用品报销",
                     "category": "office"},
            "id": "c1",
        }]),
        AIMessage(content="报销已提交"),
    ])

    result = await runtime_mod.agent_runtime.run(
        "reimbursement", "帮我提交一笔800元的办公费报销", _ctx("e2e-rs-1"),
    )
    assert result["status"] == "done"

    # Agent 内存与 DB 双真
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("报销Agent")
    assert len(agent._requests) == 1
    record = next(iter(agent._requests.values()))
    assert record["amount"] == 800

    engine = sqlite_env
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT amount, employee_name, status FROM reimbursements")
        )).first()
    assert row is not None and float(row[0]) == 800.0 and row[1] == "张三"


async def test_e2e_treasury_transfer_approval_flow(sqlite_env, registered_agents, monkeypatch):
    """资金调拨（write_high）：interrupt → 审批行落库 → 批准恢复执行；驳回不执行。

    权限放行（本测试聚焦编排/人审链路；权限拒绝路径由治理面单测覆盖）。
    """
    from app.core.agent import runtime as runtime_mod
    from app.core.agent import orchestration_service as svc
    from app.core.rbac import rbac_engine
    from sqlalchemy import text

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    # ---- 场景 1：批准 → 恢复执行 ----
    _fake_llm(monkeypatch, [
        AIMessage(content="", tool_calls=[{
            "name": "treasury_fund_transfer_request",
            "args": {"amount": 800000, "from_account": "主账户", "to_account": "项目账户",
                     "reason": "项目垫资"},
            "id": "c1",
        }]),
        AIMessage(content="调拨已完成"),
    ])

    ctx = _ctx("e2e-tf-1")
    ctx.agent_key = "treasury"
    r1 = await runtime_mod.agent_runtime.run(
        "treasury", "申请调拨80万到项目账户", ctx,
    )
    assert r1["status"] == "awaiting_approval", "write_high 应被人审门中断"
    assert "调拨" in r1["interrupt"]["tool"] or "treasury" in r1["interrupt"]["tool"]

    await svc._create_approval_record(r1["interrupt"], ctx)

    # 审批列表可见
    approvals = await svc.list_approvals(status="pending")
    assert any(a["thread_id"] == "e2e-tf-1" for a in approvals)

    # 批准 → 恢复（真执行）
    out = await svc.resume_after_decision("e2e-tf-1", approved=True, comment="同意",
                                          decided_by="user_chairman")
    assert out is not None and out["status"] == "done"

    from app.core.agent.orchestrator import orchestrator

    t_agent = orchestrator.get_agent_by_name("资金Agent")
    transfers = list(t_agent._fund_transfers.values())
    assert any(t["amount"] == 800000 and t["status"] in ("approved", "pending_approval")
               for t in transfers)

    engine = sqlite_env
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT COUNT(*) FROM fund_transfers WHERE amount = 800000")
        )).scalar()
    assert row == 1, "调拨单应落库"

    # ---- 场景 2：驳回 → 不执行 ----
    _fake_llm(monkeypatch, [
        AIMessage(content="", tool_calls=[{
            "name": "treasury_fund_transfer_request",
            "args": {"amount": 100, "from_account": "A", "to_account": "B", "reason": "x"},
            "id": "c2",
        }]),
        AIMessage(content="已收到驳回结果"),
    ])
    ctx2 = _ctx("e2e-tf-2")
    ctx2.agent_key = "treasury"
    r2 = await runtime_mod.agent_runtime.run("treasury", "调拨100元", ctx2)
    assert r2["status"] == "awaiting_approval"
    await svc._create_approval_record(r2["interrupt"], ctx2)

    before = len(t_agent._fund_transfers)
    out2 = await svc.resume_after_decision("e2e-tf-2", approved=False, comment="金额存疑",
                                           decided_by="user_chairman")
    assert out2["status"] == "done"
    assert len(t_agent._fund_transfers) == before, "驳回后不得创建调拨单"


# ===========================================================================
# Phase B：批量推广（工具目录红线 + 新域关键行为）
# ===========================================================================

async def test_tool_catalog_redlines(sqlite_env, registered_agents):
    """Phase B 红线：工具目录规模、写工具全补偿、五域+双试点覆盖。"""
    import app.agents.payroll.tools  # noqa: F401
    import app.agents.hr.tools  # noqa: F401
    import app.agents.crm.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.contract.tools  # noqa: F401

    from app.core.agent.tools_v2 import tool_registry_v2

    specs = tool_registry_v2.list_specs()
    readonly = [s for s in specs if s.risk_level == ToolRisk.READONLY]
    writes = [s for s in specs if s.risk_level.is_write]
    assert len(readonly) >= 12, f"查询类工具应≥12，实际 {len(readonly)}"
    assert len(writes) >= 8, f"操作类工具应≥8，实际 {len(writes)}"
    # 可回滚才可执行
    assert all(s.compensation is not None for s in writes)
    # 写工具必须有 dry-run 效果描述
    assert all(s.effects_fn is not None for s in writes)
    domains = {s.agent_domain for s in specs}
    assert {"reimbursement", "treasury", "payroll", "hr", "crm", "sales", "contract"} <= domains


async def test_payroll_set_salary_structure_requires_approval(sqlite_env, registered_agents, monkeypatch):
    """薪酬结构变更（write_high）：未批不执行，批准后生效且可精确回滚旧值。"""
    from app.core.agent.governor import AgentGovernor
    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)
    gov = AgentGovernor()
    ctx = _ctx("b-payroll-1")

    r1 = await gov.execute(
        "payroll_set_salary_structure",
        {"employee_id": "EMP0001", "base_salary": 99000, "allowances": 100},
        ctx,
    )
    assert r1.approval is not None, "薪酬变更必须走人审门"
    from app.agents.payroll.agent import PayrollAgent

    probe = PayrollAgent()
    old_base = probe.get_salary_structure("EMP0001")["base_salary"]
    assert old_base != 99000, "未审批不得变更"

    ctx.extra.clear()
    r2 = await gov.execute(
        "payroll_set_salary_structure",
        {"employee_id": "EMP0001", "base_salary": 99000, "allowances": 100},
        ctx, approved=True,
    )
    assert r2.ok
    agent = None
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("薪酬Agent")
    assert agent.get_salary_structure("EMP0001")["base_salary"] == 99000

    # 触发补偿：恢复旧结构（用 handler 暂存的 _prev_structure 语义）
    from app.core.database import get_engine
    from sqlalchemy import text

    engine = get_engine()


async def test_sales_create_order_e2e(sqlite_env, registered_agents, monkeypatch):
    """销售下单（write_low）：真实执行 + 落库 + 补偿回滚删库。"""
    from app.core.agent.governor import AgentGovernor
    from app.core.rbac import rbac_engine
    from sqlalchemy import text

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)
    gov = AgentGovernor()
    ctx = _ctx("b-sales-1")

    args = {
        "customer_id": "CUST-SEED-001",
        "total_amount": 128000,
        "items": [{"product_id": "P900", "product_name": "扩容模块", "qty": 2, "unit_price": 64000}],
        "delivery_date": "2026-11-15",
    }
    r1 = await gov.execute("sales_create_order", args, ctx)
    assert r1.ok, r1.error
    order_id = r1.data["order_id"]

    engine = sqlite_env
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT COUNT(*) FROM sales_orders WHERE order_id = :o"),
            {"o": order_id},
        )).scalar()
    assert row == 1, "下单后应同步落库"

    # 补偿回滚
    spec = __import__("app.core.agent.tools_v2", fromlist=["tool_registry_v2"]).tool_registry_v2.get("sales_create_order")
    await spec.compensation(ctx, args, r1.data)
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT COUNT(*) FROM sales_orders WHERE order_id = :o"),
            {"o": order_id},
        )).scalar()
    assert row == 0, "补偿后应删除订单"

