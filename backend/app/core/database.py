"""数据库引擎与会话（新架构）。"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import (async_sessionmaker, create_async_engine,
                                    AsyncEngine, AsyncSession)

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker] = None


async def init_db(url: Optional[str] = None) -> Optional[AsyncEngine]:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    from app.core.config import settings

    try:
        _engine = create_async_engine(url or settings.database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(bind=_engine, expire_on_commit=False)
        return _engine
    except Exception as e:
        print(f"[database] init failed: {e}")
        return None


def get_engine() -> Optional[AsyncEngine]:
    return _engine


def get_session() -> Optional[AsyncSession]:
    return _session_factory() if _session_factory else None


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
