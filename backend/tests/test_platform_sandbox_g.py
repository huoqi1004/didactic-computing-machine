"""
平台工具（只读查询）+ 沙箱执行器测试（D-B / D5 预案落地验证）
==============================================================
"""
import asyncio
import os

import pytest
import pytest_asyncio
from app.core.config import settings


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "platform_g.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


# ===========================================================================
# D-B：自研只读查询工具
# ===========================================================================

def test_readonly_query_whitelist_rejects_sensitive_tables():
    from pydantic import ValidationError

    from app.agents.platform.tools import ReadOnlyQueryArgs, _validate_query

    from app.agents.platform.tools import get_allowed_tables

    for table in ("users", "roles", "permissions", "audit_logs",
                  "operation_ledger", "chat_messages", "langgraph_checkpoints"):
        assert table not in get_allowed_tables(), f"敏感表 {table} 不应开放"
        with pytest.raises(ValueError, match="白名单"):
            _validate_query(ReadOnlyQueryArgs(table=table))


def test_readonly_query_rejects_non_select_tokens():
    from pydantic import ValidationError

    from app.agents.platform.tools import ReadOnlyQueryArgs, _validate_query

    for where in ("1=1; DROP TABLE users", "1=1 -- comment",
                  "1=1 UNION SELECT password FROM users", "a INTO OUTFILE '/x'"):
        with pytest.raises(ValueError, match="只读|禁止"):
            _validate_query(ReadOnlyQueryArgs(table="reimbursements", where=where))


def test_readonly_query_forces_limit_cap():
    from pydantic import ValidationError

    from app.agents.platform.tools import ReadOnlyQueryArgs, _validate_query

    with pytest.raises(ValidationError):
        ReadOnlyQueryArgs(table="reimbursements", limit=5000)  # 超上限被 pydantic 拦
    sql = _validate_query(ReadOnlyQueryArgs(table="reimbursements", limit=50))
    assert "LIMIT 50" in sql and sql.startswith("SELECT")


async def test_readonly_query_executes_on_test_db(sqlite_env, monkeypatch):
    from app.agents.platform.tools import erp_readonly_query
    from app.core.agent.tools_v2 import ToolContext
    from app.core.rbac import rbac_engine

    assert sqlite_env is not None  # 建库即本用例前提（Fresh State 空表）
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True)
    # Fresh State 空表断言即可：验证 SQL 构造/执行/行数口径（不依赖种子行）
    r = await erp_readonly_query(
        ToolContext(user_id="u1"), table="reimbursements",
        where="amount > 0", limit=10)
    assert r["total"] == 0 and "LIMIT 10" in r["sql"] and "WHERE amount > 0" in r["sql"]


async def test_readonly_query_denied_without_domain_permission(sqlite_env,
                                                                monkeypatch):
    """按表二次鉴权：platform:read 不等于能读 payroll——敏感域必须单独有读权限。"""
    from app.agents.platform.tools import erp_readonly_query
    from app.core.agent.tools_v2 import ToolContext
    from app.core.rbac import rbac_engine

    grants = {"reimbursement"}
    monkeypatch.setattr(rbac_engine, "check_permission",
                        lambda u, r, a: r in grants)
    with pytest.raises(PermissionError, match="payroll:read"):
        await erp_readonly_query(ToolContext(user_id="u1"), table="payroll_records")
    # 未被拒的域仍可执行（证明门禁按表而非按工具）
    ok = await erp_readonly_query(ToolContext(user_id="u1"),
                                  table="reimbursements", limit=5)
    assert "FROM reimbursements" in ok["sql"]


def test_readonly_whitelist_tables_exist_in_orm():
    """白名单表必须真实存在于 ORM——不存在的表放行只会得到数据库报错。"""
    from app.agents.platform.tools import get_allowed_tables
    from app.db import models

    declared = {v.__tablename__ for v in models.__dict__.values()
                if isinstance(v, type) and hasattr(v, "__tablename__")}
    missing = get_allowed_tables() - declared
    assert not missing, f"白名单表无对应 model: {sorted(missing)}"


async def test_table_name_normalization_before_domain_check(monkeypatch):
    """表名大小写/空白变体必须先归一，再进按表鉴权查表（否则 KeyError 而非拒权）。"""
    from app.agents.platform.tools import erp_readonly_query
    from app.core.agent.tools_v2 import ToolContext
    from app.core.rbac import rbac_engine

    seen: list[str] = []

    def _deny(u, r, a):
        seen.append(f"{r}:{a}")
        return False

    monkeypatch.setattr(rbac_engine, "check_permission", _deny)
    with pytest.raises(PermissionError, match="payroll:read"):
        await erp_readonly_query(ToolContext(user_id="u1"), table=" Payroll_RECORDS ")
    assert seen == ["payroll:read"]


def test_platform_read_is_entry_gate_for_business_roles():
    """platform:read 是"允许调用查询工具"的入口资格：业务角色都要有，且只给 read。

    真机实测：入口只对 chairman/ceo 开放时，cio 经 supervisor 生成周报会被
    整体挡下（白名单 5 张表全报"需要 platform:read"），周报退化成"待补"空壳；
    而按表二次鉴权（上面两个用例）才是真正收数据面的那道闸，放开入口不会放大读面。
    三个专用角色（log_auditor / customization_admin / customization_approver）
    排除在外——它们零业务域读权，且 log_auditor 的"无业务资源"是安全回归不变量。
    """
    from app.core.rbac.seed import (ROLE_PERMISSION_MATRIX,
                                    _PLATFORM_ENTRY_EXCLUDED)

    assert _PLATFORM_ENTRY_EXCLUDED == {
        "log_auditor", "customization_admin", "customization_approver"}
    granted = 0
    for role, matrix in ROLE_PERMISSION_MATRIX.items():
        actions = matrix.get("platform")
        if role in _PLATFORM_ENTRY_EXCLUDED:
            assert not actions, f"专用角色 {role} 不该有 platform 入口"
            continue
        assert actions, f"角色 {role} 缺 platform:read 入口"
        assert "read" in actions
        granted += 1
        if role not in ("chairman", "ceo"):  # 这两个由全域推导获得，本就含写权
            assert actions == {"read"}, f"{role} 的 platform 越出只读入口: {actions}"
    assert granted >= 20, f"入口覆盖过窄：{granted}"
    # 审计与操作分离：log_auditor 仍只有三个可观测资源
    assert set(ROLE_PERMISSION_MATRIX["log_auditor"]) == {
        "operationlog", "operation_rollback", "audit_verify"}


# ===========================================================================
# 工作包二：平台级文档生成工具（Skill 的工具落点）
# ===========================================================================

def test_document_tools_registered_with_governance_triplet():
    """document_generate_* 必须注册成功且写工具三件套齐备（红线：可回滚才可执行）。"""
    import app.agents.platform.document_tools  # noqa: F401
    from app.core.agent.tools_v2 import tool_registry_v2

    for name in ("document_generate_docx", "document_generate_xlsx"):
        spec = tool_registry_v2.get(name)
        assert spec is not None, f"{name} 未注册（lifespan 导入清单缺口）"
        assert spec.risk_level.is_write and spec.agent_domain == "platform"
        assert f"{spec.resource}:{spec.action}" == "document:create"
        assert spec.compensation is not None and spec.effects_fn is not None
        assert spec.idempotent is True


async def test_document_generate_docx_forces_draft_header(tmp_path, monkeypatch):
    """D-C 不可绕过：调用方自带页眉标注一律丢弃，由治理面按真实 trace 重新注入。"""
    import app.agents.platform.document_tools as dts
    from app.core.agent.tools_v2 import ToolContext
    from app.core.config import settings as s

    monkeypatch.setattr(s, "document_output_dir", str(tmp_path))
    ctx = ToolContext(user_id="u1", agent_key="platform", thread_id="t1",
                      trace_id="traceDOCX0001")
    r = await dts.document_generate_docx(
        ctx, template_type="report",
        data={"title": "第三批验收", "report_no": "T-1", "author": "治理面",
              "date": "2026-10-05", "summary": "摘要",
              "sections": [{"heading": "章节一", "content": "内容"}],
              "conclusion": "结论", "header_note": "正式交付物（伪造标注）"})

    assert r["format"] == "docx" and r["size"] > 0
    assert r["path"].startswith(str(tmp_path))
    assert r["download_url"].startswith("/api/v1/document/download/")
    from docx import Document

    header = Document(r["path"]).sections[0].header.paragraphs[0].text
    assert "AI 生成草稿" in header and "traceDOCX0001" in header
    assert "伪造" not in header


async def test_document_compensation_removes_then_noop(tmp_path, monkeypatch):
    """补偿幂等（红线 2）：首轮回滚删文件，重放 no-op 且不报错。"""
    import app.agents.platform.document_tools as dts
    from app.core.agent.tools_v2 import ToolContext, tool_registry_v2
    from app.core.config import settings as s

    monkeypatch.setattr(s, "document_output_dir", str(tmp_path))
    ctx = ToolContext(user_id="u1", trace_id="traceCOMP0002")
    spec = tool_registry_v2.get("document_generate_xlsx")
    r = await spec.handler(ctx, data={"title": "对账", "headers": ["a", "b"],
                                      "rows": [["1", "2"]]})
    assert os.path.exists(r["path"])
    from openpyxl import load_workbook

    cells = [c.value for row in load_workbook(r["path"]).active.iter_rows()
             for c in row if c.value]
    assert any("AI 生成草稿" in str(v) for v in cells)   # xlsx 无页眉，标注落备注行

    first = await spec.compensation(ctx, {}, r)
    second = await spec.compensation(ctx, {}, r)
    assert first["removed"] is True and not os.path.exists(r["path"])
    assert second["removed"] is False and "no-op" in second["reason"]
    # 补偿只碰受控目录：带目录成分的 filename 被剥成 basename
    third = await spec.compensation(ctx, {}, {"filename": "../../etc/passwd"})
    assert third["removed"] is False and not os.path.exists(tmp_path / "passwd")


# ===========================================================================
# D5 预案 §3：沙箱执行器
# ===========================================================================

async def test_sandbox_disabled_by_default():
    """闸门 1：默认禁用（E2 守卫语义延续，D5-B 裁决）。"""
    from app.core.sandbox.executor import sandbox_executor

    r = await sandbox_executor.run("print('hi')")
    assert r.ok is False and "未启用" in (r.error or "")


async def test_sandbox_run_happy_path_when_enabled(sqlite_env, monkeypatch):
    """闸门全通 + 容器通道：mock docker exec 进程（CI 无 docker）。"""
    import asyncio

    from app.core.sandbox.executor import sandbox_executor

    monkeypatch.setattr(settings, "sandbox_enabled", True)

    class _FakeProc:
        returncode = 0

        def __init__(self):
            self.stdin = None

        async def communicate(self, stdin_data):
            assert b"print" in stdin_data
            return (b"hello sandbox", b"")

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    r = await sandbox_executor.run("print('hello sandbox')", timeout_s=5)
    assert r.ok is True and r.stdout == "hello sandbox" and r.exit_code == 0


async def test_sandbox_timeout_kills(sqlite_env, monkeypatch):
    from app.core.sandbox.executor import sandbox_executor

    monkeypatch.setattr(settings, "sandbox_enabled", True)

    class _HangingProc:
        returncode = None

        async def communicate(self, stdin_data):
            await asyncio.sleep(999)

    async def _fake_exec(*args, **kwargs):
        p = _HangingProc()
        p.kill = lambda: None
        return p

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    r = await sandbox_executor.run("while True: pass", timeout_s=1)
    assert r.ok is False and "超时" in r.error


async def test_sandbox_code_size_limit(sqlite_env, monkeypatch):
    from app.core.sandbox.executor import sandbox_executor

    monkeypatch.setattr(settings, "sandbox_enabled", True)
    r = await sandbox_executor.run("x = '" + "a" * 30000 + "'")
    assert r.ok is False and "超限" in r.error


# ===========================================================================
# 工作包二：Skill 启停留痕真的落库
# ===========================================================================

async def test_skill_activation_audit_rows_persist(sqlite_env, monkeypatch):
    """启停留痕写进 audit_logs：activated/denied 各一行，拒绝原因可读。"""
    from sqlalchemy import text

    from app.core.agent.orchestration_service import _audit_skill_activation
    from app.core.agent.tools_v2 import ToolContext
    from app.core.security.audit import audit_chain

    engine = sqlite_env
    # sqlite 无 NOW(6) 默认值兼容——手工建同构表验证写入路径（与 test_trace_g1 同口径）
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                entry_id VARCHAR(64) PRIMARY KEY,
                actor_did VARCHAR(128) NOT NULL,
                action VARCHAR(128) NOT NULL,
                resource VARCHAR(256) NOT NULL,
                result VARCHAR(32) NOT NULL,
                details TEXT NOT NULL,
                risk_level VARCHAR(16) NOT NULL DEFAULT 'low',
                prev_hash VARCHAR(128),
                entry_hash VARCHAR(128),
                trace_id VARCHAR(64),
                business_key VARCHAR(128),
                timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
    monkeypatch.setattr(audit_chain, "_db_available", True, raising=False)
    monkeypatch.setattr(audit_chain, "_db_initialized", True, raising=False)

    ctx = ToolContext(user_id="user_chairman", thread_id="t-skill-audit",
                      trace_id="tr-skill-audit")
    await _audit_skill_activation(ctx, "platform", "weekly_report", "weekly_report", "")
    await _audit_skill_activation(ctx, "platform", "", "weekly_report", "技能已停用")

    async with engine.connect() as conn:
        ok_row = (await conn.execute(text(
            "SELECT resource, result, details FROM audit_logs "
            "WHERE action = 'skill.activated'"))).first()
        deny_row = (await conn.execute(text(
            "SELECT resource, result, details FROM audit_logs "
            "WHERE action = 'skill.activation_denied'"))).first()

    assert ok_row and ok_row[0] == "skill:weekly_report" and ok_row[1] == "success"
    assert deny_row and deny_row[1] == "denied" and "技能已停用" in deny_row[2]
