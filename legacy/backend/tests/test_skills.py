"""
Skill 规范与目录测试（路线图 Phase D / D4-D5 + 工作包二）
=====================================================
1. 出厂 Skill（首批 6 + 工作包二 6）全部加载且白名单工具真实注册（valid）；
2. 域过滤与热加载（新增/删除文件生效）；
3. 引用未注册工具的 skill 惰性标记 invalid；
4. 目录 API（/agent-ops/skills）。
"""
import pytest

from app.core.agent.skills import SkillRegistry


async def test_builtin_skills_all_valid():
    """出厂 Skill 全部加载、工具白名单全部真实注册（工具未注册=定义不可用）。"""
    import app.agents.contract.tools  # noqa: F401
    import app.agents.crm.tools  # noqa: F401
    import app.agents.hr.tools  # noqa: F401
    import app.agents.payroll.tools  # noqa: F401
    # 工作包二：平台级工具（文档生成 + 只读查询）与监理域工具——出厂技能引用它们
    import app.agents.platform.document_tools  # noqa: F401
    import app.agents.platform.tools  # noqa: F401
    import app.agents.project_supervisor.tools  # noqa: F401
    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401

    catalog = SkillRegistry().to_catalog()
    names = {s["name"] for s in catalog}
    assert {"reimbursement_handle", "treasury_daily", "hr_headcount",
            "crm_customer_view", "sales_order_handle", "contract_risk_review",
            "docx_report", "xlsx_report", "meeting_notes", "weekly_report",
            "data_brief", "supervision_plan"} <= names
    assert all(s["valid"] for s in catalog), (
        [s for s in catalog if not s["valid"]]
    )
    # 工作包二六件套必须真的带工具：tools 为空的技能在管理端不可发布
    # （skill_versions.validate_definition 要求 tools ≥1），等于交付一件装饰件
    by_name = {s["name"]: s for s in catalog}
    for skill_name in ("docx_report", "xlsx_report", "meeting_notes",
                       "weekly_report", "data_brief", "supervision_plan"):
        assert by_name[skill_name]["tools"], f"{skill_name} 未绑定任何工具"


async def test_skill_domain_filter_and_hot_reload(tmp_path):
    """域过滤 + 热加载（文件增删即时生效）。"""
    reg = SkillRegistry(skills_dir=tmp_path)
    (tmp_path / "demo_a.yaml").write_text(
        "name: demo_a\ndisplay_name: A\ndescription: 测试A\ndomain: domain_x\n"
        "tools: []\ninstructions: |\n  demo\n",
        encoding="utf-8",
    )
    assert [s.name for s in reg.list_skills("domain_x")] == ["demo_a"]
    assert reg.list_skills("other") == []

    # 热加载：新增文件
    (tmp_path / "demo_b.yaml").write_text(
        "name: demo_b\ndisplay_name: B\ndescription: 测试B\ndomain: domain_x\ntools: []\n",
        encoding="utf-8",
    )
    assert len(reg.list_skills("domain_x")) == 2

    # 热加载：删除文件
    (tmp_path / "demo_a.yaml").unlink()
    assert [s.name for s in reg.list_skills("domain_x")] == ["demo_b"]


async def test_skill_lazy_invalid_tool_ref(tmp_path):
    """引用未注册工具 → 惰性标记 invalid（不阻断加载）。"""
    reg = SkillRegistry(skills_dir=tmp_path)
    (tmp_path / "bad.yaml").write_text(
        "name: bad\ndisplay_name: Bad\ndescription: x\ndomain: y\n"
        "tools: [nonexistent_tool]\n",
        encoding="utf-8",
    )
    catalog = reg.to_catalog()
    assert catalog[0]["valid"] is False
    assert "nonexistent_tool" in catalog[0]["invalid_reason"]


async def test_skills_catalog_api(tmp_path, monkeypatch):
    """目录 API：GET /api/v1/agent-ops/skills（依赖注入登录态）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.api.routes import agent_ops as agent_ops_routes

    fake_user = {"user_id": "user_admin", "username": "admin"}

    class FakeAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = fake_user
            return await call_next(request)

    app = FastAPI()
    app.include_router(agent_ops_routes.router)
    app.add_middleware(FakeAuth)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/agent-ops/skills")
        assert r.status_code == 200
        skills = r.json().get("skills", [])
        assert len(skills) >= 6
        assert all("tools" in s for s in skills)

        r2 = await client.get("/api/v1/agent-ops/skills?domain=treasury")
        names = {s["name"] for s in r2.json().get("skills", [])}
        assert names == {"treasury_daily"}


# ===========================================================================
# 工作包二：Skill 激活链路（请求指名 → ctx.extra["skill"] → 图收窄 + 审计）
# ===========================================================================

def _skill_yaml(name: str, domain: str, tools: list[str]) -> str:
    return (f"name: {name}\ndisplay_name: {name}\ndescription: d\n"
            f"domain: {domain}\ntools: {tools}\ninstructions: 指令\n")


@pytest.fixture
def act_env(tmp_path, monkeypatch):
    """隔离的出厂技能目录 + 停开缓存（不依赖真库，只验激活判定）。"""
    from app.core.agent import skills as skills_mod
    from app.core.agent.customization import customization_service

    import app.agents.reimbursement.tools  # noqa: F401  注册 reimbursement_query_list
    import app.agents.platform.document_tools  # noqa: F401  platform 域工具

    reg = SkillRegistry(skills_dir=tmp_path)
    (tmp_path / "ok.yaml").write_text(
        _skill_yaml("ok", "reimbursement", ["reimbursement_query_list"]),
        encoding="utf-8")
    (tmp_path / "cross_domain.yaml").write_text(
        _skill_yaml("cross_domain", "reimbursement", ["document_generate_docx"]),
        encoding="utf-8")
    (tmp_path / "broken.yaml").write_text(
        _skill_yaml("broken", "reimbursement", ["gone_tool"]), encoding="utf-8")
    monkeypatch.setattr(skills_mod, "skill_registry", reg)
    monkeypatch.setattr(customization_service, "_cache", {}, raising=False)
    yield {"reg": reg, "tmp": tmp_path, "mod": skills_mod}
    customization_service._cache.pop("skill:ok", None)


def test_resolve_activatable_skill(act_env):
    """入口判定：存在 + 启用 + 工具已注册；不可用给出原因而不是静默忽略。"""
    resolve = act_env["mod"].resolve_activatable_skill

    assert resolve("ok") == ("ok", "")
    name, reason = resolve("ghost")
    assert name == "" and "不存在" in reason
    name, reason = resolve("broken")
    assert name == "" and "未注册" in reason
    assert resolve("") == ("", "")


def test_resolve_activatable_skill_respects_disable(act_env):
    from app.core.agent.customization import customization_service

    customization_service._cache["skill:ok"] = {"enabled": False, "overrides": {}}
    name, reason = act_env["mod"].resolve_activatable_skill("ok")
    assert name == "" and "停用" in reason


def test_skill_for_not_applied_when_tools_out_of_domain(act_env):
    """技能工具在本域一个都不注册 → 不套用（否则白名单过滤成空集＝对话故障）。"""
    from app.core.agent.runtime import AgentRuntime
    from app.core.agent.tools_v2 import ToolContext

    rt = AgentRuntime()
    ctx = ToolContext(user_id="u1", thread_id="t", extra={"skill": "cross_domain"})
    assert rt._skill_for(ctx, "reimbursement") == ""
    assert rt._skill_for(
        ToolContext(user_id="u1", thread_id="t", extra={"skill": "ok"}),
        "reimbursement") == "ok"


async def test_orchestration_activates_skill_into_ctx_and_audits(act_env, monkeypatch):
    """生产侧：kwargs.skill → ctx.extra["skill"] + 启停留痕；不可用时回落纯域。"""
    from app.core.agent import orchestration_service as orch
    from app.core.agent import runtime as runtime_mod

    calls: list[tuple] = []

    async def _fake_audit(ctx, domain, activated, requested, reason):
        calls.append((activated, requested, reason))

    captured: dict = {}

    class _Agent:
        domain = "reimbursement"
        name = "报销Agent"

    async def _fake_run(domain, message, ctx, history=None):
        captured["ctx"] = ctx
        return {"status": "done", "reply": "ok"}

    monkeypatch.setattr(orch, "_audit_skill_activation", _fake_audit)
    monkeypatch.setattr(runtime_mod.agent_runtime, "_check_budget", lambda d: True)
    monkeypatch.setattr(runtime_mod.agent_runtime, "has_domain", lambda d: True)
    monkeypatch.setattr(runtime_mod.agent_runtime, "run", _fake_run)
    monkeypatch.setattr("app.db.repositories.base.has_db", lambda: True)

    reply = await orch.run_scenario_for_agent(
        _Agent(), [{"role": "user", "content": "查报销"}],
        {"user_id": "user_admin", "skill": "ok"})
    assert reply == "ok"
    assert captured["ctx"].extra["skill"] == "ok"
    assert calls and calls[0][0] == "ok"

    captured.clear()
    await orch.run_scenario_for_agent(
        _Agent(), [{"role": "user", "content": "查报销"}],
        {"user_id": "user_admin", "skill": "broken"})
    assert "skill" not in captured["ctx"].extra     # 不可用＝按纯域执行，不报错
    assert calls[-1][0] == "" and "未注册" in calls[-1][2]
