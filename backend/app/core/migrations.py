"""
数据库迁移脚本
==============

创建 PostgreSQL 持久化层所需的表结构，包括：

- ``events``       : 事件溯源日志（不可变 append-only）
- ``snapshots``    : 聚合根状态快照（加速事件重放）
- ``audit_logs``   : 审计日志（哈希链防篡改）
- ``sagas``        : Saga 事务实例（跨Agent协作编排）

设计说明
--------
1. **幂等执行**：所有 ``CREATE TABLE`` 使用 ``IF NOT EXISTS``，可安全重复调用。
2. **原生 SQL**：使用 SQLAlchemy ``text()`` 执行原生 DDL，精确控制字段类型与约束。
3. **JSONB 优先**：JSON 字段统一使用 PostgreSQL 原生 ``JSONB``，支持结构化查询与索引。
4. **索引优化**：按常见查询模式（聚合根ID+版本、事件类型+时间、操作者+时间）建立索引。
5. **降级安全**：所有操作包裹 try/except，数据库不可用时记录警告并返回 False。
"""
from __future__ import annotations

import structlog
from typing import Any, Optional

from app.core import database as db_module

logger = structlog.get_logger()


# ===========================================================================
# DDL 语句定义
# ===========================================================================

# --- 事件溯源日志 ---
_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    event_id        UUID        PRIMARY KEY,
    event_type      VARCHAR(64) NOT NULL,
    aggregate_id    VARCHAR(128) NOT NULL,
    aggregate_type  VARCHAR(64) NOT NULL,
    event_data      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    agent_id        VARCHAR(128),
    version         INTEGER     NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prev_hash       VARCHAR(128),
    event_hash      VARCHAR(128)
);
"""

_CREATE_EVENTS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_events_aggregate_version
    ON events (aggregate_id, version);
CREATE INDEX IF NOT EXISTS ix_events_event_type
    ON events (event_type);
CREATE INDEX IF NOT EXISTS ix_events_type_timestamp
    ON events (event_type, timestamp);
CREATE INDEX IF NOT EXISTS ix_events_event_hash
    ON events (event_hash);
"""

# --- 聚合根快照 ---
_CREATE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS snapshots (
    aggregate_id    VARCHAR(128) NOT NULL,
    aggregate_type  VARCHAR(64)  NOT NULL,
    version         INTEGER      NOT NULL,
    state           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    timestamp       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

_CREATE_SNAPSHOTS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_snapshots_aggregate_version
    ON snapshots (aggregate_id, version DESC);
"""

# --- 审计日志 ---
_CREATE_AUDIT_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_logs (
    entry_id    UUID         PRIMARY KEY,
    actor_did   VARCHAR(128) NOT NULL,
    action      VARCHAR(128) NOT NULL,
    resource    VARCHAR(256) NOT NULL,
    result      VARCHAR(32)  NOT NULL,
    details     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    risk_level  VARCHAR(16)  NOT NULL DEFAULT 'low',
    prev_hash   VARCHAR(128),
    entry_hash  VARCHAR(128),
    timestamp   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

_CREATE_AUDIT_LOGS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_timestamp
    ON audit_logs (actor_did, timestamp DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action
    ON audit_logs (action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_risk_level
    ON audit_logs (risk_level);
CREATE INDEX IF NOT EXISTS ix_audit_logs_entry_hash
    ON audit_logs (entry_hash);
"""

# --- Saga 事务 ---
_CREATE_SAGAS_TABLE = """
CREATE TABLE IF NOT EXISTS sagas (
    saga_id     UUID         PRIMARY KEY,
    saga_type   VARCHAR(64)  NOT NULL,
    status      VARCHAR(32)  NOT NULL DEFAULT 'started',
    steps       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    current_step INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

_CREATE_SAGAS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_sagas_status
    ON sagas (status);
CREATE INDEX IF NOT EXISTS ix_sagas_saga_type
    ON sagas (saga_type);
"""

# 所有 DDL 语句按依赖顺序排列
_ALL_DDL: list[str] = [
    _CREATE_EVENTS_TABLE,
    _CREATE_EVENTS_INDEXES,
    _CREATE_SNAPSHOTS_TABLE,
    _CREATE_SNAPSHOTS_INDEXES,
    _CREATE_AUDIT_LOGS_TABLE,
    _CREATE_AUDIT_LOGS_INDEXES,
    _CREATE_SAGAS_TABLE,
    _CREATE_SAGAS_INDEXES,
]


# ===========================================================================
# 迁移执行
# ===========================================================================

async def run_migrations(engine: Optional[Any] = None) -> bool:
    """
    执行全部数据库迁移（建表 + 建索引）。

    参数
    ----
    engine : AsyncEngine, optional
        已初始化的 SQLAlchemy 异步引擎。若为 ``None``，则从 ``database`` 模块获取。

    返回
    ----
    bool
        ``True`` 表示迁移成功；``False`` 表示数据库不可用或迁移失败。
    """
    if engine is None:
        engine = db_module.get_engine()

    if engine is None:
        logger.warning("migrations.skipped_no_engine")
        return False

    from sqlalchemy import text

    try:
        async with engine.begin() as conn:
            for ddl in _ALL_DDL:
                # 分号分隔的多条语句需要拆分执行
                statements = [s.strip() for s in ddl.split(";") if s.strip()]
                for stmt in statements:
                    logger.debug("migrations.executing", sql=stmt[:80])
                    await conn.execute(text(stmt))

        logger.info("migrations.completed", tables=["events", "snapshots", "audit_logs", "sagas"])
        return True

    except Exception as e:
        logger.warning("migrations.failed", error=str(e))
        return False


async def init_and_migrate() -> bool:
    """
    便捷方法：初始化数据库连接 + 执行迁移。

    返回 ``True`` 表示初始化并迁移成功；``False`` 表示数据库不可用。
    此函数不会抛出异常，适合在应用启动时调用。
    """
    try:
        engine = await db_module.init_db()
        if engine is None:
            logger.warning("migrations.init_failed_db_unavailable")
            return False
        return await run_migrations(engine)
    except Exception as e:
        logger.warning("migrations.init_and_migrate_failed", error=str(e))
        return False


def get_table_schemas() -> dict[str, str]:
    """
    返回各表的 DDL 定义（供文档展示与调试使用）。

    返回 ``{table_name: ddl_statement}`` 字典。
    """
    return {
        "events": _CREATE_EVENTS_TABLE.strip(),
        "snapshots": _CREATE_SNAPSHOTS_TABLE.strip(),
        "audit_logs": _CREATE_AUDIT_LOGS_TABLE.strip(),
        "sagas": _CREATE_SAGAS_TABLE.strip(),
    }
