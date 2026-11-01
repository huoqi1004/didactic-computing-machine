"""
G3 日志查询与审计可视化测试（roadmap-logging-sandbox-2026-10-01 G3.5）
======================================================================

覆盖：
1. 附录 B 权限资源：operationlog:* / audit_verify:run / log_auditor 角色；
2. 查询 API：双键过滤 + 多维过滤 + 归档回落 + 分页（直接调用路由函数）；
3. CSV 导出 + 查询/导出行为自身审计（红线 5，闭环 P1-3）；
4. DB 级哈希链校验：全量/增量 + 篡改当场点名（P2-1，闭环 P0-1 出口）；
5. 告警通道：webhook 失败写死信 + 补推 resolved（P1-4）；
6. 高频越权阈值告警。
"""
import asyncio
import io
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    import app.core.database as database
    from app.core.config import settings

    db_file = tmp_path / "oplogs_g3.db"
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
def _reset_channel():
    from app.core.observability.alerts import alert_channel

    alert_channel._denied_buckets.clear()
    alert_channel._cooldowns.clear()
    alert_channel.stats.update({"raised": 0, "webhook_ok": 0,
                                "webhook_failed": 0, "dead_lettered": 0})
    yield


# ===========================================================================
# 1. 附录 B 权限资源与角色
# ===========================================================================

def test_seed_operationlog_permissions_and_role():
    from app.core.rbac.seed import (ROLE_PERMISSION_MATRIX, SEED_PERMISSIONS,
                                    SEED_ROLES)

    pids = {p["permission_id"] for p in SEED_PERMISSIONS}
    for pid in ("operationlog:read", "operationlog:export",
                "operation_rollback:read", "operation_rollback:execute",
                "audit_verify:run"):
        assert pid in pids, f"缺少权限点 {pid}"
    roles = {r["role_code"] for r in SEED_ROLES}
    assert "log_auditor" in roles
    assert "read" in ROLE_PERMISSION_MATRIX["log_auditor"]["operationlog"]
    assert "run" in ROLE_PERMISSION_MATRIX["log_auditor"]["audit_verify"]
    assert "execute" in ROLE_PERMISSION_MATRIX["cio"]["operation_rollback"]
    # 审计与操作分离：log_auditor 无任何业务资源权限
    assert set(ROLE_PERMISSION_MATRIX["log_auditor"].keys()) == {
        "operationlog", "operation_rollback", "audit_verify"}


# ===========================================================================
# 2/3. 查询 API 与导出（直接调用路由函数）
# ===========================================================================

async def _seed_oplog_rows(engine):
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(3):
        rows.append({
            "ts": (now - timedelta(minutes=i)).isoformat(sep=" "),
            "actor": "user_admin" if i < 2 else "user_cfo",
            "source": "http", "action": f"op.a{i}", "resource": "r",
            "result": "success", "risk_level": "low" if i else "high",
            "trace_id": "e" * 32 if i == 0 else "",
            "business_key": "RE-G3-1" if i == 0 else "",
            "request_id": None, "details": '{"k":1}',
        })
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO operation_logs (ts, actor, source, action, resource, result,"
            " risk_level, trace_id, business_key, request_id, details) "
            "VALUES (:ts,:actor,:source,:action,:resource,:result,:risk_level,"
            ":trace_id,:business_key,:request_id,:details)"), rows)
    return rows


async def _q(**kw):
    """直接调用查询路由（显式传全参数，绕过 FastAPI Query 默认值对象）。"""
    from app.api.routes.operation_logs import query_operation_logs

    params = dict(business_key="", trace_id="", actor="", action="", source="",
                  result="", risk_level="", start=None, end=None, page=1, limit=50)
    params.update(kw)
    return await query_operation_logs(current_user={"user_id": "user_admin"}, **params)


async def test_query_api_dual_key_filters(sqlite_env):
    await _seed_oplog_rows(sqlite_env)

    # business_key 第一入口
    r = await _q(business_key="RE-G3-1")
    assert r["total"] == 1 and r["items"][0]["action"] == "op.a0"
    assert r["items"][0]["trace_id"] == "e" * 32
    # trace_id 二等入口
    r2 = await _q(trace_id="e" * 32)
    assert r2["total"] == 1
    # 普通过滤
    r3 = await _q(actor="user_cfo")
    assert r3["total"] == 1 and r3["items"][0]["action"] == "op.a2"
    r4 = await _q(risk_level="high")
    assert r4["total"] == 1
    # 分页
    r5 = await _q(limit=2, page=1)
    assert len(r5["items"]) == 2 and r5["total"] == 3


async def test_query_api_archive_fallback(sqlite_env):
    # 仅归档表有该单据（留存分层后链路仍可还原）
    async with sqlite_env.begin() as conn:
        await conn.execute(text(
            "INSERT INTO operation_logs_archive (ts, actor, source, action, resource,"
            " result, risk_level, trace_id, business_key, request_id, details) "
            "VALUES (:ts,'user_admin','http','op.old','', 'success','low',"
            "'f'||'0'*31||'1','RE-G3-ARC',NULL,'{}')"),
            {"ts": "2026-09-01T00:00:00+00:00"})
    r = await _q(business_key="RE-G3-ARC")
    assert r["total"] == 1 and r["table"] == "archive"


async def test_export_csv_and_self_audit(sqlite_env):
    from app.api.routes.operation_logs import export_operation_logs
    from app.core.security.audit import audit_chain

    await _seed_oplog_rows(sqlite_env)
    resp = await export_operation_logs(
        business_key="RE-G3-1", trace_id="", actor="", action="", source="",
        result="", risk_level="", start=None, end=None,
        current_user={"user_id": "user_admin"})
    body = b""
    async for chunk in resp.body_iterator:  # type: ignore[attr-defined]
        body += chunk if isinstance(chunk, bytes) else str(chunk).encode()
    lines = body.decode().strip().splitlines()
    assert lines[0].startswith("id,ts,actor") and len(lines) == 2

    # 自身审计（红线 5）：导出行为进审计链，含过滤条件与行数
    export_entries = [e for e in audit_chain._entries
                      if e.action == "operationlog.export"]
    assert export_entries and export_entries[-1].details["rows"] == 1
    assert export_entries[-1].business_key == "RE-G3-1"  # 追踪键继承


# ===========================================================================
# 4. DB 级哈希链校验（P2-1：篡改当场点名）
# ===========================================================================

async def _mk_audit_db(engine):
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
                timestamp DATETIME NOT NULL
            )
        """))


async def test_verify_chain_detects_tampering(sqlite_env, monkeypatch):
    """篡改库内审计条目 → 手动全量校验当场点名（P2-1 / UAT-G3-1 核心）。"""
    from app.core.security.audit import audit_chain

    await _mk_audit_db(sqlite_env)
    monkeypatch.setattr(audit_chain, "_db_available", True, raising=False)
    monkeypatch.setattr(audit_chain, "_db_initialized", True, raising=False)
    ids = []
    for i in range(3):
        ids.append(await audit_chain.log(
            actor_did="user_admin", action=f"tool.op{i}.executed", resource="t:t",
            result="success", details={"i": i}, risk_level="low",
            trace_id="a" * 32, business_key=f"RE-V{i}"))
    monkeypatch.setattr(audit_chain, "_db_available", False, raising=False)

    # 干净链：全量校验通过
    report = await audit_chain.verify_chain(full=True)
    assert report["status"] == "ok" and report["checked"] >= 3

    # 增量校验（无新数据）通过
    report2 = await audit_chain.verify_chain(full=False)
    assert report2["status"] == "ok"

    # 篡改中间条目的 details → 全量校验当场点名
    async with sqlite_env.begin() as conn:
        await conn.execute(text(
            "UPDATE audit_logs SET details = '{\"i\": 999}' WHERE entry_id = :e"
        ), {"e": ids[1]})
    report3 = await audit_chain.verify_chain(full=True)
    assert report3["status"] == "broken"
    assert report3["broken_at"] == ids[1]
    assert report3["expected"] != report3["actual"]

    # 断链告警已发出（G3.4 联动）
    from app.core.observability.alerts import alert_channel
    assert alert_channel.stats["raised"] >= 1


# ===========================================================================
# 5/6. 告警通道（P1-4）
# ===========================================================================

async def test_webhook_failure_dead_letter_and_retry(sqlite_env, monkeypatch):
    import httpx

    import app.core.observability.alerts as alerts_mod
    from app.core.config import settings
    from app.core.observability.alerts import alert_channel

    monkeypatch.setattr(settings, "alert_webhook_url", "http://hook.invalid/x")

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("net down")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    alert_id = await alert_channel.raise_alert(
        rule="test_rule", title="测试告警", severity="warning",
        details={"k": "v"})
    assert alert_id and alert_channel.stats["dead_lettered"] == 1

    async with sqlite_env.connect() as conn:
        row = (await conn.execute(text(
            "SELECT alert_id, resolved, error FROM alert_dead_letters"
        ))).first()
    assert row[0] == alert_id and row[1] == 0 and "net down" in row[2]

    # 恢复后补推 → resolved
    class _Ok(_Boom):
        async def post(self, *a, **k):
            class _Resp:
                status_code = 200

            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Ok)
    retried = await alert_channel.retry_dead_letters()
    assert retried == 1
    async with sqlite_env.connect() as conn:
        resolved = (await conn.execute(text(
            "SELECT resolved FROM alert_dead_letters"))).scalar()
    assert resolved == 1


async def test_high_freq_denied_alert(sqlite_env, monkeypatch):
    from app.core.observability.alerts import note_permission_denied

    for _ in range(10):
        note_permission_denied("user_bad", "treasury", "create")
    await asyncio.sleep(0.1)  # 等告警 task 执行
    from app.core.observability.alerts import alert_channel

    assert alert_channel.stats["raised"] == 1
    # 冷却期内重复触顶不再轰炸
    for _ in range(10):
        note_permission_denied("user_bad", "treasury", "create")
    await asyncio.sleep(0.05)
    assert alert_channel.stats["raised"] == 1
