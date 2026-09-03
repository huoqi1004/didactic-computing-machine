"""
P2-3 高可用与备份恢复测试

覆盖：
1. EventBus 优雅排空（drain）：等待队列清空、接口兼容
2. EventBus 健康检查（health_check）：运行/停止状态
3. BackupManager 实时数据快照导出（export_snapshot）：
   - 导出系统真实数据（事件存储/审计/RBAC/Agent状态）
   - 生成 JSON 文件 + SHA-256 校验和
   - 登记备份记录
4. 快照数据读取（get_snapshot_data）
"""
import asyncio
import json
import os
import tempfile

import pytest

from app.core.event.bus import EventBus
from app.core.event.models import Event, EventSubscription, EventType


# ============================================================
# 1. EventBus 优雅排空测试
# ============================================================

@pytest.fixture
def memory_bus():
    """内存事件总线（未启动）"""
    return EventBus()


async def test_event_bus_drain_empty_queue(memory_bus):
    """空队列 drain 立即返回"""
    await memory_bus.start()
    await memory_bus.drain(timeout=1.0)
    assert memory_bus._event_queue.qsize() == 0
    await memory_bus.stop()


async def test_event_bus_drain_waits_queue_drain(memory_bus):
    """drain 等待队列中事件分发完成"""
    received: list[Event] = []

    async def handler(event: Event):
        received.append(event)

    await memory_bus.start()
    memory_bus.subscribe(EventSubscription(
        "sub-drain", [EventType.INVENTORY_ADJUSTED], handler,
    ))

    for i in range(3):
        await memory_bus.publish(Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id=f"prod-{i}",
            aggregate_type="inventory",
            event_data={},
        ))

    # 等待分发循环开始处理
    await asyncio.sleep(0.2)
    await memory_bus.drain(timeout=2.0)

    # 队列应为空
    assert memory_bus._event_queue.qsize() == 0

    # 稍等让分发循环把剩余事件处理完
    await asyncio.sleep(0.5)
    await memory_bus.stop()


async def test_event_bus_health_check_running(memory_bus):
    """运行中健康检查返回 healthy"""
    await memory_bus.start()
    hc = await memory_bus.health_check()
    assert hc["status"] == "healthy"
    assert hc["implementation"] == "memory"
    assert hc["degraded"] is False
    assert hc["connected"] is True
    assert "stats" in hc
    await memory_bus.stop()


async def test_event_bus_health_check_stopped(memory_bus):
    """停止后健康检查返回 unhealthy"""
    hc = await memory_bus.health_check()
    assert hc["status"] == "unhealthy"
    assert hc["connected"] is False


async def test_event_bus_drain_interface_compatible(memory_bus):
    """drain 接口与 KafkaEventBus 兼容（带 timeout 参数）"""
    await memory_bus.start()
    await memory_bus.drain(timeout=0.5)  # 不抛异常即兼容
    await memory_bus.stop()


# ============================================================
# 2. BackupManager 快照导出测试
# ============================================================

@pytest.fixture
def backup_manager():
    """独立的 BackupManager 实例"""
    from app.core.admin.backup import BackupManager
    return BackupManager()


@pytest.fixture(autouse=True)
def temp_backup_dir(monkeypatch):
    """将备份目录重定向到临时目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import app.core.admin.backup as backup_module
        monkeypatch.setattr(backup_module, "_SNAPSHOT_DIR", tmpdir)
        yield tmpdir


def test_export_snapshot_creates_file(backup_manager, temp_backup_dir):
    """快照导出生成 JSON 文件并登记备份记录"""
    result = backup_manager.export_snapshot()

    assert result["status"] == "success"
    assert "backup" in result
    assert "checksum" in result
    backup = result["backup"]
    assert backup["type"] == "snapshot"
    assert backup["status"] == "completed"

    # 文件存在
    path = backup["storage_path"]
    assert os.path.exists(path)

    # 文件内容可解析且结构正确
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "exported_at" in data
    assert "sections" in data
    assert "event_store" in data["sections"]
    assert "config" in data["sections"]


def test_export_snapshot_checksum_valid(backup_manager):
    """校验和与文件内容匹配"""
    result = backup_manager.export_snapshot()
    backup = result["backup"]

    with open(backup["storage_path"], "rb") as f:
        content = f.read()

    import hashlib
    actual = hashlib.sha256(content).hexdigest()
    assert actual == result["checksum"]
    assert actual == backup["checksum"]


def test_export_snapshot_registers_backup(backup_manager):
    """导出后可通过 get_backups 查询到快照记录"""
    result = backup_manager.export_snapshot()
    backup_id = result["backup"]["backup_id"]

    backups = backup_manager.get_backups(backup_type="snapshot")
    ids = [b["backup_id"] for b in backups]
    assert backup_id in ids


def test_get_snapshot_data_success(backup_manager):
    """读取已导出的快照数据"""
    result = backup_manager.export_snapshot()
    backup_id = result["backup"]["backup_id"]

    data = backup_manager.get_snapshot_data(backup_id)
    assert data["status"] == "success"
    assert "snapshot" in data
    assert "sections" in data["snapshot"]


def test_get_snapshot_data_not_found(backup_manager):
    """读取不存在的备份返回错误"""
    data = backup_manager.get_snapshot_data("backup-404")
    assert data["error"] == "backup_not_found"


def test_get_snapshot_data_wrong_type(backup_manager):
    """读取非快照类型的备份返回错误"""
    # 创建一个普通备份
    result = backup_manager.create_backup(type="full")
    backup_id = result["backup"]["backup_id"]

    data = backup_manager.get_snapshot_data(backup_id)
    assert data["error"] == "not_a_snapshot_backup"


def test_export_snapshot_collects_real_data(backup_manager):
    """快照采集事件存储与配置真实数据"""
    # 注入一条事件到事件存储
    from app.core.event.store import event_store
    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-snap-001",
        aggregate_type="inventory",
        event_data={"qty": 10},
    )
    # 直接写入内存流（绕过异步接口）
    event_store._streams["prod-snap-001"].append(event)

    result = backup_manager.export_snapshot()
    backup_id = result["backup"]["backup_id"]
    data = backup_manager.get_snapshot_data(backup_id)
    snapshot = data["snapshot"]

    # 事件存储部分包含注入的事件
    event_store_section = snapshot["sections"]["event_store"]
    if isinstance(event_store_section, list):
        ids = [e["aggregate_id"] for e in event_store_section]
        assert "prod-snap-001" in ids

    # 配置部分包含非敏感配置
    config_section = snapshot["sections"]["config"]
    assert "app_version" in config_section
    assert "kafka_enabled" in config_section
