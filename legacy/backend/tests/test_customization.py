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


async def test_relax_pending_does_not_evict_active_override(cust_env, monkeypatch):
    """红线 2 回归：待批的放宽变更不得顶掉当前生效的收紧（进程重启/缓存重载后仍成立）。"""
    from sqlalchemy import select

    from app.db.models import agent_customizations, customization_versions
    from app.db.repositories.base import session_scope

    svc = _svc()

    async def _versions(status: str) -> int:
        async with session_scope() as session:
            rows = (await session.execute(
                select(customization_versions).where(
                    customization_versions.c.type == "tool",
                    customization_versions.c.target == "reimbursement_submit",
                    customization_versions.c.status == status)
            )).all()
        return len(rows)

    await svc.apply_change("tool", "reimbursement_submit", {"reason": "临时下线"},
                           enabled=False, changed_by="user_admin")
    assert svc.is_tool_enabled("reimbursement_submit") is False

    r = await svc.apply_change("tool", "reimbursement_submit", {},
                               enabled=True, changed_by="user_admin")
    assert r["status"] == "pending_approval"
    assert await _versions("pending_approval") == 1, "提案须落版本流（管理端待审批计数）"

    # 缓存重载（版本信号漂移或新进程首次加载都会走到）：只看 active 行
    await svc._reload()
    assert svc.is_tool_enabled("reimbursement_submit") is False, (
        "待批变更把 active 行改成了 pending_approval，重载后停用被抹掉=放宽未审批即生效"
    )

    # 批准后才是启用
    done = await svc.apply_pending("tool", "reimbursement_submit", approved=True,
                                   decided_by="user_chairman")
    assert done["status"] == "executed"
    assert svc.is_tool_enabled("reimbursement_submit") is True
    assert await _versions("pending_approval") == 0, "决定后不留待批行（同一次变更只留终态）"
    assert await _versions("approved_executed") == 1
    await svc._reload()
    assert svc.is_tool_enabled("reimbursement_submit") is True

    # 驳回：先重新收紧（停用），再提启用=放宽 → 驳回后收紧仍在
    await svc.apply_change("tool", "reimbursement_submit", {"reason": "再次下线"},
                           enabled=False, changed_by="user_admin")
    assert svc.is_tool_enabled("reimbursement_submit") is False
    r2 = await svc.apply_change("tool", "reimbursement_submit", {},
                                enabled=True, changed_by="user_admin")
    assert r2["status"] == "pending_approval"
    rejected = await svc.apply_pending("tool", "reimbursement_submit", approved=False,
                                       decided_by="user_chairman")
    assert rejected["status"] == "rejected"
    assert svc.is_tool_enabled("reimbursement_submit") is False, "驳回不得改动生效配置"
    assert await _versions("rejected") == 1
    await svc._reload()
    assert svc.is_tool_enabled("reimbursement_submit") is False

    # 待批提案必须有一条可批的人审任务（此前 sqlite 上 MySQL upsert 失败 → 无行可批）
    from app.db.models import agent_approval_requests

    async with session_scope() as session:
        approvals = (await session.execute(
            select(agent_approval_requests).where(
                agent_approval_requests.c.domain == "customization",
                agent_approval_requests.c.tool == "tool:reimbursement_submit")
        )).all()
    assert len(approvals) == 1, "放宽类变更须落一条 domain=customization 的审批行"
    # 卡片跟着最后一次决定走终态（此前 apply_pending 不回写卡片，批完仍停在
    # pending，同一张卡可以被反复批准）
    assert approvals[0].status == "rejected"

    # 待批不落 active 行：agent_customizations 只应有一条 active 记录
    async with session_scope() as session:
        rows = (await session.execute(
            select(agent_customizations).where(
                agent_customizations.c.type == "tool",
                agent_customizations.c.target == "reimbursement_submit")
        )).all()
    assert [r.status for r in rows] == ["active"], "待批变更必须走版本流，不能改 active 行"


async def test_decide_route_records_decider_not_comment(cust_env, monkeypatch):
    """审批链路回归：从 decide 入口批准时，approved_by 必须是审批人而非意见文本。

    orchestration_service 曾按位置传 apply_pending(ctype, target, approved, comment, decided_by)，
    与签名 (…, decided_by, comment) 顺序相反 → approved_by/审计 actor 写入的是意见。
    """
    from sqlalchemy import select

    from app.core.agent import orchestration_service as orch
    from app.db.models import customization_versions
    from app.db.repositories.base import session_scope

    svc = _svc()
    # 先收紧（停用）→ 再提启用，确保这次提案被判定为放宽（与缓存历史状态无关）
    await svc.apply_change("tool", "reimbursement_submit", {"reason": "临时下线"},
                           enabled=False, changed_by="user_admin")
    r = await svc.apply_change("tool", "reimbursement_submit", {},
                               enabled=True, changed_by="user_admin",
                               comment="恢复上线申请")
    assert r["status"] == "pending_approval"

    out = await orch.resume_after_decision(r["thread_id"], approved=True,
                                           comment="同意，注意复核额度",
                                           decided_by="user_chairman")
    assert out is not None and out["status"] == "executed"
    assert svc.is_tool_enabled("reimbursement_submit") is True

    async with session_scope() as session:
        row = (await session.execute(
            select(customization_versions).where(
                customization_versions.c.type == "tool",
                customization_versions.c.target == "reimbursement_submit",
                customization_versions.c.status == "approved_executed")
        )).first()
    assert row is not None
    assert row.approved_by == "user_chairman", (
        f"approved_by 被写成了审批意见：{row.approved_by!r}"
    )
    assert row.comment == "同意，注意复核额度"


async def test_gate_threshold_override_affects_workflow_gate(cust_env, monkeypatch):
    """阈值覆盖：报销免审线 5000→6000 是**上调=放宽**，批准后工作流才按新线走（红线 2）。"""
    from app.core.agent.customization import customization_service as svc_obj

    svc = _svc()
    r = await svc.apply_change("gate", "reimbursement_auto_approve_limit",
                              {"value": 6000}, enabled=True, changed_by="user_admin")
    assert r["status"] == "pending_approval", r
    assert svc.get_threshold("reimbursement_auto_approve_limit") is None, (
        "阈值上调在批准前不得生效"
    )

    from app.core.agent.workflows import _threshold

    assert _threshold("reimbursement_auto_approve_limit", 5000) == 5000.0

    done = await svc.apply_pending("gate", "reimbursement_auto_approve_limit",
                                   approved=True, decided_by="user_chairman")
    assert done["status"] == "executed"
    assert svc.get_threshold("reimbursement_auto_approve_limit") == 6000.0
    assert _threshold("reimbursement_auto_approve_limit", 5000) == 6000.0, (
        "管理端调高免审线后，工作流阈值读取器必须跟上"
    )


async def test_gate_target_whitelist_rejects_dead_keys(cust_env):
    """gate 白名单（红线：防"保存成功但运行期无人读"的死键）+ 阈值数值校验。"""
    from app.core.agent.customization import (
        CustomizationError, GATE_TARGETS, THRESHOLD_DEFAULTS,
    )

    svc = _svc()

    # 白名单外的 gate target 一律拒绝：写进去没人读，管理端却会显示"已覆盖"
    with pytest.raises(CustomizationError):
        await svc.apply_change("gate", "payment_human_gate_limit_x", {"value": 1},
                               changed_by="user_admin")
    # 矩阵与阈值同挂 gate 类型，白名单漏掉矩阵会打断 F2.5 的审批路由保存
    assert "approval_route_matrix" in GATE_TARGETS
    assert set(GATE_TARGETS) == set(THRESHOLD_DEFAULTS) | {"approval_route_matrix"}

    # value 必须是非负数字：get_threshold 读不懂会静默回退内置默认（假成功）
    for bad in ("5000", None, True, -1):
        with pytest.raises(CustomizationError):
            await svc.apply_change("gate", "payment_human_gate_limit", {"value": bad},
                                   changed_by="user_admin")
    assert svc.get_threshold("payment_human_gate_limit") is None

    # 收紧方向（下调）仍即时生效——白名单不能把正常运营动作也堵住
    r = await svc.apply_change("gate", "payment_human_gate_limit", {"value": 50000},
                               changed_by="user_admin", comment="UAT 阈值入口")
    assert r["status"] == "applied"
    assert svc.get_threshold("payment_human_gate_limit") == 50000.0


async def test_gate_threshold_admin_flow_is_audited(cust_env):
    """管理端阈值动线：元数据下发阈值清单（前端不硬编码键名）+ 变更进版本流。"""
    from sqlalchemy import select

    from app.core.agent.customization import THRESHOLD_DEFAULTS, THRESHOLD_LABELS
    from app.db.models import customization_versions
    from app.db.repositories.base import session_scope

    svc = _svc()
    assert (await svc.apply_change("gate", "agent_daily_token_budget", {"value": 1000000},
                                   changed_by="user_admin", comment="预算收紧"))["status"] == "applied"

    async with session_scope() as session:
        rows = (await session.execute(
            select(customization_versions).where(
                customization_versions.c.target == "agent_daily_token_budget")
        )).all()
    assert len(rows) >= 1, "阈值变更必须留痕（红线 3）"
    assert rows[0].changed_by == "user_admin"

    # /meta 的阈值清单与后端白名单同源（两处口径必须一致，否则界面能改后端拒）
    assert set(THRESHOLD_DEFAULTS) == set(THRESHOLD_LABELS)


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


async def test_stacked_relax_supersedes_stale_proposal_and_card_decision(cust_env):
    """真机验证暴露（红线 2/3）：同一 target 连续两次放宽时——

    1. 旧提案必须让位：审批卡按 thread_id（每 type+target+发起人只有一格）定位，
       而 apply_pending 取的是待批提案，两边都要锁定"最新那一条"，否则审批人看到的
       内容与系统执行的内容可以是两件事；
    2. 卡片复用时不得残留上一轮决定（decided_by/decided_at/result_reply）；
    3. 决定后卡片必须落终态：/customizations/{ctype}/{target}/decide 只调
       apply_pending，若它不回写卡片，卡片会永远停在 pending 并可被反复批准。
    """
    from sqlalchemy import select

    from app.db.models import agent_approval_requests, customization_versions
    from app.db.repositories.base import session_scope

    svc = _svc()
    target = "reimbursement_submit"

    async def _count(status: str) -> int:
        async with session_scope() as session:
            return len((await session.execute(
                select(customization_versions).where(
                    customization_versions.c.type == "tool",
                    customization_versions.c.target == target,
                    customization_versions.c.status == status)
            )).all())

    async def _card():
        async with session_scope() as session:
            return (await session.execute(
                select(agent_approval_requests).where(
                    agent_approval_requests.c.thread_id ==
                    f"cust:tool:{target}:user_admin")
            )).first()

    # 收紧一次，才有"启用=放宽"可提
    await svc.apply_change("tool", target, {"reason": "临时下线"},
                           enabled=False, changed_by="user_admin")

    assert (await svc.apply_change("tool", target, {"reason": "提案A"},
                                   enabled=True, changed_by="user_admin")
            )["status"] == "pending_approval"
    await svc.apply_change("tool", target, {"reason": "提案B"},
                           enabled=True, changed_by="user_admin")

    assert await _count("pending_approval") == 1, "同目标只允许一条待批提案"
    assert await _count("superseded") == 1, "被顶替的旧提案要留痕，不能悄悄消失"

    card = await _card()
    assert card and "提案B" in (card.preview or ""), "卡片内容必须是被执行的那一条"
    assert card.status == "pending"

    decided = await svc.apply_pending("tool", target, approved=True,
                                      decided_by="user_chairman")
    assert decided["status"] == "executed"
    card = await _card()
    assert card.status == "executed", "决定后卡片不得留在 pending（可反复批准）"
    assert card.decided_by == "user_chairman"

    # 再提一次放宽：复用的卡片必须清掉上一轮审批人/时间/结果
    await svc.apply_change("tool", target, {"reason": "再次下线"},
                           enabled=False, changed_by="user_admin")
    await svc.apply_change("tool", target, {"reason": "提案C"},
                           enabled=True, changed_by="user_admin")
    card = await _card()
    assert card.status == "pending"
    assert card.decided_by is None and card.decided_at is None, "审批人字段串号到下一轮"
    assert "提案C" in (card.preview or "")
