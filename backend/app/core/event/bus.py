"""
事件总线
Agent执行域内的核心通信机制，支持发布订阅模式
所有Agent通过事件总线感知环境变化并采取行动
"""
import asyncio
import structlog
from typing import Optional
from collections import defaultdict
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.event.models import Event, EventSubscription, EventType

logger = structlog.get_logger()


class EventBus:
    """
    内存事件总线（开发环境）
    生产环境将替换为Kafka实现的KafkaEventBus

    特性：
    - 异步发布订阅
    - 至少一次传递语义
    - 支持事件类型过滤
    - 错误重试与隔离
    """

    def __init__(self):
        self._subscriptions: list[EventSubscription] = []
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._dead_letter_queue: list[tuple[Event, Exception]] = []
        self._stats = {
            "published": 0,
            "delivered": 0,
            "failed": 0,
        }

    async def start(self):
        """启动事件总线分发器"""
        if self._running:
            return
        self._running = True
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        logger.info("event_bus.started")

    async def stop(self):
        """停止事件总线"""
        self._running = False
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
        logger.info("event_bus.stopped")

    def subscribe(self, subscription: EventSubscription):
        """注册事件订阅"""
        self._subscriptions.append(subscription)
        logger.info("event_bus.subscribed",
                     subscriber=subscription.subscriber_id,
                     event_types=[e.value for e in subscription.event_types])

    def unsubscribe(self, subscriber_id: str):
        """取消订阅"""
        self._subscriptions = [s for s in self._subscriptions if s.subscriber_id != subscriber_id]

    async def publish(self, event: Event):
        """发布事件到总线"""
        self._stats["published"] += 1
        await self._event_queue.put(event)
        logger.debug("event_bus.published",
                      event_type=event.event_type.value,
                      aggregate_id=event.aggregate_id)

    async def _dispatch_loop(self):
        """事件分发循环"""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                await self._dispatch_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("event_bus.dispatch_error", error=str(e))

    async def _dispatch_event(self, event: Event):
        """将事件分发给所有匹配的订阅者"""
        matched = [sub for sub in self._subscriptions if sub.matches(event)]
        if not matched:
            return

        # 并行投递给所有订阅者
        tasks = [self._deliver_to_subscriber(sub, event) for sub in matched]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for sub, result in zip(matched, results):
            if isinstance(result, Exception):
                self._stats["failed"] += 1
                self._dead_letter_queue.append((event, result))
                logger.error("event_bus.delivery_failed",
                             subscriber=sub.subscriber_id,
                             error=str(result))
            else:
                self._stats["delivered"] += 1

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0))
    async def _deliver_to_subscriber(self, subscription: EventSubscription, event: Event):
        """投递事件给单个订阅者（带重试）"""
        try:
            result = subscription.handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.warning("event_bus.delivery_retry",
                          subscriber=subscription.subscriber_id,
                          error=str(e))
            raise

    def get_stats(self) -> dict:
        """获取总线统计信息"""
        return {
            **self._stats,
            "queue_size": self._event_queue.qsize(),
            "dead_letters": len(self._dead_letter_queue),
            "subscriptions": len(self._subscriptions),
        }


# 全局事件总线单例
event_bus = EventBus()


# ===================================================================
# 工厂函数 - 根据配置选择 Kafka 或内存事件总线
# ===================================================================

def get_event_bus():
    """
    获取事件总线全局单例（同步工厂函数）。

    选择逻辑：
    1. ``settings.kafka_enabled == False`` → 返回内存 ``EventBus``
    2. ``aiokafka`` 未安装 → 返回内存 ``EventBus``（记录警告）
    3. 返回 ``KafkaEventBus`` 实例（未启动，调用方需执行 ``await bus.start()``）
       - ``start()`` 时 Kafka 连接失败会自动降级到内存分发模式

    懒加载：首次调用时创建，后续调用直接返回缓存实例。

    注意：返回的总线实例未启动，调用方需要手动调用 ``await bus.start()``。
    如需获取已启动的总线，使用 :func:`create_event_bus`。

    此函数委托给 :mod:`app.core.event.kafka_bus` 模块的工厂实现，
    使用懒导入避免循环依赖（``kafka_bus`` 模块需要导入 ``EventBus``）。
    """
    from app.core.event.kafka_bus import get_event_bus as _get_event_bus
    return _get_event_bus()


async def create_event_bus():
    """
    异步工厂函数：创建并启动新的事件总线实例。

    根据配置选择实现：
    - ``settings.kafka_enabled == True`` 且 ``aiokafka`` 可用 → ``KafkaEventBus``
    - 否则 → 内存 ``EventBus``

    返回已启动的总线实例，调用方负责在合适时机调用 ``await bus.stop()``。

    与 :func:`get_event_bus` 的区别：
    - :func:`get_event_bus` 返回全局单例（未启动）
    - :func:`create_event_bus` 每次创建新实例并启动（用于需要独立生命周期的场景）

    此函数委托给 :mod:`app.core.event.kafka_bus` 模块的工厂实现，
    使用懒导入避免循环依赖。
    """
    from app.core.event.kafka_bus import create_event_bus as _create_event_bus
    return await _create_event_bus()
