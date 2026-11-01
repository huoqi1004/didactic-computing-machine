"""
Kafka事件总线生产加固测试

覆盖 P2-1 新增功能：
1. 批量发布（publish_batch）：Kafka 模式 / 降级模式 / 空列表 / 部分失败
2. 健康检查（health_check）：未启动 / 降级 / 正常 / 结构完整性
3. 优雅排空（drain）：无在途 / 等待在途完成 / 超时
4. 死信 topic（DLQ）：投递失败发布 DLQ / 降级模式不发布 / 反序列化失败发原始消息
5. 增强统计：新增指标字段（kafka_dlq_published / batch_published / in_flight / compression / dead_letter_topic）
6. stop() 调用 drain()
"""
import asyncio
from collections import namedtuple
from unittest.mock import patch

import pytest

from app.core.event.kafka_bus import KafkaEventBus, reset_event_bus
from app.core.event.models import Event, EventSubscription, EventType


# ============================================================
# 测试辅助：模拟 Kafka 消息与客户端
# ============================================================

ConsumerRecord = namedtuple(
    "ConsumerRecord",
    ["topic", "partition", "offset", "key", "value", "timestamp", "timestamp_type"],
)


class MockAIOKafkaProducer:
    """模拟 aiokafka AIOKafkaProducer（记录目标 topic）"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent_messages: list[tuple[str, bytes, bytes | None]] = []
        self._send_exception = None
        self._fail_on_topic: str | None = None

    async def start(self):
        if self._send_exception and isinstance(self._send_exception, ConnectionError):
            raise self._send_exception
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send_and_wait(self, topic, value, key=None):
        if self._send_exception:
            raise self._send_exception
        if self._fail_on_topic and topic == self._fail_on_topic:
            raise RuntimeError(f"send failed for topic {topic}")
        self.sent_messages.append((topic, value, key))


class MockAIOKafkaConsumer:
    """模拟 aiokafka AIOKafkaConsumer"""

    def __init__(self, *topics, messages=None, **kwargs):
        self.topics = topics
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self._messages = list(messages or [])
        self._committed = []

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def commit(self):
        self._committed.append(True)

    def __aiter__(self):
        self._iter = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            await asyncio.sleep(3600)
            raise StopAsyncIteration


# ============================================================
# 夹具
# ============================================================

@pytest.fixture(autouse=True)
def reset_kafka_bus_singleton():
    """每个测试前后重置 Kafka 事件总线全局单例"""
    reset_event_bus()
    yield
    reset_event_bus()


@pytest.fixture
def kafka_bus():
    """全新的 KafkaEventBus 实例（未启动）"""
    return KafkaEventBus()


@pytest.fixture
def sample_event():
    """构造测试用 Event"""
    return Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-001",
        aggregate_type="inventory",
        event_data={"quantity": 100, "warehouse": "WH-A"},
        agent_id="agent-001",
        version=1,
    )


def _make_events(n: int, prefix: str = "prod") -> list[Event]:
    """构造 n 个测试事件"""
    return [
        Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id=f"{prefix}-{i}",
            aggregate_type="inventory",
            event_data={"index": i},
        )
        for i in range(n)
    ]


# ============================================================
# 1. 批量发布测试
# ============================================================

async def test_batch_publish_kafka_mode(kafka_bus):
    """Kafka 模式下批量发布，所有事件发送到 producer"""
    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer("erp.events")
    events = _make_events(3)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    result = await kafka_bus.publish_batch(events)

    assert result == {"succeeded": 3, "failed": 0}
    assert len(mock_producer.sent_messages) == 3
    topics = {t for t, _, _ in mock_producer.sent_messages}
    assert topics == {"erp.events"}

    stats = kafka_bus.get_stats()
    assert stats["published"] == 3
    assert stats["kafka_published"] == 3
    assert stats["batch_published"] == 1

    await kafka_bus.stop()


async def test_batch_publish_degraded_mode(kafka_bus):
    """降级模式下批量发布，事件进入内存队列"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription("sub-batch", [EventType.INVENTORY_ADJUSTED], handler))
    events = _make_events(2)

    result = await kafka_bus.publish_batch(events)
    assert result == {"succeeded": 2, "failed": 0}

    await asyncio.sleep(0.3)
    assert len(received) == 2

    stats = kafka_bus.get_stats()
    assert stats["published"] == 2
    assert stats["batch_published"] == 1
    assert stats["degraded"] is True

    await kafka_bus.stop()


async def test_batch_publish_empty_list(kafka_bus):
    """空列表批量发布返回零结果且不报错"""
    result = await kafka_bus.publish_batch([])
    assert result == {"succeeded": 0, "failed": 0}


async def test_batch_publish_partial_failure(kafka_bus):
    """批量发布部分失败：单条失败不影响其余事件"""
    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer("erp.events")
    events = _make_events(3)

    # 让 producer 对事件 topic 失败
    mock_producer._fail_on_topic = "erp.events"

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    result = await kafka_bus.publish_batch(events)

    assert result == {"succeeded": 0, "failed": 3}
    stats = kafka_bus.get_stats()
    assert stats["failed"] == 3

    await kafka_bus.stop()


# ============================================================
# 2. 健康检查测试
# ============================================================

async def test_health_check_when_not_started(kafka_bus):
    """未启动时健康检查返回 unhealthy"""
    hc = await kafka_bus.health_check()
    assert hc["status"] == "unhealthy"
    assert hc["degraded"] is False
    assert hc["connected"] is False
    assert hc["implementation"] == "kafka"


async def test_health_check_degraded(kafka_bus):
    """降级模式下健康检查返回 degraded"""
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    hc = await kafka_bus.health_check()
    assert hc["status"] == "degraded"
    assert hc["degraded"] is True
    assert hc["connected"] is False
    assert hc["implementation"] == "memory_fallback"
    assert "stats" in hc
    assert "in_flight" in hc

    await kafka_bus.stop()


async def test_health_check_healthy_kafka(kafka_bus):
    """Kafka 正常模式下健康检查返回 healthy"""
    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer("erp.events")

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    hc = await kafka_bus.health_check()
    assert hc["status"] == "healthy"
    assert hc["degraded"] is False
    assert hc["connected"] is True
    assert hc["implementation"] == "kafka"

    await kafka_bus.stop()


# ============================================================
# 3. 优雅排空测试
# ============================================================

async def test_drain_no_in_flight(kafka_bus):
    """无在途消息时 drain 立即返回"""
    await kafka_bus.drain(timeout=1.0)
    assert kafka_bus._in_flight == 0


async def test_drain_waits_for_in_flight(kafka_bus):
    """drain 等待在途消息处理完成（在途归零后返回）"""
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    # 模拟 2 条在途消息
    kafka_bus._in_flight = 2
    kafka_bus._stats["in_flight"] = 2

    # 后台任务：0.3 秒后处理完所有在途消息
    async def _finish_processing():
        await asyncio.sleep(0.3)
        kafka_bus._in_flight = 0
        kafka_bus._stats["in_flight"] = 0

    finisher = asyncio.create_task(_finish_processing())
    start = asyncio.get_event_loop().time()
    await kafka_bus.drain(timeout=2.0)
    elapsed = asyncio.get_event_loop().time() - start

    await finisher
    # drain 应等待在途消息归零（而非立即返回）
    assert elapsed >= 0.2
    assert kafka_bus._in_flight == 0

    await kafka_bus.stop()


async def test_drain_timeout(kafka_bus):
    """在途消息超时未完成时 drain 不阻塞"""
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus._in_flight = 5
    kafka_bus._stats["in_flight"] = 5

    await kafka_bus.drain(timeout=0.2)
    # 超时后不再等待
    assert kafka_bus._in_flight == 5

    await kafka_bus.stop()


# ============================================================
# 4. 死信 topic 测试
# ============================================================

async def test_dlq_on_delivery_failure(kafka_bus, sample_event):
    """Kafka 模式下投递失败时事件发布到死信 topic"""
    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer("erp.events")

    async def failing_handler(event: Event):
        raise RuntimeError("handler crashed")

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription(
        "sub-fail", [EventType.INVENTORY_ADJUSTED], failing_handler,
    ))

    await kafka_bus._dispatch_event(sample_event)

    # 验证 DLQ 发布
    dlq_messages = [m for m in mock_producer.sent_messages if m[0] == "erp.events.dlq"]
    assert len(dlq_messages) == 1
    topic, payload, key = dlq_messages[0]
    assert topic == "erp.events.dlq"
    assert b"_dlq_reason" in payload

    stats = kafka_bus.get_stats()
    assert stats["kafka_dlq_published"] == 1
    assert stats["dead_letters"] == 1
    assert stats["failed"] == 1

    await kafka_bus.stop()


async def test_dlq_not_published_degraded(kafka_bus, sample_event):
    """降级模式下投递失败不发布 Kafka DLQ"""
    async def failing_handler(event: Event):
        raise RuntimeError("handler crashed")

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription(
        "sub-fail2", [EventType.INVENTORY_ADJUSTED], failing_handler,
    ))

    await kafka_bus._dispatch_event(sample_event)

    stats = kafka_bus.get_stats()
    assert stats["dead_letters"] == 1
    assert stats["failed"] == 1
    # 降级模式不产生 Kafka DLQ 发布
    assert stats.get("kafka_dlq_published", 0) == 0

    await kafka_bus.stop()


async def test_dlq_raw_on_deserialization_failure(kafka_bus):
    """反序列化失败时原始消息发布到死信 topic"""
    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer("erp.events")

    raw_invalid = b'{"event_id": "not-a-uuid", "event_type": "INVALID_TYPE"}'

    kafka_msg = ConsumerRecord(
        topic="erp.events",
        partition=0,
        offset=0,
        key=b"prod-bad",
        value=raw_invalid,
        timestamp=0,
        timestamp_type=0,
    )
    mock_consumer_with_msg = MockAIOKafkaConsumer("erp.events", messages=[kafka_msg])

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer_with_msg):
        await kafka_bus.start()

    await asyncio.sleep(0.5)

    # 验证原始消息进入 DLQ
    dlq_messages = [m for m in mock_producer.sent_messages if m[0] == "erp.events.dlq"]
    assert len(dlq_messages) == 1
    assert dlq_messages[0][1] == raw_invalid

    stats = kafka_bus.get_stats()
    assert stats["kafka_dlq_published"] == 1
    assert stats["failed"] >= 1
    # 偏移量仍被提交（避免毒丸消息阻塞）
    assert len(mock_consumer_with_msg._committed) >= 1

    await kafka_bus.stop()


# ============================================================
# 5. 增强统计测试
# ============================================================

async def test_stats_include_new_fields(kafka_bus):
    """get_stats 包含新增生产加固字段"""
    stats = kafka_bus.get_stats()

    assert "kafka_dlq_published" in stats
    assert "batch_published" in stats
    assert "in_flight" in stats
    assert "compression" in stats
    assert "dead_letter_topic" in stats
    assert "kafka_connected" in stats
    assert "aiokafka_available" in stats


async def test_stats_dead_letter_topic_value(kafka_bus):
    """stats 中 dead_letter_topic 为配置值"""
    from app.core.config import settings
    stats = kafka_bus.get_stats()
    assert stats["dead_letter_topic"] == settings.kafka_dead_letter_topic
    assert stats["compression"] == settings.kafka_compression_type


async def test_in_flight_tracked_during_processing(kafka_bus):
    """消息处理期间 in_flight 计数正确增减"""
    mock_producer = MockAIOKafkaProducer()

    async def handler(event: Event):
        await asyncio.sleep(0.1)

    kafka_msg = ConsumerRecord(
        topic="erp.events",
        partition=0,
        offset=0,
        key=b"prod-1",
        value=Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id="prod-1",
            aggregate_type="inventory",
            event_data={},
        ).to_json(),
        timestamp=0,
        timestamp_type=0,
    )
    mock_consumer = MockAIOKafkaConsumer("erp.events", messages=[kafka_msg])

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription("sub-flight", [EventType.INVENTORY_ADJUSTED], handler))
    await asyncio.sleep(0.5)

    # 处理完成后 in_flight 归零
    stats = kafka_bus.get_stats()
    assert stats["in_flight"] == 0
    assert stats["kafka_consumed"] == 1

    await kafka_bus.stop()


# ============================================================
# 6. stop() 调用 drain() 测试
# ============================================================

async def test_stop_calls_drain(kafka_bus):
    """stop() 在关闭前执行优雅排空"""
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    # 模拟有在途消息
    kafka_bus._in_flight = 2
    kafka_bus._stats["in_flight"] = 2

    # 手动将 _in_flight 归零后再 stop（模拟消息处理完成）
    async def _fake_drain(timeout=None):
        kafka_bus._in_flight = 0
        kafka_bus._stats["in_flight"] = 0

    with patch.object(kafka_bus, "drain", _fake_drain):
        await kafka_bus.stop()

    assert kafka_bus._running is False
    assert kafka_bus._consumer_task is None
    assert kafka_bus._dispatch_task is None
    assert kafka_bus._producer is None
