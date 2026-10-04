"""
平台工具（只读查询）+ 沙箱执行器测试（D-B / D5 预案落地验证）
==============================================================
"""
import asyncio

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
    from sqlalchemy import text

    from app.agents.platform.tools import erp_readonly_query
    from app.core.agent.tools_v2 import ToolContext

    engine = sqlite_env
    # Fresh State 空表断言即可：验证 SQL 构造/执行/行数口径（不依赖种子行）
    r = await erp_readonly_query(
        ToolContext(user_id="u1"), table="reimbursements",
        where="amount > 0", limit=10)
    assert r["total"] == 0 and "LIMIT 10" in r["sql"] and "WHERE amount > 0" in r["sql"]


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
