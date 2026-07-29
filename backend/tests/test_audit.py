"""
审计链测试
验证不可篡改审计日志的记录、查询、哈希链完整性、高风险告警
"""
import asyncio

import pytest

from app.core.security.audit import AuditChain, AuditEntry
from app.core.event.models import EventType


# ========== test_log_and_query ==========

async def test_log_and_query(fresh_audit_chain):
    """记录并查询审计日志"""
    chain = fresh_audit_chain

    # 记录多条审计日志
    entry_id1 = await chain.log(
        actor_did="did:erp:inventory:agent001",
        action="inventory.adjust",
        resource="inventory:prod-001",
        result="success",
        details={"quantity": 100, "adjustment": +50},
        risk_level="low",
    )
    entry_id2 = await chain.log(
        actor_did="did:erp:accounting:agent001",
        action="voucher.generate",
        resource="voucher:V001",
        result="success",
        details={"amount": 1000.0, "standard": "CAS"},
        risk_level="medium",
    )
    entry_id3 = await chain.log(
        actor_did="did:erp:inventory:agent001",
        action="inventory.delete",
        resource="inventory:prod-002",
        result="failed",
        details={"error": "权限不足"},
        risk_level="high",
    )

    # 每条返回entry_id
    assert entry_id1 is not None
    assert entry_id2 is not None
    assert entry_id3 is not None
    assert entry_id1 != entry_id2

    # 查询所有日志
    all_entries = chain.query()
    assert len(all_entries) == 3

    # 按操作者查询
    inv_entries = chain.query(actor_did="did:erp:inventory:agent001")
    assert len(inv_entries) == 2
    for entry in inv_entries:
        assert entry["actor_did"] == "did:erp:inventory:agent001"

    # 按操作类型查询
    delete_entries = chain.query(action="inventory.delete")
    assert len(delete_entries) == 1
    assert delete_entries[0]["resource"] == "inventory:prod-002"
    assert delete_entries[0]["result"] == "failed"

    # 按风险等级查询
    high_entries = chain.query(risk_level="high")
    assert len(high_entries) == 1
    assert high_entries[0]["action"] == "inventory.delete"

    # 统计
    stats = chain.get_stats()
    assert stats["total_entries"] == 3
    assert stats["risk_distribution"]["low"] == 1
    assert stats["risk_distribution"]["medium"] == 1
    assert stats["risk_distribution"]["high"] == 1


# ========== test_chain_integrity ==========

async def test_chain_integrity(fresh_audit_chain):
    """审计链完整性验证"""
    chain = fresh_audit_chain

    # 记录多条审计日志
    for i in range(5):
        await chain.log(
            actor_did=f"did:erp:test:agent{i:03d}",
            action=f"test.action.{i}",
            resource=f"resource:{i}",
            result="success",
            details={"index": i, "data": f"payload-{i}"},
            risk_level="low",
        )

    # 初始状态完整性验证通过
    assert chain.verify_integrity() is True

    # 篡改第三条日志的详情
    chain._entries[2].details["tampered"] = True

    # 完整性验证应失败
    assert chain.verify_integrity() is False

    # 恢复
    del chain._entries[2].details["tampered"]
    assert chain.verify_integrity() is True

    # 篡改哈希链指针
    original_prev = chain._entries[3].prev_hash
    chain._entries[3].prev_hash = "fake_hash"
    assert chain.verify_integrity() is False

    # 恢复
    chain._entries[3].prev_hash = original_prev
    assert chain.verify_integrity() is True

    # 统计中的完整性状态
    stats = chain.get_stats()
    assert stats["integrity_valid"] is True


# ========== test_high_risk_alert ==========

async def test_high_risk_alert(fresh_audit_chain, started_bus):
    """高风险操作告警 - 发布SECURITY_ALERT事件"""
    chain = fresh_audit_chain

    # 记录一个高风险操作
    await chain.log(
        actor_did="did:erp:test:risky_agent",
        action="data.delete_bulk",
        resource="database:production",
        result="success",
        details={"rows_deleted": 10000},
        risk_level="critical",
    )

    # 验证审计日志已记录
    entries = chain.query(risk_level="critical")
    assert len(entries) == 1
    assert entries[0]["action"] == "data.delete_bulk"

    # 验证事件总线收到审计事件和安全告警事件
    await asyncio.sleep(0.5)

    stats = started_bus.get_stats()
    # 高风险操作发布2个事件：AUDIT_LOG_CREATED + SECURITY_ALERT
    assert stats["published"] >= 2


# ========== 额外测试：审计条目哈希计算 ==========

async def test_audit_entry_hash(fresh_audit_chain):
    """审计条目哈希正确计算"""
    chain = fresh_audit_chain

    entry_id = await chain.log(
        actor_did="did:erp:test:hash_agent",
        action="test.hash",
        resource="test:resource",
        result="success",
        details={"key": "value"},
        risk_level="low",
    )

    entry = chain._entries[0]
    assert entry.hash is not None
    assert len(entry.hash) == 64  # SHA-256 hex

    # 第一个条目的 prev_hash 为 None
    assert entry.prev_hash is None

    # 添加第二个条目
    await chain.log(
        actor_did="did:erp:test:hash_agent2",
        action="test.hash2",
        resource="test:resource2",
        result="success",
        details={"key": "value2"},
        risk_level="low",
    )

    entry2 = chain._entries[1]
    # 第二个条目的 prev_hash 应等于第一个条目的 hash
    assert entry2.prev_hash == entry.hash


# ========== 额外测试：查询限制 ==========

async def test_query_limit(fresh_audit_chain):
    """查询结果数量限制"""
    chain = fresh_audit_chain

    for i in range(10):
        await chain.log(
            actor_did=f"did:erp:test:agent{i}",
            action="test.action",
            resource=f"resource:{i}",
            result="success",
            details={"index": i},
            risk_level="low",
        )

    # 限制返回数量
    limited = chain.query(limit=3)
    assert len(limited) == 3

    # 查询返回最新的（逆序）
    all_entries = chain.query()
    assert all_entries[0]["details"]["index"] == 9  # 最新


# ========== 额外测试：空审计链 ==========

def test_empty_audit_chain(fresh_audit_chain):
    """空审计链完整性验证通过"""
    chain = fresh_audit_chain

    assert chain.verify_integrity() is True
    assert chain.query() == []

    stats = chain.get_stats()
    assert stats["total_entries"] == 0
    assert stats["risk_distribution"] == {}
