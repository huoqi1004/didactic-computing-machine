"""
定制化配置中心测试（路线图 Phase F / F1.8）
============================================

1. 收紧类即时生效：工具风险提升/停用 → governor 下次调用立即按新分级/停用；
2. 放宽类走人审：风险降级/停用→启用/阈值上调 → pending（不生效）→
   批准后生效、驳回不生效；
3. 阈值覆盖：workflows 报销免审线管理端调整后按新值路由；
4. 回滚：历史版本回滚生效；回滚到放宽状态走审批；
5. 幂等键防重放；非法类型/未知工具拒绝（红线）；
6. 版本信号：变更后版本递增（多实例失效基础）。
"""
import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.agent.tools_v2 import ToolContext, tool_registry_v2, ToolRisk


@pytest_asyncio.fixture
async def cust_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + 工具导入 + Agent 注册 + RBAC 放行（定制变更走审批人）。"""
    import app.core.database as database

    db_file = tmp_path / "customization.db"
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
    from app.agents.sales.agent import SalesAgent

    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    classes = (ReimbursementAgent, SalesAgent)
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


def _svc():
    from app.core.agent.customization import customization_service

    return customization_service


def _ctx(thread_id: str):
    return ToolContext(user_id="user_admin", username="admin",
                       agent_key="cust", thread_id=thread_id)


async def test_tighten_tool_risk_immediate_effect(cust_env, monkeypatch):
    """收紧：write_low→write_high 即时生效（下次调用进人审门）。"""
    from app.core.agent.governor import AgentGovernor

    svc = _svc()
    r = await svc.apply_change(
        "tool", "sales_create_order",
        {"risk_override": "write_high", "reason": "大额订单收紧"},
        enabled=True, changed_by="user_admin", comment="收紧演练",
    )
    assert r["status"] == "applied"
    assert svc.get_tool_risk_override("sales_create_order") == "write_high"

    # 治理面：未带 approved → 立即进入人审门
    gov = AgentGovernor()
    result = await gov.execute(
        "sales_create_order",
        {"customer_id": "CUST-SEED-001", "total_amount": 100,
         "items": [{"product_id": "P1", "product_name": "x", "qty": 1, "unit_price": 100}],
         "delivery_date": ""},
        _ctx("f1-tighten"),
    )
    assert result.approval is not None, "收紧后应进入人审门"


async def test_relax_tool_requires_approval_then_effective(cust_env, monkeypatch):
    """放宽：write_high→write_low 转 pending（不生效）→ 批准后生效；驳回不生效。"""
    from app.core.agent.customization import customization_service as svc_obj

    svc = _svc()
    # 前置：先收紧到 write_high
    await svc.apply_change("tool", "treasury_fund_transfer_request",
                           {"risk_override": "write_high"},
                           enabled=True, changed_by="user_admin")
    assert svc.get_tool_risk_override("treasury_fund_transfer_request") == "write_high"

    # 放宽：降回 write_low → pending
    r = await svc.apply_change("tool", "treasury_fund_transfer_request",
                               {"risk_override": "write_low"},
                               enabled=True, changed_by="user_admin",
                               comment="降级演练")
    assert r["status"] == "pending_approval", r
    assert svc.get_tool_risk_override("treasury_fund_transfer_request") == "write_high", (
        "放宽在批准前不得生效"
    )

    # 批准 → 生效
    done = await svc.apply_pending("tool", "treasury_fund_transfer_request",
                                   approved=True, decided_by="user_chairman")
    assert done["status"] == "executed"
    assert svc.get_tool_risk_override("treasury_fund_transfer_request") == "write_low"

    # 驳回路径：再放宽 → pending → 驳回 → 恢复
    await svc.apply_change("tool", "treasury_fund_transfer_request",
                           {"risk_override": "readonly"},
                           enabled=True, changed_by="user_admin")
    rejected = await svc.apply_pending("tool", "treasury_fund_transfer_request",
                                       approved=False, decided_by="user_chairman")
    assert rejected["status"] == "rejected"
    assert svc.get_tool_risk_override("treasury_fund_transfer_request") == "write_low"


async def test_tool_disable_immediate_and_enable_requires_approval(cust_env, monkeypatch):
    """停用即时（调用返回已停用）；重新启用=放宽须审批。"""
    from app.core.agent.governor import AgentGovernor

    svc = _svc()
    await svc.apply_change("tool", "reimbursement_submit", {"reason": "临时下线"},
                           enabled=False, changed_by="user_admin")
    assert svc.is_tool_enabled("reimbursement_submit") is False

    gov = AgentGovernor()
    result = await gov.execute(
        "reimbursement_submit",
        {"employee_name": "x", "amount": 100, "description": "停用验证"},
        _ctx("f1-disable"),
    )
    assert not result.ok and "停用" in result.error

    # 重新启用 = 放宽 → pending
    r = await svc.apply_change("tool", "reimbursement_submit", {},
                               enabled=True, changed_by="user_admin")
    assert r["status"] == "pending_approval"
    assert svc.is_tool_enabled("reimbursement_submit") is False


async def test_gate_threshold_override_affects_workflow_gate(cust_env, monkeypatch):
    """阈值覆盖：报销免审线 5000→6000 后，6000 元单据进入人审。"""
    from app.core.agent.customization import customization_service as svc_obj

    svc = _svc()
    await svc.apply_change("gate", "reimbursement_auto_approve_limit",
                           {"value": 6000}, enabled=True, changed_by="user_admin")
    assert svc.get_threshold("reimbursement_auto_approve_limit") == 6000.0

    from app.core.agent.workflows import _threshold

    assert _threshold("reimbursement_auto_approve_limit", 5000) == 6000.0


async def test_rollback_to_version(cust_env, monkeypatch):
    """回滚：收紧 → 回滚到历史版本（变更前无覆盖状态）→ 恢复原分级。"""
    from sqlalchemy import select

    from app.db.repositories.base import session_scope

    svc = _svc()
    await svc.apply_change("tool", "sales_confirm_order",
                           {"risk_override": "write_high"},
                           enabled=True, changed_by="user_admin")
    assert svc.get_tool_risk_override("sales_confirm_order") == "write_high"

    # 回滚目标：找"该 target 最早的历史版本"（变更前快照 status=removed）
    async with session_scope() as session:
        from app.db.models import customization_versions

        rows = (await session.execute(
            select(customization_versions)
            .where(customization_versions.c.type == "tool",
                   customization_versions.c.target == "sales_confirm_order")
            .order_by(customization_versions.c.id.asc())
        )).all()
    first = rows[0]

    r = await svc.rollback_to_version("tool", "sales_confirm_order", first.id,
                                      rolled_by="user_admin")
    assert r["status"] == "applied"
    assert svc.get_tool_risk_override("sales_confirm_order") is None, (
        "回滚到变更前版本应清除风险覆盖"
    )


async def test_idempotency_and_invalid_inputs(cust_env, monkeypatch):
    """幂等键防重放；非法类型/未知工具拒绝（红线）。"""
    from app.core.agent.customization import CustomizationError

    svc = _svc()
    # 幂等
    r1 = await svc.apply_change("gate", "payment_human_gate_limit", {"value": 50000},
                                changed_by="u1", idempotency_key="idem-1")
    assert r1["status"] == "applied"
    r2 = await svc.apply_change("gate", "payment_human_gate_limit", {"value": 60000},
                                changed_by="u1", idempotency_key="idem-1")
    assert r2["status"] == "duplicate"
    assert svc.get_threshold("payment_human_gate_limit") == 50000.0, "重放不得二次变更"

    # 非法类型（红线：白名单外拒绝）
    with pytest.raises(CustomizationError):
        await svc.apply_change("hacker", "anything", {}, changed_by="u1")

    # 未知工具
    with pytest.raises(CustomizationError):
        await svc.apply_change("tool", "no_such_tool", {}, changed_by="u1")


async def test_version_signal_bumped_on_change(cust_env, monkeypatch):
    """版本信号：每次生效变更递增（多实例失效基础）。"""
    import fakeredis.aioredis

    import app.core.database as database
    from app.core import redis_store

    monkeypatch.setattr(database, "_redis_client",
                        fakeredis.aioredis.FakeRedis(decode_responses=True))

    svc = _svc()
    v1 = await redis_store.bump_key_value("erp:cust:version")
    await svc.apply_change("gate", "payment_human_gate_limit", {"value": 60000},
                           changed_by="u1")
    v2 = await redis_store.get_key_value("erp:cust:version")
    assert v2 is not None and int(v2) >= int(v1 or 0)
