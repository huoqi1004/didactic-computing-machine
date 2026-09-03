"""
数据库连接管理
==============

基于 SQLAlchemy 2.0 async 模式，提供：

- 异步引擎（``AsyncEngine``）懒加载单例
- 异步会话工厂（``async_sessionmaker``）
- FastAPI 依赖注入函数 ``get_db()``
- 显式的 ``init_db()`` / ``close_db()`` 生命周期管理函数

设计要点
--------
1. **懒加载**：模块导入时不创建引擎，避免未安装 ``aiomysql`` 时报错；
   只有在实际调用 ``get_engine()`` 或 ``init_db()`` 时才创建。
2. **连接池**：通过 ``settings`` 配置池大小、溢出、回收周期等。
3. **依赖注入**：``get_db()`` 是异步生成器，可用于 FastAPI ``Depends``。
4. **零依赖导入**：本模块可在仅有 SQLAlchemy 的环境下安全导入。
"""
from __future__ import annotations

import structlog
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = structlog.get_logger()


# 模块级单例：仅在显式初始化后赋值
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _build_engine() -> AsyncEngine:
    """
    根据配置构建异步引擎。

    依赖 ``aiomysql`` 驱动（通过 ``mysql+aiomysql://`` URL 隐式加载）。
    若未安装 aiomysql，会在创建引擎时抛出 ``ModuleNotFoundError``，
    调用方应捕获并降级到内存后端。
    """
    engine = create_async_engine(
        settings.mysql_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
    )
    safe_url = settings.mysql_url
    if ":" in safe_url and "@" in safe_url:
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


async def init_db() -> AsyncEngine:
    """
    初始化数据库引擎与会话工厂。

    幂等：重复调用不会重复创建引擎。
    返回已创建（或已存在）的 ``AsyncEngine`` 实例。
    """
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    _engine = _build_engine()
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    logger.info("database.initialized", backend="mysql")
    return _engine


async def close_db() -> None:
    """关闭并释放数据库引擎（应用退出时调用）"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("database.closed", backend="mysql")
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    """
    获取已初始化的异步引擎。

    若未调用 ``init_db()``，则自动触发懒初始化。
    """
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂（需先 ``init_db``）"""
    if _session_factory is None:
        if _engine is None:
            get_engine()
        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入：提供一个异步会话，请求结束自动关闭。
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_connection() -> bool:
    """健康检查：尝试与数据库建立一次连接。"""
    try:
        engine = await init_db()
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except ModuleNotFoundError as e:
        logger.warning("database.driver_missing", error=str(e))
        return False
    except Exception as e:
        logger.error("database.connection_failed", error=str(e))
        return False
