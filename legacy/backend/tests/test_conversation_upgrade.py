"""
监理对话链路升级测试（工作包一：规则收窄 / 分组降级 / 接真实工具）
==================================================================

对应评审委员会裁决 D-A（降级显性化 + 按意图分组）、D-C（创作产物受控）、
D-E（兜底触发率进指标）。断言口径都是"数据从哪来"——
降级回复里的每个项目编号/百分比都必须能在 Agent 本体快照里找到，
写死文案（历史上把对话路由和监理 Agent 分成两套数据源的缺陷）一律判失败。
"""
import os

import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def conv_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + 监理 Agent 注册 + 监理工具导入 + RBAC 放行。"""
    import app.core.database as database

    db_file = tmp_path / "conversation_upgrade.db"
    monkeypatch.setattr(settings, "mysql_url",
                        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info

    from app.core.agent.orchestrator import orchestrator
    from app.core.rbac import rbac_engine
    from app.core.security.did import did_registry
    from app.agents.project_supervisor.agent import ProjectSupervisorAgent

    import app.agents.project_supervisor.tools  # noqa: F401

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    agent = ProjectSupervisorAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities, security_clearance=agent.security_clearance,
    )
    await orchestrator.register_agent(agent)

    yield {"engine": engine, "agent": agent, "orchestrator": orchestrator}

    for key in list(orchestrator._agents.keys()):  # noqa: SLF001
        if orchestrator._agents[key].domain == "project_supervisor":
            del orchestrator._agents[key]
    await database.close_db()


SNAPSHOT = {
    "total": 2,
    "projects": [
        {"project_id": "proj-001", "project_name": "ERP系统实施项目", "stage": "开发测试",
         "project_status": "in_progress", "milestones_total": 3, "milestones_completed": 2,
         "budget_usage": 0.4, "launch_ready": False,
         "risks": ["进度轻微落后，需关注2026-09-30上线窗口", "UAT未完成，暂不具备上线条件"]},
        {"project_id": "proj-002", "project_name": "企业官网改版项目", "stage": "UAT验收",
         "project_status": "in_progress", "milestones_total": 2, "milestones_completed": 2,
         "budget_usage": 0.62, "launch_ready": False, "risks": []},
    ],
}

USER = {"user_id": "u-conv-1", "username": "conv-user", "role_codes": ["project_manager"]}


# ========== 1. 规则收窄：泛指词不再吞掉所有问题 ==========

def test_rules_generic_project_word_no_longer_monopolizes():
    """"项目"这种泛指词不再命中查询意图——它是"多个问题同一答案"的根因。"""
    from app.api.routes import conversation as conv

    assert conv._classify_intent_by_rules("项目") == conv.INTENT_GUIDE
    assert conv._classify_intent_by_rules("你好") == conv.INTENT_GUIDE
    assert conv._classify_intent_by_rules("今天心情不错") == conv.INTENT_GUIDE


def test_rules_specific_intents_still_hit():
    from app.api.routes import conversation as conv

    assert conv._classify_intent_by_rules("proj-001 进展如何") == conv.INTENT_PROGRESS
    assert conv._classify_intent_by_rules("检查项目合规情况") == conv.INTENT_COMPLIANCE
    assert conv._classify_intent_by_rules("可以上线了吗") == conv.INTENT_LAUNCH_AUDIT
    assert conv._classify_intent_by_rules("出一份监理报告") == conv.INTENT_CREATIVE
    assert conv._classify_intent_by_rules("帮我拟一份监理大纲") == conv.INTENT_CREATIVE


async def test_classify_intent_extracts_project_id():
    from app.api.routes import conversation as conv

    got = await conv.classify_intent("proj-002 的里程碑完成情况怎么样")
    assert got["intent"] == conv.INTENT_PROGRESS
    assert got["project_id"] == "proj-002"
    assert got["classifier"] == "rules"


async def test_intent_llm_arbitration_only_when_rules_miss(monkeypatch):
    """双路分类的成本口径：规则命中不再多花一次 LLM 调用（裁决 D-E 成本项）。"""
    from app.api.routes import conversation as conv

    calls = []

    async def fake_llm(message):
        calls.append(message)
        return conv.INTENT_COMPLIANCE

    monkeypatch.setattr(conv, "_classify_intent_by_llm", fake_llm)
    monkeypatch.setattr(settings, "llm_enabled", True)

    hit = await conv.classify_intent("proj-001 进展如何")
    assert hit["intent"] == conv.INTENT_PROGRESS and not calls

    miss = await conv.classify_intent("看看有没有踩线的地方")
    assert miss["intent"] == conv.INTENT_COMPLIANCE and miss["classifier"] == "llm"
    assert calls == ["看看有没有踩线的地方"]


async def test_intent_llm_arbitration_failure_falls_back_to_rules(monkeypatch):
    from app.api.routes import conversation as conv

    async def dead_llm(message):
        return None

    monkeypatch.setattr(conv, "_classify_intent_by_llm", dead_llm)
    monkeypatch.setattr(settings, "llm_enabled", True)
    got = await conv.classify_intent("随便聊聊")
    assert got["intent"] == conv.INTENT_GUIDE and got["classifier"] == "rules"


# ========== LLM 分类调用的载荷与预算（真机 0/5 全炸的两条根因） ==========

async def test_intent_classify_disables_thinking(monkeypatch):
    """分类调用必须关思考模式：思考态下网关 content 为空，client 回落到 reasoning
    自然语言，JSON 解析必炸（真机实测 5/5 intent_classify_failed）。"""
    from app.api.routes import conversation as conv
    from app.core.llm.client import NO_THINK_EXTRA

    seen = {}

    class _Client:
        async def chat_completion(self, messages, **kwargs):
            seen.update(kwargs)
            return '{"intent": "query_compliance"}'

    monkeypatch.setattr(conv, "_get_llm_client", lambda: _Client())
    assert await conv._classify_intent_by_llm("看看有没有踩线的地方") == conv.INTENT_COMPLIANCE
    assert seen.get("extra") == NO_THINK_EXTRA


async def test_intent_classify_accepts_whichever_key_the_model_used(monkeypatch):
    """认值不认键：同一 prompt 实测既出 {"intent":…} 也出 {"label":…}，
    认死键就等于把命中过的分类又判成失败。"""
    from app.api.routes import conversation as conv

    class _Client:
        async def chat_completion(self, messages, **kwargs):
            return '{"label": "query_compliance"}'

    monkeypatch.setattr(conv, "_get_llm_client", lambda: _Client())
    assert await conv._classify_intent_by_llm("本月合规情况怎么样") == conv.INTENT_COMPLIANCE


async def test_intent_classify_rejects_non_json_without_raising(monkeypatch):
    """网关不守 JSON 契约时回落规则，而不是把异常抛给对话链路。"""
    from app.api.routes import conversation as conv

    class _Client:
        async def chat_completion(self, messages, **kwargs):
            return "用户询问合规情况，我判断这属于合规检查……"

    monkeypatch.setattr(conv, "_get_llm_client", lambda: _Client())
    assert await conv._classify_intent_by_llm("本月合规情况怎么样") is None


async def test_intent_classify_is_bounded_by_timeout(monkeypatch):
    """分类调用串在首帧之前，供应商抖动时它能独自吃掉整个首 token 预算
    （真机实测该调用 0.79~7.08s）——超时必须主动放手回落规则。"""
    import asyncio
    import time

    from app.api.routes import conversation as conv

    class _Slow:
        async def chat_completion(self, messages, **kwargs):
            await asyncio.sleep(5)
            return '{"intent": "query_compliance"}'

    monkeypatch.setattr(conv, "_get_llm_client", lambda: _Slow())
    monkeypatch.setattr(conv, "_INTENT_CLASSIFY_TIMEOUT", 0.2)
    monkeypatch.setattr(settings, "llm_enabled", True)

    t0 = time.perf_counter()
    assert await conv._classify_intent_by_llm("随便聊聊") is None
    assert time.perf_counter() - t0 < 1.0, "超时预算没生效，分类会把首 token 拖过 2s"

    got = await conv.classify_intent("随便聊聊")
    assert got["intent"] == conv.INTENT_GUIDE and got["classifier"] == "rules"


# ========== 2. 规则层不再自带一份监理数据（两类数据源缺陷回归防线） ==========

@pytest.mark.parametrize("msg", [
    "proj-001 进展如何", "检查合规", "能上线吗", "出一份监理报告", "随便聊聊",
])
def test_rule_layer_no_longer_hardcodes_supervisor_data(msg):
    from app.api.routes import conversation as conv

    body = conv._rule_based_response("project-supervisor-agent", msg, {"name": "项目监理agent"})
    assert "proj-001" not in body["content"]
    assert "proj-002" not in body["content"]
    assert "dataCard" not in body, "规则层不得再直出项目数据卡片"
    assert "已就绪" in body["content"] or "能力" in body["content"]


# ========== 3. 查询类降级：数据来自 Agent 本体（经治理面） ==========

async def test_degraded_query_uses_agent_native_numbers(monkeypatch):
    from app.api.routes import conversation as conv

    seen = {}

    async def fake_tool(tool_name, args, agent_key, current_user):
        seen.update({"tool": tool_name, "args": args, "agent_key": agent_key,
                     "user_id": (current_user or {}).get("user_id")})
        return SNAPSHOT

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    intent = {"intent": conv.INTENT_PROGRESS, "classifier": "rules", "project_id": None}
    result = await conv._degraded_response(
        "project-supervisor-agent", "proj-001 进展如何", {"name": "项目监理agent"},
        intent, USER)

    assert result["degraded"] is True
    assert result["degradedReason"] == "llm_unavailable"  # conftest 已置 llm_enabled=False
    assert result["dataSource"] == "agent_native"
    assert seen["tool"] == "supervisor_query_progress"
    assert seen["agent_key"] == "project_supervisor"
    assert seen["user_id"] == USER["user_id"], "降级取数必须带真实身份（RBAC/审计口径）"
    # 回复里的数字逐条来自快照
    for needle in ["proj-001", "里程碑 2/3", "预算使用率 40.0%", "风险 2 项",
                   "proj-002", "UAT验收"]:
        assert needle in result["content"], f"降级回复缺少实时数据「{needle}」"
    assert result["dataCard"]["rows"][0]["project_id"] == "proj-001"


async def test_degraded_query_scoped_by_project_id(monkeypatch):
    from app.api.routes import conversation as conv

    captured = {}

    async def fake_tool(tool_name, args, agent_key, current_user):
        captured["args"] = args
        only = [p for p in SNAPSHOT["projects"]
                if p["project_id"] == args.get("project_id")]
        return {"total": len(only), "projects": only}

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    intent = {"intent": conv.INTENT_LAUNCH_AUDIT, "classifier": "rules",
              "project_id": "proj-002"}
    result = await conv._degraded_supervisor_response(intent, USER)

    assert captured["args"] == {"project_id": "proj-002"}
    assert result["dataSource"] == "agent_native"
    assert "proj-002" in result["content"] and "proj-001" not in result["content"]


async def test_degraded_query_refuses_to_fabricate_when_data_missing(monkeypatch):
    """治理面取不到数时只能说实话，不能回退到写死文案。"""
    from app.api.routes import conversation as conv

    async def no_tool(tool_name, args, agent_key, current_user):
        return None

    monkeypatch.setattr(conv, "_call_governance_tool", no_tool)
    result = await conv._degraded_supervisor_response(
        {"intent": conv.INTENT_COMPLIANCE, "classifier": "rules", "project_id": None}, USER)

    assert result["dataSource"] == "unavailable"
    assert "不可用" in result["content"]
    for fabricated in ["proj-001", "3个在建项目", "预算使用率"]:
        assert fabricated not in result["content"]


@pytest.mark.parametrize("intent_name,tool", [
    ("query_progress", "supervisor_query_progress"),
    ("query_launch_audit", "supervisor_query_progress"),
    ("query_compliance", "supervisor_check_compliance"),
    ("query_report", "supervisor_generate_report"),
])
async def test_each_query_intent_maps_to_a_real_tool(intent_name, tool, monkeypatch):
    from app.api.routes import conversation as conv

    used = []

    async def fake_tool(tool_name, args, agent_key, current_user):
        used.append(tool_name)
        return ({"total": 1, "projects": [SNAPSHOT["projects"][0]]}
                if tool_name.endswith("query_progress")
                else {"stages": {"立项审批": "通过"}, "high_risk": False, "risks": [],
                      "report_id": "SRPT-1", "supervised_projects": 1,
                      "violations_recorded": 0, "overall": "整体合规",
                      "high_risks": [], "medium_risks": [],
                      "projects": [SNAPSHOT["projects"][0]]})

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    out = await conv._degraded_supervisor_response(
        {"intent": intent_name, "classifier": "rules", "project_id": None}, USER)
    assert used == [tool]
    assert out["dataSource"] == "agent_native"


# ========== 4. 治理面工具真实读取 Agent 本体（含权限拒绝路径） ==========

async def test_supervisor_tools_read_agent_seed_through_governor(conv_env):
    from app.core.agent.governor import agent_governor
    from app.core.agent.tools_v2 import ToolContext

    ctx = ToolContext(user_id="u-conv-1", username="conv-user",
                      agent_key="project_supervisor", thread_id="conv-test")

    progress = await agent_governor.execute("supervisor_query_progress", {}, ctx)
    assert progress.ok, progress.error
    ids = [p["project_id"] for p in progress.data["projects"]]
    assert ids == ["proj-001", "proj-002", "proj-003"]
    assert progress.data["projects"][0]["milestones_completed"] == 2
    assert progress.data["projects"][0]["budget_usage"] == 0.40

    one = await agent_governor.execute(
        "supervisor_query_progress", {"project_id": "proj-003"}, ctx)
    assert one.ok and [p["project_id"] for p in one.data["projects"]] == ["proj-003"]

    compliance = await agent_governor.execute("supervisor_check_compliance", {}, ctx)
    assert compliance.ok
    assert set(compliance.data["stages"]) == {
        "立项审批", "需求评审", "开发测试", "UAT验收", "上线发布", "结项审计"}

    report = await agent_governor.execute("supervisor_generate_report", {}, ctx)
    assert report.ok, report.error
    assert report.data["report_id"].startswith("SRPT-")
    assert report.data["supervised_projects"] == 3


async def test_supervisor_tools_are_readonly_and_permission_checked(conv_env, monkeypatch):
    from app.core.agent.tools_v2 import ToolRisk, tool_registry_v2
    from app.core.rbac import rbac_engine

    for name in ("supervisor_query_progress", "supervisor_check_compliance",
                 "supervisor_generate_report"):
        spec = tool_registry_v2.get(name)
        assert spec is not None, f"{name} 未注册"
        assert spec.risk_level == ToolRisk.READONLY, f"{name} 必须是只读（不产生业务变更）"
        assert (spec.resource, spec.action) == ("project", "read"), (
            f"{name} 权限点必须落在 RBAC 既有资源上，不能自造资源名")

    # 权限缺失 → 治理面拒绝，对话链路据此返回"数据不可用"而不是编造答案
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: False, raising=True)
    from app.core.agent.governor import agent_governor
    from app.core.agent.tools_v2 import ToolContext

    ctx = ToolContext(user_id="u-no-perm", username="guest",
                      agent_key="project_supervisor", thread_id="conv-denied")
    result = await agent_governor.execute("supervisor_query_progress", {}, ctx)
    assert not result.ok
    assert "权限" in (result.error or "") or "permission" in (result.error or "").lower()


# ========== 5. 创作类降级：管理员待办真实落库并可受理 ==========

async def test_admin_todo_persists_and_closes_without_graph_resume(conv_env, monkeypatch):
    from app.core.agent import runtime as runtime_mod
    from app.core.agent import orchestration_service as svc
    from app.api.routes import conversation as conv

    async def boom(*args, **kwargs):
        raise AssertionError("admin_todo 不应触发 LangGraph 恢复（无检查点）")

    monkeypatch.setattr(runtime_mod.agent_runtime, "resume", boom)

    intent = {"intent": conv.INTENT_CREATIVE, "classifier": "rules",
              "project_id": "proj-001"}
    body = await conv._manual_queue_response(
        "project-supervisor-agent", "帮我出一份监理大纲", intent, USER, "llm_unavailable")
    todo_id = body["todoId"]
    assert todo_id and todo_id.startswith("todo:")
    assert body["manualQueue"] is True

    from sqlalchemy import select
    from app.db.models import agent_approval_requests
    from app.db.repositories.base import session_scope

    async with session_scope() as session:
        row = (await session.execute(
            select(agent_approval_requests).where(
                agent_approval_requests.c.thread_id == todo_id))).mappings().first()
    assert row is not None, "管理员待办没有落库"
    assert row["domain"] == "admin_todo"
    assert row["status"] == "pending"
    assert row["risk"] == "manual_review"
    assert row["requested_by"] == USER["user_id"]
    assert "监理大纲" in row["preview"]

    out = await svc.resume_after_decision(
        todo_id, approved=True, comment="已按需求人工出稿", decided_by="u-admin-1")
    assert out is not None and out["status"] == "done"

    async with session_scope() as session:
        row2 = (await session.execute(
            select(agent_approval_requests).where(
                agent_approval_requests.c.thread_id == todo_id))).mappings().first()
    assert row2["status"] == "executed"
    assert row2["decided_by"] == "u-admin-1"
    assert "已受理并处理" in row2["result_reply"]


async def test_admin_todo_rejection_path(conv_env):
    from app.core.agent import orchestration_service as svc
    from app.api.routes import conversation as conv

    body = await conv._manual_queue_response(
        "project-supervisor-agent", "拟一份实施方案", {"intent": conv.INTENT_CREATIVE,
        "classifier": "rules", "project_id": None}, USER, "doc_failed")
    out = await svc.resume_after_decision(
        body["todoId"], approved=False, comment="需求不完整", decided_by="u-admin-2")
    assert "已驳回" in out["reply"]

    from sqlalchemy import select
    from app.db.models import agent_approval_requests
    from app.db.repositories.base import session_scope

    async with session_scope() as session:
        row = (await session.execute(
            select(agent_approval_requests).where(
                agent_approval_requests.c.thread_id == body["todoId"]))).mappings().first()
    assert row["status"] == "rejected"


async def test_admin_todo_degrades_gracefully_without_db(monkeypatch):
    """数据库不可用时对话不能崩，回复要说明工单没建成。"""
    from app.api.routes import conversation as conv

    async def no_db(**kwargs):
        return None

    from app.core.agent import orchestration_service as svc

    monkeypatch.setattr(svc, "create_admin_todo", no_db)
    body = await conv._manual_queue_response(
        "project-supervisor-agent", "出一份监理大纲",
        {"intent": conv.INTENT_CREATIVE, "classifier": "rules", "project_id": None},
        USER, "llm_failed")
    assert body["todoId"] is None
    assert "工单未能创建" in body["content"]


# ========== 6. 兜底触发率进指标（裁决 D-E） ==========

async def test_fallback_metric_counts_query_degradation(monkeypatch):
    from app.api.routes import conversation as conv
    from app.core import metrics

    async def fake_tool(tool_name, args, agent_key, current_user):
        return SNAPSHOT

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    child = metrics.CONVERSATION_FALLBACK.labels(
        agent_id="project-supervisor-agent", intent="query_progress",
        reason="llm_unavailable")
    before = float(child._value.get())  # noqa: SLF001

    await conv._degraded_response(
        "project-supervisor-agent", "proj-001 进展如何", {"name": "项目监理agent"},
        {"intent": conv.INTENT_PROGRESS, "classifier": "rules", "project_id": "proj-001"},
        USER)

    assert float(child._value.get()) == before + 1  # noqa: SLF001


async def test_creative_success_does_not_count_as_fallback(monkeypatch):
    """成功产出文档不计入兜底率，否则指标会被成功流量污染。"""
    from app.api.routes import conversation as conv
    from app.core import metrics

    async def fake_tool(tool_name, args, agent_key, current_user):
        return SNAPSHOT

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    before = float(metrics.CONVERSATION_FALLBACK.labels(
        agent_id="x", intent="y", reason="z")._value.get())  # noqa: SLF001
    conv._note_fallback("x", "y", "z")
    assert float(metrics.CONVERSATION_FALLBACK.labels(
        agent_id="x", intent="y", reason="z")._value.get()) == before + 1  # noqa: SLF001


# ========== 7. /chat 端点接线（含认证依赖不能被摘掉） ==========

def test_chat_endpoint_requires_authentication():
    """对话端点必须挂在认证依赖上：降级取数依赖真实 user_id 走 RBAC 与审计。"""
    import inspect

    from app.api.middleware.auth import PUBLIC_PATHS
    from app.api.routes import conversation as conv

    sig = inspect.signature(conv.chat)
    dep = sig.parameters["current_user"].default
    assert getattr(dep, "dependency", None).__name__ == "get_current_user"
    assert not any("conversation" in str(p) for p in PUBLIC_PATHS), \
        "对话端点不得进入免认证白名单"


async def test_chat_endpoint_unknown_and_offline_agents():
    from app.api.routes import conversation as conv

    unknown = await conv.chat(
        conv.ChatRequest(agent_id="no-such-agent", message="你好"), USER)
    assert unknown["degraded"] is False
    assert "未知" in unknown["content"]

    offline_id = next(a["id"] for a in conv.CHAT_AGENTS if a["status"] == "offline")
    offline = await conv.chat(conv.ChatRequest(agent_id=offline_id, message="在监项目进展"), USER)
    assert offline["degraded"] is False and "离线" in offline["content"]


async def test_chat_endpoint_degrades_to_agent_native_data(conv_env, monkeypatch):
    from app.api.routes import conversation as conv

    async def fake_tool(tool_name, args, agent_key, current_user):
        return SNAPSHOT

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    monkeypatch.setattr(settings, "llm_enabled", False)

    out = await conv.chat(
        conv.ChatRequest(agent_id="project-supervisor-agent", message="proj-001 进展如何"), USER)
    assert out["degraded"] is True and out["degradedReason"] == "llm_unavailable"
    assert out["intent"] == "query_progress" and out["dataSource"] == "agent_native"
    assert "proj-001" in out["content"] and "预算使用率 40.0%" in out["content"]


async def test_chat_endpoint_creative_without_llm_goes_to_manual_queue(conv_env, monkeypatch):
    from app.api.routes import conversation as conv

    async def fake_tool(tool_name, args, agent_key, current_user):
        return SNAPSHOT

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    monkeypatch.setattr(settings, "llm_enabled", False)

    out = await conv.chat(
        conv.ChatRequest(agent_id="project-supervisor-agent",
                         message="帮我出一份项目监理大纲"), USER)
    assert out["degraded"] is True and out["intent"] == "creative_document"
    assert out["manualQueue"] is True and out["todoId"].startswith("todo:")
    assert "attachments" not in out


# ========== 8. 受控产物目录（裁决 D-C） ==========

def test_output_dir_contains_every_artifact_even_with_traversal_names(tmp_path, monkeypatch):
    from app.core.document.output import output_dir, resolve_output_path

    monkeypatch.setattr(settings, "document_output_dir", str(tmp_path / "documents"))
    for sneaky in ["../../evil.docx", "..\\..\\evil.docx",
                   "/absolute/path/evil.docx", "a\\b\\c.docx"]:
        got = resolve_output_path(sneaky)
        assert got.startswith(str(tmp_path / "documents")), f"越界：{got}"
        assert got == os.path.join(output_dir(), os.path.basename(sneaky)), (
            f"只应剥离路径成分，不该改写文件名：{got}")
    assert output_dir().rstrip("\\/") == str(tmp_path / "documents")
    assert os.path.isabs(output_dir()), "受控目录必须解析为绝对路径（产物落点不能随 cwd 漂移）"


# ========== 9. 创作类 token 成本归集（裁决 D-E 第 3 条） ==========

async def test_creative_tokens_are_recorded_by_domain(monkeypatch):
    """长文本创作的 token 必须计入按域日累计，否则 P2-7 全成本报表看不到这一类。"""
    from app.core.agent.runtime import agent_runtime
    from app.core.llm.client import LLMClient

    client = LLMClient()

    async def fake_chat_async(messages, **kwargs):
        return {"choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 800,
                          "total_tokens": 2000}}

    monkeypatch.setattr(client, "chat_async", fake_chat_async)
    agent_runtime._token_usage_today.pop("project_supervisor", None)

    await client.chat_completion([{"role": "user", "content": "出一份监理大纲"}],
                                 scene="creative_document",
                                 cost_domain="project_supervisor")

    assert agent_runtime._token_usage_today["project_supervisor"] == 2000

    # 计量异常不能把模型调用带崩（对话可用性优先于成本统计）
    def boom(*a, **k):
        raise RuntimeError("计量器坏了")

    monkeypatch.setattr(agent_runtime, "_record_tokens", boom)
    got = await client.chat_completion([{"role": "user", "content": "x"}],
                                       scene="creative_document",
                                       cost_domain="project_supervisor")
    assert got == "{}"


async def test_conversation_creative_passes_cost_domain(monkeypatch, tmp_path):
    """创作链路的 cost_domain 取 Agent 域键（project_supervisor），不是界面 id。"""
    from app.api.routes import conversation as conv

    seen = {}

    class _Recorder:
        async def chat_completion(self, messages, **kwargs):
            seen.update(kwargs)
            return '{"title": "监理大纲", "sections": [], "conclusion": ""}'

    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(conv, "_get_llm_client", lambda: _Recorder())
    monkeypatch.setattr(settings, "document_output_dir", str(tmp_path))

    async def fake_tool(tool_name, args, agent_key, current_user):
        return None

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)
    await conv._llm_creative_outline(
        "project-supervisor-agent", "出一份监理大纲",
        {"intent": conv.INTENT_CREATIVE, "classifier": "rules", "project_id": None}, USER)

    assert seen["scene"] == "creative_document"
    assert seen["cost_domain"] == "project_supervisor"

    from docx import Document

    from app.core.document.output import resolve_output_path
    from app.core.document.docx_generator import DocxGenerator

    monkeypatch.setattr(settings, "document_output_dir", str(tmp_path / "documents"))
    note = "AI 生成草稿 · 生成时间 2026-10-04T10:00:00+08:00 · trace_id " + "T" * 400
    path = DocxGenerator().generate(
        "report",
        {"title": "监理大纲", "summary": "摘要",
         "sections": [{"heading": "监理范围与依据", "content": "内容"}],
         "header_note": note},
        "header-test.docx")
    doc = Document(path)
    header = "\n".join(p.text for s in doc.sections for p in s.header.paragraphs)
    assert "AI 生成草稿" in header and "trace_id" in header
    assert len(header) <= 300 + 10, "页眉标注必须截断，不能把模型输出无限写进页眉"
    assert resolve_output_path("header-test.docx") == path
