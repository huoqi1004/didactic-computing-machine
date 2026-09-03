"""
数据库迁移脚本
==============

创建数据库持久化层所需的表结构（兼容 PostgreSQL 与 MySQL），包括：

- ``events``       : 事件溯源日志（不可变 append-only）
- ``snapshots``    : 聚合根状态快照（加速事件重放）
- ``audit_logs``   : 审计日志（哈希链防篡改）
- ``sagas``        : Saga 事务实例（跨Agent协作编排）

设计说明
--------
1. **幂等执行**：所有 ``CREATE TABLE`` 使用 ``IF NOT EXISTS``，可安全重复调用。
2. **原生 SQL**：使用 SQLAlchemy ``text()`` 执行原生 DDL，精确控制字段类型与约束。
3. **双后端兼容**：根据 ``mysql_url`` 自动检测后端（PostgreSQL / MySQL），
   生成对应的 DDL（UUID/JSONB/TIMESTAMPTZ → CHAR(36)/JSON/DATETIME(6)）。
4. **索引优化**：按常见查询模式（聚合根ID+版本、事件类型+时间、操作者+时间）建立索引。
5. **降级安全**：所有操作包裹 try/except，数据库不可用时记录警告并返回 False。
"""
from __future__ import annotations

import structlog
from typing import Any, Optional

from app.core import database as db_module
from app.core.config import settings

logger = structlog.get_logger()


# ===========================================================================
# 后端检测
# ===========================================================================

def _is_mysql() -> bool:
    """检测当前数据库后端是否为 MySQL"""
    url = (settings.mysql_url or "").lower()
    return "mysql" in url


def _json_type() -> str:
    return "JSON" if _is_mysql() else "JSONB"


def _uuid_type() -> str:
    return "CHAR(36)" if _is_mysql() else "UUID"


def _timestamp_type() -> str:
    return "DATETIME(6)" if _is_mysql() else "TIMESTAMPTZ"


def _now_func() -> str:
    return "NOW(6)" if _is_mysql() else "NOW()"


def _json_default(val: str) -> str:
    """JSON 默认值表达式，'{}'::jsonb → ('{}')（MySQL 8.0.13+ 需要引号包裹）"""
    if _is_mysql():
        return f"('{val}')"
    return f"'{val}'::jsonb"


def _json_cast(param: str) -> str:
    """CAST 表达式，CAST(:x AS JSONB) → CAST(:x AS JSON)"""
    return f"CAST({param} AS {_json_type()})"


# ===========================================================================
# DDL 语句定义
# ===========================================================================

_UUID = _uuid_type()
_JSON = _json_type()
_TS = _timestamp_type()
_NOW = _now_func()
_JSON_EMPTY_OBJ = _json_default("{}")
_JSON_EMPTY_ARR = _json_default("[]")

# --- 事件溯源日志 ---
_CREATE_EVENTS_TABLE = f"""
CREATE TABLE IF NOT EXISTS events (
    event_id        {_UUID}     PRIMARY KEY,
    event_type      VARCHAR(64) NOT NULL,
    aggregate_id    VARCHAR(128) NOT NULL,
    aggregate_type  VARCHAR(64) NOT NULL,
    event_data      {_JSON}     NOT NULL DEFAULT {_JSON_EMPTY_OBJ},
    agent_id        VARCHAR(128),
    version         INTEGER     NOT NULL,
    timestamp       {_TS}       NOT NULL DEFAULT {_NOW},
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
_CREATE_SNAPSHOTS_TABLE = f"""
CREATE TABLE IF NOT EXISTS snapshots (
    aggregate_id    VARCHAR(128) NOT NULL,
    aggregate_type  VARCHAR(64)  NOT NULL,
    version         INTEGER      NOT NULL,
    state           {_JSON}      NOT NULL DEFAULT {_JSON_EMPTY_OBJ},
    timestamp       {_TS}        NOT NULL DEFAULT {_NOW}
);
"""

_CREATE_SNAPSHOTS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_snapshots_aggregate_version
    ON snapshots (aggregate_id, version DESC);
"""

# --- 审计日志 ---
_CREATE_AUDIT_LOGS_TABLE = f"""
CREATE TABLE IF NOT EXISTS audit_logs (
    entry_id    {_UUID}      PRIMARY KEY,
    actor_did   VARCHAR(128) NOT NULL,
    action      VARCHAR(128) NOT NULL,
    resource    VARCHAR(256) NOT NULL,
    result      VARCHAR(32)  NOT NULL,
    details     {_JSON}      NOT NULL DEFAULT {_JSON_EMPTY_OBJ},
    risk_level  VARCHAR(16)  NOT NULL DEFAULT 'low',
    prev_hash   VARCHAR(128),
    entry_hash  VARCHAR(128),
    timestamp   {_TS}        NOT NULL DEFAULT {_NOW}
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
_CREATE_SAGAS_TABLE = f"""
CREATE TABLE IF NOT EXISTS sagas (
    saga_id     {_UUID}      PRIMARY KEY,
    saga_type   VARCHAR(64)  NOT NULL,
    status      VARCHAR(32)  NOT NULL DEFAULT 'started',
    steps       {_JSON}      NOT NULL DEFAULT {_JSON_EMPTY_ARR},
    current_step INTEGER     NOT NULL DEFAULT 0,
    created_at  {_TS}        NOT NULL DEFAULT {_NOW},
    updated_at  {_TS}        NOT NULL DEFAULT {_NOW}
);
"""

_CREATE_SAGAS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_sagas_status
    ON sagas (status);
CREATE INDEX IF NOT EXISTS ix_sagas_saga_type
    ON sagas (saga_type);
"""

# MySQL 不支持 CREATE INDEX IF NOT EXISTS，去掉 IF NOT EXISTS 子句
# 索引重复创建的错误由 run_migrations 中的语句级异常处理捕获（errno 1061）
if _is_mysql():
    _CREATE_EVENTS_INDEXES = _CREATE_EVENTS_INDEXES.replace("IF NOT EXISTS ", "")
    _CREATE_SNAPSHOTS_INDEXES = _CREATE_SNAPSHOTS_INDEXES.replace("IF NOT EXISTS ", "")
    _CREATE_AUDIT_LOGS_INDEXES = _CREATE_AUDIT_LOGS_INDEXES.replace("IF NOT EXISTS ", "")
    _CREATE_SAGAS_INDEXES = _CREATE_SAGAS_INDEXES.replace("IF NOT EXISTS ", "")

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
                statements = [s.strip() for s in ddl.split(";") if s.strip()]
                for stmt in statements:
                    logger.debug("migrations.executing", sql=stmt[:80])
                    try:
                        await conn.execute(text(stmt))
                    except Exception as stmt_err:
                        err_str = str(stmt_err).lower()
                        # MySQL 不支持 CREATE INDEX IF NOT EXISTS（errno 1061），
                        # 索引已存在时忽略错误，其他错误才抛出
                        if _is_mysql() and ("1061" in err_str or "duplicate key" in err_str):
                            logger.debug("migrations.index_already_exists", sql=stmt[:80])
                            continue
                        raise

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
