"""
保留策略与审计链续接测试（评估报告 B-03/D-05 / 路线图任务 2.7、2.8）
====================================================================

使用 sqlite 验证：
1. purge_expired_records 按保留天数清理 events/snapshots/audit_logs，
   未过期数据保留、审计保留期长于事件；
2. 清理幂等：重复执行不再删除；
3. restore_chain_from_db 从库中恢复哈希链尾，重启后新条目接续历史链。
"""
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.security.audit import AuditChain
from app.core.maintenance.retention import purge_expired_records


@pytest_asyncio.fixture
async def sqlite_db(tmp_path, monkeypatch):
    """临时 sqlite 库 + 三张原生表 + 全局引擎重置。"""
    import app.core.database as database

    db_file = tmp_path / "retention_test.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None

    # 建表（与 migrations.py 的原生表对齐的 sqlite 兼容 DDL）
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE events (
                event_id VARCHAR(64) PRIMARY KEY,
                event_type VARCHAR(64) NOT NULL,
                aggregate_id VARCHAR(64),
                aggregate_type VARCHAR(64),
                event_data TEXT,
                agent_id VARCHAR(64),
                security_level VARCHAR(32),
                timestamp DATETIME NOT NULL
            )
        """))
        await conn.execute(text("""
            CREATE TABLE snapshots (
                snapshot_id VARCHAR(64) PRIMARY KEY,
                agent_id VARCHAR(64) NOT NULL,
                state_version INTEGER NOT NULL,
                state_data TEXT,
                timestamp DATETIME NOT NULL
            )
        """))
        await conn.execute(text("""
            CREATE TABLE audit_logs (
                entry_id VARCHAR(64) PRIMARY KEY,
                actor_did VARCHAR(128) NOT NULL,
                action VARCHAR(128) NOT NULL,
                resource VARCHAR(256),
                result VARCHAR(32),
                details TEXT,
                risk_level VARCHAR(16),
                prev_hash VARCHAR(64),
                entry_hash VARCHAR(64),
                timestamp DATETIME NOT NULL
            )
        """))
    yield engine
    await database.close_db()


def _ts(days_ago: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


async def _count(engine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()


async def test_purge_expired_records_by_retention(sqlite_db, monkeypatch):
    """过期事件被清理，未过期事件与审计数据按各自保留期保留。"""
    engine = sqlite_db
    monkeypatch.setattr(settings, "event_retention_days", 7)
    monkeypatch.setattr(settings, "snapshot_retention_days", 30)
    monkeypatch.setattr(settings, "audit_retention_days", 90)

    async with engine.begin() as conn:
        # events：8 天前（应删）+ 1 天前（应留）
        await conn.execute(text(
            "INSERT INTO events (event_id, event_type, aggregate_id, aggregate_type, "
            "event_data, agent_id, security_level, timestamp) VALUES "
            "('e_old', 'TEST', 'a1', 'agg', '{}', 'agent', 'internal', :ts), "
            "('e_new', 'TEST', 'a2', 'agg', '{}', 'agent', 'internal', :ts2)"
        ), {"ts": _ts(8), "ts2": _ts(1)})
        # snapshots：40 天前（应删，保留期 30 天）+ 5 天前（应留）
        await conn.execute(text(
            "INSERT INTO snapshots (snapshot_id, agent_id, state_version, state_data, "
            "timestamp) VALUES "
            "('s_old', 'agent', 1, '{}', :ts), ('s_new', 'agent', 2, '{}', :ts2)"
        ), {"ts": _ts(40), "ts2": _ts(5)})
        # audit_logs：100 天前（应删，保留期 90 天）+ 80 天前（应留）
        await conn.execute(text(
            "INSERT INTO audit_logs (entry_id, actor_did, action, resource, result, "
            "details, risk_level, prev_hash, entry_hash, timestamp) VALUES "
            "('l_old', 'did', 'act', 'res', 'success', '{}', 'low', NULL, 'h1', :ts), "
            "('l_new', 'did', 'act', 'res', 'success', '{}', 'low', 'h1', 'h2', :ts2)"
        ), {"ts": _ts(100), "ts2": _ts(80)})

    result = await purge_expired_records(engine)

    assert result["events_deleted"] == 1
    assert result["snapshots_deleted"] == 1
    assert result["audit_logs_deleted"] == 1
    assert await _count(engine, "events") == 1
    assert await _count(engine, "snapshots") == 1
    assert await _count(engine, "audit_logs") == 1

    # 幂等：再跑一次无删除
    again = await purge_expired_records(engine)
    assert again["events_deleted"] == 0
    assert again["snapshots_deleted"] == 0
    assert again["audit_logs_deleted"] == 0


async def test_audit_retention_longer_than_events(sqlite_db, monkeypatch):
    """审计保留期配置必须长于事件保留期（审计合规）。"""
    assert settings.audit_retention_days > settings.event_retention_days


async def test_restore_chain_from_db(sqlite_db, monkeypatch):
    """重启后从 audit_logs 恢复链尾：新条目的 prev_hash 接续库中最后一条。"""
    engine = sqlite_db
    last_hash = "a" * 64

    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO audit_logs (entry_id, actor_did, action, resource, result, "
            "details, risk_level, prev_hash, entry_hash, timestamp) VALUES "
            "('l1', 'did:u1', 'user.login', 'auth', 'success', '{}', 'low', NULL, :h1, :t1), "
            "('l2', 'did:u1', 'user.login', 'auth', 'success', '{}', 'low', :h1, :h2, :t2)"
        ), {"h1": "b" * 64, "h2": last_hash, "t1": _ts(2), "t2": _ts(1)})

    chain = AuditChain()
    chain._db_available = True

    restored = await chain.restore_chain_from_db()
    assert restored is True
    assert chain._last_hash == last_hash

    # 恢复后的链尾写入：新条目 prev_hash 接续
    new_entry_id = await chain.log(
        actor_did="did:u2", action="user.login", resource="auth",
        result="success", details={"k": "v"},
    )
    assert new_entry_id
    assert chain._entries[-1].prev_hash == last_hash, "重启后新审计条目应接续历史链尾"


async def test_restore_chain_empty_db(sqlite_db):
    """全新库（无审计数据）：不恢复，从零起链是正确行为。"""
    chain = AuditChain()
    chain._db_available = True
    restored = await chain.restore_chain_from_db()
    assert restored is False
    assert chain._last_hash is None


async def test_audit_memory_entries_capped():
    """内存审计条目有上限（长期运行不膨胀），超出淘汰最旧条目。"""
    chain = AuditChain()
    chain._max_memory_entries = 50
    for i in range(60):
        await chain.log(
            actor_did="did:test", action=f"action.{i}", resource="r",
            result="success", details={"i": i},
        )
    assert len(chain._entries) == 50
    # 最旧的条目（action.0~9）已被淘汰
    assert chain._entries[0].action == "action.10"
