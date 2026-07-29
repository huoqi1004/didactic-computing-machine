"""
事件存储
不可变事件日志，支持事件重放和状态重建
所有业务事件的持久化源头

持久化架构：
- 内存层（Dict）：作为高速缓存，保证读操作低延迟，同时是测试/开发的零依赖后端
- 数据库层（PostgreSQL events 表）：作为持久化存储，保证数据不丢失
- 自动降级：数据库不可用时，透明降级到纯内存模式
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime
from collections import defaultdict

from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class EventStore:
    """
    事件存储（双写：内存缓存 + PostgreSQL 持久化）

    架构：
    - 内存层（``_streams`` / ``_snapshots``）：高速读缓存 + 零依赖降级后端
    - 数据库层（PostgreSQL ``events`` / ``snapshots`` 表）：持久化存储
    - ``init_db()`` 初始化数据库表结构，设置 ``_db_available`` 标志
    - 所有写操作（``append`` / ``save_snapshot``）双写内存 + 数据库
    - 所有读操作优先从内存读取，缓存未命中时回源数据库
    - 数据库不可用时，自动降级到纯内存模式

    特性：
    - 不可变事件日志（append-only）
    - 支持按聚合根查询事件流
    - 支持事件重放重建状态
    - 哈希链防篡改审计
    - 快照机制加速状态重建
    """

    def __init__(self):
        # 按聚合根ID组织的事件流（内存缓存层）
        self._streams: dict[str, list[Event]] = defaultdict(list)
        # 快照存储 {aggregate_id: (version, snapshot_data)}（内存缓存层）
        self._snapshots: dict[str, list[tuple[int, dict]]] = defaultdict(list)
        # 全局事件哈希链（审计追踪）
        self._last_hash: Optional[str] = None
        self._hash_chain: list[str] = []
        self._lock = asyncio.Lock()

        # 数据库可用性标志（init_db() 成功后置为 True）
        self._db_available: bool = False
        self._db_initialized: bool = False

    # ===================================================================
    # 数据库初始化
    # ===================================================================

    async def init_db(self) -> bool:
        """
        初始化数据库表结构（events + snapshots 表）。

        幂等：重复调用不会重复建表。
        成功后设置 ``_db_available = True``，后续写操作将双写数据库。
        失败时记录警告，``_db_available`` 保持 ``False``，自动降级到内存模式。

        返回 ``True`` 表示初始化成功，``False`` 表示数据库不可用。
        """
        if self._db_initialized:
            return self._db_available

        try:
            from app.core import database as db_module
            from app.core.migrations import run_migrations

            # 确保数据库引擎已初始化
            engine = await db_module.init_db()
            if engine is None:
                logger.warning("event_store.init_db_failed_engine_none")
                self._db_initialized = True
                self._db_available = False
                return False

            # 执行迁移（建表）
            success = await run_migrations(engine)
            self._db_initialized = True
            self._db_available = success

            if success:
                logger.info("event_store.db_initialized", backend="postgres")
            else:
                logger.warning("event_store.db_init_failed_degraded_to_memory")

            return success

        except Exception as e:
            logger.warning("event_store.init_db_error", error=str(e))
            self._db_initialized = True
            self._db_available = False
            return False

    # ===================================================================
    # 事件追加（写操作）
    # ===================================================================

    async def append(self, event: Event) -> str:
        """
        追加事件到存储（不可变）。
        返回事件哈希用于审计链。

        写入策略：
        1. 内存层：始终写入（保证缓存一致性）
        2. 数据库层：``_db_available`` 为 True 时写入 PostgreSQL ``events`` 表
        3. 数据库写入失败时记录警告，不影响内存写入（降级模式）
        """
        async with self._lock:
            # 计算审计链哈希
            event.hash_chain = self._last_hash
            event_hash = event.compute_hash(self._last_hash)
            self._last_hash = event_hash
            self._hash_chain.append(event_hash)

            # 追加到内存聚合根事件流
            self._streams[event.aggregate_id].append(event)

            # 尝试写入数据库
            if self._db_available:
                await self._append_to_db(event, event_hash)

            logger.info("event_store.appended",
                        event_type=event.event_type.value,
                        aggregate_id=event.aggregate_id,
                        version=event.version,
                        hash=event_hash[:16],
                        db_persisted=self._db_available)

            return event_hash

    async def _append_to_db(self, event: Event, event_hash: str) -> None:
        """将事件写入 PostgreSQL events 表（内部方法）"""
        try:
            from app.core import database as db_module
            from sqlalchemy import text
            import orjson

            engine = db_module.get_engine()
            if engine is None:
                return

            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO events
                            (event_id, event_type, aggregate_id, aggregate_type,
                             event_data, agent_id, version, timestamp,
                             prev_hash, event_hash)
                        VALUES
                            (:event_id, :event_type, :aggregate_id, :aggregate_type,
                             CAST(:event_data AS JSONB), :agent_id, :version, :timestamp,
                             :prev_hash, :event_hash)
                    """),
                    {
                        "event_id": str(event.event_id),
                        "event_type": event.event_type.value,
                        "aggregate_id": event.aggregate_id,
                        "aggregate_type": event.aggregate_type,
                        "event_data": orjson.dumps(event.event_data).decode(),
                        "agent_id": event.agent_id,
                        "version": event.version,
                        "timestamp": event.timestamp,
                        "prev_hash": event.hash_chain,
                        "event_hash": event_hash,
                    }
                )

            logger.debug("event_store.db_appended",
                         event_id=str(event.event_id),
                         aggregate_id=event.aggregate_id)

        except Exception as e:
            logger.warning("event_store.db_append_failed",
                           error=str(e),
                           event_id=str(event.event_id),
                           degraded="memory_only")

    # ===================================================================
    # 事件查询（读操作）
    # ===================================================================

    async def get_events(
        self,
        aggregate_id: str,
        from_version: int = 0,
        to_version: Optional[int] = None
    ) -> list[Event]:
        """
        获取聚合根的事件流。

        读取策略：
        1. 优先从内存缓存读取（低延迟）
        2. 缓存未命中时，若数据库可用则从数据库回源
        3. 数据库也不可用时返回空列表
        """
        # 优先从内存缓存读取
        events = self._streams.get(aggregate_id, [])
        if events:
            filtered = []
            for e in events:
                if e.version < from_version:
                    continue
                if to_version is not None and e.version > to_version:
                    continue
                filtered.append(e)
            return filtered

        # 缓存未命中，尝试从数据库读取
        if self._db_available:
            db_events = await self._get_events_from_db(
                aggregate_id, from_version, to_version
            )
            if db_events:
                # 回填内存缓存
                self._streams[aggregate_id] = db_events
                return db_events

        return []

    async def _get_events_from_db(
        self,
        aggregate_id: str,
        from_version: int = 0,
        to_version: Optional[int] = None
    ) -> list[Event]:
        """从 PostgreSQL events 表读取事件流（内部方法）"""
        try:
            from app.core import database as db_module
            from sqlalchemy import text
            import orjson

            engine = db_module.get_engine()
            if engine is None:
                return []

            query = """
                SELECT event_id, event_type, aggregate_id, aggregate_type,
                       event_data, agent_id, version, timestamp, prev_hash
                FROM events
                WHERE aggregate_id = :aggregate_id
                  AND version >= :from_version
            """
            params = {"aggregate_id": aggregate_id, "from_version": from_version}

            if to_version is not None:
                query += " AND version <= :to_version"
                params["to_version"] = to_version

            query += " ORDER BY version ASC"

            async with engine.connect() as conn:
                result = await conn.execute(text(query), params)
                rows = result.fetchall()

            events = []
            for row in rows:
                event_data = row[4] if isinstance(row[4], dict) else orjson.loads(row[4])
                event = Event(
                    event_id=row[0],
                    event_type=EventType(row[1]),
                    aggregate_id=row[2],
                    aggregate_type=row[3],
                    event_data=event_data,
                    agent_id=row[5],
                    version=row[6],
                    timestamp=row[7],
                    hash_chain=row[8],
                )
                events.append(event)

            logger.debug("event_store.db_read",
                         aggregate_id=aggregate_id,
                         count=len(events))
            return events

        except Exception as e:
            logger.warning("event_store.db_read_failed",
                           error=str(e),
                           aggregate_id=aggregate_id)
            return []

    async def get_events_by_type(
        self,
        event_type: EventType,
        limit: int = 100
    ) -> list[Event]:
        """
        按事件类型查询。

        读取策略：
        1. 数据库可用时，从 PostgreSQL 查询（支持跨聚合根查询）
        2. 数据库不可用时，从内存缓存遍历查询
        """
        # 数据库可用时优先从数据库查询
        if self._db_available:
            db_events = await self._get_events_by_type_from_db(event_type, limit)
            if db_events is not None:
                return db_events

        # 降级到内存查询
        result = []
        for events in self._streams.values():
            for e in events:
                if e.event_type == event_type:
                    result.append(e)
                    if len(result) >= limit:
                        return result
        return result

    async def _get_events_by_type_from_db(
        self,
        event_type: EventType,
        limit: int
    ) -> Optional[list[Event]]:
        """从 PostgreSQL events 表按类型查询（内部方法），返回 None 表示查询失败"""
        try:
            from app.core import database as db_module
            from sqlalchemy import text
            import orjson

            engine = db_module.get_engine()
            if engine is None:
                return None

            query = """
                SELECT event_id, event_type, aggregate_id, aggregate_type,
                       event_data, agent_id, version, timestamp, prev_hash
                FROM events
                WHERE event_type = :event_type
                ORDER BY timestamp ASC
                LIMIT :limit
            """

            async with engine.connect() as conn:
                result = await conn.execute(
                    text(query),
                    {"event_type": event_type.value, "limit": limit}
                )
                rows = result.fetchall()

            events = []
            for row in rows:
                event_data = row[4] if isinstance(row[4], dict) else orjson.loads(row[4])
                event = Event(
                    event_id=row[0],
                    event_type=EventType(row[1]),
                    aggregate_id=row[2],
                    aggregate_type=row[3],
                    event_data=event_data,
                    agent_id=row[5],
                    version=row[6],
                    timestamp=row[7],
                    hash_chain=row[8],
                )
                events.append(event)

            logger.debug("event_store.db_read_by_type",
                         event_type=event_type.value,
                         count=len(events))
            return events

        except Exception as e:
            logger.warning("event_store.db_read_by_type_failed",
                           error=str(e),
                           event_type=event_type.value)
            return None

    # ===================================================================
    # 快照管理
    # ===================================================================

    async def save_snapshot(self, aggregate_id: str, version: int, snapshot: dict):
        """
        保存聚合根快照。

        写入策略：
        1. 内存层：始终写入
        2. 数据库层：``_db_available`` 为 True 时写入 PostgreSQL ``snapshots`` 表
        """
        # 内存层写入
        self._snapshots[aggregate_id].append((version, snapshot))

        # 数据库层写入
        if self._db_available:
            await self._save_snapshot_to_db(aggregate_id, version, snapshot)

        logger.info("event_store.snapshot_saved",
                     aggregate_id=aggregate_id,
                     version=version,
                     db_persisted=self._db_available)

    async def _save_snapshot_to_db(
        self, aggregate_id: str, version: int, snapshot: dict
    ) -> None:
        """将快照写入 PostgreSQL snapshots 表（内部方法）"""
        try:
            from app.core import database as db_module
            from sqlalchemy import text
            import orjson

            engine = db_module.get_engine()
            if engine is None:
                return

            # 快照表无主键约束，直接插入（保留历史快照记录）
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO snapshots
                            (aggregate_id, aggregate_type, version, state, timestamp)
                        VALUES
                            (:aggregate_id, :aggregate_type, :version,
                             CAST(:state AS JSONB), NOW())
                    """),
                    {
                        "aggregate_id": aggregate_id,
                        "aggregate_type": snapshot.get("aggregate_type", "unknown"),
                        "version": version,
                        "state": orjson.dumps(snapshot).decode(),
                    }
                )

            logger.debug("event_store.db_snapshot_saved",
                         aggregate_id=aggregate_id,
                         version=version)

        except Exception as e:
            logger.warning("event_store.db_snapshot_failed",
                           error=str(e),
                           aggregate_id=aggregate_id,
                           degraded="memory_only")

    async def get_latest_snapshot(self, aggregate_id: str) -> Optional[tuple[int, dict]]:
        """
        获取最新快照。

        读取策略：
        1. 优先从内存缓存读取
        2. 缓存未命中时，若数据库可用则从数据库回源
        """
        # 优先从内存读取
        snapshots = self._snapshots.get(aggregate_id, [])
        if snapshots:
            return snapshots[-1]

        # 缓存未命中，尝试从数据库读取
        if self._db_available:
            db_snapshot = await self._get_latest_snapshot_from_db(aggregate_id)
            if db_snapshot is not None:
                # 回填内存缓存
                self._snapshots[aggregate_id].append(db_snapshot)
                return db_snapshot

        return None

    async def _get_latest_snapshot_from_db(
        self, aggregate_id: str
    ) -> Optional[tuple[int, dict]]:
        """从 PostgreSQL snapshots 表读取最新快照（内部方法）"""
        try:
            from app.core import database as db_module
            from sqlalchemy import text
            import orjson

            engine = db_module.get_engine()
            if engine is None:
                return None

            query = """
                SELECT version, state
                FROM snapshots
                WHERE aggregate_id = :aggregate_id
                ORDER BY version DESC
                LIMIT 1
            """

            async with engine.connect() as conn:
                result = await conn.execute(
                    text(query),
                    {"aggregate_id": aggregate_id}
                )
                row = result.fetchone()

            if row is None:
                return None

            state = row[1] if isinstance(row[1], dict) else orjson.loads(row[1])
            return (row[0], state)

        except Exception as e:
            logger.warning("event_store.db_snapshot_read_failed",
                           error=str(e),
                           aggregate_id=aggregate_id)
            return None

    # ===================================================================
    # 完整性验证
    # ===================================================================

    async def verify_integrity(self) -> bool:
        """
        验证事件链完整性（防篡改检查）。

        始终从内存缓存读取事件对象进行验证，
        这样可以检测到对内存中事件对象的任何篡改。
        """
        prev_hash = None
        for i, event_list in enumerate(self._streams.values()):
            for event in event_list:
                expected_hash = event.compute_hash(prev_hash)
                if event.hash_chain != prev_hash:
                    logger.error("event_store.integrity_broken",
                                 event_id=str(event.event_id))
                    return False
                prev_hash = expected_hash
        return True

    # ===================================================================
    # 统计信息
    # ===================================================================

    def get_stats(self) -> dict:
        """获取存储统计"""
        total_events = sum(len(events) for events in self._streams.values())
        total_snapshots = sum(len(snaps) for snaps in self._snapshots.values())
        return {
            "total_events": total_events,
            "total_streams": len(self._streams),
            "total_snapshots": total_snapshots,
            "hash_chain_length": len(self._hash_chain),
            "db_available": self._db_available,
            "db_initialized": self._db_initialized,
        }


# 全局事件存储单例
event_store = EventStore()
