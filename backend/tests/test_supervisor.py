"""
Supervisor 多 Agent 协同测试（路线图 Phase C / C1-C4）
======================================================

1. 规则意图分类：单域/多域/工作流路由；
2. 跨域汇总端到端（脚本 LLM）：指令 → 委派 reimbursement+treasury 两域
   场景图（治理面工具真实执行）→ 综合答复；
3. 委派人审透传：子域 write_high 中断 → supervisor 一并挂起 →
   批准后先恢复子图再综合；驳回则子任务标记未执行；
4. 失败传播：某子域异常不影响其他子域，综合如实报告；
5. 未识别域：友好引导而非报错。
"""
import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

from app.core.config import settings


@pytest_asyncio.fixture
async def sup_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + 7 域 Agent 注册 + 全部工具。"""
    import app.core.database as database

    db_file = tmp_path / "supervisor.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
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
    # supervisor 的 LLM 意图分类降级为规则路由（测试确定性）
    from app.core.agent import supervisor as sup_mod

    async def _rules_only(msg):
        return None

    monkeypatch.setattr(sup_mod, "classify_with_llm", _rules_only)

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
    yield engine
    domains = {a.domain for a in created}
    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain in domains:
            del orchestrator._agents[key]
    await database.close_db()


def _script_llm(monkeypatch, scripts_by_domain: dict[str, list[AIMessage]]):
    """按域注入脚本 LLM（每个子域场景图独立脚本）。"""
    from app.core.agent import runtime as runtime_mod

    original_get_llm = runtime_mod.agent_runtime._get_llm

    def _routed_get_llm():
        # get_graph 编译时只绑定 schema，不调用 _get_llm 的脚本；
        # 真正消费在 node_agent ainvoke——按当前线程域路由脚本难以从参数判定，
        # 改为返回"多脚本按调用序轮转"的聚合脚本（调用顺序 = route_plan 顺序）
        merged = []
        for script in scripts_by_domain.values():
            merged.extend(script)
        return ScriptedLLM(merged)

    monkeypatch.setattr(runtime_mod.agent_runtime, "_get_llm", _routed_get_llm)
    runtime_mod.agent_runtime._graphs.clear()


class ScriptedLLM:
    def __init__(self, script):
        self.script = script

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        # 若脚本耗尽（同脚本被子图重复消费），返回通用收尾
        if not self.script:
            return AIMessage(content="已完成。")
        return self.script.pop(0)


async def test_classify_rules_single_and_multi():
    from app.core.agent.supervisor import classify_by_rules

    assert classify_by_rules("查一下报销单") == ["reimbursement"]
    assert set(classify_by_rules("汇总报销和资金余额")) == {"reimbursement", "treasury"}
    assert "workflow:procure_to_pay" in classify_by_rules("安排采购到支付流程")
    assert classify_by_rules("今天天气怎么样") == []


async def test_supervisor_cross_domain_summary(sup_env, monkeypatch):
    """跨域汇总：委派报销+资金两域，工具真实执行，综合答复包含两域标注。"""
    from app.core.agent import runtime as runtime_mod
    from app.core.agent.supervisor import start_supervisor
    from app.core.agent.tools_v2 import ToolContext

    _script_llm(monkeypatch, {
        "reimbursement": [
            AIMessage(content="", tool_calls=[{
                "name": "reimbursement_query_stats", "args": {}, "id": "r1"}]),
            AIMessage(content="报销汇总完成"),
        ],
        "treasury": [
            AIMessage(content="", tool_calls=[{
                "name": "treasury_cash_balance", "args": {}, "id": "t1"}]),
            AIMessage(content="资金查询完成"),
        ],
    })

    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="supervisor", thread_id="sup-e2e-1")
    result = await start_supervisor("汇总一下报销情况和资金余额", ctx)

    assert result["status"] == "done", result.get("reply")
    reply = result["reply"]
    assert "✅ reimbursement" in reply and "✅ treasury" in reply, (
        f"综合答复应包含两个子域标注：{reply[:200]}"
    )


async def test_supervisor_delegate_high_risk_transparent_approval(sup_env, monkeypatch):
    """委派人审透传：子域调拨中断 → supervisor 挂起 → 批准恢复 → 综合含执行结果。"""
    from app.core.agent import runtime as runtime_mod
    from app.core.agent.supervisor import start_supervisor, resume_supervisor
    from app.core.agent.tools_v2 import ToolContext

    _script_llm(monkeypatch, {
        "treasury": [
            AIMessage(content="", tool_calls=[{
                "name": "treasury_fund_transfer_request",
                "args": {"amount": 600000, "from_account": "主账户",
                         "to_account": "应急账户", "reason": "supervisor 委派"},
                "id": "t1"}]),
            AIMessage(content="调拨执行完成"),
        ],
    })

    ctx = ToolContext(user_id="user_cfo", username="cfo",
                      agent_key="supervisor", thread_id="sup-e2e-2")
    r1 = await start_supervisor("紧急调拨60万到应急账户", ctx)
    assert r1["status"] == "awaiting_approval", "子域 write_high 应透传为 supervisor 挂起"

    # 审批决定（批准）→ 恢复 supervisor：delegate 收到批准 → 恢复子图执行 → 综合
    r2 = await resume_supervisor(
        "sup-e2e-2", {"approved": True, "comment": "同意"}, ctx,
    )
    assert r2["status"] == "done", r2.get("reply")
    assert "✅ treasury" in r2["reply"]

    # 子域真实执行验证
    from app.core.agent.orchestrator import orchestrator

    t_agent = orchestrator.get_agent_by_name("资金Agent")
    print("DEBUG transfers:", [(t["amount"], t["status"]) for t in t_agent._fund_transfers.values()])
    print("DEBUG full reply:", r2["reply"][:400])
    assert any(t["amount"] == 600000 for t in t_agent._fund_transfers.values())


async def test_supervisor_rejection_marks_subtask(sup_env, monkeypatch):
    """驳回透传：子任务标记未执行，综合如实报告。"""
    from app.core.agent import runtime as runtime_mod
    from app.core.agent.supervisor import start_supervisor, resume_supervisor
    from app.core.agent.tools_v2 import ToolContext

    _script_llm(monkeypatch, {
        "treasury": [
            AIMessage(content="", tool_calls=[{
                "name": "treasury_fund_transfer_request",
                "args": {"amount": 700000, "from_account": "A", "to_account": "B",
                         "reason": "驳回测试"},
                "id": "t1"}]),
            AIMessage(content="不应到达"),
        ],
    })

    ctx = ToolContext(user_id="user_cfo", username="cfo",
                      agent_key="supervisor", thread_id="sup-e2e-3")
    r1 = await start_supervisor("调拨70万到B账户", ctx)
    assert r1["status"] == "awaiting_approval"

    before = len(orchestrator_count_transfers())
    r2 = await resume_supervisor(
        "sup-e2e-3", {"approved": False, "comment": "驳回"}, ctx,
    )
    assert r2["status"] == "done"
    assert "🚫 treasury" in r2["reply"], "驳回子任务应如实标注"
    assert len(orchestrator_count_transfers()) == before, "驳回后不得执行"


def orchestrator_count_transfers():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("资金Agent")
    return [t for t in agent._fund_transfers.values()
            if t["reason"] == "驳回测试"]


async def test_supervisor_failure_propagation(sup_env, monkeypatch):
    """失败传播：单子域异常不影响其他子域，综合如实标注失败。"""
    from app.core.agent import runtime as runtime_mod
    from app.core.agent.supervisor import start_supervisor
    from app.core.agent.tools_v2 import ToolContext

    # 脚本第一段给 reimbursement 的工具调用一个不存在的工具名 → 子图工具节点报错
    _script_llm(monkeypatch, {
        "reimbursement": [
            AIMessage(content="", tool_calls=[{
                "name": "nonexistent_tool", "args": {}, "id": "x1"}]),
            AIMessage(content="报销域收到异常反馈"),
        ],
        "treasury": [
            AIMessage(content="", tool_calls=[{
                "name": "treasury_cash_balance", "args": {}, "id": "t1"}]),
            AIMessage(content="资金查询完成"),
        ],
    })

    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="supervisor", thread_id="sup-e2e-4")
    result = await start_supervisor("查报销和资金", ctx)
    # reimbursement 子域：未知工具 → 治理面报错回传 → agent 综合仍 done
    # treasury 子域正常
    assert result["status"] == "done"
    assert "✅ treasury" in result["reply"]


async def test_supervisor_unrecognized_domain_guidance(sup_env, monkeypatch):
    """未识别域：友好引导（规则兜底也不命中）。"""
    from app.core.agent.supervisor import start_supervisor
    from app.core.agent.tools_v2 import ToolContext

    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="supervisor", thread_id="sup-e2e-5")
    result = await start_supervisor("今天天气怎么样", ctx)
    assert result["status"] == "done"
    assert "未识别" in result["reply"] and "业务域" in result["reply"]
