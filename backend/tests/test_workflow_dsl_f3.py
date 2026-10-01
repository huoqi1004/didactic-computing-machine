"""
工作流 DSL 化与人在环配置测试（路线图 Phase F / F3.7）
========================================================

覆盖 F3 出口准则中的可测项：

1. **DSL 校验拒绝矩阵**（F3.1）：未注册 handler / 非法节点类型 / 环 /
   孤立节点 / 路由到未声明目标 / condition 缺 default / gate 缺 rejected
   分支 / 阈值键越界 / 保留节点 id / 缺 gate 节点（红线 1）；
2. **condition 多路路由**（D-2 + 附录A UAT-F3-2）：
   ``category=entertainment`` → 额外合规审批门，其余走 default 门；
3. **人在环 per-node 配置**（F3.4）：gate 的 approve_roles / document_type /
   timeout_hours 落到审批行，且 per-node 角色优先于路由矩阵（F2.5）；
4. **版本管理与粘性灰度**（F3.5）：草稿→申请发布转人审→批准才生效、
   已发布版本不可变、回滚即时、灰度桶按单据恒定命中、
   线程钉住版本跨后续发布不变（S1）；
5. **发布评测门禁**（D-4 修订）：资金/薪酬/合同域评测 FAIL → 自动回滚。

与存量 ``test_workflows.py``（9 条）合并构成"DSL 版与硬编码版行为等价"的
冻结规格——硬编码 builder 已删除，故等价性由该 9 条测试守着。
"""
import asyncio
import copy

import pytest
import pytest_asyncio

from app.core.config import settings

_TH_KEYS = {"reimbursement_auto_approve_limit", "payment_human_gate_limit"}
_HANDLERS = {"a.do"}


async def _instant(info: dict) -> dict:
    """桩：评测立即结束并给出 summary。"""
    return info


# fixture 默认静音发布门禁；门禁专项测试用原函数还原
from app.core.agent.workflow_versions import _start_publish_eval as _REAL_START_PUBLISH_EVAL  # noqa: E402


def _good_defn() -> dict:
    """最小合法 DSL（1 task + 1 gate + 双分支终止）。"""
    return {
        "name": "demo_flow",
        "entry": "a",
        "nodes": [
            {"id": "a", "type": "task", "handler": "a.do", "next": "g"},
            {"id": "g", "type": "gate",
             "threshold": {"key": "reimbursement_auto_approve_limit", "default": 100},
             "next_approved": "b", "next_rejected": "c"},
            {"id": "b", "type": "task", "handler": "a.do", "next": "END"},
            {"id": "c", "type": "task", "handler": "a.do", "next": "END"},
        ],
    }


def _v(defn: dict) -> list[str]:
    from app.core.agent.workflow_dsl import validate_dsl

    return validate_dsl(defn, _TH_KEYS, _HANDLERS)


@pytest_asyncio.fixture
async def f3_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + bootstrap + 3 域 Agent + RBAC 放行（发布门禁默认静音）。"""
    import app.core.database as database

    db_file = tmp_path / "f3_dsl.db"
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
    from app.agents.contract.agent import ContractAgent

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

    # 编译图/定制单例由 conftest 的 autouse 复位统一清理
    from app.core.agent import workflow_versions as wfv

    monkeypatch.setattr(wfv, "_start_publish_eval", lambda *a, **k: None)

    yield engine

    domains = {a.domain for a in created}
    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain in domains:
            del orchestrator._agents[key]
    await database.close_db()


def _ctx(thread_id: str, agent_key: str = "workflow"):
    from app.core.agent.tools_v2 import ToolContext

    return ToolContext(user_id="user_admin", username="admin",
                       agent_key=agent_key, thread_id=thread_id)


# ===========================================================================
# F3.1 DSL 校验拒绝矩阵
# ===========================================================================

def test_dsl_validate_rejects_illegal_definitions():
    """非法 DSL 逐条被拒（治理红线 + 失败安全 + 引用完整性）。"""
    def _mutate(fn):
        d = _good_defn()
        fn(d)
        return d

    cases: list[tuple[str, dict]] = [
        ("未注册 handler", _mutate(
            lambda d: d["nodes"][0].__setitem__("handler", "evil.drop_table"))),
        ("类型非法", _mutate(lambda d: d["nodes"][0].__setitem__("type", "llm_free"))),
        ("缺少 gate 节点", {
            "name": "no_gate", "entry": "a",
            "nodes": [{"id": "a", "type": "task", "handler": "a.do", "next": "END"}],
        }),
        ("缺少 next_rejected", _mutate(
            lambda d: d["nodes"][1].pop("next_rejected"))),
        ("阈值键不在白名单", _mutate(
            lambda d: d["nodes"][1]["threshold"].__setitem__("key", "who_knows"))),
        ("路由到未声明目标", _mutate(
            lambda d: d["nodes"][0].__setitem__("next", "ghost"))),
        ("为保留字", _mutate(lambda d: d["nodes"][0].__setitem__("id", "END"))),
        ("检测到环", _mutate(lambda d: d["nodes"][2].__setitem__("next", "a"))),
        ("孤立节点", _mutate(
            lambda d: d["nodes"].append(
                {"id": "orphan", "type": "task", "handler": "a.do", "next": "END"}))),
        ("approve_roles 必须为非空字符串数组", _mutate(
            lambda d: d["nodes"][1].__setitem__("approve_roles", "cfo"))),
        ("timeout_hours 必须为正数", _mutate(
            lambda d: d["nodes"][1].__setitem__("timeout_hours", 0))),
    ]

    failures = []
    for expect, defn in cases:
        errors = _v(defn)
        if not any(expect in e for e in errors):
            failures.append(f"{expect}: 未被拒绝，errors={errors}")

    # condition 节点专项（D-2：必须可声明式多路，且失败安全）
    cond_cases = [
        ("缺少 default", {"field": "input.category",
                          "branches": [{"equals": "x", "goto": "b"}]}),
        ("branches 不能为空", {"field": "input.category", "branches": [],
                               "default": "b"}),
        ("缺少条件算子", {"field": "input.category",
                          "branches": [{"goto": "b"}], "default": "c"}),
        ("只能一个条件算子", {"field": "input.category",
                              "branches": [{"equals": "x", "gt": 1, "goto": "b"}],
                              "default": "c"}),
        ("缺少 field", {"branches": [{"equals": "x", "goto": "b"}], "default": "c"}),
    ]
    for expect, cond in cond_cases:
        d = _good_defn()
        d["nodes"][0]["next"] = "cond"
        node = {"id": "cond", "type": "condition", **cond}
        node.setdefault("branches", [{"equals": "x", "goto": "b"}])
        d["nodes"].insert(1, node)
        errors = _v(d)
        if not any(expect in e for e in errors):
            failures.append(f"condition {expect}: 未被拒绝，errors={errors}")

    assert not failures, "\n".join(failures)


def test_dsl_validate_accepts_legal_and_rejects_empty():
    """合法 DSL 通过；空/非对象/重复 id 拒绝。"""
    assert _v(_good_defn()) == []
    assert "DSL 必须为对象" in _v(None)
    assert "nodes 不能为空" in _v({"name": "x", "nodes": []})

    dup = _good_defn()
    dup["nodes"][0] = {"id": "g", "type": "task", "handler": "a.do", "next": "b"}
    assert any("节点 id 重复" in e for e in _v(dup)), _v(dup)


def test_builtin_dsl_templates_all_valid():
    """三条出厂 DSL 模板必须通过校验器（管理端默认基线）。"""
    from app.core.agent import workflows as wf_mod
    from app.core.agent.workflow_versions import validate_definition

    for name, defn in wf_mod.BUILTIN_DSL.items():
        assert validate_definition(defn) == [], f"{name} 出厂定义非法"


# ===========================================================================
# F3.1/D-2 condition 多路路由 + F3.4 人在环 per-node 配置（运行时）
# ===========================================================================

_REIMB_WITH_CONDITION = {
    "name": "reimbursement_approval",
    "entry": "submit",
    "nodes": [
        {"id": "submit", "type": "task", "handler": "reimb.submit", "next": "precheck"},
        {"id": "precheck", "type": "task", "handler": "reimb.precheck", "next": "route"},
        {"id": "route", "type": "condition", "field": "input.category",
         "branches": [{"equals": "entertainment", "goto": "compliance_gate"}],
         "default": "gate"},
        {"id": "compliance_gate", "type": "gate",
         "threshold": {"key": "reimbursement_auto_approve_limit", "default": 5000},
         "amount_from": "result.amount",
         "document_type": "entertainment_expense",
         "approve_roles": ["legal_manager", "cfo"], "timeout_hours": 12,
         "preview": "招待类报销需合规审批：{employee_name} ¥{amount}",
         "next_approved": "verify", "next_rejected": "reject"},
        {"id": "gate", "type": "gate",
         "threshold": {"key": "reimbursement_auto_approve_limit", "default": 5000},
         "amount_from": "result.amount", "document_type": "expense",
         "approve_roles": ["fin_manager"],
         "preview": "报销单 {reimbursement_no} 金额 ¥{amount}",
         "next_approved": "verify", "next_rejected": "reject"},
        {"id": "verify", "type": "task", "handler": "reimb.verify_approve", "next": "END"},
        {"id": "reject", "type": "task", "handler": "reimb.reject_mark", "next": "END"},
    ],
}


def _reimb_input(category: str, amount: float, name: str) -> dict:
    return {"employee_name": name, "employee_id": "EMP9001", "department": "市场部",
            "category": category, "amount": amount,
            "description": "客户端差旅" if category != "entertainment" else "客户招待",
            "has_receipt": True}


async def test_condition_routes_entertainment_to_extra_gate(f3_env):
    """UAT-F3-2：category=entertainment 走额外合规门（多一路、多一个审批角色）。"""
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.workflows import start_workflow

    await wfv.save_draft("reimbursement_approval", "v1", copy.deepcopy(_REIMB_WITH_CONDITION),
                         created_by="user_admin", comment="招待分流")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")

    r_ent = await start_workflow("reimbursement_approval",
                                 _reimb_input("entertainment", 20000, "招待分流"),
                                 _ctx("f3-cond-ent"))
    assert r_ent["status"] == "awaiting_approval", r_ent.get("reply")
    assert "合规审批" in r_ent["interrupt"]["preview"]
    assert r_ent["interrupt"]["approve_roles"] == ["legal_manager", "cfo"]
    assert r_ent["interrupt"]["document_type"] == "entertainment_expense"
    assert r_ent["interrupt"]["args"]["timeout_hours"] == 12
    assert r_ent["interrupt"]["args"]["node"] == "compliance_gate"

    r_norm = await start_workflow("reimbursement_approval",
                                  _reimb_input("travel", 20000, "普通差旅"),
                                  _ctx("f3-cond-travel"))
    assert r_norm["status"] == "awaiting_approval", r_norm.get("reply")
    assert r_norm["interrupt"]["approve_roles"] == ["fin_manager"]
    assert "合规审批" not in r_norm["interrupt"]["preview"]


async def test_condition_branch_still_honours_threshold(f3_env):
    """condition 分流后 gate 阈值仍生效：小额自动通过、不进人审。"""
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.workflows import start_workflow

    await wfv.save_draft("reimbursement_approval", "v1", copy.deepcopy(_REIMB_WITH_CONDITION),
                         created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")

    r = await start_workflow("reimbursement_approval",
                             _reimb_input("entertainment", 300, "小额招待"),
                             _ctx("f3-cond-small"))
    assert r["status"] == "done", r.get("reply")
    assert r["result"]["status"] == "approved"


async def test_gate_per_node_roles_win_over_matrix(f3_env):
    """F3.4：gate per-node approve_roles 优先于 F2.5 路由矩阵。"""
    from sqlalchemy import select

    from app.core.agent.customization import customization_service
    from app.core.agent.orchestration_service import _create_approval_record
    from app.db.models import agent_approval_requests
    from app.db.repositories.base import session_scope

    monkey_roles = ["matrix_role"]
    original = customization_service.resolve_approve_roles
    customization_service.resolve_approve_roles = lambda dt, amount=None: list(monkey_roles)
    try:
        # 带 per-node 角色 → 用节点角色
        await _create_approval_record(
            {"tool": "workflow:reimbursement_approval", "risk": "write_high",
             "args": {"node": "compliance_gate", "amount": 20000},
             "preview": "招待类报销需合规审批", "document_type": "entertainment_expense",
             "approve_roles": ["legal_manager", "cfo"]},
            _ctx("f3-roles-node", agent_key="workflow:reimbursement_approval"))
        # 不带 per-node 角色 → 回落矩阵
        await _create_approval_record(
            {"tool": "workflow:reimbursement_approval", "risk": "write_high",
             "args": {"node": "gate", "amount": 20000}, "preview": "普通报销",
             "document_type": "expense"},
            _ctx("f3-roles-matrix", agent_key="workflow:reimbursement_approval"))
    finally:
        customization_service.resolve_approve_roles = original

    async with session_scope() as session:
        rows = (await session.execute(
            select(agent_approval_requests.c.thread_id,
                   agent_approval_requests.c.approve_roles)
            .where(agent_approval_requests.c.thread_id.in_(
                ["f3-roles-node", "f3-roles-matrix"]))
        )).all()
    got = {r.thread_id: r.approve_roles for r in rows}
    assert got["f3-roles-node"] == ["legal_manager", "cfo"]
    assert got["f3-roles-matrix"] == ["matrix_role"]


async def test_workflow_gate_interrupt_lands_approval_row_with_roles(f3_env):
    """工作流人审中断 → 审批行 per-node 角色落库（审批中心路由数据源）。"""
    from sqlalchemy import select

    from app.core.agent import workflow_versions as wfv
    from app.core.agent.orchestration_service import _create_approval_record
    from app.core.agent.workflows import start_workflow
    from app.db.models import agent_approval_requests
    from app.db.repositories.base import session_scope

    await wfv.save_draft("reimbursement_approval", "v1", copy.deepcopy(_REIMB_WITH_CONDITION),
                         created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")

    r = await start_workflow("reimbursement_approval",
                             _reimb_input("entertainment", 20000, "审批行落库"),
                             _ctx("f3-appr-1"))
    ctx = _ctx(r["thread_id"], agent_key="workflow:reimbursement_approval")
    await _create_approval_record(r["interrupt"], ctx)

    async with session_scope() as session:
        row = (await session.execute(
            select(agent_approval_requests).where(
                agent_approval_requests.c.thread_id == r["thread_id"])
        )).first()
    assert row.approve_roles == ["legal_manager", "cfo"]
    assert row.domain == "workflow:reimbursement_approval"
    assert row.status == "pending"


# ===========================================================================
# F3.5 版本生命周期：草稿 → 申请发布（转人审）→ 批准生效
# ===========================================================================

def _reimb_v1(limit: float = 5000) -> dict:
    d = copy.deepcopy(_REIMB_WITH_CONDITION)
    for n in d["nodes"]:
        if n["type"] == "gate":
            n["threshold"]["default"] = limit
    return d


async def test_publish_requires_human_approval_then_active(f3_env):
    """红线 2：DSL 发布影响面大 → 必须人审；批准后才成为 stable。"""
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.customization import customization_service

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(),
                         created_by="user_admin", comment="首版")

    req = await wfv.request_publish("reimbursement_approval", "v1", 0,
                                    "user_admin", "上线招待分流")
    assert req["status"] == "pending_approval", req

    # 未批准：不得成为 stable，运行时仍用出厂模板
    versions = {v["version"]: v["status"] for v in await wfv.list_versions(
        "reimbursement_approval")}
    assert versions["v1"] == "draft"

    done = await customization_service.apply_pending(
        "workflow", "reimbursement_approval", True, "cio", "同意发布")
    assert done["status"] == "executed"
    assert done["publish"]["mode"] == "stable"

    versions = {v["version"]: v["status"] for v in await wfv.list_versions(
        "reimbursement_approval")}
    assert versions["v1"] == "stable"


async def test_publish_rejected_does_not_take_effect(f3_env):
    """驳回：发布不落地，版本保持非 stable。"""
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.customization import customization_service

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(), created_by="user_admin")
    req = await wfv.request_publish("reimbursement_approval", "v1", 0, "user_admin")
    assert req["status"] == "pending_approval"

    rejected = await customization_service.apply_pending(
        "workflow", "reimbursement_approval", False, "cio", "定义有风险")
    assert rejected["status"] == "rejected"

    versions = {v["version"]: v["status"] for v in await wfv.list_versions(
        "reimbursement_approval")}
    assert versions["v1"] != "stable"


async def test_published_version_is_immutable(f3_env):
    """版本不可变：已发布版本不能原地改，只能另存新版本。"""
    from app.core.agent import workflow_versions as wfv

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")

    with pytest.raises(wfv.WorkflowVersionError, match="不可变"):
        await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(1),
                             created_by="user_admin")


async def test_draft_rejects_invalid_dsl(f3_env):
    """草稿入库前校验：非法 DSL 直接拒绝（不让脏定义进发布流程）。"""
    from app.core.agent import workflow_versions as wfv

    bad = _good_defn()
    bad["name"] = "reimbursement_approval"
    bad["nodes"][0]["handler"] = "not.registered"
    with pytest.raises(wfv.WorkflowVersionError, match="未注册 handler"):
        await wfv.save_draft("reimbursement_approval", "v1", bad, created_by="user_admin")

    assert await wfv.list_versions("reimbursement_approval") == []


async def test_stable_version_drives_runtime(f3_env):
    """UAT-F3-1：管理端改门限后发布，运行时即刻按新版本执行（无需改代码）。"""
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.workflows import start_workflow

    # 出厂默认 5000：3000 元自动通过
    r0 = await start_workflow("reimbursement_approval",
                             _reimb_input("travel", 3000, "出厂门限"), _ctx("f3-th-builtin"))
    assert r0["status"] == "done", r0.get("reply")
    assert r0["workflow_version"] == "builtin"

    # 发布 v1：免审线降到 200 → 3000 元必须人审
    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(200),
                         created_by="user_admin", comment="降低免审线")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")

    r1 = await start_workflow("reimbursement_approval",
                             _reimb_input("travel", 3000, "新版门限"), _ctx("f3-th-v1"))
    assert r1["status"] == "awaiting_approval", r1.get("reply")
    assert r1["workflow_version"] == "v1"

    # 发布 v2：门限收回 5000 → 又自动通过
    await wfv.save_draft("reimbursement_approval", "v2", _reimb_v1(5000),
                         created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v2", 0, "user_admin")
    r2 = await start_workflow("reimbursement_approval",
                             _reimb_input("travel", 4000, "放宽后收紧"), _ctx("f3-th-v2"))
    assert r2["status"] == "done", r2.get("reply")
    assert r2["workflow_version"] == "v2"

    # 回滚到 v1（严格门限）→ 同类单据重新进人审
    await wfv.rollback("reimbursement_approval", "v1", "cio")
    r3 = await start_workflow("reimbursement_approval",
                             _reimb_input("travel", 4000, "回滚后"),
                             _ctx("f3-th-rollback"), document_key="RB-ROLLBACK")
    assert r3["status"] == "awaiting_approval", r3.get("reply")
    assert r3["workflow_version"] == "v1"


# ===========================================================================
# F3.5 粘性灰度（S1）
# ===========================================================================

def test_sticky_bucket_is_deterministic_and_spread():
    """同单据恒定同桶；桶号均匀分布（灰度比例可预期）。"""
    from app.core.agent.workflow_versions import sticky_bucket

    keys = [f"PO-{i:05d}" for i in range(400)]
    first = {k: sticky_bucket(k) for k in keys}
    assert all(sticky_bucket(k) == first[k] for k in keys), "同单据桶号漂移"
    assert all(0 <= b < 100 for b in first.values())

    hit = sum(1 for b in first.values() if b < 20)
    assert 0.10 < hit / len(keys) < 0.32, f"20% 灰度命中率异常：{hit / len(keys):.3f}"


async def test_graph_pending_probe_for_supervisor_replay(f3_env):
    """supervisor 重放探测：挂起线程 → True；已完成/未知流程 → False。

    （该函数是 supervisor 委派 workflow 路由的导入依赖，缺失即 ImportError。）
    """
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.workflows import _graph_pending, start_workflow

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")

    r_wait = await start_workflow("reimbursement_approval",
                                 _reimb_input("travel", 20000, "重放探测"), _ctx("f3-pin-3"))
    assert r_wait["status"] == "awaiting_approval", r_wait.get("reply")
    assert await _graph_pending("reimbursement_approval", r_wait["thread_id"]) is True

    r_done = await start_workflow("reimbursement_approval",
                                 _reimb_input("travel", 300, "已完成线程"), _ctx("f3-pin-4"))
    assert r_done["status"] == "done", r_done.get("reply")
    assert await _graph_pending("reimbursement_approval", r_done["thread_id"]) is False

    assert await _graph_pending("no_such_workflow", "whatever") is False


async def test_canary_keeps_stable_baseline(f3_env):
    """灰度发布：上一 stable 保留（否则灰度桶外单据无主版本，退化为全量切换）。"""
    from app.core.agent import workflow_versions as wfv

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")
    await wfv.save_draft("reimbursement_approval", "v2", _reimb_v1(200), created_by="user_admin")
    res = await wfv.publish("reimbursement_approval", "v2", 30, "user_admin")
    assert res["mode"] == "canary"

    st = {v["version"]: (v["status"], v["traffic"]) for v in await wfv.list_versions(
        "reimbursement_approval")}
    assert st["v1"] == ("stable", 0)
    assert st["v2"] == ("canary", 30)


async def test_canary_assignment_is_sticky_per_document(f3_env):
    """S1 粘性：同一单据反复解析恒得同一版本；30% 灰度确有单据命中 canary。"""
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.workflow_versions import resolve_sticky, sticky_bucket

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")
    await wfv.save_draft("reimbursement_approval", "v2", _reimb_v1(200), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v2", 30, "user_admin")

    canary_hits = stable_hits = 0
    for i in range(120):
        key = f"RB-{i:04d}"
        expect = "v2" if sticky_bucket(key) < 30 else "v1"
        for _ in range(2):
            _, version = await resolve_sticky("reimbursement_approval", key)
            assert version == expect, f"{key} 版本解析不稳定：{version} != {expect}"
        if expect == "v2":
            canary_hits += 1
        else:
            stable_hits += 1
    assert canary_hits > 0 and stable_hits > 0, "灰度未分流（一侧为空）"


async def test_thread_keeps_pinned_version_after_new_publish(f3_env):
    """S1 核心：线程启动即钉版本，后续发布不得让在途单据换流程。"""
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.workflows import resume_workflow, start_workflow

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")

    r1 = await start_workflow("reimbursement_approval",
                             _reimb_input("travel", 20000, "在途钉版本"), _ctx("f3-pin-1"))
    assert r1["status"] == "awaiting_approval", r1.get("reply")
    assert r1["workflow_version"] == "v1"

    # 在途期间发布 v2 全量
    await wfv.save_draft("reimbursement_approval", "v2", _reimb_v1(200),
                         created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v2", 0, "user_admin")

    r2 = await resume_workflow("reimbursement_approval", r1["thread_id"],
                              {"approved": True, "comment": "同意"}, _ctx("f3-pin-1"))
    assert r2["status"] == "done", r2.get("reply")
    assert r2["workflow_version"] == "v1", "在途线程被新版本劫持"
    assert await wfv.pinned_version(r1["thread_id"]) == "v1"

    # 新单据走新版本
    r3 = await start_workflow("reimbursement_approval",
                             _reimb_input("travel", 20000, "新单据"), _ctx("f3-pin-2"))
    assert r3["workflow_version"] == "v2"


async def test_rollback_switches_stable_immediately(f3_env):
    """回滚属收紧方向 → 即时生效，目标版本重新 stable、当前版本 superseded。"""
    from app.core.agent import workflow_versions as wfv

    await wfv.save_draft("reimbursement_approval", "v1", _reimb_v1(), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v1", 0, "user_admin")
    await wfv.save_draft("reimbursement_approval", "v2", _reimb_v1(200), created_by="user_admin")
    await wfv.publish("reimbursement_approval", "v2", 0, "user_admin")

    out = await wfv.rollback("reimbursement_approval", "v1", "cio")
    assert out["status"] == "rolled_back"

    st = {v["version"]: v["status"] for v in await wfv.list_versions("reimbursement_approval")}
    assert st["v1"] == "stable"
    assert st["v2"] == "superseded"
    assert (await wfv.resolve_sticky("reimbursement_approval", "RB-1"))[1] == "v1"


async def test_rollback_to_unknown_version_rejected(f3_env):
    from app.core.agent import workflow_versions as wfv

    with pytest.raises(wfv.WorkflowVersionError, match="版本不存在"):
        await wfv.rollback("reimbursement_approval", "nope", "cio")


# ===========================================================================
# F3.5 / D-4 发布评测门禁：资金域低分强制阻断
# ===========================================================================

async def test_publish_eval_gate_blocks_and_rolls_back(f3_env, monkeypatch):
    """D-4：资金域发布后评测 FAIL → 自动回滚上一 stable（低分强制阻断）。"""
    from app.core.agent import eval_runner, workflows as wf_mod
    from app.core.agent import workflow_versions as wfv

    p2p = copy.deepcopy(wf_mod.BUILTIN_DSL["procure_to_pay"])
    await wfv.save_draft("procure_to_pay", "v1", p2p, created_by="user_admin")
    await wfv.publish("procure_to_pay", "v1", 0, "user_admin")
    await wfv.save_draft("procure_to_pay", "v2", p2p, created_by="user_admin")
    await wfv.publish("procure_to_pay", "v2", 0, "user_admin")

    # 门禁只在 v3 这次发布上armed（前置发布用 fixture 的静音版本）
    monkeypatch.setattr(wfv, "_start_publish_eval", _REAL_START_PUBLISH_EVAL)
    gate_args = {}

    def _fake_start(domain="", run_id="", source="manual", triggered_by=""):
        gate_args.update(domain=domain, source=source, triggered_by=triggered_by)
        return "run-f3-eval"

    monkeypatch.setattr(eval_runner, "start", _fake_start)
    monkeypatch.setattr(eval_runner, "wait",
                        lambda run_id, timeout=0: _instant({"summary": "FAIL"}))

    # v3 发布（评测必 FAIL）→ 门禁应把 stable 拉回 v2
    await wfv.save_draft("procure_to_pay", "v3", p2p, created_by="user_admin")
    await wfv.publish("procure_to_pay", "v3", 0, "user_admin")

    st = {}
    for _ in range(60):
        st = {v["version"]: v["status"] for v in await wfv.list_versions("procure_to_pay")}
        if st.get("v2") == "stable":
            break
        await asyncio.sleep(0.05)
    assert st.get("v2") == "stable", f"评测门禁未自动回滚：{st}"
    assert st.get("v3") == "superseded"
    # F5.2：门禁触发的评测同样进趋势看板（source=gate + 触发人），
    # 否则"发布被门禁拦了几次"只能去翻日志。
    assert gate_args == {"domain": "treasury", "source": "gate",
                         "triggered_by": "publish:user_admin"}, gate_args


# ===========================================================================
# F3.6 管理端契约（HTTP 层：设计器实际调用的端点 + 权限点）
# ===========================================================================

def _api_app():
    """挂 workflow-definitions 路由 + 假登录态的独立 app。"""
    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.api.routes import workflow_definitions as wf_def_routes

    class FakeAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = {"user_id": "user_admin", "username": "admin"}
            return await call_next(request)

    app = FastAPI()
    app.include_router(wf_def_routes.router)
    app.add_middleware(FakeAuth)
    return app


async def test_workflow_definition_api_contract(f3_env, monkeypatch):
    """设计器端点全链路：元数据 → 校验 → 存草稿 → 申请发布（转人审）。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True)
    transport = ASGITransport(app=_api_app())
    base = "/api/v1/agent-ops/workflow-definitions"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        meta = (await client.get(f"{base}/meta")).json()
        assert set(meta["node_types"]) == {"task", "condition", "gate"}
        assert "equals" in meta["condition_ops"]
        assert "reimb.submit" in meta["handlers"]
        assert "reimbursement_auto_approve_limit" in meta["threshold_keys"]
        assert "procure_to_pay" in meta["builtin_dsl"]

        bad = (await client.post(f"{base}/validate", json={"definition": {
            "name": "x", "entry": "a",
            "nodes": [{"id": "a", "type": "task", "handler": "no.such", "next": "END"}],
        }})).json()
        assert bad["valid"] is False and bad["errors"]

        draft = await client.put(f"{base}/reimbursement_approval/v9/draft",
                                 json={"definition": _reimb_v1(200), "comment": "API 草稿"})
        assert draft.status_code == 200, draft.text

        pub = await client.post(f"{base}/reimbursement_approval/v9/publish",
                                json={"traffic": 0, "comment": "API 发布"})
        assert pub.status_code == 200, pub.text
        assert pub.json()["status"] == "pending_approval"

        hist = (await client.get(f"{base}/reimbursement_approval")).json()
        assert hist["stable"] is None and hist["canary"] is None
        assert {v["version"] for v in hist["versions"]} >= {"v9"}

        gone = await client.post(f"{base}/reimbursement_approval/rollback",
                                 json={"version": "nope"})
        assert gone.status_code == 400

        notfound = await client.get(f"{base}/reimbursement_approval/v0")
        assert notfound.status_code == 404


async def test_publish_endpoint_requires_publish_permission(f3_env, monkeypatch):
    """附录B：发布/回滚必须 customization:publish（仅 update 权限不够）。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.rbac import rbac_engine

    # 只放开 read/update，publish 拒绝
    monkeypatch.setattr(rbac_engine, "check_permission",
                        lambda u, r, a: a != "publish")
    transport = ASGITransport(app=_api_app())
    base = "/api/v1/agent-ops/workflow-definitions"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(f"{base}/meta")).status_code == 200
        denied = await client.post(f"{base}/reimbursement_approval/v1/publish",
                                   json={"traffic": 0})
        assert denied.status_code == 403
        assert "customization:publish" in denied.json()["detail"]
        assert (await client.post(f"{base}/reimbursement_approval/rollback",
                                  json={"version": "v1"})).status_code == 403


async def test_form_metadata_and_validator_agree():
    """设计器元数据的 handler/阈值白名单与校验器口径一致（避免前端放过、后端拒绝）。"""
    from app.core.agent.workflow_versions import form_metadata, validate_definition

    meta = form_metadata()
    defn = copy.deepcopy(meta["builtin_dsl"]["contract_approval"])
    assert validate_definition(defn) == []
    for node in defn["nodes"]:
        if node["type"] == "task":
            assert node["handler"] in meta["handlers"]
        th = node.get("threshold") or {}
        if th.get("key"):
            assert th["key"] in meta["threshold_keys"]
    # 越界 handler 必被拒
    defn["nodes"][0]["handler"] = "shell.exec"
    assert any("未注册 handler" in e for e in validate_definition(defn))


async def test_publish_eval_pass_keeps_new_stable(f3_env, monkeypatch):
    """评测 PASS → 不回滚（门禁只在低分时介入）。"""
    from app.core.agent import eval_runner, workflows as wf_mod
    from app.core.agent import workflow_versions as wfv

    monkeypatch.setattr(wfv, "_start_publish_eval", _REAL_START_PUBLISH_EVAL)
    # 形参要跟 eval_runner.start 的真实签名一致：只写 domain= 的话，门禁任务里
    # 多传一个 source 就会 TypeError——而这条用例断言的是"不回滚"，任务死了它照样通过。
    monkeypatch.setattr(eval_runner, "start",
                        lambda domain="", run_id="", source="manual",
                        triggered_by="": "run-f3-ok")
    monkeypatch.setattr(eval_runner, "wait",
                        lambda run_id, timeout=0: _instant({"summary": "PASS"}))

    p2p = copy.deepcopy(wf_mod.BUILTIN_DSL["procure_to_pay"])
    await wfv.save_draft("procure_to_pay", "v1", p2p, created_by="user_admin")
    await wfv.publish("procure_to_pay", "v1", 0, "user_admin")
    await wfv.save_draft("procure_to_pay", "v2", p2p, created_by="user_admin")
    await wfv.publish("procure_to_pay", "v2", 0, "user_admin")

    for _ in range(20):
        await asyncio.sleep(0.05)
    st = {v["version"]: v["status"] for v in await wfv.list_versions("procure_to_pay")}
    assert st["v2"] == "stable"
    assert st["v1"] == "superseded"
