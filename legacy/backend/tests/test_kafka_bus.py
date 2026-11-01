"""
Kafka事件总线测试

测试覆盖：
1. 降级行为：aiokafka 未安装 / Kafka 连接失败时自动降级到内存总线
2. 事件序列化/反序列化：JSON bytes ↔ Event 对象互转
3. Mock aiokafka 测试发布和消费流程
4. 订阅者匹配和投递（事件类型过滤、条件过滤、多订阅者）
5. 统计信息（published / delivered / failed / degraded 等）
6. 工厂函数（get_event_bus / create_event_bus）的选择逻辑
"""
import asyncio
from collections import namedtuple
from unittest.mock import patch

import pytest

from app.core.event.bus import EventBus, get_event_bus
from app.core.event.kafka_bus import (
    KafkaEventBus,
    create_event_bus as kafka_create_event_bus,
    reset_event_bus,
)
from app.core.event.models import Event, EventSubscription, EventType


# ============================================================
# 测试辅助：模拟 Kafka ConsumerRecord
# ============================================================

ConsumerRecord = namedtuple(
    "ConsumerRecord",
    ["topic", "partition", "offset", "key", "value", "timestamp", "timestamp_type"],
)


class MockAIOKafkaProducer:
    """模拟 aiokafka AIOKafkaProducer"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent_messages: list[tuple[str, bytes, bytes | None]] = []
        self._send_exception = None

    async def start(self):
        if self._send_exception and isinstance(self._send_exception, ConnectionError):
            raise self._send_exception
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send_and_wait(self, topic, value, key=None):
        if self._send_exception:
            raise self._send_exception
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
            # 模拟真实 Kafka consumer 行为：消息耗尽后阻塞等待新消息
            # 而非结束迭代（避免 _consume_loop while 循环重复进入导致重复消费）
            # 在测试中通过 stop() → 取消任务来终止
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


# ============================================================
# 1. 降级行为测试
# ============================================================

async def test_degrade_when_aiokafka_not_installed(kafka_bus):
    """aiokafka 未安装时，KafkaEventBus 降级到内存分发模式"""
    # 模拟 aiokafka 不可用
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    assert kafka_bus._degraded is True
    assert kafka_bus._running is True
    assert kafka_bus._dispatch_task is not None
    assert kafka_bus._consumer_task is None

    stats = kafka_bus.get_stats()
    assert stats["degraded"] is True
    assert stats["implementation"] == "memory_fallback"

    await kafka_bus.stop()


async def test_degrade_when_kafka_connection_fails(kafka_bus):
    """Kafka 连接失败时，KafkaEventBus 降级到内存分发模式"""
    mock_producer = MockAIOKafkaProducer()
    mock_producer._send_exception = ConnectionError("Kafka 连接失败")

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=MockAIOKafkaConsumer()):
        await kafka_bus.start()

    assert kafka_bus._degraded is True
    assert kafka_bus._running is True
    assert kafka_bus._dispatch_task is not None

    stats = kafka_bus.get_stats()
    assert stats["degraded"] is True
    assert stats["implementation"] == "memory_fallback"

    await kafka_bus.stop()


async def test_degraded_bus_publish_and_deliver(kafka_bus):
    """降级模式下，事件通过内存队列分发（与 EventBus 行为一致）"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription(
        "sub-001",
        [EventType.INVENTORY_ADJUSTED],
        handler,
    ))

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-degraded",
        aggregate_type="inventory",
        event_data={"quantity": 50},
    )
    await kafka_bus.publish(event)

    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].aggregate_id == "prod-degraded"

    stats = kafka_bus.get_stats()
    assert stats["published"] == 1
    assert stats["delivered"] == 1
    assert stats["degraded"] is True

    await kafka_bus.stop()


# ============================================================
# 2. 事件序列化/反序列化测试
# ============================================================

def test_event_serialization_roundtrip(sample_event):
    """Event.to_json() → _deserialize_event() 往返保持数据一致"""
    json_bytes = sample_event.to_json()
    assert isinstance(json_bytes, bytes)

    restored = KafkaEventBus._deserialize_event(json_bytes)

    assert restored.event_id == sample_event.event_id
    assert restored.event_type == sample_event.event_type
    assert restored.aggregate_id == sample_event.aggregate_id
    assert restored.aggregate_type == sample_event.aggregate_type
    assert restored.event_data == sample_event.event_data
    assert restored.metadata == sample_event.metadata
    assert restored.timestamp == sample_event.timestamp
    assert restored.version == sample_event.version
    assert restored.agent_id == sample_event.agent_id
    assert restored.security_level == sample_event.security_level
    assert restored.hash_chain == sample_event.hash_chain


def test_deserialize_all_event_types():
    """所有 EventType 都能正确序列化和反序列化"""
    for event_type in EventType:
        event = Event(
            event_type=event_type,
            aggregate_id=f"agg-{event_type.value}",
            aggregate_type="test",
            event_data={"type": event_type.value},
        )
        restored = KafkaEventBus._deserialize_event(event.to_json())
        assert restored.event_type == event_type
        assert restored.aggregate_id == f"agg-{event_type.value}"


def test_deserialize_with_optional_fields_null():
    """agent_id 和 hash_chain 为 None 时能正确反序列化"""
    event = Event(
        event_type=EventType.VOUCHER_POSTED,
        aggregate_id="voucher-001",
        aggregate_type="accounting",
        event_data={"amount": 1000},
        agent_id=None,
    )
    restored = KafkaEventBus._deserialize_event(event.to_json())
    assert restored.agent_id is None
    assert restored.hash_chain is None
    assert restored.event_data["amount"] == 1000


# ============================================================
# 3. Mock aiokafka 测试发布和消费
# ============================================================

async def test_kafka_mode_publish_to_kafka(kafka_bus, sample_event):
    """Kafka 模式下 publish 将事件发送到 Kafka producer"""
    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer("erp.events")

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    assert kafka_bus._degraded is False

    await kafka_bus.publish(sample_event)

    # 验证事件已发送到 producer
    assert len(mock_producer.sent_messages) == 1
    topic, value, key = mock_producer.sent_messages[0]
    assert topic == "erp.events"
    assert value == sample_event.to_json()
    assert key == sample_event.aggregate_id.encode("utf-8")

    stats = kafka_bus.get_stats()
    assert stats["published"] == 1
    assert stats["kafka_published"] == 1
    assert stats["degraded"] is False
    assert stats["implementation"] == "kafka"

    await kafka_bus.stop()


async def test_kafka_mode_consume_and_deliver(kafka_bus, sample_event):
    """Kafka 模式下消费者从 Kafka 消费事件并分发给本地订阅者"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    # 构造 Kafka 消息
    kafka_msg = ConsumerRecord(
        topic="erp.events",
        partition=0,
        offset=0,
        key=sample_event.aggregate_id.encode("utf-8"),
        value=sample_event.to_json(),
        timestamp=0,
        timestamp_type=0,
    )

    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer(messages=[kafka_msg])

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    # 订阅事件
    kafka_bus.subscribe(EventSubscription(
        "sub-consume",
        [EventType.INVENTORY_ADJUSTED],
        handler,
    ))

    # 等待消费者处理消息
    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].event_type == EventType.INVENTORY_ADJUSTED
    assert received[0].aggregate_id == "prod-001"
    assert received[0].event_data["quantity"] == 100

    # 验证偏移量已提交
    assert len(mock_consumer._committed) >= 1

    stats = kafka_bus.get_stats()
    assert stats["kafka_consumed"] == 1
    assert stats["delivered"] == 1

    await kafka_bus.stop()


async def test_kafka_mode_consume_multiple_events(kafka_bus):
    """Kafka 模式下消费多条事件并分发"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    events = [
        Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id=f"prod-{i}",
            aggregate_type="inventory",
            event_data={"index": i},
        )
        for i in range(3)
    ]

    kafka_msgs = [
        ConsumerRecord(
            topic="erp.events",
            partition=0,
            offset=i,
            key=e.aggregate_id.encode("utf-8"),
            value=e.to_json(),
            timestamp=0,
            timestamp_type=0,
        )
        for i, e in enumerate(events)
    ]

    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer(messages=kafka_msgs)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription(
        "sub-multi",
        [EventType.INVENTORY_ADJUSTED],
        handler,
    ))

    await asyncio.sleep(0.5)

    assert len(received) == 3
    aggregate_ids = [e.aggregate_id for e in received]
    assert "prod-0" in aggregate_ids
    assert "prod-1" in aggregate_ids
    assert "prod-2" in aggregate_ids

    stats = kafka_bus.get_stats()
    assert stats["kafka_consumed"] == 3
    assert stats["delivered"] == 3

    await kafka_bus.stop()


# ============================================================
# 4. 订阅者匹配和投递测试
# ============================================================

async def test_subscriber_event_type_filter(kafka_bus):
    """事件类型过滤 - 只有匹配的订阅者收到事件"""
    received_inv: list[Event] = []
    received_voucher: list[Event] = []

    async def handler_inv(event: Event):
        received_inv.append(event)

    async def handler_voucher(event: Event):
        received_voucher.append(event)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription("sub-inv", [EventType.INVENTORY_ADJUSTED], handler_inv))
    kafka_bus.subscribe(EventSubscription("sub-voucher", [EventType.VOUCHER_POSTED], handler_voucher))

    inv_event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-001",
        aggregate_type="inventory",
        event_data={},
    )
    voucher_event = Event(
        event_type=EventType.VOUCHER_POSTED,
        aggregate_id="voucher-001",
        aggregate_type="accounting",
        event_data={},
    )

    await kafka_bus.publish(inv_event)
    await kafka_bus.publish(voucher_event)

    await asyncio.sleep(0.5)

    assert len(received_inv) == 1
    assert received_inv[0].event_type == EventType.INVENTORY_ADJUSTED

    assert len(received_voucher) == 1
    assert received_voucher[0].event_type == EventType.VOUCHER_POSTED

    await kafka_bus.stop()


async def test_subscriber_filter_expr(kafka_bus):
    """filter_expr 条件过滤"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription(
        "sub-filtered",
        [EventType.INVENTORY_ADJUSTED],
        handler,
        filter_expr={"warehouse": "WH-A"},
    ))

    # 匹配
    await kafka_bus.publish(Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-match",
        aggregate_type="inventory",
        event_data={"warehouse": "WH-A"},
    ))

    # 不匹配
    await kafka_bus.publish(Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-no-match",
        aggregate_type="inventory",
        event_data={"warehouse": "WH-B"},
    ))

    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].aggregate_id == "prod-match"

    await kafka_bus.stop()


async def test_multiple_subscribers_parallel_delivery(kafka_bus):
    """多订阅者并行接收同一事件"""
    received_a: list[Event] = []
    received_b: list[Event] = []

    async def handler_a(event: Event):
        received_a.append(event)

    async def handler_b(event: Event):
        received_b.append(event)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription("sub-a", [EventType.INVENTORY_ADJUSTED], handler_a))
    kafka_bus.subscribe(EventSubscription("sub-b", [EventType.INVENTORY_ADJUSTED], handler_b))

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-parallel",
        aggregate_type="inventory",
        event_data={},
    )
    await kafka_bus.publish(event)

    await asyncio.sleep(0.5)

    assert len(received_a) == 1
    assert len(received_b) == 1
    assert received_a[0].aggregate_id == "prod-parallel"
    assert received_b[0].aggregate_id == "prod-parallel"

    stats = kafka_bus.get_stats()
    assert stats["delivered"] == 2

    await kafka_bus.stop()


async def test_unsubscribe(kafka_bus):
    """取消订阅后不再收到事件"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    kafka_bus.subscribe(EventSubscription("sub-cancel", [EventType.INVENTORY_ADJUSTED], handler))
    assert kafka_bus.get_stats()["subscriptions"] == 1

    kafka_bus.unsubscribe("sub-cancel")
    assert kafka_bus.get_stats()["subscriptions"] == 0

    await kafka_bus.publish(Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-after-unsub",
        aggregate_type="inventory",
        event_data={},
    ))

    await asyncio.sleep(0.5)
    assert len(received) == 0

    await kafka_bus.stop()


async def test_dead_letter_queue_on_failure(kafka_bus):
    """处理失败的事件进入死信队列"""
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    async def failing_handler(event: Event):
        raise RuntimeError("模拟处理失败")

    kafka_bus.subscribe(EventSubscription(
        "failing-sub",
        [EventType.INVENTORY_ADJUSTED],
        failing_handler,
    ))

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-fail",
        aggregate_type="inventory",
        event_data={},
    )
    await kafka_bus.publish(event)

    # 等待重试完成（3次重试 + 指数退避）
    await asyncio.sleep(2.0)

    stats = kafka_bus.get_stats()
    assert stats["published"] == 1
    assert stats["failed"] == 1
    assert stats["dead_letters"] == 1

    assert len(kafka_bus._dead_letter_queue) == 1
    dead_event, dead_error = kafka_bus._dead_letter_queue[0]
    assert dead_event.aggregate_id == "prod-fail"

    await kafka_bus.stop()


# ============================================================
# 5. 统计信息测试
# ============================================================

async def test_stats_initial_state(kafka_bus):
    """初始状态统计信息"""
    stats = kafka_bus.get_stats()
    assert stats["published"] == 0
    assert stats["delivered"] == 0
    assert stats["failed"] == 0
    assert stats["kafka_published"] == 0
    assert stats["kafka_consumed"] == 0
    assert stats["queue_size"] == 0
    assert stats["dead_letters"] == 0
    assert stats["subscriptions"] == 0
    assert stats["degraded"] is False
    assert stats["implementation"] == "kafka"  # 未启动时默认显示 kafka
    assert stats["aiokafka_available"] is False  # 测试环境未安装 aiokafka


async def test_stats_after_degraded_publish(kafka_bus):
    """降级模式发布后的统计"""
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()

    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    kafka_bus.subscribe(EventSubscription(
        "sub-stats",
        [EventType.INVENTORY_ADJUSTED],
        handler,
    ))

    await kafka_bus.publish(Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-stats",
        aggregate_type="inventory",
        event_data={},
    ))

    await asyncio.sleep(0.5)

    stats = kafka_bus.get_stats()
    assert stats["published"] == 1
    assert stats["delivered"] == 1
    assert stats["subscriptions"] == 1
    assert stats["degraded"] is True
    assert stats["implementation"] == "memory_fallback"
    assert stats["kafka_published"] == 0  # 降级模式不发送到 Kafka

    await kafka_bus.stop()


async def test_stats_kafka_mode(kafka_bus, sample_event):
    """Kafka 模式下的统计信息"""
    mock_producer = MockAIOKafkaProducer()
    mock_consumer = MockAIOKafkaConsumer()

    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True), \
         patch("app.core.event.kafka_bus.AIOKafkaProducer", return_value=mock_producer), \
         patch("app.core.event.kafka_bus.AIOKafkaConsumer", return_value=mock_consumer):
        await kafka_bus.start()

    await kafka_bus.publish(sample_event)

    stats = kafka_bus.get_stats()
    assert stats["published"] == 1
    assert stats["kafka_published"] == 1
    assert stats["degraded"] is False
    assert stats["implementation"] == "kafka"
    assert stats["kafka_connected"] is True

    await kafka_bus.stop()


# ============================================================
# 6. 工厂函数测试
# ============================================================

def test_factory_returns_memory_bus_when_disabled():
    """kafka_enabled=False 时工厂返回内存 EventBus"""
    reset_event_bus()
    with patch("app.core.config.settings.kafka_enabled", False):
        bus = get_event_bus()
    assert isinstance(bus, EventBus)
    assert not isinstance(bus, KafkaEventBus)


def test_factory_returns_kafka_bus_when_enabled():
    """kafka_enabled=True 时工厂返回 KafkaEventBus（即使 aiokafka 未安装）"""
    reset_event_bus()
    with patch("app.core.config.settings.kafka_enabled", True), \
         patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        bus = get_event_bus()
    assert isinstance(bus, KafkaEventBus)
    # 未启动时 degraded=False；start() 后才降级
    assert bus._degraded is False


def test_factory_returns_kafka_bus_when_aiokafka_available():
    """kafka_enabled=True 且 aiokafka 可用时工厂返回 KafkaEventBus"""
    reset_event_bus()
    with patch("app.core.config.settings.kafka_enabled", True), \
         patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True):
        bus = get_event_bus()
    assert isinstance(bus, KafkaEventBus)


def test_factory_singleton_caching():
    """工厂函数返回全局单例（多次调用返回同一实例）"""
    reset_event_bus()
    with patch("app.core.config.settings.kafka_enabled", True), \
         patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", True):
        bus1 = get_event_bus()
        bus2 = get_event_bus()
    assert bus1 is bus2


async def test_create_event_bus_memory():
    """create_event_bus 在 kafka 未启用时返回已启动的内存 EventBus"""
    with patch("app.core.config.settings.kafka_enabled", False):
        bus = await kafka_create_event_bus()
    try:
        assert isinstance(bus, EventBus)
        assert not isinstance(bus, KafkaEventBus)
        assert bus._running is True
    finally:
        await bus.stop()


async def test_create_event_bus_kafka_degraded():
    """create_event_bus 在 Kafka 不可用时返回已降级启动的 KafkaEventBus"""
    with patch("app.core.config.settings.kafka_enabled", True), \
         patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        bus = await kafka_create_event_bus()
    try:
        assert isinstance(bus, KafkaEventBus)
        assert bus._running is True
        assert bus._degraded is True
        assert bus._dispatch_task is not None
        assert bus._consumer_task is None

        stats = bus.get_stats()
        assert stats["degraded"] is True
        assert stats["implementation"] == "memory_fallback"
    finally:
        await bus.stop()


# ============================================================
# 7. 接口兼容性测试
# ============================================================

def test_kafka_bus_has_same_interface_as_event_bus():
    """KafkaEventBus 与 EventBus 接口兼容"""
    event_bus_methods = {
        "start", "stop", "subscribe", "unsubscribe",
        "publish", "get_stats",
    }
    kafka_bus_methods = set(dir(KafkaEventBus))
    missing = event_bus_methods - kafka_bus_methods
    assert not missing, f"KafkaEventBus 缺少方法: {missing}"


async def test_kafka_bus_start_stop_idempotent(kafka_bus):
    """重复 start/stop 不会出错"""
    with patch("app.core.event.kafka_bus._AIOKAFKA_AVAILABLE", False):
        await kafka_bus.start()
        await kafka_bus.start()  # 重复启动应幂等

    assert kafka_bus._running is True

    await kafka_bus.stop()
    await kafka_bus.stop()  # 重复停止应幂等

    assert kafka_bus._running is False
