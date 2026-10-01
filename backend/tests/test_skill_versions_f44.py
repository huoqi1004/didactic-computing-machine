"""
Skill 编辑器 / 版本流 / 启停 / 导出导入测试（路线图 Phase F4.4-F4.5）
====================================================================

覆盖 F4 退出标准中与 Skill 相关的判据：

- 表单定义 → 草稿 → **发布必经人审**（红线 2）→ 批准后物化为发布目录 YAML
  → ``SkillRegistry`` 热加载生效（UAT"热加载生效"的端到端证据：批前无文件、
  批后同一进程内立即可见，无需重启）；
- 已发布版本内容不可变，只能另存新版本；回滚即时生效（回滚目标必经人审批准过）；
- 启停：停用=收紧即时、启用=放宽须人审；停用项运行时不可见、管理端仍列出；
- 导出/导入 JSON 模板（附录 B 权限点 customization:export）；**导入只落草稿**，
  不得绕过本环境审批；
- 运行期消费点 ``AgentRuntime._skill_for/_skill_cfg``：域不匹配/停用/未登记一律
  不套用（技能白名单只收窄工具集，套错域会把工具过滤成空集）。
"""
import pytest
import pytest_asyncio

from app.core.config import settings

TOOL_OK = "reimbursement_query_list"


def _defn(name: str = "f44_skill", **over) -> dict:
    d = {"name": name, "display_name": "F44 技能", "description": "测试用技能",
          "domain": "reimbursement", "tools": [TOOL_OK],
          "instructions": "你是测试技能。回答前先复述关键要素。",
          "guardrails": {"max_amount": 100}}
    d.update(over)
    return d


@pytest_asyncio.fixture
async def sk_env(tmp_path, monkeypatch):
    """sqlite 库 + 发布目录指向 tmp + RBAC 放行 + 全局单例状态归零。"""
    import app.core.database as database
    from app.agents.reimbursement import tools  # noqa: F401  注册治理面工具
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry
    from app.core.rbac import rbac_engine

    overlay = tmp_path / "skills_overlay"
    monkeypatch.setattr(settings, "skills_overlay_dir", str(overlay))
    db_file = tmp_path / "skill_f44.db"
    monkeypatch.setattr(settings, "mysql_url",
                        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info, info
    # skill_definitions 是本轮新增表：确认 bootstrap 的差集逻辑真的建出了它
    async with engine.begin() as conn:
        got = (await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='skill_definitions'")).fetchall()
    assert got, "bootstrap_schema 未建出 skill_definitions 表"

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True)

    def _reset_registry():
        skill_registry._skills.clear()
        skill_registry._dir_mtime = ""

    def _reset_cache():
        customization_service._cache.clear()
        customization_service._loaded_from_db = False
        customization_service._cache_version = -1

    _reset_cache()
    _reset_registry()
    yield {"overlay": overlay, "engine": engine}
    await database.close_db()
    _reset_cache()
    _reset_registry()


# ===========================================================================
# 1. 校验规则
# ===========================================================================

def test_validate_definition_rules():
    from app.core.agent.skill_versions import validate_definition

    assert validate_definition(_defn()) == []
    errs = validate_definition(_defn(name="Bad Name", domain="", tools=[],
                                     instructions="  ", extra_field=1))
    assert any("name" in e for e in errs)
    assert any("domain" in e for e in errs)
    assert any("tools" in e for e in errs)
    assert any("instructions" in e for e in errs)
    assert any("未支持字段" in e for e in errs)
    assert validate_definition("不是对象") == ["定义必须是对象"]


def test_validate_publishable_requires_registered_tool():
    from app.core.agent.skill_versions import (
        validate_definition, validate_publishable)

    bad = _defn(tools=["tool_that_does_not_exist"])
    assert validate_definition(bad) == []          # 草稿可存
    assert any("未在治理面注册" in e for e in validate_publishable(bad))


# ===========================================================================
# 2. 注册表：发布目录优先 + 启停
# ===========================================================================

def test_overlay_dir_wins_over_builtin_and_marks_published(tmp_path, monkeypatch):
    from app.core.agent.skills import SkillRegistry

    overlay = tmp_path / "overlay"
    overlay.mkdir()
    monkeypatch.setattr(settings, "skills_overlay_dir", str(overlay))
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "demo.yaml").write_text(
        "name: demo\ndisplay_name: 出厂\ndescription: b\ndomain: d\n"
        f"tools: [{TOOL_OK}]\ninstructions: 出厂指令\n", encoding="utf-8")
    reg = SkillRegistry(skills_dir=builtin)
    assert reg.get("demo").display_name == "出厂"
    assert reg.get("demo").published is False

    (overlay / "demo.yaml").write_text(
        "name: demo\ndisplay_name: 已发布\ndescription: p\ndomain: d\n"
        f"tools: [{TOOL_OK}]\ninstructions: 发布指令\nversion: v2\n", encoding="utf-8")
    live = reg.get("demo")
    assert live.display_name == "已发布" and live.version == "v2"
    assert live.published is True


def test_disabled_skill_hidden_from_runtime_visible_in_catalog(monkeypatch):
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import SkillRegistry

    reg = SkillRegistry()
    name = "reimbursement_handle"
    assert reg.get(name) is not None                  # 无定制行 = 启用

    customization_service._cache[f"skill:{name}"] = {"enabled": False, "overrides": {}}
    try:
        assert reg.get(name) is None                   # 运行时不可见
        row = next(s for s in reg.to_catalog() if s["name"] == name)
        assert row["enabled"] is False                 # 管理端仍列出
        assert name in [s.name for s in reg.list_skills()]   # 目录视图含停用项
        assert name not in [s.name for s in reg.list_skills(include_disabled=False)]
    finally:
        customization_service._cache.pop(f"skill:{name}", None)


# ===========================================================================
# 3. 版本流：草稿 → 人审 → 发布物化 → 热加载
# ===========================================================================

async def test_publish_requires_approval_then_materializes_and_hot_loads(sk_env):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry

    await skill_versions.save_draft("f44_skill", "v1", _defn(), "user_admin", "首版")
    rows = await skill_versions.list_versions("f44_skill")
    assert [r["status"] for r in rows] == ["draft"]

    r = await skill_versions.request_publish("f44_skill", "v1", "user_admin")
    assert r["status"] == "pending_approval", r        # 红线 2：发布必经人审

    # 未批：既没有发布文件，也没有生效版本，运行时看不见
    assert not (sk_env["overlay"] / "f44_skill.yaml").exists()
    assert skill_registry.get("f44_skill") is None
    assert await skill_versions.pending_proposal("f44_skill") is not None

    decided = await customization_service.apply_pending(
        "skill", "f44_skill", True, decided_by="user_cio")
    assert decided["status"] == "executed"
    assert decided["skill"]["status"] == "published"

    f = sk_env["overlay"] / "f44_skill.yaml"
    assert f.exists(), "批准后未物化发布目录 YAML"
    live = skill_registry.get("f44_skill")             # 同进程热加载，无需重启
    assert live is not None and live.version == "v1" and live.published
    assert live.tools == [TOOL_OK] and "复述" in live.instructions
    assert live.enabled is True

    stable = await skill_versions.get_definition("f44_skill", "v1")
    assert stable["status"] == "stable"


async def test_rejected_publish_changes_nothing(sk_env):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry

    await skill_versions.save_draft("f44_rej", "v1", _defn("f44_rej"), "user_admin")
    await skill_versions.request_publish("f44_rej", "v1", "user_admin")
    decided = await customization_service.apply_pending(
        "skill", "f44_rej", False, decided_by="user_cio", comment="指令太宽泛")
    assert decided["status"] == "rejected"
    assert not (sk_env["overlay"] / "f44_rej.yaml").exists()
    assert skill_registry.get("f44_rej") is None
    assert await skill_versions.pending_proposal("f44_rej") is None
    rows = await skill_versions.list_versions("f44_rej")
    assert [r["status"] for r in rows] == ["draft"]    # 驳回不动版本状态


async def test_published_version_is_immutable(sk_env):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service

    await skill_versions.save_draft("f44_imm", "v1", _defn("f44_imm"), "user_admin")
    await skill_versions.request_publish("f44_imm", "v1", "user_admin")
    await customization_service.apply_pending("skill", "f44_imm", True,
                                              decided_by="user_cio")
    with pytest.raises(skill_versions.SkillVersionError) as e:
        await skill_versions.save_draft("f44_imm", "v1",
                                        _defn("f44_imm", instructions="改写"),
                                        "user_admin")
    assert "不可变" in str(e.value)
    # 草稿仍可另存新版本
    out = await skill_versions.save_draft("f44_imm", "v2", _defn("f44_imm"),
                                          "user_admin")
    assert out["status"] == "draft"


async def test_rollback_rematerializes_previous_version(sk_env):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry

    async def publish(version: str, instructions: str):
        await skill_versions.save_draft("f44_rb", version,
                                        _defn("f44_rb", instructions=instructions),
                                        "user_admin")
        await skill_versions.request_publish("f44_rb", version, "user_admin")
        await customization_service.apply_pending("skill", "f44_rb", True,
                                                  decided_by="user_cio")

    await publish("v1", "第一版指令")
    await publish("v2", "第二版指令")
    assert skill_registry.get("f44_rb").version == "v2"
    statuses = {r["version"]: r["status"]
                for r in await skill_versions.list_versions("f44_rb")}
    assert statuses == {"v1": "superseded", "v2": "stable"}

    back = await skill_versions.rollback("f44_rb", "v1", "user_admin")
    assert back["status"] == "rolled_back"
    live = skill_registry.get("f44_rb")
    assert live.version == "v1" and "第一版" in live.instructions
    assert back["previous"] == ["stable:v2"]

    with pytest.raises(skill_versions.SkillVersionError):
        await skill_versions.rollback("f44_rb", "v3", "user_admin")


# ===========================================================================
# 4. 启停 / 幂等 / target 合法性
# ===========================================================================

async def test_disable_is_immediate_enable_needs_approval(sk_env):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry

    await skill_versions.save_draft("f44_sw", "v1", _defn("f44_sw"), "user_admin")
    await skill_versions.request_publish("f44_sw", "v1", "user_admin")
    await customization_service.apply_pending("skill", "f44_sw", True,
                                              decided_by="user_cio")
    assert skill_registry.get("f44_sw") is not None

    off = await skill_versions.set_enabled("f44_sw", False, "user_admin")
    assert off["status"] == "applied"                  # 收紧即时
    assert skill_registry.get("f44_sw") is None

    on = await skill_versions.set_enabled("f44_sw", True, "user_admin")
    assert on["status"] == "pending_approval"          # 放宽须批
    assert skill_registry.get("f44_sw") is None        # 未批仍停用
    await customization_service.apply_pending("skill", "f44_sw", True,
                                              decided_by="user_cio")
    assert skill_registry.get("f44_sw") is not None


async def test_publish_preserves_disabled_state(sk_env):
    """停用中发布新版本不得顺带把技能打开（启停与内容是两条独立轴）。"""
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry

    await skill_versions.save_draft("f44_keep", "v1", _defn("f44_keep"), "user_admin")
    await skill_versions.request_publish("f44_keep", "v1", "user_admin")
    await customization_service.apply_pending("skill", "f44_keep", True,
                                              decided_by="user_cio")
    await skill_versions.set_enabled("f44_keep", False, "user_admin")

    await skill_versions.save_draft("f44_keep", "v2", _defn("f44_keep"), "user_admin")
    await skill_versions.request_publish("f44_keep", "v2", "user_admin")
    await customization_service.apply_pending("skill", "f44_keep", True,
                                              decided_by="user_cio")
    assert skill_registry.get("f44_keep") is None
    entry = customization_service.get_override("skill", "f44_keep")
    assert entry["enabled"] is False
    assert entry["overrides"]["version"] == "v2"       # 内容轴确实前进了


async def test_apply_change_rejects_unknown_skill_target(sk_env):
    from app.core.agent.customization import CustomizationError, customization_service

    with pytest.raises(CustomizationError) as e:
        await customization_service.apply_change(
            ctype="skill", target="no_such_skill_anywhere",
            overrides={"action": "set_enabled"}, enabled=False,
            changed_by="user_admin")
    assert "未登记的 Skill" in str(e.value)
    # 出厂技能是合法 target（即使库里没有版本行）
    r = await customization_service.apply_change(
        ctype="skill", target="treasury_daily",
        overrides={"action": "set_enabled", "enabled": False}, enabled=False,
        changed_by="user_admin")
    assert r["status"] == "applied"


async def test_idempotency_key_blocks_replayed_publish_request(sk_env):
    from app.core.agent import skill_versions

    await skill_versions.save_draft("f44_idem", "v1", _defn("f44_idem"), "user_admin")
    first = await skill_versions.request_publish("f44_idem", "v1", "user_admin",
                                                 idempotency_key="k-f44-1")
    second = await skill_versions.request_publish("f44_idem", "v1", "user_admin",
                                                  idempotency_key="k-f44-1")
    assert first["status"] == "pending_approval"
    assert second["status"] == "duplicate", second     # §2.4 防重放


# ===========================================================================
# 5. 导出 / 导入
# ===========================================================================

async def test_export_import_bundle_lands_as_drafts_only(sk_env):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service

    await skill_versions.save_draft("f44_ex", "v1", _defn("f44_ex"), "user_admin")
    await skill_versions.request_publish("f44_ex", "v1", "user_admin")
    await customization_service.apply_pending("skill", "f44_ex", True,
                                              decided_by="user_cio")

    bundle = await skill_versions.export_bundle(include_runtime=False)
    assert bundle["format"] == skill_versions.BUNDLE_FORMAT
    item = next(s for s in bundle["skills"] if s["name"] == "f44_ex")
    assert item["status"] == "stable" and item["version"] == "v1"

    # 改名导入 → 只落草稿，绝不自动发布（跨环境搬运不得绕过本环境审批）
    imported = bundle
    for s in imported["skills"]:
        s["definition"]["name"] = "f44_ex_target"
        s["name"] = "f44_ex_target"
    out = await skill_versions.import_bundle(imported, "user_admin")
    assert out["imported"] >= 1 and not out["failed"]
    assert (await skill_versions.get_definition("f44_ex_target", "v1"))["status"] \
        == "draft"
    assert not (sk_env["overlay"] / "f44_ex_target.yaml").exists()

    # 非法包与非法条目
    with pytest.raises(skill_versions.SkillVersionError):
        await skill_versions.import_bundle({"format": "other"}, "user_admin")
    bad = {"format": skill_versions.BUNDLE_FORMAT, "skills":
           [{"version": "v1", "definition": {"name": "X Bad", "domain": ""}}]}
    res = await skill_versions.import_bundle(bad, "user_admin")
    assert res["imported"] == 0 and len(res["failed"]) == 1


async def test_export_carries_factory_skills_without_version_row(sk_env, tmp_path,
                                                                 monkeypatch):
    """
    出厂技能只存在于出厂 YAML、没有 skill_definitions 流行：不带出来就是空包，
    UAT-F4-2 的"把已有技能搬到另一环境"在没发布过任何东西的环境里根本走不通。
    """
    import yaml

    from app.core.agent import skill_versions
    from app.core.agent.skills import skill_registry

    factory = tmp_path / "factory"
    factory.mkdir()
    (factory / "f44_factory_skill.yaml").write_text(yaml.safe_dump({
        "name": "f44_factory_skill", "display_name": "出厂助手",
        "description": "出厂技能", "domain": "reimbursement",
        "tools": [TOOL_OK], "instructions": "只复述关键要素。",
        "guardrails": {"max_amount": 9}}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(skill_registry, "skills_dir", factory)
    skill_registry._skills.clear()
    skill_registry._dir_mtime = ""

    bundle = await skill_versions.export_bundle()
    item = next((s for s in bundle["skills"] if s["name"] == "f44_factory_skill"), None)
    assert item is not None, bundle
    assert item["status"] == "factory" and item["version"] == "factory"
    assert item["definition"]["tools"] == [TOOL_OK]
    assert item["definition"]["guardrails"] == {"max_amount": 9}

    # 同名已有版本流行时以版本流为准，不重复出现两条
    await skill_versions.save_draft("f44_factory_skill", "v1",
                                    _defn("f44_factory_skill"), "user_admin")
    again = [s for s in (await skill_versions.export_bundle(include_draft=True))["skills"]
             if s["name"] == "f44_factory_skill"]
    assert len(again) == 1 and again[0]["version"] == "v1"

    # 出厂项导入后仍是草稿，发布目录不落文件（搬运不得绕过本环境审批）
    out = await skill_versions.import_bundle(bundle, "user_admin")
    assert out["imported"] >= 1 and not out["failed"]
    row = await skill_versions.get_definition("f44_factory_skill", "factory")
    assert row["status"] == "draft"
    assert not (sk_env["overlay"] / "f44_factory_skill.yaml").exists()


# ===========================================================================
# 6. 运行期消费点
# ===========================================================================

async def test_runtime_applies_only_matching_enabled_skill(sk_env):
    from app.core.agent import skill_versions
    from app.core.agent.customization import customization_service
    from app.core.agent.runtime import AgentRuntime
    from app.core.agent.tools_v2 import ToolContext

    await skill_versions.save_draft("f44_rt", "v1", _defn("f44_rt"), "user_admin")
    await skill_versions.request_publish("f44_rt", "v1", "user_admin")
    await customization_service.apply_pending("skill", "f44_rt", True,
                                              decided_by="user_cio")

    rt = AgentRuntime()
    ctx = ToolContext(user_id="u1", thread_id="t1", extra={"skill": "f44_rt"})
    assert rt._skill_for(ctx, "reimbursement") == "f44_rt"
    assert rt._skill_for(ctx, "treasury") == ""            # 域不匹配不套用
    assert rt._skill_for(ToolContext(user_id="u1"), "reimbursement") == ""
    assert rt._skill_for(ToolContext(user_id="u1", thread_id="t",
                                     extra={"skill": "ghost"}),
                         "reimbursement") == ""            # 未登记 → 按纯域跑

    cfg = rt._skill_cfg("f44_rt")
    assert cfg["tools"] == [TOOL_OK] and "复述" in cfg["instructions"]

    await skill_versions.set_enabled("f44_rt", False, "user_admin")
    assert rt._skill_for(ctx, "reimbursement") == ""


# ===========================================================================
# 7. 审批入口（HTTP 路由层）
# ===========================================================================

async def test_decide_route_publishes_skill_with_correct_approver(sk_env):
    """
    技能发布走的是定制层通用 decide 入口（红线 3 曾在此处把 comment 顶到
    decided_by 位置上）。这里用真实路由函数验证：批准后发布落地，
    且历史行的审批人是**审批人**而不是意见文本。
    """
    from sqlalchemy import select

    from app.api.routes.customizations import DecideRequest, decide_customization
    from app.core.agent import skill_versions
    from app.db.models import customization_versions
    from app.db.repositories.base import session_scope

    await skill_versions.save_draft("f44_dec", "v1", _defn("f44_dec"), "user_admin")
    r = await skill_versions.request_publish("f44_dec", "v1", "user_admin")
    assert r["status"] == "pending_approval"

    out = await decide_customization(
        "skill", "f44_dec", DecideRequest(approved=True, comment="同意发布"),
        current_user={"user_id": "user_chairman"})
    assert out["status"] == "executed", out
    assert (sk_env["overlay"] / "f44_dec.yaml").exists()

    async with session_scope() as session:
        row = (await session.execute(
            select(customization_versions).where(
                customization_versions.c.type == "skill",
                customization_versions.c.target == "f44_dec",
                customization_versions.c.status == "approved_executed")
        )).first()
    assert row is not None
    assert row.approved_by == "user_chairman", row.approved_by
    assert row.comment == "同意发布"


# ===========================================================================
# 8. 路由接线
# ===========================================================================

async def test_skills_admin_routes_are_mounted(sk_env):
    from app.main import app

    paths = {r.path for r in app.routes}
    base = "/api/v1/agent-ops/skills"
    for p in (f"{base}/meta", f"{base}/versions", f"{base}/validate",
              f"{base}/export", f"{base}/import",
              f"{base}/{{name}}", f"{base}/{{name}}/{{version}}",
              f"{base}/{{name}}/{{version}}/draft",
              f"{base}/{{name}}/{{version}}/publish",
              f"{base}/{{name}}/enabled", f"{base}/{{name}}/rollback"):
        assert p in paths, f"缺少路由 {p}"


# ===========================================================================
# 9. 设计器元数据 /skills/meta
# ===========================================================================

async def test_form_metadata_groups_tools_by_agent_domain(sk_env):
    """真机回归：GET /skills/meta 曾 500（ToolSpecV2 没有 .domain，只有 .agent_domain）。

    直接打**真实**注册表——归零注册表会让循环体不执行，正是这行漏测的原因。
    """
    from app.core.agent import tools_v2 as tv2
    from app.core.agent.skill_versions import form_metadata

    specs = tv2.tool_registry_v2.list_specs()
    assert specs, "fixture 未注册治理面工具，本用例失去意义"

    meta = form_metadata()

    expected: dict[str, list[str]] = {}
    for s in specs:
        expected.setdefault(s.agent_domain or "_", []).append(s.name)
    assert meta["tools_by_domain"] == {k: sorted(v) for k, v in sorted(expected.items())}
    assert meta["domains"] == sorted(expected.keys())
    flat = {n for names in meta["tools_by_domain"].values() for n in names}
    assert flat == {s.name for s in specs}, "按域分组不得丢工具"

    assert meta["overlay_dir"] == str(sk_env["overlay"])
    assert set(meta["template"]) == {"name", "display_name", "description", "domain",
                                     "tools", "instructions", "guardrails"}
    assert isinstance(meta["skills"], list)
    assert meta["name_pattern"]


async def test_form_metadata_buckets_unlabeled_domain(sk_env, monkeypatch):
    """未标注归属域的工具落到 "_"，不能让它们在编辑器里静默消失。"""
    from pydantic import BaseModel

    from app.core.agent import tools_v2 as tv2
    from app.core.agent.skill_versions import form_metadata

    class _Args(BaseModel):
        q: str = ""

    async def _noop(ctx, **kw):
        return {}

    reg = tv2.ToolRegistryV2()
    for name, dom in (("meta_b_two", "crm"), ("meta_b_one", "crm"), ("meta_b_unk", "")):
        reg.register(tv2.ToolSpecV2(
            name=name, description=name, handler=_noop, args_model=_Args,
            risk_level=tv2.ToolRisk.READONLY, resource="meta_b", action="read",
            agent_domain=dom))
    monkeypatch.setattr(tv2, "tool_registry_v2", reg)

    meta = form_metadata()
    assert meta["tools_by_domain"] == {"_": ["meta_b_unk"],
                                       "crm": ["meta_b_one", "meta_b_two"]}
    assert meta["domains"] == ["_", "crm"]
