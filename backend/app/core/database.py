"""
数据库连接管理（统一 MySQL + Redis）
======================================

基于 SQLAlchemy 2.0 async + aiomysql 提供 MySQL 异步连接池，
基于 redis.asyncio 提供 Redis 异步连接池。

提供：
- MySQL 异步引擎（``AsyncEngine``）懒加载单例
- MySQL 异步会话工厂（``async_sessionmaker``）
- Redis 异步连接池（``redis.asyncio.Redis``）
- FastAPI 依赖注入函数 ``get_db()`` 和 ``get_redis()``
- 生命周期管理 ``init_db()`` / ``close_db()`` / ``init_redis()`` / ``close_redis()``
- 健康检查 ``check_db_connection()`` / ``check_redis_connection()``

设计要点
--------
1. **懒加载**：模块导入时不创建连接，避免未安装 ``aiomysql`` / ``redis`` 时报错；
   只有在实际调用 ``init_db()`` / ``init_redis()`` 时才创建。
2. **降级友好**：所有初始化操作包裹 try/except，DB/Redis 不可用时返回 None，
   调用方可据此降级到内存模式。
3. **配置驱动**：从 ``config.py`` 的 ``settings.mysql_url`` 和 ``settings.redis_url`` 读取。
"""
from __future__ import annotations

import structlog
from typing import AsyncGenerator, Optional, Any

from app.core.config import settings

logger = structlog.get_logger()


# ===========================================================================
# MySQL 连接管理
# ===========================================================================

# 模块级单例：仅在显式初始化后赋值
_engine: Optional[Any] = None          # AsyncEngine
_session_factory: Optional[Any] = None  # async_sessionmaker[AsyncSession]


def _build_engine():
    """
    根据配置构建 MySQL 异步引擎。

    依赖 ``aiomysql`` 驱动（通过 ``mysql+aiomysql://`` URL 隐式加载）。
    若未安装 aiomysql，会在创建引擎时抛出 ``ModuleNotFoundError``，
    调用方应捕获并降级到内存后端。
    """
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        async_sessionmaker,
        create_async_engine,
    )

    engine_kwargs: dict[str, Any] = {"echo": settings.db_echo}
    if "mysql" in (settings.mysql_url or "").lower():
        # MySQL 连接池参数；sqlite 等其他后端的池实现不接受这些参数
        engine_kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
            # 注意：pool_pre_ping 与 aiomysql 的 ping() 签名不兼容，
            # 使用 pool_recycle 代替，定期回收连接避免使用已断开的连接
        )

    engine = create_async_engine(settings.mysql_url, **engine_kwargs)

    # 日志中隐藏密码
    safe_url = settings.mysql_url
    if ":" in safe_url and "@" in safe_url:
        # 将 mysql+aiomysql://user:pass@host -> mysql+aiomysql://***@host
        protocol_end = safe_url.index("://") + 3
        at_index = safe_url.index("@")
        safe_url = safe_url[:protocol_end] + "***@" + safe_url[at_index + 1:]

    logger.info(
        "database.engine_created",
        url=safe_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    return engine


async def init_db():
    """
    初始化 MySQL 引擎与会话工厂。

    幂等：重复调用不会重复创建引擎。
    返回已创建（或已存在）的 ``AsyncEngine`` 实例。
    若创建失败（驱动缺失、连接不通），返回 ``None`` 并记录警告。
    """
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker
        _engine = _build_engine()
        _session_factory = async_sessionmaker(
            bind=_engine,
            expire_on_commit=False,
            autoflush=False,
        )
        logger.info("database.initialized", backend="mysql")
        return _engine
    except ModuleNotFoundError as e:
        logger.warning("database.driver_missing", error=str(e), driver="aiomysql")
        _engine = None
        _session_factory = None
        return None
    except Exception as e:
        logger.warning("database.init_failed", error=str(e), backend="mysql")
        _engine = None
        _session_factory = None
        return None


async def close_db() -> None:
    """关闭并释放 MySQL 引擎（应用退出时调用）"""
    global _engine, _session_factory
    if _engine is not None:
        try:
            await _engine.dispose()
            logger.info("database.closed", backend="mysql")
        except Exception as e:
            logger.warning("database.close_failed", error=str(e), backend="mysql")
    _engine = None
    _session_factory = None


def get_engine():
    """
    获取已初始化的 MySQL 异步引擎。

    若未调用 ``init_db()``，返回 ``None``（不自动创建，避免同步上下文中的副作用）。
    """
    return _engine


def _is_mysql_engine(engine: Any = None) -> bool:
    """检测引擎是否为 MySQL 后端（用于 SQL 方言切换）"""
    url = (settings.mysql_url or "").lower()
    return "mysql" in url


def get_session_factory():
    """获取 MySQL 会话工厂（需先 ``init_db``），未初始化返回 ``None``"""
    return _session_factory


async def get_db() -> AsyncGenerator[Any, None]:
    """
    FastAPI 依赖注入：提供一个 MySQL 异步会话，请求结束自动关闭。

    用法::

        @app.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...

    若数据库未初始化或不可用，yield ``None``，调用方应处理此情况。
    异常处理：会话发生异常时自动回滚。
    """
    if _session_factory is None:
        logger.debug("database.get_db_no_factory")
        yield None
        return

    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_db_connection() -> bool:
    """
    MySQL 健康检查：尝试建立一次连接并执行 ``SELECT 1``。

    返回 ``True`` 表示连接正常；``False`` 表示连接失败（含驱动缺失）。
    """
    try:
        from sqlalchemy import text
        engine = await init_db()
        if engine is None:
            return False
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("database.connection_failed", error=str(e), backend="mysql")
        return False


async def bootstrap_schema() -> dict:
    """
    建库兜底（评估报告 C-01）：检查目标库的表数量，空库时用 ORM 元数据
    ``Base.metadata.create_all`` 一次性建出全部业务表。

    背景：全仓库此前没有任何有效的建表路径（alembic 迁移为空壳、
    migrations.py 仅 4 张系统表、init-db.sql 为旧版遗留表），
    全新环境部署后业务表不存在，系统会"静默降级"到内存模式。

    策略：以 ``app/db/models.py`` 为唯一事实源，计算「ORM 期望表」与
    「库中实际表」的差集，只建缺失的表（``create_all`` 默认
    ``checkfirst=True``，已存在的表不会被修改）；再对已存在的表做
    **列级差集**（仅 ``ADD COLUMN`` 增量补列，如 ``users.must_change_password``），
    破坏性变更仍须走 alembic 迁移：

    - 表齐备：跳过，不动存量结构
    - 缺表：仅补建缺失部分，并记录日志（含缺失清单，便于排查漂移）
    - 表在缺列：增量补列，记录补列清单

    说明：早期实现以「库是否为空」为判据，导致挂载了 init-db.sql 的
    环境（库里有 5 张与 ORM 无关的遗留表）被误判为非空而跳过建表，
    业务表缺失后系统静默降级到内存模式。改为差集判定后该问题消失。

    Returns:
        {"existed": bool, "created": int, "table_count": int}
        - existed: 目标库原本就有业务表
        - created: 本次新建的表数量（空库兜底时 > 0）

    并发安全：MySQL 环境使用命名顾问锁 ``GET_LOCK('erp_schema_bootstrap')``
    串行化多个 uvicorn worker / 多实例的建库过程，避免并发 DDL 互撞
    （MySQL 1684 错误）。锁在专用连接上持有，函数返回时释放；
    连接意外断开时 MySQL 会自动释放锁。
    """
    from sqlalchemy import text

    engine = get_engine() or await init_db()
    if engine is None:
        logger.warning("database.bootstrap_schema_no_engine")
        return {"existed": False, "created": 0, "table_count": 0}

    if _is_mysql_engine():
        async with engine.connect() as lock_conn:
            got = (
                await lock_conn.execute(
                    text("SELECT GET_LOCK('erp_schema_bootstrap', 300)")
                )
            ).scalar()
            if not got:
                logger.warning("database.bootstrap_schema_lock_timeout")
                return {"existed": True, "created": 0, "table_count": -1}
            try:
                return await _bootstrap_schema_locked(engine)
            finally:
                await lock_conn.execute(
                    text("SELECT RELEASE_LOCK('erp_schema_bootstrap')")
                )
    # sqlite 等测试后端：单进程，无需锁
    return await _bootstrap_schema_locked(engine)


async def _bootstrap_schema_locked(engine) -> dict:
    """bootstrap_schema 的锁内实现（调用方保证已持有顾问锁）。"""
    from sqlalchemy import text

    try:
        # 导入 models 模块，确保所有表模型注册到 Base.metadata
        from app.db.models import Base  # noqa: F401

        async with engine.connect() as conn:
            if _is_mysql_engine():
                # 从连接 URL 提取库名（mysql+aiomysql://user:pass@host:port/dbname?...）
                db_name = (settings.mysql_url or "").rsplit("/", 1)[-1]
                db_name = db_name.split("?")[0]
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :db AND table_type = 'BASE TABLE'"
                    ),
                    {"db": db_name},
                )
            else:  # sqlite 等其他后端（测试环境）
                result = await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            existing = {row[0] for row in result.fetchall()}

        # 以 ORM 元数据为准，计算缺失的表（差集）
        missing = sorted(set(Base.metadata.tables.keys()) - existing)
        if missing:
            # 只建缺失的表。create_all 默认 checkfirst=True：
            # 已存在的表不会被修改，因此存量数据与结构不受影响。
            tables = [Base.metadata.tables[name] for name in missing]
            async with engine.begin() as conn:
                await conn.run_sync(
                    lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables)
                )
            logger.info(
                "database.bootstrap_schema_created",
                created=len(missing),
                missing=missing,
                skipped_existing=len(existing),
                source="app.db.models.Base.metadata",
            )
        else:
            logger.debug(
                "database.bootstrap_schema_complete",
                table_count=len(existing),
                expected=len(Base.metadata.tables),
            )

        # 列级差集：存量表缺列时增量补列（仅 ADD COLUMN，不改不删）。
        # 场景：模型新增字段（如 users.must_change_password），存量库
        # 逐行 INSERT 数据不能重建表，必须走 ALTER TABLE 补列。
        added_columns = await _reconcile_columns(
            engine, set(Base.metadata.tables.keys()) & existing
        )

        return {
            "existed": len(existing) > 0,
            "created": len(missing),
            "table_count": len(existing) + len(missing),
            "added_columns": added_columns,
        }
    except Exception as e:
        logger.error(
            "database.bootstrap_schema_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        return {"existed": False, "created": 0, "table_count": 0, "error": str(e)}


async def _reconcile_columns(engine, existing_tables: set[str]) -> dict[str, list[str]]:
    """
    列级差集对账：对已存在的表，将 ORM 中新增的列以 ``ADD COLUMN`` 补齐。

    仅做增量（新增列），不做删列 / 改类型 / 改名——破坏性变更必须走
    alembic 迁移显式处理。对每张表的检查失败仅记录警告并跳过，
    不阻塞其余表的对账。

    Returns:
        {表名: [新增列名, ...]}，无补列时为空 dict。
    """
    from sqlalchemy import inspect as sa_inspect, text as sa_text
    from sqlalchemy.schema import CreateColumn

    from app.db.models import Base

    added: dict[str, list[str]] = {}

    def _reconcile(sync_conn) -> None:
        inspector = sa_inspect(sync_conn)
        preparer = sync_conn.dialect.identifier_preparer
        for table_name, table in sorted(Base.metadata.tables.items()):
            if table_name not in existing_tables:
                continue  # 本次新建的表本身就是完整结构
            try:
                db_cols = {c["name"] for c in inspector.get_columns(table_name)}
            except Exception as e:
                logger.warning(
                    "database.bootstrap_column_inspect_failed",
                    table=table_name,
                    error=str(e),
                )
                continue
            for column in table.columns:
                if column.name in db_cols:
                    continue
                # CreateColumn 编译输出已包含列名（如 `contract_type` VARCHAR(32)），
                # 不能再重复拼一次列名（MySQL 1064 语法错误且整个补列事务回滚）
                col_ddl = str(
                    CreateColumn(column).compile(dialect=sync_conn.dialect)
                ).strip()
                stmt = (
                    f"ALTER TABLE {preparer.quote(table_name)} "
                    f"ADD COLUMN {col_ddl}"
                )
                sync_conn.execute(sa_text(stmt))
                added.setdefault(table_name, []).append(column.name)
                logger.info(
                    "database.bootstrap_column_added",
                    table=table_name,
                    column=column.name,
                    ddl=stmt,
                )

    try:
        async with engine.begin() as conn:
            await conn.run_sync(_reconcile)
    except Exception as e:
        logger.error(
            "database.bootstrap_reconcile_columns_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
    return added


# ===========================================================================
# Redis 连接管理
# ===========================================================================

# 模块级单例：仅在显式初始化后赋值
_redis_client: Optional[Any] = None  # redis.asyncio.Redis


async def init_redis():
    """
    初始化 Redis 异步连接池。

    幂等：重复调用不会重复创建客户端。
    返回已创建（或已存在）的 ``redis.asyncio.Redis`` 实例。
    若创建失败（驱动缺失、连接不通），返回 ``None`` 并记录警告。
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    try:
        import redis.asyncio as aioredis

        # 必须用 redis.asyncio 的连接池——误用 redis.connection（同步）的
        # ConnectionPool 会让异步客户端拿到同步 Connection，
        # await ping() 时报 "object Connection can't be used in 'await' expression"
        pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
        )
        _redis_client = aioredis.Redis(connection_pool=pool)

        # 尝试 PING 验证连接
        await _redis_client.ping()
        logger.info(
            "redis.initialized",
            url=settings.redis_url,
            max_connections=settings.redis_max_connections,
        )
        return _redis_client
    except ModuleNotFoundError as e:
        logger.warning("redis.driver_missing", error=str(e), driver="redis")
        _redis_client = None
        return None
    except Exception as e:
        logger.warning("redis.init_failed", error=str(e), backend="redis")
        _redis_client = None
        return None


async def close_redis() -> None:
    """关闭并释放 Redis 连接池（应用退出时调用）"""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("redis.closed", backend="redis")
        except Exception as e:
            logger.warning("redis.close_failed", error=str(e), backend="redis")
    _redis_client = None


def get_redis_client():
    """
    获取已初始化的 Redis 客户端。

    若未调用 ``init_redis()``，返回 ``None``。
    """
    return _redis_client


async def get_redis() -> AsyncGenerator[Any, None]:
    """
    FastAPI 依赖注入：提供 Redis 客户端实例。

    用法::

        @app.get("/cache")
        async def get_cache(r: Redis = Depends(get_redis)):
            ...

    若 Redis 未初始化或不可用，yield ``None``，调用方应处理此情况。
    """
    if _redis_client is None:
        logger.debug("redis.get_redis_not_initialized")
        yield None
        return

    yield _redis_client


async def check_redis_connection() -> bool:
    """
    Redis 健康检查：尝试建立连接并执行 ``PING``。

    返回 ``True`` 表示连接正常；``False`` 表示连接失败。
    """
    try:
        client = await init_redis()
        if client is None:
            return False
        await client.ping()
        return True
    except Exception as e:
        logger.warning("redis.connection_failed", error=str(e), backend="redis")
        return False


# ===========================================================================
# 统一生命周期管理
# ===========================================================================

async def init_all() -> dict[str, bool]:
    """
    统一初始化所有持久化后端（MySQL + Redis）。

    在应用启动时调用，返回各后端的初始化状态::

        {"mysql": True/False, "redis": True/False}

    任何后端初始化失败不会影响其他后端，也不会抛出异常。
    """
    results = {}

    # MySQL
    try:
        engine = await init_db()
        results["mysql"] = engine is not None
    except Exception as e:
        logger.warning("database.init_all_mysql_failed", error=str(e))
        results["mysql"] = False

    # Redis
    try:
        client = await init_redis()
        results["redis"] = client is not None
    except Exception as e:
        logger.warning("database.init_all_redis_failed", error=str(e))
        results["redis"] = False

    logger.info("database.init_all_complete", results=results)
    return results


async def close_all() -> None:
    """统一关闭所有持久化后端（应用退出时调用）"""
    await close_db()
    await close_redis()
    logger.info("database.close_all_complete")
