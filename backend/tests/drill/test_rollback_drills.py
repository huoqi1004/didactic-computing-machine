"""
回滚演练框架（路线图 Phase E / E1）
====================================

对**全部写类工具**执行标准演练：真实执行（approved）→ 断言副作用存在 →
运行注册的补偿函数 → 断言副作用消除（数据库一致性）。

设计红线验证：**可回滚才可执行**——任何写工具若补偿后无法恢复一致性，
本演练失败即阻断 CI。新增写工具时必须在 ``ROLLBACK_DRILLS`` 登记
演练条目（缺失登记的写工具由 test_all_write_tools_have_drills 强制）。

演练失败语义：
- effect_check 失败 = 工具未正确执行（功能问题）；
- cleanup_check 失败 = 补偿回滚失效（**安全问题，最高优先级**）。

变更类工具（薪资结构/员工档案/订单确认/合同审批）的补偿依赖
ctx.extra 暂存旧值，在专项测试体内逐步演练。
"""
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings
from app.core.agent.tools_v2 import ToolContext, tool_registry_v2


# ---------------------------------------------------------------------------
# 演练清单（新增写工具必须登记）
# ---------------------------------------------------------------------------

async def _scalar_one(engine, sql: str, params: dict | None = None):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).scalar()


class Drill:
    def __init__(self, tool, args, effect_sql, effect_expect,
                 cleanup_sql, cleanup_expect, note=""):
        self.tool = tool
        self.args = args
        self.effect_sql = effect_sql
        self.effect_expect = effect_expect
        self.cleanup_sql = cleanup_sql
        self.cleanup_expect = cleanup_expect
        self.note = note


ROLLBACK_DRILLS = [
    Drill(
        tool="reimbursement_submit",
        args={"employee_name": "演练报销员", "category": "office",
              "amount": 777, "description": "回滚演练", "has_receipt": True},
        effect_sql="SELECT COUNT(*) FROM reimbursements WHERE employee_name='演练报销员' AND amount=777",
        effect_expect=1,
        cleanup_sql="SELECT COUNT(*) FROM reimbursements WHERE employee_name='演练报销员' AND amount=777",
        cleanup_expect=0,
        note="提交类：补偿=按 reimbursement_id 删库+清内存",
    ),
    Drill(
        tool="treasury_fund_transfer_request",
        args={"amount": 450000, "from_account": "主账户", "to_account": "演练账户",
              "reason": "回滚演练"},
        effect_sql="SELECT COUNT(*) FROM fund_transfers WHERE reason='回滚演练' AND amount=450000",
        effect_expect=1,
        cleanup_sql="SELECT status FROM fund_transfers WHERE reason='回滚演练' AND amount=450000",
        cleanup_expect="cancelled",
        note="调拨类：补偿=软撤销（status=cancelled），保留审计痕迹",
    ),
    Drill(
        tool="payroll_calculate",
        args={"employee_id": "EMP0001", "period": "2099-01"},
        effect_sql="SELECT COUNT(*) FROM payroll_records WHERE employee_id='EMP0001' AND period='2099-01'",
        effect_expect=1,
        cleanup_sql="SELECT COUNT(*) FROM payroll_records WHERE employee_id='EMP0001' AND period='2099-01'",
        cleanup_expect=0,
        note="计算类：补偿=按 record_id 删库+清内存",
    ),
    Drill(
        tool="hr_add_employee",
        args={"name": "演练新员工", "department": "演练部", "position": "演练岗"},
        effect_sql="SELECT COUNT(*) FROM employees WHERE full_name='演练新员工'",
        effect_expect=1,
        cleanup_sql="SELECT COUNT(*) FROM employees WHERE full_name='演练新员工'",
        cleanup_expect=0,
        note="新增类：补偿=按 employee_id 删库+清内存",
    ),
    Drill(
        tool="crm_create_customer",
        args={"name": "演练客户", "industry": "演练业", "segment": "C",
              "contact_name": "演练联系人", "contact_phone": "13000000000"},
        effect_sql="SELECT COUNT(*) FROM customers WHERE customer_name='演练客户'",
        effect_expect=1,
        cleanup_sql="SELECT COUNT(*) FROM customers WHERE customer_name='演练客户'",
        cleanup_expect=0,
        note="新增类：补偿=按 customer_id 删库+清内存+清分级",
    ),
    Drill(
        tool="sales_create_order",
        args={"customer_id": "CUST-SEED-001", "total_amount": 66000,
              "items": [{"product_id": "P-DRILL", "product_name": "演练品", "qty": 1,
                         "unit_price": 66000}],
              "delivery_date": ""},
        effect_sql="SELECT COUNT(*) FROM sales_orders WHERE total_amount=66000",
        effect_expect=1,
        cleanup_sql="SELECT COUNT(*) FROM sales_orders WHERE total_amount=66000",
        cleanup_expect=0,
        note="下单类：补偿=按 order_id 删库+清内存",
    ),
    Drill(
        tool="contract_create",
        args={"title": "演练合同", "type": "服务", "counterparty": "演练对手方",
              "amount": 88000, "department": "技术部",
              "start_date": "2026-10-01", "end_date": "2027-09-30", "terms": ""},
        effect_sql="SELECT COUNT(*) FROM contracts WHERE title='演练合同'",
        effect_expect=1,
        cleanup_sql="SELECT COUNT(*) FROM contracts WHERE title='演练合同'",
        cleanup_expect=0,
        note="创建类：补偿=按 contract_id 删库+清内存",
    ),
]

# 变更类专项演练（ctx.extra 暂存旧值 → 补偿恢复）
CHANGE_DRILLS = {
    "payroll_set_salary_structure",
    "hr_update_employee",
    "sales_confirm_order",
    "contract_approve",
}


@pytest_asyncio.fixture
async def drill_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + 7 域 Agent 注册 + 工具 + RBAC 放行。"""
    import app.core.database as database

    db_file = tmp_path / "rollback_drills.db"
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


async def _execute_approved(engine, tool: str, args: dict, ctx):
    """演练辅助：write 工具以 approved=True 真实执行。"""
    from app.core.agent.governor import AgentGovernor

    gov = AgentGovernor()
    result = await gov.execute(tool, dict(args), ctx, approved=True)
    assert result.ok, f"[{tool}] 执行失败：{result.error}"
    return result


@pytest.mark.parametrize("drill", ROLLBACK_DRILLS, ids=lambda d: d.tool)
async def test_rollback_drill_table_driven(drill, drill_env):
    """表驱动演练：执行 → 副作用存在 → 补偿 → 副作用消除。"""
    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="drill", thread_id=f"drill:{drill.tool}")
    result = await _execute_approved(drill_env, drill.tool, drill.args, ctx)

    effect = await _scalar_one(drill_env, drill.effect_sql)
    assert effect == drill.effect_expect, (
        f"[{drill.tool}] 副作用未出现（工具未正确落库）：{effect}"
    )

    spec = tool_registry_v2.get(drill.tool)
    assert spec.compensation is not None, f"[{drill.tool}] 写工具必须有补偿"
    await spec.compensation(ctx, dict(drill.args), result.data)

    cleanup = await _scalar_one(drill_env, drill.cleanup_sql)
    assert cleanup == drill.cleanup_expect, (
        f"[{drill.tool}] 补偿后副作用仍存在——回滚失效（安全问题）：{cleanup}"
    )


async def test_rollback_drill_payroll_set_salary_structure(drill_env):
    """变更类演练：薪资结构变更 → 补偿精确恢复旧值。"""
    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="drill", thread_id="drill:salary")
    args = {"employee_id": "EMP0001", "base_salary": 99000, "allowances": 100}

    result = await _execute_approved(drill_env, "payroll_set_salary_structure", args, ctx)
    row = await _scalar_one(
        drill_env, "SELECT base_salary FROM salary_structures WHERE employee_id='EMP0001'")
    assert row == 99000, "变更应生效"

    spec = tool_registry_v2.get("payroll_set_salary_structure")
    await spec.compensation(ctx, dict(args), result.data)
    row = await _scalar_one(
        drill_env, "SELECT base_salary FROM salary_structures WHERE employee_id='EMP0001'")
    assert row == 25000, f"补偿应恢复旧值 25000，实际 {row}"


async def test_rollback_drill_hr_update_employee(drill_env):
    """变更类演练：员工档案更新 → 补偿恢复旧档案。"""
    from app.core.agent.orchestrator import orchestrator

    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="drill", thread_id="drill:hrupd")
    await _execute_approved(drill_env, "hr_add_employee",
                            {"name": "演练更新员工", "department": "技术部",
                             "position": "初级"}, ctx)
    hr_agent = orchestrator.get_agent_by_name("人力资源Agent")
    emp_id = next(e["employee_id"] for e in hr_agent._employees.values()
                  if e["name"] == "演练更新员工")

    upd_args = {"employee_id": emp_id, "position": "高级"}
    result = await _execute_approved(drill_env, "hr_update_employee", upd_args, ctx)
    row = await _scalar_one(drill_env,
                            "SELECT position FROM employees WHERE employee_id=:e",
                            {"e": emp_id})
    assert row == "高级"

    spec = tool_registry_v2.get("hr_update_employee")
    await spec.compensation(ctx, dict(upd_args), result.data)
    row = await _scalar_one(drill_env,
                            "SELECT position FROM employees WHERE employee_id=:e",
                            {"e": emp_id})
    assert row == "初级", f"补偿应恢复旧职位，实际 {row}"


async def test_rollback_drill_sales_confirm_order(drill_env):
    """确认订单演练：confirmed → 补偿退回 pending。"""
    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="drill", thread_id="drill:confirm")
    created = await _execute_approved(
        drill_env, "sales_create_order",
        {"customer_id": "CUST-SEED-002", "total_amount": 33000,
         "items": [{"product_id": "P-C", "product_name": "确认演练",
                    "qty": 1, "unit_price": 33000}],
         "delivery_date": ""}, ctx)
    order_id = created.data["order_id"]

    confirm_args = {"order_id": order_id}
    await _execute_approved(drill_env, "sales_confirm_order", confirm_args, ctx)
    row = await _scalar_one(drill_env,
                            "SELECT status FROM sales_orders WHERE order_id=:o",
                            {"o": order_id})
    assert row == "confirmed"

    spec = tool_registry_v2.get("sales_confirm_order")
    await spec.compensation(ctx, dict(confirm_args), {"order_id": order_id})
    row = await _scalar_one(drill_env,
                            "SELECT status FROM sales_orders WHERE order_id=:o",
                            {"o": order_id})
    assert row == "pending", f"补偿应退回 pending，实际 {row}"


async def test_rollback_drill_contract_approve(drill_env):
    """合同审批演练：approved → 补偿退回 draft。"""
    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="drill", thread_id="drill:ctappr")
    created = await _execute_approved(
        drill_env, "contract_create",
        {"title": "审批演练合同", "type": "服务", "counterparty": "演练方",
         "amount": 50000, "department": "技术部",
         "start_date": "2026-11-01", "end_date": "2027-10-31", "terms": ""}, ctx)
    contract_id = created.data["contract_id"]

    approve_args = {"contract_id": contract_id, "comment": "演练批准"}
    await _execute_approved(drill_env, "contract_approve", approve_args, ctx)
    row = await _scalar_one(drill_env,
                            "SELECT status FROM contracts WHERE contract_id=:c",
                            {"c": contract_id})
    assert row == "approved"

    spec = tool_registry_v2.get("contract_approve")
    await spec.compensation(ctx, dict(approve_args), {"contract_id": contract_id})
    row = await _scalar_one(drill_env,
                            "SELECT status FROM contracts WHERE contract_id=:c",
                            {"c": contract_id})
    assert row == "draft", f"补偿应退回 draft，实际 {row}"


async def test_all_write_tools_have_drills(drill_env):
    """红线：每个写类工具必须登记演练（表驱动或专项）。"""
    covered = {d.tool for d in ROLLBACK_DRILLS} | CHANGE_DRILLS
    writes = [s.name for s in tool_registry_v2.list_specs()
              if s.risk_level.is_write]
    missing = [w for w in writes if w not in covered]
    assert not missing, f"以下写工具未登记回滚演练：{missing}"
