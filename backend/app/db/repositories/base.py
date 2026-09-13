"""
Agent 持久化 Repository 公共设施（评估报告 E-01 / 路线图任务 2.1）
==================================================================

架构决策（路线图 §3.2）：repository 层下沉，而非 33 个 Agent 各自直连 DB。
Agent 保持"纯业务逻辑 + 状态读写经 repository"的结构，参照系统内成熟
持久化范本（routes/messaging.py 的 AsyncSession 直用、rbac_engine 的
MySQL+缓存双轨）。

迁移方式（路线图 2.2）：写路径"内存更新 + DB 落库"双写过渡，
启动期"DB 水合、空库回播种"；任何一批出问题可单独回退不影响其他 Agent。

本模块提供：
- :func:`has_db`          会话工厂是否可用（DB 不可用时调用方降级内存）
- :func:`session_scope`   异步会话上下文（提交/回滚/关闭）
- :func:`schedule`        fire-and-forget 写穿任务（异常仅告警不抛出）
- :class:`RepositoryBase` 通用 CRUD 助手（upsert/list，子类声明字段映射）

降级语义：DB 不可用或写入失败时**不抛异常**、不影响内存主流程，
但按类别节流告警（吸取 C-01"静默降级掩盖失败"的教训）。
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

import structlog

logger = structlog.get_logger()

# 告警节流：category -> 上次告警时间
_last_warn: dict[str, float] = {}
_WARN_INTERVAL = 60.0


def warn_throttled(category: str, event: str, **kwargs: Any) -> None:
    """同一类别的持久化失败告警每 60 秒最多记录一次。"""
    now = time.monotonic()
    last = _last_warn.get(category, 0.0)
    if now - last < _WARN_INTERVAL:
        return
    _last_warn[category] = now
    logger.warning(event, degraded="in_memory", **kwargs)


def has_db() -> bool:
    """MySQL 会话工厂是否已初始化（DB 不可用时 Agent 状态保持内存态）。"""
    from app.core.database import get_session_factory

    return get_session_factory() is not None


@asynccontextmanager
async def session_scope() -> AsyncGenerator[Any, None]:
    """提供一个异步会话，正常退出时提交，异常时回滚。"""
    from app.core.database import get_session_factory

    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("session factory unavailable")
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def schedule(coro, category: str) -> None:
    """
    调度 fire-and-forget 的写穿任务（双写过渡期的 DB 落库）。

    异常只做节流告警，不影响内存主流程；无事件循环时静默跳过
    （纯同步测试环境）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _runner():
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            warn_throttled(category, f"{category}.write_failed", error=str(e))

    task = asyncio.create_task(_runner())
    # 防止任务被 GC
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


_PENDING: set[asyncio.Task] = set()


class RepositoryBase:
    """
    Repository 通用基类。

    子类提供 ``__model__``（ORM 类）与 ``__to_row__``/``__to_dict__``
    （业务 dict ↔ ORM 列的双向映射），即可获得 upsert/list/get 能力；
    复杂查询在子类中以显式方法实现。
    """

    __model__: Any = None
    # 降级告警类别（如 "payroll_repo"）
    __category__: str = "repository"

    async def upsert(self, data: dict, key_field: str) -> bool:
        """按主键 upsert 一行（先查后插/改）。失败返回 False（降级内存）。"""
        if not has_db():
            return False
        try:
            row_key = data[key_field]
            columns = self.__to_row__(data)
            async with session_scope() as session:
                existing = await session.get(self.__model__, row_key)
                if existing is not None:
                    for col, value in columns.items():
                        setattr(existing, col, value)
                else:
                    session.add(self.__model__(**columns))
            return True
        except Exception as e:  # noqa: BLE001
            warn_throttled(
                self.__category__, f"{self.__category__}.upsert_failed",
                key=str(data.get(key_field)), error=str(e),
            )
            return False

    async def list_all(self, limit: int = 5000) -> list[dict]:
        """读取全表（上限保护），返回业务 dict 列表。失败返回空列表。"""
        if not has_db():
            return []
        try:
            from sqlalchemy import select

            async with session_scope() as session:
                rows = (
                    (await session.execute(select(self.__model__).limit(limit)))
                    .scalars()
                    .all()
                )
            return [self.__to_dict__(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            warn_throttled(
                self.__category__, f"{self.__category__}.list_failed", error=str(e),
            )
            return []

    async def count(self) -> int:
        """表行数（用于空库判定/水合决策）。失败返回 -1。"""
        if not has_db():
            return -1
        try:
            from sqlalchemy import func, select

            async with session_scope() as session:
                return int(
                    (await session.execute(select(func.count()).select_from(self.__model__))).scalar()
                )
        except Exception as e:  # noqa: BLE001
            warn_throttled(
                self.__category__, f"{self.__category__}.count_failed", error=str(e),
            )
            return -1
