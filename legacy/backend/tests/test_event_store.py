"""
事件存储测试
验证不可变事件日志的追加、检索、事件重放、哈希链完整性、快照机制
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.event.store import EventStore
from app.core.event.aggregate import AggregateRoot


# ========== test_append_and_retrieve ==========

async def test_append_and_retrieve(fresh_store):
    """追加事件后可检索"""
    store = fresh_store

    event1 = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-001",
        aggregate_type="inventory",
        event_data={"quantity": 100, "product_name": "Widget"},
        version=1,
    )
    event2 = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-001",
        aggregate_type="inventory",
        event_data={"quantity": 80, "product_name": "Widget"},
        version=2,
    )
    event3 = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-002",
        aggregate_type="inventory",
        event_data={"quantity": 50, "product_name": "Gadget"},
        version=1,
    )

    hash1 = await store.append(event1)
    hash2 = await store.append(event2)
    hash3 = await store.append(event3)

    # 每次追加返回哈希
    assert isinstance(hash1, str)
    assert len(hash1) == 64  # SHA-256 hex
    assert hash1 != hash2
    assert hash2 != hash3

    # 检索 prod-001 的事件流
    events = await store.get_events("prod-001")
    assert len(events) == 2
    assert events[0].version == 1
    assert events[0].event_data["quantity"] == 100
    assert events[1].version == 2
    assert events[1].event_data["quantity"] == 80

    # 检索 prod-002 的事件流
    events_p2 = await store.get_events("prod-002")
    assert len(events_p2) == 1
    assert events_p2[0].event_data["product_name"] == "Gadget"

    # 按版本范围检索
    events_from_v2 = await store.get_events("prod-001", from_version=2)
    assert len(events_from_v2) == 1
    assert events_from_v2[0].version == 2

    # 按事件类型检索
    typed_events = await store.get_events_by_type(EventType.INVENTORY_ADJUSTED)
    assert len(typed_events) == 3

    # 统计
    stats = store.get_stats()
    assert stats["total_events"] == 3
    assert stats["total_streams"] == 2
    assert stats["hash_chain_length"] == 3


# ========== test_event_replay ==========

async def test_event_replay(store):
    """事件重放重建状态"""
    store = store

    # 追加一系列库存调整事件
    for i in range(1, 6):
        event = Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id="prod-replay",
            aggregate_type="inventory",
            event_data={
                "quantity": 100 + i * 10,
                "product_name": f"Product-{i}",
                "safety_stock": 50,
                "reorder_point": 100,
                "unit_cost": 10.0,
                "warehouse": "WH-A",
            },
            version=i,
        )
        await store.append(event)

    # 从事件存储创建聚合根并重放历史
    from app.agents.inventory.agent import InventoryAggregate

    aggregate = InventoryAggregate("prod-replay")
    await aggregate.load_from_history()

    # 验证状态由最后一个事件重建
    assert aggregate.quantity == 150  # 100 + 5*10
    assert aggregate.product_name == "Product-5"
    assert aggregate.safety_stock == 50
    assert aggregate.reorder_point == 100
    assert aggregate.unit_cost == 10.0
    assert aggregate.version == 5


# ========== test_hash_chain_integrity ==========

async def test_hash_chain_integrity(fresh_store):
    """哈希链完整性验证"""
    store = fresh_store

    # 追加3个事件到同一流
    events = []
    for i in range(1, 4):
        event = Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id="prod-chain",
            aggregate_type="inventory",
            event_data={"quantity": i * 10, "batch": f"B{i}"},
            version=i,
        )
        await store.append(event)
        events.append(event)

    # 初始状态下完整性验证通过
    assert await store.verify_integrity() is True

    # 篡改第一个事件的负载数据（非最后事件，篡改应被链式哈希检测到）
    events[0].event_data["quantity"] = 999

    # 完整性验证应失败
    assert await store.verify_integrity() is False

    # 恢复数据
    events[0].event_data["quantity"] = 10

    # 再次验证应通过
    assert await store.verify_integrity() is True

    # 篡改中间事件的 hash_chain 指针
    original_hash_chain = events[1].hash_chain
    events[1].hash_chain = "tampered_hash_value"
    assert await store.verify_integrity() is False

    # 恢复
    events[1].hash_chain = original_hash_chain
    assert await store.verify_integrity() is True


# ========== test_snapshot ==========

async def test_snapshot(fresh_store):
    """快照保存和恢复"""
    store = fresh_store

    # 追加事件
    for i in range(1, 4):
        event = Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id="prod-snap",
            aggregate_type="inventory",
            event_data={"quantity": i * 10},
            version=i,
        )
        await store.append(event)

    # 无快照时返回 None
    assert await store.get_latest_snapshot("prod-snap") is None

    # 保存快照
    snapshot_data = {
        "version": 3,
        "quantity": 30,
        "product_name": "SnapshottedProduct",
    }
    await store.save_snapshot("prod-snap", 3, snapshot_data)

    # 检索快照
    snapshot = await store.get_latest_snapshot("prod-snap")
    assert snapshot is not None
    version, state = snapshot
    assert version == 3
    assert state["quantity"] == 30
    assert state["product_name"] == "SnapshottedProduct"

    # 保存多个快照，获取最新的
    await store.save_snapshot("prod-snap", 5, {"version": 5, "quantity": 50})
    latest = await store.get_latest_snapshot("prod-snap")
    assert latest is not None
    assert latest[0] == 5
    assert latest[1]["quantity"] == 50

    # 统计
    stats = store.get_stats()
    assert stats["total_snapshots"] == 2


# ========== 额外测试：空流查询 ==========

async def test_get_events_empty_stream(fresh_store):
    """查询不存在的事件流返回空列表"""
    store = fresh_store
    events = await store.get_events("nonexistent-stream")
    assert events == []


# ========== 额外测试：快照加速重放 ==========

async def test_snapshot_accelerates_replay(store):
    """快照加速重放 - 从快照点开始而非从头"""
    store = store

    # 追加10个事件
    for i in range(1, 11):
        event = Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id="prod-fast",
            aggregate_type="inventory",
            event_data={"quantity": i * 10, "safety_stock": 50,
                        "reorder_point": 100, "unit_cost": 10.0,
                        "product_name": "FastProduct", "warehouse": "WH-A"},
            version=i,
        )
        await store.append(event)

    # 在版本5保存快照
    await store.save_snapshot("prod-fast", 5, {
        "version": 5,
        "quantity": 50,
        "product_name": "FastProduct",
        "safety_stock": 50,
        "reorder_point": 100,
        "unit_cost": 10.0,
        "warehouse": "WH-A",
    })

    # 创建聚合根，从快照重放
    from app.agents.inventory.agent import InventoryAggregate

    aggregate = InventoryAggregate("prod-fast")
    await aggregate.load_from_history()

    # 最终状态应该是最后一个事件（版本10）的值
    assert aggregate.version == 10
    assert aggregate.quantity == 100  # 10 * 10
