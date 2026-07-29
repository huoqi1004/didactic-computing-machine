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

    engine = create_async_engine(
        settings.mysql_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        # 注意：pool_pre_ping 与 aiomysql 的 ping() 签名不兼容，
        # 使用 pool_recycle 代替，定期回收连接避免使用已断开的连接
    )

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
        from redis.connection import ConnectionPool

        pool = ConnectionPool.from_url(
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
