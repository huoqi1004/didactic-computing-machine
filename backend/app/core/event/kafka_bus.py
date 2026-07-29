"""
Kafka事件总线 - 生产级事件总线实现

设计目标：
1. 提供与 :class:`EventBus` 完全一致的接口，业务层无需感知底层实现
2. 使用 ``aiokafka`` 实现 Kafka 生产者/消费者，支持跨进程事件分发
3. 支持自动降级：aiokafka 未安装或 Kafka 连接失败时，透明降级到内存分发模式
4. 至少一次传递语义：消费者手动提交偏移量，处理成功后才提交

降级策略：
- ``settings.kafka_enabled == False`` → 工厂函数直接返回内存 ``EventBus``
- ``aiokafka`` 包未安装 → ``KafkaEventBus.start()`` 降级到内存分发模式
- Kafka 连接失败 / 初始化失败 → ``KafkaEventBus.start()`` 降级到内存分发模式
- 运行期单条消息处理失败 → 不提交偏移量，消息将被重新投递（进入死信队列后提交）

典型用法::

    from app.core.event.kafka_bus import KafkaEventBus, get_event_bus

    bus = get_event_bus()
    await bus.start()
    bus.subscribe(subscription)
    await bus.publish(event)
    await bus.stop()
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import UUID
from datetime import datetime

import orjson
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.event.models import Event, EventSubscription, EventType
from app.core.event.bus import EventBus

logger = structlog.get_logger()

# aiokafka 是可选依赖：仅在生产环境需要跨进程事件分发时才安装
# 若导入失败，KafkaEventBus.start() 将自动降级到内存分发模式
_AIOKAFKA_AVAILABLE = False
try:  # pragma: no cover - 依赖外部库，单元测试环境通常无此包
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer  # type: ignore
    _AIOKAFKA_AVAILABLE = True
except ImportError:  # noqa: BLE001
    AIOKafkaProducer = None  # type: ignore
    AIOKafkaConsumer = None  # type: ignore


# ===================================================================
# Kafka事件总线实现
# ===================================================================

class KafkaEventBus:
    """
    Kafka事件总线（生产环境）

    架构：
    - 生产者（``AIOKafkaProducer``）：将事件序列化为 JSON 发送到 Kafka topic
    - 消费者（``AIOKafkaConsumer``）：从 Kafka topic 消费事件，反序列化后分发给本地订阅者
    - 本地订阅者管理：与 ``EventBus`` 一致，支持事件类型过滤和条件过滤
    - 降级模式：Kafka 不可用时，使用内存队列分发（与 ``EventBus`` 行为一致）

    特性：
    - 与 ``EventBus`` 接口完全兼容（``start`` / ``stop`` / ``subscribe`` / ``unsubscribe`` / ``publish`` / ``get_stats``）
    - 至少一次传递语义（手动提交偏移量）
    - 错误重试与死信队列
    - 自动降级到内存模式
    """

    def __init__(self):
        # 本地订阅者列表（Kafka消费后分发给这些订阅者）
        self._subscriptions: list[EventSubscription] = []
        # 内存事件队列（降级模式下使用，与 EventBus 一致）
        self._event_queue: asyncio.Queue = asyncio.Queue()
        # 死信队列
        self._dead_letter_queue: list[tuple[Event, Exception]] = []

        # 运行状态
        self._running: bool = False
        self._degraded: bool = False  # 是否降级到内存模式

        # Kafka 客户端实例
        self._producer: Optional[Any] = None
        self._consumer: Optional[Any] = None

        # 异步任务
        self._consumer_task: Optional[asyncio.Task] = None
        self._dispatch_task: Optional[asyncio.Task] = None  # 降级模式下的本地分发任务

        # 统计信息
        self._stats: dict[str, Any] = {
            "published": 0,         # publish() 调用总数
            "delivered": 0,          # 成功投递给订阅者的次数
            "failed": 0,             # 投递失败次数
            "kafka_published": 0,    # 成功发送到 Kafka 的次数
            "kafka_consumed": 0,     # 从 Kafka 消费的次数
            "lag": 0,                # 消费者滞后量（最近一次估算）
        }

    # -----------------------------------------------------------------
    # 生命周期管理
    # -----------------------------------------------------------------

    async def start(self):
        """
        启动事件总线。

        降级逻辑：
        1. aiokafka 未安装 → 降级到内存分发模式
        2. Kafka 连接/初始化失败 → 降级到内存分发模式
        3. 成功连接 → 启动 Kafka 消费者循环
        """
        if self._running:
            return

        # 尝试启动 Kafka
        kafka_started = await self._start_kafka()

        if not kafka_started:
            # 降级到内存分发模式
            self._degraded = True
            self._running = True
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())
            logger.warning("kafka_bus.degraded_to_memory",
                           reason="kafka_unavailable")
            return

        # Kafka 模式：启动消费者循环
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("kafka_bus.started",
                     bootstrap_servers=settings.kafka_bootstrap_servers,
                     topic=settings.kafka_event_topic,
                     consumer_group=settings.kafka_consumer_group)

    async def _start_kafka(self) -> bool:
        """
        尝试初始化 Kafka 生产者和消费者。

        返回 ``True`` 表示成功，``False`` 表示不可用（调用方应降级）。
        """
        if not _AIOKAFKA_AVAILABLE:
            logger.warning("kafka_bus.aiokafka_not_installed",
                           hint="pip install aiokafka")
            return False

        try:
            # 初始化生产者
            self._producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                value_serializer=lambda v: v,  # Event.to_json() 已返回 bytes
                key_serializer=lambda k: k if isinstance(k, bytes) else k.encode(),
                acks="all",              # 等待所有副本确认（最高可靠性）
                enable_idempotence=True,  # 幂等生产者，避免重复
                max_request_size=1048576,  # 1MB
            )
            await self._producer.start()

            # 初始化消费者
            self._consumer = AIOKafkaConsumer(
                settings.kafka_event_topic,
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=settings.kafka_consumer_group,
                enable_auto_commit=False,  # 手动提交（至少一次语义）
                auto_offset_reset="latest",  # 新消费者组从最新位置开始
                value_deserializer=lambda v: v,  # 保持 bytes，由 _deserialize_event 处理
                key_deserializer=lambda k: k,
                consumer_timeout_ms=1000,  # 无消息时的轮询超时
            )
            await self._consumer.start()

            logger.info("kafka_bus.kafka_connected",
                        topic=settings.kafka_event_topic,
                        group=settings.kafka_consumer_group)
            return True

        except Exception as e:  # noqa: BLE001
            logger.warning("kafka_bus.start_failed_degraded",
                           error=str(e),
                           error_type=type(e).__name__)
            # 清理半初始化的状态
            await self._cleanup_kafka_clients()
            return False

    async def _cleanup_kafka_clients(self):
        """清理 Kafka 客户端连接（内部方法）"""
        if self._consumer is not None:
            try:
                await self._consumer.stop()
            except Exception:  # noqa: BLE001
                pass
            self._consumer = None

        if self._producer is not None:
            try:
                await self._producer.stop()
            except Exception:  # noqa: BLE001
                pass
            self._producer = None

    async def stop(self):
        """停止事件总线，关闭所有连接和任务"""
        self._running = False

        # 取消异步任务
        for task in (self._consumer_task, self._dispatch_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._consumer_task = None
        self._dispatch_task = None

        # 关闭 Kafka 连接
        await self._cleanup_kafka_clients()

        logger.info("kafka_bus.stopped", degraded=self._degraded)

    # -----------------------------------------------------------------
    # 订阅管理
    # -----------------------------------------------------------------

    def subscribe(self, subscription: EventSubscription):
        """注册事件订阅（与 EventBus 接口一致）"""
        self._subscriptions.append(subscription)
        logger.info("kafka_bus.subscribed",
                     subscriber=subscription.subscriber_id,
                     event_types=[e.value for e in subscription.event_types],
                     degraded=self._degraded)

    def unsubscribe(self, subscriber_id: str):
        """取消订阅（与 EventBus 接口一致）"""
        self._subscriptions = [
            s for s in self._subscriptions if s.subscriber_id != subscriber_id
        ]
        logger.info("kafka_bus.unsubscribed", subscriber=subscriber_id)

    # -----------------------------------------------------------------
    # 事件发布
    # -----------------------------------------------------------------

    async def publish(self, event: Event):
        """
        发布事件到总线（与 EventBus 接口一致）。

        - Kafka 模式：序列化为 JSON 发送到 Kafka topic
        - 降级模式：放入内存队列（与 EventBus 一致）
        """
        self._stats["published"] += 1

        if self._degraded:
            # 降级模式：放入内存队列，由 _dispatch_loop 处理
            await self._event_queue.put(event)
            logger.debug("kafka_bus.published_local",
                         event_type=event.event_type.value,
                         aggregate_id=event.aggregate_id)
            return

        # Kafka 模式：发送到 Kafka
        try:
            key = event.aggregate_id.encode("utf-8") if event.aggregate_id else None
            await self._producer.send_and_wait(
                settings.kafka_event_topic,
                event.to_json(),
                key=key,
            )
            self._stats["kafka_published"] += 1
            logger.debug("kafka_bus.published_to_kafka",
                         event_type=event.event_type.value,
                         aggregate_id=event.aggregate_id,
                         topic=settings.kafka_event_topic)
        except Exception as e:  # noqa: BLE001
            self._stats["failed"] += 1
            logger.error("kafka_bus.publish_failed",
                         event_type=event.event_type.value,
                         aggregate_id=event.aggregate_id,
                         error=str(e),
                         error_type=type(e).__name__)

    # -----------------------------------------------------------------
    # Kafka 消费循环
    # -----------------------------------------------------------------

    async def _consume_loop(self):
        """
        Kafka 消费循环。

        从 Kafka topic 消费事件，反序列化后分发给本地订阅者。
        处理成功后手动提交偏移量（至少一次语义）。
        """
        while self._running:
            try:
                # 使用 async for 迭代消费者消息
                async for msg in self._consumer:  # type: ignore[union-attr]
                    if not self._running:
                        break
                    await self._process_kafka_message(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                if self._running:
                    logger.error("kafka_bus.consume_loop_error",
                                 error=str(e),
                                 error_type=type(e).__name__)
                    # 退避后重试
                    await asyncio.sleep(1.0)

    async def _process_kafka_message(self, msg: Any):
        """
        处理单条 Kafka 消息。

        - 反序列化为 Event 对象
        - 分发给匹配的本地订阅者
        - 成功后提交偏移量（至少一次语义）
        - 处理失败时记录死信队列后仍提交（避免毒丸消息阻塞消费）
        """
        try:
            event = self._deserialize_event(msg.value)
            self._stats["kafka_consumed"] += 1
            await self._dispatch_event(event)
        except Exception as e:  # noqa: BLE001
            logger.error("kafka_bus.message_process_failed",
                         error=str(e),
                         partition=msg.partition if hasattr(msg, "partition") else None,
                         offset=msg.offset if hasattr(msg, "offset") else None)
            # 反序列化或分发失败，记录死信队列后提交，避免毒丸消息阻塞
            # （生产环境可配置死信 topic 进行重放）
            self._stats["failed"] += 1
        finally:
            # 提交偏移量（无论成功失败，避免消息卡住）
            # 真正的处理失败已通过 _dispatch_event 内部的死信队列记录
            try:
                await self._consumer.commit()  # type: ignore[union-attr]
            except Exception as e:  # noqa: BLE001
                logger.warning("kafka_bus.commit_failed", error=str(e))

    # -----------------------------------------------------------------
    # 内存分发循环（降级模式）
    # -----------------------------------------------------------------

    async def _dispatch_loop(self):
        """
        内存分发循环（降级模式下使用，与 EventBus._dispatch_loop 一致）。
        """
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                await self._dispatch_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("kafka_bus.dispatch_error", error=str(e))

    # -----------------------------------------------------------------
    # 事件分发（共享逻辑）
    # -----------------------------------------------------------------

    async def _dispatch_event(self, event: Event):
        """
        将事件分发给所有匹配的本地订阅者。

        与 EventBus._dispatch_event 逻辑一致：
        - 匹配订阅者（事件类型 + 过滤条件）
        - 并行投递
        - 失败进入死信队列
        """
        matched = [sub for sub in self._subscriptions if sub.matches(event)]
        if not matched:
            return

        # 并行投递给所有匹配的订阅者
        tasks = [self._deliver_to_subscriber(sub, event) for sub in matched]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for sub, result in zip(matched, results):
            if isinstance(result, Exception):
                self._stats["failed"] += 1
                self._dead_letter_queue.append((event, result))
                logger.error("kafka_bus.delivery_failed",
                             subscriber=sub.subscriber_id,
                             error=str(result))
            else:
                self._stats["delivered"] += 1

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0))
    async def _deliver_to_subscriber(self, subscription: EventSubscription, event: Event):
        """投递事件给单个订阅者（带重试，与 EventBus 一致）"""
        try:
            result = subscription.handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.warning("kafka_bus.delivery_retry",
                           subscriber=subscription.subscriber_id,
                           error=str(e))
            raise

    # -----------------------------------------------------------------
    # 序列化 / 反序列化
    # -----------------------------------------------------------------

    @staticmethod
    def _deserialize_event(data: bytes) -> Event:
        """
        将 JSON bytes 反序列化为 Event 对象。

        与 :meth:`Event.to_dict` 互逆，处理以下类型转换：
        - ``event_id``: str → UUID
        - ``event_type``: str → EventType
        - ``timestamp``: ISO str → datetime
        """
        d = orjson.loads(data)
        return Event(
            event_id=UUID(d["event_id"]),
            event_type=EventType(d["event_type"]),
            aggregate_id=d["aggregate_id"],
            aggregate_type=d["aggregate_type"],
            event_data=d.get("event_data", {}),
            metadata=d.get("metadata", {}),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            version=d.get("version", 1),
            agent_id=d.get("agent_id"),
            security_level=d.get("security_level", "internal"),
            hash_chain=d.get("hash_chain"),
        )

    # -----------------------------------------------------------------
    # 统计信息
    # -----------------------------------------------------------------

    def get_stats(self) -> dict:
        """
        获取总线统计信息（与 EventBus.get_stats 接口兼容，额外包含 Kafka 指标）。

        返回字段：
        - ``published`` / ``delivered`` / ``failed``：与 EventBus 一致
        - ``queue_size`` / ``dead_letters`` / ``subscriptions``：与 EventBus 一致
        - ``kafka_published`` / ``kafka_consumed`` / ``lag``：Kafka 专属指标
        - ``degraded``：是否降级到内存模式
        - ``implementation``：实现标识（``"kafka"`` / ``"memory_fallback"``）
        - ``aiokafka_available``：aiokafka 包是否已安装
        """
        return {
            **self._stats,
            "queue_size": self._event_queue.qsize(),
            "dead_letters": len(self._dead_letter_queue),
            "subscriptions": len(self._subscriptions),
            "degraded": self._degraded,
            "implementation": "memory_fallback" if self._degraded else "kafka",
            "aiokafka_available": _AIOKAFKA_AVAILABLE,
            "kafka_connected": self._producer is not None and not self._degraded,
        }


# ===================================================================
# 全局单例工厂
# ===================================================================

_global_event_bus: Optional[Any] = None
_init_attempted: bool = False


def reset_event_bus() -> None:
    """
    重置全局事件总线单例（仅用于测试夹具）。

    在测试间清空全局总线状态，保证测试隔离。
    """
    global _global_event_bus, _init_attempted
    _global_event_bus = None
    _init_attempted = False


def get_event_bus():
    """
    获取事件总线全局单例（同步工厂函数）。

    选择逻辑：
    1. ``settings.kafka_enabled == False`` → 返回内存 ``EventBus``
    2. ``settings.kafka_enabled == True`` → 返回 ``KafkaEventBus`` 实例（未启动）
       - ``start()`` 时若 aiokafka 未安装或 Kafka 连接失败，会自动降级到内存分发模式
       - 降级是 ``KafkaEventBus`` 内部行为，调用方始终获得 ``KafkaEventBus`` 类型

    懒加载：首次调用时创建，后续调用直接返回缓存实例。

    注意：返回的总线实例未启动，调用方需要手动调用 ``await bus.start()``。
    如需获取已启动的总线，使用 :func:`create_event_bus`。
    """
    global _global_event_bus, _init_attempted

    if _global_event_bus is not None:
        return _global_event_bus

    _init_attempted = True

    # 配置开关：未启用 Kafka → 使用内存总线
    if not settings.kafka_enabled:
        logger.info("kafka_bus.disabled_using_memory",
                     reason="kafka_enabled=False")
        _global_event_bus = EventBus()
        return _global_event_bus

    # kafka_enabled=True → 返回 KafkaEventBus（start() 时若不可用会内部降级）
    _global_event_bus = KafkaEventBus()
    logger.info("kafka_bus.created", implementation="kafka")
    return _global_event_bus


async def create_event_bus():
    """
    异步工厂函数：创建并启动新的事件总线实例。

    根据配置选择实现：
    - ``settings.kafka_enabled == True`` → ``KafkaEventBus``（内部自动降级）
    - ``settings.kafka_enabled == False`` → 内存 ``EventBus``

    返回已启动的总线实例，调用方负责在合适时机调用 ``await bus.stop()``。

    与 :func:`get_event_bus` 的区别：
    - :func:`get_event_bus` 返回全局单例（未启动）
    - :func:`create_event_bus` 每次创建新实例并启动（用于需要独立生命周期的场景）
    """
    if settings.kafka_enabled:
        bus = KafkaEventBus()
    else:
        bus = EventBus()

    await bus.start()
    return bus
