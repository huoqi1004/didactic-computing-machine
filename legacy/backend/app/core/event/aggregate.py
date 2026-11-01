"""
聚合根基类
基于事件溯源的领域模型基础，状态由事件重放重建
"""
import structlog
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.core.event.models import Event, EventType
from app.core.event.store import event_store

logger = structlog.get_logger()


class AggregateRoot(ABC):
    """
    聚合根基类
    所有业务聚合根继承此类，通过事件重建状态

    特性：
    - 状态由事件流重放重建
    - 变更通过产生事件实现
    - 支持快照加速重建
    - 版本号乐观并发控制
    """

    def __init__(self, aggregate_id: str):
        self.aggregate_id = aggregate_id
        self.version = 0
        self._pending_events: list[Event] = []

    @property
    @abstractmethod
    def aggregate_type(self) -> str:
        """聚合根类型标识"""
        pass

    @abstractmethod
    def apply_event(self, event: Event):
        """
        应用事件到聚合根状态（纯状态变更，无副作用）
        子类必须实现此方法来更新内部状态
        """
        pass

    async def load_from_history(self):
        """从事件历史重建状态"""
        # 尝试加载快照
        snapshot = await event_store.get_latest_snapshot(self.aggregate_id)
        if snapshot:
            self.version, state = snapshot
            self._restore_from_snapshot(state)
            start_version = self.version + 1
        else:
            start_version = 0

        # 重放快照之后的事件
        events = await event_store.get_events(self.aggregate_id, from_version=start_version)
        for event in events:
            self.apply_event(event)
            self.version = event.version

        logger.debug("aggregate.loaded",
                     aggregate_id=self.aggregate_id,
                     aggregate_type=self.aggregate_type,
                     version=self.version,
                     events_replayed=len(events))

    async def commit(self) -> list[str]:
        """提交待处理事件到事件存储"""
        if not self._pending_events:
            return []

        hashes = []
        for event in self._pending_events:
            self.version += 1
            event.version = self.version
            event_hash = await event_store.append(event)
            hashes.append(event_hash)
            self.apply_event(event)

        # 每100个事件保存一次快照
        if self.version % 100 == 0:
            await event_store.save_snapshot(
                self.aggregate_id, self.version, self._create_snapshot()
            )

        self._pending_events.clear()
        return hashes

    def raise_event(self, event_type: EventType, data: dict, agent_id: Optional[str] = None,
                    security_level: str = "internal"):
        """产生新事件（加入待处理队列）"""
        event = Event(
            event_type=event_type,
            aggregate_id=self.aggregate_id,
            aggregate_type=self.aggregate_type,
            event_data=data,
            agent_id=agent_id,
            security_level=security_level,
        )
        self._pending_events.append(event)

    def _create_snapshot(self) -> dict:
        """创建快照（子类可覆盖）"""
        return {"version": self.version}

    def _restore_from_snapshot(self, state: dict):
        """从快照恢复（子类可覆盖）"""
        self.version = state.get("version", 0)
