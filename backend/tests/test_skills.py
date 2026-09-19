"""
Skill 规范与目录测试（路线图 Phase D / D4-D5）
================================================
1. 6 个首批 Skill 全部加载且白名单工具真实注册（valid）；
2. 域过滤与热加载（新增/删除文件生效）；
3. 引用未注册工具的 skill 惰性标记 invalid；
4. 目录 API（/agent-ops/skills）。
"""
import pytest

from app.core.agent.skills import SkillRegistry


async def test_six_builtin_skills_valid():
    """首批 6 个 Skill 全部加载、工具白名单全部真实注册。"""
    import app.agents.contract.tools  # noqa: F401
    import app.agents.crm.tools  # noqa: F401
    import app.agents.hr.tools  # noqa: F401
    import app.agents.payroll.tools  # noqa: F401
    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401

    catalog = SkillRegistry().to_catalog()
    names = {s["name"] for s in catalog}
    assert {"reimbursement_handle", "treasury_daily", "hr_headcount",
            "crm_customer_view", "sales_order_handle",
            "contract_risk_review"} <= names
    assert all(s["valid"] for s in catalog), (
        [s for s in catalog if not s["valid"]]
    )


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
