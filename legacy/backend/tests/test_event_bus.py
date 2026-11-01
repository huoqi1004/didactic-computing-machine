"""
事件总线测试
验证发布订阅模式、多订阅者并行投递、事件类型过滤、死信队列机制
"""
import asyncio

import pytest

from app.core.event.bus import EventBus
from app.core.event.models import Event, EventType, EventSubscription


# ========== test_publish_subscribe ==========

async def test_publish_subscribe(started_fresh_bus):
    """发布事件后订阅者收到"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    bus = started_fresh_bus
    sub = EventSubscription(
        subscriber_id="sub-001",
        event_types=[EventType.INVENTORY_ADJUSTED],
        handler=handler,
    )
    bus.subscribe(sub)

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-001",
        aggregate_type="inventory",
        event_data={"quantity": 100, "product_name": "Widget"},
    )
    await bus.publish(event)

    # 等待分发器处理
    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].event_type == EventType.INVENTORY_ADJUSTED
    assert received[0].aggregate_id == "prod-001"
    assert received[0].event_data["quantity"] == 100

    stats = bus.get_stats()
    assert stats["published"] == 1
    assert stats["delivered"] == 1


# ========== test_multiple_subscribers ==========

async def test_multiple_subscribers(started_fresh_bus):
    """多订阅者并行接收"""
    received_a: list[Event] = []
    received_b: list[Event] = []

    async def handler_a(event: Event):
        received_a.append(event)

    async def handler_b(event: Event):
        received_b.append(event)

    bus = started_fresh_bus
    bus.subscribe(EventSubscription("sub-a", [EventType.INVENTORY_ADJUSTED], handler_a))
    bus.subscribe(EventSubscription("sub-b", [EventType.INVENTORY_ADJUSTED], handler_b))

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-002",
        aggregate_type="inventory",
        event_data={"quantity": 50},
    )
    await bus.publish(event)

    await asyncio.sleep(0.5)

    assert len(received_a) == 1
    assert len(received_b) == 1
    assert received_a[0].aggregate_id == "prod-002"
    assert received_b[0].aggregate_id == "prod-002"

    stats = bus.get_stats()
    assert stats["delivered"] == 2


# ========== test_event_filter ==========

async def test_event_filter(started_fresh_bus):
    """事件类型过滤 - 只有匹配的订阅者收到事件"""
    received_matching: list[Event] = []
    received_other: list[Event] = []

    async def handler_match(event: Event):
        received_matching.append(event)

    async def handler_other(event: Event):
        received_other.append(event)

    bus = started_fresh_bus
    # sub-match 订阅库存调整事件
    bus.subscribe(EventSubscription(
        "sub-match",
        [EventType.INVENTORY_ADJUSTED],
        handler_match,
    ))
    # sub-other 订阅凭证生成事件
    bus.subscribe(EventSubscription(
        "sub-other",
        [EventType.VOUCHER_GENERATED],
        handler_other,
    ))

    # 发布库存调整事件 - 只有 sub-match 应收到
    inv_event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-003",
        aggregate_type="inventory",
        event_data={"quantity": 200},
    )
    await bus.publish(inv_event)

    # 发布凭证生成事件 - 只有 sub-other 应收到
    voucher_event = Event(
        event_type=EventType.VOUCHER_GENERATED,
        aggregate_id="voucher-001",
        aggregate_type="accounting",
        event_data={"amount": 1000},
    )
    await bus.publish(voucher_event)

    await asyncio.sleep(0.5)

    assert len(received_matching) == 1
    assert received_matching[0].event_type == EventType.INVENTORY_ADJUSTED

    assert len(received_other) == 1
    assert received_other[0].event_type == EventType.VOUCHER_GENERATED


# ========== test_dead_letter ==========

async def test_dead_letter(started_fresh_bus):
    """失败事件进入死信队列"""
    bus = started_fresh_bus

    async def failing_handler(event: Event):
        raise RuntimeError("模拟处理失败")

    bus.subscribe(EventSubscription(
        "failing-sub",
        [EventType.INVENTORY_ADJUSTED],
        failing_handler,
    ))

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-004",
        aggregate_type="inventory",
        event_data={"quantity": 10},
    )
    await bus.publish(event)

    # 等待重试完成（3次重试 + 指数退避，约0.3秒）
    await asyncio.sleep(2.0)

    stats = bus.get_stats()
    assert stats["published"] == 1
    assert stats["failed"] == 1
    assert stats["dead_letters"] == 1

    # 验证死信队列内容
    assert len(bus._dead_letter_queue) == 1
    dead_event, dead_error = bus._dead_letter_queue[0]
    assert dead_event.aggregate_id == "prod-004"
    # RetryError 包装了原始异常，提取内部异常消息
    if hasattr(dead_error, 'last_attempt'):
        inner = dead_error.last_attempt.exception()
        assert "模拟处理失败" in str(inner)
    else:
        assert "模拟处理失败" in str(dead_error)


# ========== 额外测试：filter_expr 过滤 ==========

async def test_event_filter_expr(started_fresh_bus):
    """filter_expr 条件过滤 - 只有满足条件的事件被投递"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    bus = started_fresh_bus
    bus.subscribe(EventSubscription(
        "filtered-sub",
        [EventType.INVENTORY_ADJUSTED],
        handler,
        filter_expr={"warehouse": "WH-A"},
    ))

    # 匹配的事件
    event_match = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-005",
        aggregate_type="inventory",
        event_data={"quantity": 100, "warehouse": "WH-A"},
    )
    await bus.publish(event_match)

    # 不匹配的事件
    event_no_match = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-006",
        aggregate_type="inventory",
        event_data={"quantity": 50, "warehouse": "WH-B"},
    )
    await bus.publish(event_no_match)

    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].aggregate_id == "prod-005"


# ========== 额外测试：取消订阅 ==========

async def test_unsubscribe(started_fresh_bus):
    """取消订阅后不再收到事件"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    bus = started_fresh_bus
    bus.subscribe(EventSubscription("sub-cancel", [EventType.INVENTORY_ADJUSTED], handler))

    # 取消订阅
    bus.unsubscribe("sub-cancel")

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-007",
        aggregate_type="inventory",
        event_data={},
    )
    await bus.publish(event)

    await asyncio.sleep(0.5)

    assert len(received) == 0
    assert bus.get_stats()["subscriptions"] == 0
