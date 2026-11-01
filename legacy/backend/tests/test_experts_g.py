"""
M1 专家体系内核测试（PRD expert-team-orchestration v1.0 · FR-E1/E2/E3/E4）
==========================================================================
"""
import json

import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "experts_g.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


@pytest.fixture(autouse=True)
def _pin_rules(monkeypatch):
    monkeypatch.setattr(settings, "llm_enabled", False)
    monkeypatch.setattr(settings, "sandbox_enabled", False)


# ===========================================================================
# FR-E1/E3：出厂模板全量有效
# ===========================================================================

def test_eight_expert_templates_all_valid(sqlite_env):
    """FR-E3：8 个企业专家模板全部通过校验（技能/工具引用真实存在）。"""
    import app.agents.platform.tools  # noqa: F401  （工具注册）

    from app.core.agent.experts import expert_registry

    items = expert_registry.list_experts()
    assert len(items) >= 8
    invalid = [x for x in items if not x["valid"]]
    assert not invalid, f"存在无效专家档案: {invalid}"
    domains = {x["domain"] for x in items}
    assert {"project_supervisor", "accounting", "procurement", "sales",
            "risk", "legal", "hr", "admin"} <= domains


def test_expert_validation_rejects_unknown_refs(sqlite_env, tmp_path, monkeypatch):
    """FR-E1 校验器：引用不存在的技能/工具 → invalid_reason 点名。"""
    import app.agents.platform.tools  # noqa: F401

    from app.core.agent.experts import expert_registry

    bad_dir = tmp_path / "experts_bad"
    bad_dir.mkdir()
    (bad_dir / "bad_expert.yaml").write_text(
        "name: bad_expert\ndisplay_name: 坏档案\ndomain: test\n"
        "skills:\n  - 不存在的技能\ntools:\n  - 不存在的工具\n",
        encoding="utf-8")
    monkeypatch.setattr(type(expert_registry), "_dirs",
                        lambda self: [(bad_dir, "overlay")])
    expert_registry._scanned_mtime = None  # 强制重扫
    try:
        e = expert_registry.get("bad_expert")
        assert e is not None and not e.valid
        assert "未注册技能" in e.invalid_reason
        assert "未注册工具" in e.invalid_reason
    finally:
        expert_registry._scanned_mtime = None


# ===========================================================================
# FR-E2：目录发现 + 详情（含建议模式标注）
# ===========================================================================

async def test_expert_discovery_and_detail(sqlite_env):
    import app.agents.platform.tools  # noqa: F401

    from app.core.agent.experts import expert_registry

    items = expert_registry.list_experts(domain="project_supervisor")
    assert len(items) == 1
    assert items[0]["name"] == "supervision_expert"
    assert "supervision_plan" in items[0]["skills"]

    detail = expert_registry.get("supervision_expert").to_detail()
    assert "五大监理阶段" in detail["methodology"]
    assert detail["budget_daily"] > 0
    assert detail["checks"]


async def test_expert_kpi_aggregation(sqlite_env):
    """FR-E4：KPI 从操作日志按绑定域聚合（calls/tokens/degraded）。"""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.core.agent.experts import expert_registry
    from app.core.observability.operation_log import log_operation

    engine = sqlite_env
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS operation_logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts DATETIME, "
            "actor VARCHAR(128), source VARCHAR(32), action VARCHAR(128), "
            "resource VARCHAR(256), result VARCHAR(32), risk_level VARCHAR(16), "
            "trace_id VARCHAR(64), business_key VARCHAR(128), "
            "request_id VARCHAR(64), details TEXT)"))

    # 经统一通道写入两条 accounting 域交互（1 正常 1 降级）
    from app.core.observability.operation_log import log_operation, operation_log_buffer
    from app.core.observability.trace import bind_trace

    bind_trace(trace_id="f" * 32)
    for degraded in (False, True):
        await log_operation(
            actor="finance_expert", action="llm.interaction",
            resource="llm:accounting", result="success",
            details={"domain": "accounting", "tokens": 500,
                     "degraded": degraded, "fulltext": False},
            source="agent")

    from app.core.observability.operation_log import operation_log_buffer

    await operation_log_buffer.flush_once()

    kpi = await expert_registry.kpi(expert_registry.get("__missing__") or
                              type("E", (), {"name": "x", "domain": "accounting",
                                             "kpi": {}})(),
                              days=30)
    assert kpi["available"] is True
    assert kpi["calls"] == 2 and kpi["tokens"] == 1000 and kpi["degraded"] == 1


# ===========================================================================
# FR-E2：目录 API（路由函数直调）
# ===========================================================================

async def test_experts_api_endpoints(sqlite_env):
    from app.api.routes.experts import get_expert, list_experts

    me = {"user_id": "user_admin"}
    r = await list_experts(domain="", current_user=me)
    assert r["total"] >= 8

    r2 = await get_expert("supervision_expert", days=30, current_user=me)
    assert r2["name"] == "supervision_expert"
    assert "methodology" in r2 and "kpi_window" in r2
    assert r2["advisory_mode"] is True  # 测试环境无 orchestrator 注册 → 建议模式
