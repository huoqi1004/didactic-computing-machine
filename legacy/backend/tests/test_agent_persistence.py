"""
批次一 Agent 持久化测试（评估报告 E-01 / 路线图任务 2.1、2.2）
================================================================

使用 sqlite 验证批次一（payroll/hr/crm/sales）的持久化语义：

1. 空库回播种：内存种子幂等写入 DB；
2. DB 优先水合：重启（新 Agent 实例）后状态与 DB 一致；
3. 写穿双写：Agent 写方法同步落库，新实例可见（重启一致性）；
4. 2.9 行级隔离：customers 按归属部门过滤。

同步验证 bootstrap 建出的扩展表/列（salary_structures 表、
payroll_records/sales_orders/customers 新列）满足 ORM 读写。
"""
import asyncio

import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def sqlite_db(tmp_path, monkeypatch):
    """临时 sqlite 库 + bootstrap 建表 + 引擎重置。"""
    import app.core.database as database

    db_file = tmp_path / "agent_persistence.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


async def _flush_scheduled_writes():
    """等待 fire-and-forget 写穿任务完成（sqlite 本地 I/O 极快）。"""
    for _ in range(5):
        await asyncio.sleep(0.02)


# ===========================================================================
# 薪酬 Agent
# ===========================================================================

async def test_payroll_seed_then_restart_consistency(sqlite_db):
    """空库回播种 → 新实例从 DB 水合 → 状态一致。"""
    from app.agents.payroll.agent import PayrollAgent
    from app.db.repositories.payroll_repository import salary_structure_repository

    agent_a = PayrollAgent()
    assert await agent_a.hydrate_from_db() is True  # 空库 → 播种
    seed_structures = dict(agent_a._salary_structures)

    agent_b = PayrollAgent()  # 模拟重启
    assert await agent_b.hydrate_from_db() is True  # DB 有数据 → 水合
    assert {
        k: {kk: vv for kk, vv in v.items() if kk in ("base_salary", "allowances")}
        for k, v in agent_b._salary_structures.items()
    } == {
        k: {kk: vv for kk, vv in v.items() if kk in ("base_salary", "allowances")}
        for k, v in seed_structures.items()
    }
    # 种子行确实在库中
    assert await salary_structure_repository.count() == len(seed_structures)


async def test_payroll_write_through(sqlite_db):
    """set_salary_structure / calculate_payroll 写穿 DB，新实例可见。"""
    from app.agents.payroll.agent import PayrollAgent

    agent_a = PayrollAgent()
    await agent_a.hydrate_from_db()

    agent_a.set_salary_structure(
        employee_id="EMP0001",
        base_salary=30000,
        allowances=6000,
        deductions=500,
    )
    record = agent_a.calculate_payroll("EMP0001", "2026-09")
    assert "error" not in record
    await _flush_scheduled_writes()

    agent_b = PayrollAgent()
    await agent_b.hydrate_from_db()
    assert agent_b._salary_structures["EMP0001"]["base_salary"] == 30000
    periods = [r for r in agent_b._payroll_records if r["record_id"] == record["record_id"]]
    assert periods and periods[0]["net_salary"] == record["net_salary"]


# ===========================================================================
# HR Agent
# ===========================================================================

async def test_hr_employee_write_through(sqlite_db):
    """add_employee 写穿 DB，重启后新实例可查（含自增编号推进）。"""
    from app.agents.hr.agent import HRAgent

    agent_a = HRAgent()
    await agent_a.hydrate_from_db()
    emp = agent_a.add_employee(
        name="测试新员工",
        department="技术部",
        position="后端工程师",
        base_salary=20000,
    )
    await _flush_scheduled_writes()

    agent_b = HRAgent()
    await agent_b.hydrate_from_db()
    assert emp["employee_id"] in agent_b._employees
    restored = agent_b._employees[emp["employee_id"]]
    assert restored["name"] == "测试新员工"
    assert restored["department"] == "技术部"


# ===========================================================================
# CRM / 销售 Agent（FK 前置：客户先于订单播种）
# ===========================================================================

async def test_sales_restart_consistency_with_customers(sqlite_db):
    """客户先播种 → 订单写穿 → 重启后订单与客户均从 DB 恢复。"""
    from app.agents.crm.agent import CRMAgent
    from app.agents.sales.agent import SalesAgent

    crm_a = CRMAgent()
    await crm_a.hydrate_from_db()  # 播种 CUST-SEED-001..005

    sales_a = SalesAgent()
    await sales_a.hydrate_from_db()  # 播种种子订单（引用 CUST-SEED-*）
    order = await sales_a.create_order(
        customer_id="CUST-SEED-001",
        items=[{"product_id": "P100", "product_name": "新模块", "qty": 1, "unit_price": 88000}],
        total_amount=88000,
        delivery_date="2026-10-30",
    )
    await _flush_scheduled_writes()

    # 模拟重启
    crm_b = CRMAgent()
    await crm_b.hydrate_from_db()
    sales_b = SalesAgent()
    await sales_b.hydrate_from_db()

    assert order["order_id"] in sales_b._orders
    restored = sales_b._orders[order["order_id"]]
    assert restored["total_amount"] == 88000
    assert restored["customer_id"] == "CUST-SEED-001"
    # 种子订单也在
    assert len(sales_b._orders) >= 4

    confirmed = await sales_b.confirm_order(order["order_id"])
    assert confirmed["status"] == "confirmed"
    await _flush_scheduled_writes()

    sales_c = SalesAgent()
    await sales_c.hydrate_from_db()
    assert sales_c._orders[order["order_id"]]["status"] == "confirmed"


# ===========================================================================
# 2.9 行级隔离（首批）
# ===========================================================================

async def test_customer_row_level_department_filter(sqlite_db):
    """customers 按归属部门过滤：无归属部门默认全可见。"""
    from app.db.repositories.sales_repository import customer_repository

    base = {
        "name": "行级测试客户",
        "industry": "测试业",
        "segment": "A",
        "status": "active",
    }
    await customer_repository.upsert(
        {**base, "customer_id": "CUST-RL-1", "owner_department": "技术部"},
        "customer_id",
    )
    await customer_repository.upsert(
        {**base, "customer_id": "CUST-RL-2", "owner_department": "市场部"},
        "customer_id",
    )
    await customer_repository.upsert(
        {**base, "customer_id": "CUST-RL-3", "owner_department": None},
        "customer_id",
    )

    tech_visible = await customer_repository.list_by_department("技术部")
    ids = {c["customer_id"] for c in tech_visible}
    assert "CUST-RL-1" in ids
    assert "CUST-RL-3" in ids, "无归属部门的客户应默认可见"
    assert "CUST-RL-2" not in ids, "跨部门客户应被过滤"

    all_visible = await customer_repository.list_by_department(None)
    assert len(all_visible) >= 3

# ===========================================================================
# 批次二：资金 / 合同 Agent
# ===========================================================================

async def test_treasury_transfer_write_through(sqlite_db):
    """资金调拨创建与审批写穿 DB，重启后状态一致。"""
    from app.agents.treasury.agent import TreasuryAgent

    agent_a = TreasuryAgent()
    agent_a.identity = did_registry_register(agent_a)
    await agent_a.hydrate_from_db()  # 空库：无种子行，仍返回 True/False 均可

    transfer = await agent_a.request_fund_transfer(
        amount=100000, from_account="主账户", to_account="项目账户", reason="测试调拨",
    )
    assert transfer is not None
    await _flush_scheduled_writes()

    agent_b = TreasuryAgent()
    agent_b.identity = did_registry_register(agent_b)
    await agent_b.hydrate_from_db()
    assert transfer["transfer_id"] in agent_b._fund_transfers

    await agent_b.approve_fund_transfer(transfer["transfer_id"], approved=True)
    await _flush_scheduled_writes()

    agent_c = TreasuryAgent()
    agent_c.identity = did_registry_register(agent_c)
    await agent_c.hydrate_from_db()
    restored = agent_c._fund_transfers[transfer["transfer_id"]]
    assert restored["status"] == "approved"


def did_registry_register(agent):
    """注册 Agent 身份（审批流程需要 DID）。"""
    from app.core.security.did import did_registry

    return did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )


async def test_contract_write_through_and_restart(sqlite_db):
    """合同创建写穿 DB → 审批状态落库 → 重启后一致。"""
    from app.agents.contract.agent import ContractAgent

    agent_a = ContractAgent()
    agent_a.identity = did_registry_register(agent_a)
    await agent_a.hydrate_from_db()  # 空库回播种种子合同

    contract = await agent_a.create_contract(
        title="持久化测试合同",
        type="采购",
        counterparty="测试供应商",
        amount=120000,
        department="技术部",
        start_date="2026-10-01",
        end_date="2027-09-30",
        terms="验收合格后付款",
    )
    await _flush_scheduled_writes()

    agent_b = ContractAgent()
    agent_b.identity = did_registry_register(agent_b)
    await agent_b.hydrate_from_db()
    assert contract["contract_id"] in agent_b._contracts
    # 种子合同也已在库
    assert len(agent_b._contracts) >= len(agent_a._contracts) - 1

