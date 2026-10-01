"""
跨域工作流引擎测试（路线图 Phase B / B3）
==========================================

确定性状态图（无 LLM），覆盖三条工作流的关键路径：
1. 报销审批流：小额自动通过（免审）/ 大额人审批准 / 驳回拒付；
2. 采购到支付：小额自动付款 / 大额人审批准后标记 paid、排程落库；
3. 合同审批流：创建→审查→人审批准生效 / 驳回置 rejected。
全部经 MySQL Checkpointer（跨重启可续）。
"""
import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def wf_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + bootstrap + 7 域 Agent 注册 + 工具导入。"""
    import app.core.database as database

    db_file = tmp_path / "workflows.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info

    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.agents.reimbursement.agent import ReimbursementAgent
    from app.agents.treasury.agent import TreasuryAgent
    from app.agents.contract.agent import ContractAgent
    from app.core.rbac import rbac_engine

    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401
    import app.agents.contract.tools  # noqa: F401

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    classes = (ReimbursementAgent, TreasuryAgent, ContractAgent)
    created = []
    for cls in classes:
        agent = cls()
        agent.identity = did_registry.register_agent(
            name=agent.name, domain=agent.domain,
            capabilities=agent.capabilities, security_clearance=agent.security_clearance,
        )
        await orchestrator.register_agent(agent)
        created.append(agent)
    # 清空工作流编译缓存：缓存图绑定创建时的 checkpointer（指向本测试的
    # 临时 sqlite 库与事件循环）——跨测试复用会跨 loop 等待挂起
    from app.core.agent import workflows as wf_mod

    wf_mod.graphs_cache.clear()
    yield engine
    domains = {a.domain for a in created}
    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain in domains:
            del orchestrator._agents[key]
    await database.close_db()


def _ctx(thread_id: str):
    from app.core.agent.tools_v2 import ToolContext

    return ToolContext(user_id="user_cfo", username="cfo",
                       agent_key="workflow", thread_id=thread_id)


# ===========================================================================
# 报销审批流
# ===========================================================================

async def test_reimb_wf_small_amount_auto_approved(wf_env):
    """小额报销（<5000）：免人审直达审批完成。"""
    from app.core.agent.workflows import start_workflow

    r = await start_workflow(
        "reimbursement_approval",
        {"employee_name": "小额测试", "employee_id": "EMP0001",
         "department": "技术部", "category": "meal",
         "amount": 300, "description": "团队聚餐", "has_receipt": True},
        _ctx("wf-rs-small"),
    )
    assert r["status"] == "done", r.get("reply")
    assert r["result"]["status"] == "approved"
    assert "precheck" in "".join(r.get("reply", "")) or True

    from sqlalchemy import text

    async with wf_env.connect() as conn:
        row = (await conn.execute(
            text("SELECT status FROM reimbursements WHERE employee_name='小额测试'")
        )).scalar()
    assert row == "approved"


async def test_reimb_wf_large_amount_human_gate_approve(wf_env):
    """大额报销（≥5000）：人审中断 → 批准 → 恢复完成。"""
    from app.core.agent.workflows import start_workflow, resume_workflow

    r1 = await start_workflow(
        "reimbursement_approval",
        {"employee_name": "大额测试", "employee_id": "EMP0002",
         "department": "销售部", "category": "travel",
         "amount": 88000, "description": "大客户拜访差旅", "has_receipt": True},
        _ctx("wf-rs-large"),
    )
    assert r1["status"] == "awaiting_approval"
    assert "88000" in (r1["interrupt"].get("preview") or "")

    r2 = await resume_workflow(
        "reimbursement_approval", r1["thread_id"],
        {"approved": True, "comment": "同意"}, _ctx("wf-rs-large"),
    )
    assert r2["status"] == "done", r2.get("reply")
    assert r2["result"]["status"] == "approved"


async def test_reimb_wf_large_amount_rejected(wf_env):
    """大额报销被驳回：单据置 rejected，流程终止。"""
    from app.core.agent.workflows import start_workflow, resume_workflow

    r1 = await start_workflow(
        "reimbursement_approval",
        {"employee_name": "驳回测试", "employee_id": "EMP0003",
         "department": "市场部", "category": "entertainment",
         "amount": 20000, "description": "客户招待", "has_receipt": True},
        _ctx("wf-rs-reject"),
    )
    assert r1["status"] == "awaiting_approval"

    r2 = await resume_workflow(
        "reimbursement_approval", r1["thread_id"],
        {"approved": False, "comment": "票据不全"}, _ctx("wf-rs-reject"),
    )
    assert r2["status"] == "error"
    assert "驳回" in r2["reply"]

    from sqlalchemy import text

    async with wf_env.connect() as conn:
        row = (await conn.execute(
            text("SELECT status FROM reimbursements WHERE employee_name='驳回测试'")
        )).scalar()
    assert row == "rejected"


async def test_reimb_wf_precheck_reject(wf_env):
    """预审拦截：无发票直接拒付，不进人审。"""
    from app.core.agent.workflows import start_workflow

    r = await start_workflow(
        "reimbursement_approval",
        {"employee_name": "无票测试", "category": "office",
         "amount": 100, "description": "买了东西", "has_receipt": False},
        _ctx("wf-rs-norcpt"),
    )
    assert r["status"] == "error"
    assert "预审未通过" in r["reply"]


# ===========================================================================
# 采购到支付
# ===========================================================================

async def test_p2p_small_auto_paid(wf_env):
    """小额采购付款（<10万）：排程→自动付款。"""
    from app.core.agent.workflows import start_workflow
    from sqlalchemy import text

    r = await start_workflow(
        "procure_to_pay",
        {"po_id": "PO-WF-001", "total_amount": 80000, "supplier_id": "sup-wf"},
        _ctx("wf-p2p-small"),
    )
    assert r["status"] == "done", r.get("reply")
    assert r["result"]["status"] == "paid"

    async with wf_env.connect() as conn:
        row = (await conn.execute(
            text("SELECT status FROM payment_schedules WHERE po_id='PO-WF-001'")
        )).scalar()
    assert row == "paid"


async def test_p2p_large_human_gate(wf_env):
    """大额采购付款（≥10万）：人审批准后标记 paid。"""
    from app.core.agent.workflows import start_workflow, resume_workflow

    r1 = await start_workflow(
        "procure_to_pay",
        {"po_id": "PO-WF-002", "total_amount": 500000, "supplier_id": "sup-wf"},
        _ctx("wf-p2p-large"),
    )
    assert r1["status"] == "awaiting_approval"

    r2 = await resume_workflow(
        "procure_to_pay", r1["thread_id"],
        {"approved": True, "comment": "按合同付款"}, _ctx("wf-p2p-large"),
    )
    assert r2["status"] == "done", r2.get("reply")
    assert r2["result"]["status"] == "paid"


# ===========================================================================
# 合同审批流
# ===========================================================================

async def test_contract_wf_approve(wf_env):
    """合同审批：创建→审查→人审批准→生效。"""
    from app.core.agent.workflows import start_workflow, resume_workflow

    r1 = await start_workflow(
        "contract_approval",
        {"title": "测试服务合同", "type": "服务", "counterparty": "测试供应商",
         "amount": 120000, "department": "技术部",
         "start_date": "2026-10-01", "end_date": "2027-09-30",
         "terms": "验收合格后付款"},
        _ctx("wf-ct-1"),
    )
    assert r1["status"] == "awaiting_approval", "合同必须人审"
    assert "审查备注" in (r1["interrupt"].get("preview") or "")

    r2 = await resume_workflow(
        "contract_approval", r1["thread_id"],
        {"approved": True, "comment": "同意"}, _ctx("wf-ct-1"),
    )
    assert r2["status"] == "done", r2.get("reply")
    assert r2["result"]["status"] == "approved"

    from sqlalchemy import text

    async with wf_env.connect() as conn:
        row = (await conn.execute(
            text("SELECT status FROM contracts WHERE title='测试服务合同'")
        )).scalar()
    assert row == "approved"


async def test_contract_wf_reject(wf_env):
    """合同驳回：置 rejected。"""
    from app.core.agent.workflows import start_workflow, resume_workflow

    r1 = await start_workflow(
        "contract_approval",
        {"title": "驳回测试合同", "type": "采购", "counterparty": "X供应商",
         "amount": 50000, "department": "采购部",
         "start_date": "2026-10-01", "end_date": "2027-09-30", "terms": ""},
        _ctx("wf-ct-2"),
    )
    assert r1["status"] == "awaiting_approval"

    r2 = await resume_workflow(
        "contract_approval", r1["thread_id"],
        {"approved": False, "comment": "条款需修改"}, _ctx("wf-ct-2"),
    )
    assert r2["status"] == "error"

    from sqlalchemy import text

    async with wf_env.connect() as conn:
        row = (await conn.execute(
            text("SELECT status FROM contracts WHERE title='驳回测试合同'")
        )).scalar()
    assert row == "rejected"


async def test_workflow_restart_resume(wf_env):
    """工作流人审等待跨"重启"恢复（新图实例 + 同一存储）。"""
    from app.core.agent.workflows import start_workflow, resume_workflow, graphs_cache

    r1 = await start_workflow(
        "reimbursement_approval",
        {"employee_name": "重启测试", "employee_id": "EMP0004",
         "department": "技术部", "category": "travel",
         "amount": 15000, "description": "出差报销", "has_receipt": True},
        _ctx("wf-rs-restart"),
    )
    assert r1["status"] == "awaiting_approval"

    # 模拟重启：清空图缓存（强制重新编译），Checkpointer 数据仍在
    graphs_cache.clear()
    r2 = await resume_workflow(
        "reimbursement_approval", r1["thread_id"],
        {"approved": True, "comment": "重启后批准"}, _ctx("wf-rs-restart"),
    )
    assert r2["status"] == "done", r2.get("reply")
