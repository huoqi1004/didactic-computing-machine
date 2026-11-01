"""
区块链审计层测试

测试覆盖：
1. HashChainFallbackClient 全部方法（submit/query_range/verify/height/stats）
2. FabricAuditClient 降级行为（Fabric 不可用时自动降级）
3. AuditChain 与区块链的集成（log 自动提交、verify_on_blockchain、query_from_blockchain）
4. API 端点（/security/blockchain/stats、verify、query）
5. 异常类与全局单例工厂
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes import router
from app.core.blockchain import (
    BlockchainAuditClient,
    BlockchainError,
    BlockchainUnavailableError,
    BlockchainTimeoutError,
    BlockchainValidationError,
    ChaincodeInvokeError,
    FabricAuditClient,
    HashChainFallbackClient,
    get_blockchain_client,
    reset_blockchain_client,
)
from app.core.blockchain.fabric_client import _compute_entry_hash
from app.core.config import settings
from app.core.security.audit import AuditChain


# ============================================================
# 辅助函数与夹具
# ============================================================

def _make_entry(
    entry_id: str = "test-entry-001",
    actor_did: str = "did:erp:test:agent",
    action: str = "test.action",
    resource: str = "test:resource",
    result: str = "success",
    risk_level: str = "low",
    timestamp: str = "2026-01-01T00:00:00Z",
    details: dict | None = None,
) -> dict:
    """构造测试用 audit entry dict"""
    return {
        "entry_id": entry_id,
        "actor_did": actor_did,
        "action": action,
        "resource": resource,
        "result": result,
        "details": details or {"key": "value"},
        "risk_level": risk_level,
        "timestamp": timestamp,
    }


@pytest.fixture
def fallback_client():
    """全新的 HashChainFallbackClient 实例"""
    return HashChainFallbackClient()


@pytest_asyncio.fixture
async def api_client():
    """API 测试客户端（不触发 lifespan）"""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def initialized_audit_chain():
    """已初始化区块链客户端（降级模式）的 AuditChain 实例"""
    chain = AuditChain()
    await chain.init_blockchain()
    return chain


# ============================================================
# 1. HashChainFallbackClient 测试
# ============================================================

async def test_fallback_submit_returns_tx_id(fallback_client):
    """测试1: submit_audit_entry 返回以 'hc:' 前缀的交易ID"""
    entry = _make_entry()
    tx_id = await fallback_client.submit_audit_entry(entry)

    assert isinstance(tx_id, str)
    assert tx_id.startswith("hc:")
    # tx_id 应唯一
    tx_id2 = await fallback_client.submit_audit_entry(_make_entry(entry_id="test-002"))
    assert tx_id != tx_id2


async def test_fallback_submit_builds_hash_chain(fallback_client):
    """测试2: submit 后记录包含 prev_hash 和 entry_hash，且哈希链正确链接"""
    entry1 = _make_entry(entry_id="e1")
    entry2 = _make_entry(entry_id="e2")

    tx1 = await fallback_client.submit_audit_entry(entry1)
    tx2 = await fallback_client.submit_audit_entry(entry2)

    # 查询并校验哈希链
    record1 = await fallback_client.query_audit_entry(tx1)
    record2 = await fallback_client.query_audit_entry(tx2)

    assert record1["prev_hash"] is None or record1["prev_hash"] == ""
    # 第二条记录的 prev_hash 应等于第一条的 entry_hash
    assert record2["prev_hash"] == record1["entry_hash"]
    # entry_hash 应为 64 字符的 SHA-256 hex
    assert len(record1["entry_hash"]) == 64
    assert len(record2["entry_hash"]) == 64


async def test_fallback_query_by_entry_id(fallback_client):
    """测试3: query_audit_entry 支持按 entry_id 查询（兼容 Fabric tx_id 与业务 entry_id）"""
    entry = _make_entry(entry_id="biz-001")
    tx_id = await fallback_client.submit_audit_entry(entry)

    # 按 tx_id 查询
    record_by_tx = await fallback_client.query_audit_entry(tx_id)
    assert record_by_tx["entry_id"] == "biz-001"

    # 按 entry_id 查询
    record_by_entry = await fallback_client.query_audit_entry("biz-001")
    assert record_by_entry["tx_id"] == tx_id
    assert record_by_entry["entry_id"] == "biz-001"

    # 查询不存在的ID返回空 dict
    empty = await fallback_client.query_audit_entry("nonexistent")
    assert empty == {}


async def test_fallback_query_range_by_time(fallback_client):
    """测试4: query_audit_range 按时间范围过滤"""
    # 提交3条记录，时间各不相同
    for i, ts in enumerate([
        "2026-01-01T00:00:00Z",
        "2026-02-01T00:00:00Z",
        "2026-03-01T00:00:00Z",
    ]):
        await fallback_client.submit_audit_entry(
            _make_entry(entry_id=f"e{i}", timestamp=ts)
        )

    # 查询全部
    all_records = await fallback_client.query_audit_range("", "")
    assert len(all_records) == 3

    # 查询 2 月之后的
    feb_plus = await fallback_client.query_audit_range(
        "2026-02-01T00:00:00Z", "9999-12-31T23:59:59Z"
    )
    assert len(feb_plus) == 2
    assert feb_plus[0]["entry_id"] == "e1"
    assert feb_plus[1]["entry_id"] == "e2"


async def test_fallback_verify_integrity_valid(fallback_client):
    """测试5: verify_entry_integrity 对未篡改记录返回 True"""
    entry = _make_entry()
    tx_id = await fallback_client.submit_audit_entry(entry)
    record = await fallback_client.query_audit_entry(tx_id)

    assert await fallback_client.verify_entry_integrity(record) is True


async def test_fallback_verify_integrity_tampered(fallback_client):
    """测试6: verify_entry_integrity 对篡改记录返回 False"""
    entry = _make_entry()
    tx_id = await fallback_client.submit_audit_entry(entry)
    record = await fallback_client.query_audit_entry(tx_id)

    # 篡改 action 字段
    record["action"] = "tampered.action"
    assert await fallback_client.verify_entry_integrity(record) is False

    # 篡改 entry_hash
    record2 = await fallback_client.query_audit_entry(tx_id)
    record2["entry_hash"] = "fake_hash"
    assert await fallback_client.verify_entry_integrity(record2) is False


async def test_fallback_get_chain_height_and_stats(fallback_client):
    """测试7: get_chain_height 与 get_chain_stats 返回正确数据"""
    for i in range(5):
        await fallback_client.submit_audit_entry(
            _make_entry(entry_id=f"h{i}", risk_level="low" if i % 2 == 0 else "high")
        )

    height = await fallback_client.get_chain_height()
    assert height == 5

    stats = await fallback_client.get_chain_stats()
    assert stats["backend"] == "hash_chain_fallback"
    assert stats["total_entries"] == 5
    assert stats["chain_height"] == 5
    assert stats["integrity_valid"] is True
    assert stats["fabric_available"] is False
    assert stats["risk_distribution"]["low"] == 3
    assert stats["risk_distribution"]["high"] == 2


# ============================================================
# 2. FabricAuditClient 降级行为测试
# ============================================================

async def test_fabric_client_degrades_when_sdk_unavailable():
    """测试8: FabricAuditClient 在 SDK 不可用时抛出 BlockchainUnavailableError"""
    # FabricAuditClient._ensure_initialized 会检查 _FABRIC_SDK_AVAILABLE
    # 在测试环境中 fabric-sdk-py 通常未安装，应抛出 BlockchainUnavailableError
    client = FabricAuditClient()
    with pytest.raises(BlockchainUnavailableError):
        await client._ensure_initialized()


async def test_get_blockchain_client_falls_back_when_disabled():
    """测试9: blockchain_enabled=False 时 get_blockchain_client 返回 HashChainFallbackClient"""
    reset_blockchain_client()
    original = settings.blockchain_enabled
    try:
        settings.blockchain_enabled = False
        client = await get_blockchain_client()
        assert isinstance(client, HashChainFallbackClient)
        # 二次调用应返回同一实例（懒加载缓存）
        client2 = await get_blockchain_client()
        assert client2 is client
    finally:
        settings.blockchain_enabled = original
        reset_blockchain_client()


async def test_get_blockchain_client_falls_back_when_sdk_missing():
    """测试10: blockchain_enabled=True 但 SDK 缺失时降级到 HashChainFallbackClient"""
    reset_blockchain_client()
    original = settings.blockchain_enabled
    try:
        settings.blockchain_enabled = True
        client = await get_blockchain_client()
        # 测试环境无 fabric-sdk-py，应降级
        assert isinstance(client, HashChainFallbackClient)
    finally:
        settings.blockchain_enabled = original
        reset_blockchain_client()


async def test_fabric_client_retry_with_mock():
    """测试11: FabricAuditClient 重试机制（通过 mock _do_invoke 模拟临时失败后成功）"""
    client = FabricAuditClient()
    # 跳过初始化检查：手动设置已初始化 + sdk_client
    client._initialized = True
    client._sdk_client = MagicMock()  # 非 None，绕过初始化

    # mock _do_invoke：前两次抛临时异常（generic Exception，会被重试），
    # 第三次返回成功
    call_count = {"n": 0}

    async def mock_do_invoke(fn_name, args, *, is_query=False):
        call_count["n"] += 1
        if call_count["n"] < 3:
            # 临时性失败（非 ChaincodeInvokeError），应触发重试
            raise ConnectionError("模拟网络临时故障")
        return {"tx_id": "fabric-tx-abc-123", "entry_id": "e1"}

    with patch.object(client, "_do_invoke", side_effect=mock_do_invoke):
        # 缩短退避时间，避免测试等待过久
        with patch.object(asyncio, "sleep", new=AsyncMock()):
            tx_id = await client.submit_audit_entry(_make_entry())

    assert tx_id == "fabric-tx-abc-123"
    assert call_count["n"] == 3  # 重试2次后第3次成功


async def test_fabric_client_no_retry_on_business_error():
    """测试12: ChaincodeInvokeError（业务异常）不重试，直接抛出"""
    client = FabricAuditClient()
    client._initialized = True
    client._sdk_client = MagicMock()

    call_count = {"n": 0}

    async def mock_do_invoke(fn_name, args, *, is_query=False):
        call_count["n"] += 1
        raise ChaincodeInvokeError("业务异常，不应重试")

    with patch.object(client, "_do_invoke", side_effect=mock_do_invoke):
        with patch.object(asyncio, "sleep", new=AsyncMock()):
            with pytest.raises(ChaincodeInvokeError):
                await client.submit_audit_entry(_make_entry())

    assert call_count["n"] == 1  # 业务异常不重试


# ============================================================
# 3. AuditChain 与区块链集成测试
# ============================================================

async def test_audit_chain_init_blockchain_degraded_mode():
    """测试13: AuditChain.init_blockchain 在测试环境下降级到 HashChainFallbackClient"""
    chain = AuditChain()
    ok = await chain.init_blockchain()
    assert ok is True
    assert chain._blockchain_client is not None
    assert isinstance(chain._blockchain_client, HashChainFallbackClient)


async def test_audit_chain_log_submits_to_blockchain(initialized_audit_chain):
    """测试14: log() 后审计记录同步提交到区块链（降级客户端）"""
    chain = initialized_audit_chain
    entry_id = await chain.log(
        actor_did="did:erp:test:agent",
        action="test.blockchain",
        resource="test:resource",
        result="success",
        details={"key": "value"},
        risk_level="low",
    )

    # 验证 entry_id -> tx_id 映射已建立
    assert entry_id in chain._blockchain_tx_index
    tx_id = chain._blockchain_tx_index[entry_id]
    assert tx_id.startswith("hc:")

    # 验证区块链客户端内存中有该记录
    record = await chain._blockchain_client.query_audit_entry(tx_id)
    assert record["entry_id"] == entry_id
    assert record["action"] == "test.blockchain"


async def test_audit_chain_log_blockchain_failure_does_not_break(fresh_audit_chain):
    """测试15: 区块链提交失败不影响审计日志主流程"""
    chain = fresh_audit_chain
    # 注入一个会抛异常的 mock 客户端
    mock_client = AsyncMock(spec=BlockchainAuditClient)
    mock_client.implementation_name = "MockFailingClient"
    mock_client.submit_audit_entry = AsyncMock(
        side_effect=ChaincodeInvokeError("模拟链码调用失败")
    )
    chain._blockchain_client = mock_client

    # log 应正常返回 entry_id，不被区块链异常影响
    entry_id = await chain.log(
        actor_did="did:erp:test",
        action="test.resilience",
        resource="test:resource",
        result="success",
        details={},
        risk_level="low",
    )
    assert entry_id is not None
    # 本地哈希链仍有记录
    assert len(chain._entries) == 1
    # mock 被调用过
    mock_client.submit_audit_entry.assert_called_once()


async def test_audit_chain_verify_on_blockchain(initialized_audit_chain):
    """测试16: verify_on_blockchain 对已提交记录返回 True"""
    chain = initialized_audit_chain
    entry_id = await chain.log(
        actor_did="did:erp:test",
        action="test.verify",
        resource="test:resource",
        result="success",
        details={"key": "value"},
        risk_level="low",
    )

    verified = await chain.verify_on_blockchain(entry_id)
    assert verified is True

    # 验证不存在的 entry_id 返回 False
    verified_missing = await chain.verify_on_blockchain("nonexistent-id")
    assert verified_missing is False


async def test_audit_chain_verify_on_blockchain_no_client(fresh_audit_chain):
    """测试17: 区块链客户端未初始化时 verify_on_blockchain 返回 False"""
    chain = fresh_audit_chain
    # _blockchain_client 为 None（未调用 init_blockchain）
    assert chain._blockchain_client is None
    verified = await chain.verify_on_blockchain("any-id")
    assert verified is False


async def test_audit_chain_query_from_blockchain(initialized_audit_chain):
    """测试18: query_from_blockchain 返回区块链上的审计记录"""
    chain = initialized_audit_chain
    # 提交多条记录
    for i in range(3):
        await chain.log(
            actor_did=f"did:erp:agent{i}",
            action="test.query",
            resource=f"resource:{i}",
            result="success",
            details={"index": i},
            risk_level="low",
        )

    # 查询全部
    results = await chain.query_from_blockchain({"limit": 100})
    assert len(results) == 3

    # 按 actor 过滤
    filtered = await chain.query_from_blockchain(
        {"actor_did": "did:erp:agent1", "limit": 100}
    )
    assert len(filtered) == 1
    assert filtered[0]["actor_did"] == "did:erp:agent1"

    # 按 action 过滤
    action_filtered = await chain.query_from_blockchain(
        {"action": "test.query", "limit": 100}
    )
    assert len(action_filtered) == 3


async def test_audit_chain_query_from_blockchain_no_client(fresh_audit_chain):
    """测试19: 区块链客户端未初始化时 query_from_blockchain 返回空列表"""
    chain = fresh_audit_chain
    assert chain._blockchain_client is None
    results = await chain.query_from_blockchain({"limit": 100})
    assert results == []


async def test_audit_chain_get_stats_includes_blockchain_fields(initialized_audit_chain):
    """测试20: get_stats 包含区块链相关字段"""
    chain = initialized_audit_chain
    await chain.log(
        actor_did="did:erp:test",
        action="test.stats",
        resource="test:resource",
        result="success",
        details={},
        risk_level="low",
    )

    stats = chain.get_stats()
    assert "blockchain_available" in stats
    assert stats["blockchain_available"] is True
    assert "blockchain_initialized" in stats
    assert stats["blockchain_initialized"] is True
    assert "blockchain_implementation" in stats
    assert stats["blockchain_implementation"] == "HashChainFallbackClient"
    assert "chain_height" in stats
    assert stats["chain_height"] == 1
    assert "blockchain_tx_count" in stats
    assert stats["blockchain_tx_count"] == 1


# ============================================================
# 4. API 端点测试
# ============================================================

async def test_api_blockchain_stats_uninitialized(api_client):
    """测试21: 区块链未初始化时 stats 端点返回 available=False"""
    # 注意：api_client 不触发 lifespan，audit_chain._blockchain_client 为 None
    response = await api_client.get("/api/v1/security/blockchain/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False


async def test_api_blockchain_stats_initialized(api_client, audit):
    """测试22: 区块链初始化后 stats 端点返回完整统计"""
    # audit fixture 已重置 audit_chain 单例；手动初始化区块链
    await audit.init_blockchain()
    await audit.log(
        actor_did="did:erp:api-test",
        action="test.api",
        resource="test:resource",
        result="success",
        details={},
        risk_level="low",
    )

    response = await api_client.get("/api/v1/security/blockchain/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert data["backend"] == "hash_chain_fallback"
    assert data["total_entries"] >= 1


async def test_api_blockchain_verify(api_client, audit):
    """测试23: verify 端点在链上验证审计记录"""
    await audit.init_blockchain()
    entry_id = await audit.log(
        actor_did="did:erp:verify-test",
        action="test.verify",
        resource="test:resource",
        result="success",
        details={},
        risk_level="low",
    )

    response = await api_client.get(
        f"/api/v1/security/blockchain/verify/{entry_id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["entry_id"] == entry_id
    assert data["verified"] is True
    assert data["blockchain_available"] is True


async def test_api_blockchain_query(api_client, audit):
    """测试24: query 端点从区块链查询审计记录"""
    await audit.init_blockchain()
    for i in range(3):
        await audit.log(
            actor_did="did:erp:query-test",
            action="test.query",
            resource=f"resource:{i}",
            result="success",
            details={"index": i},
            risk_level="low",
        )

    # 查询全部
    response = await api_client.get(
        "/api/v1/security/blockchain/query?limit=100"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 3
    assert data["blockchain_available"] is True

    # 按 actor 过滤
    response_filtered = await api_client.get(
        "/api/v1/security/blockchain/query?actor_did=did:erp:query-test&limit=100"
    )
    assert response_filtered.status_code == 200
    assert response_filtered.json()["count"] == 3


# ============================================================
# 5. 异常类与工具函数测试
# ============================================================

def test_exception_hierarchy():
    """测试25: 异常类继承关系正确"""
    assert issubclass(BlockchainUnavailableError, BlockchainError)
    assert issubclass(BlockchainTimeoutError, BlockchainError)
    assert issubclass(BlockchainValidationError, BlockchainError)
    assert issubclass(ChaincodeInvokeError, BlockchainError)


def test_exception_carries_detail():
    """测试26: 异常携带 detail 上下文"""
    err = BlockchainUnavailableError("连接失败", detail={"peer": "peer0.erp.com"})
    assert err.message == "连接失败"
    assert err.detail["peer"] == "peer0.erp.com"
    assert "peer0.erp.com" in str(err)


def test_compute_entry_hash_consistency():
    """测试27: _compute_entry_hash 与 AuditEntry.compute_hash 算法一致"""
    from app.core.security.audit import AuditEntry

    # 构造相同输入
    entry_dict = {
        "entry_id": "consistency-001",
        "actor_did": "did:erp:test",
        "action": "test",
        "resource": "res",
        "result": "success",
        "risk_level": "low",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "details": {"b": 2, "a": 1},
    }

    # blockchain 层计算
    hash_blockchain = _compute_entry_hash(entry_dict, prev_hash=None)

    # audit 层计算（AuditEntry）
    audit_entry = AuditEntry(
        actor_did="did:erp:test",
        action="test",
        resource="res",
        result="success",
        details={"b": 2, "a": 1},
        risk_level="low",
    )
    audit_entry.entry_id = "consistency-001"
    audit_entry.timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    hash_audit = audit_entry.compute_hash(prev_hash=None)

    # 两个哈希应一致（算法相同）
    assert hash_blockchain == hash_audit
    assert len(hash_blockchain) == 64


async def test_fallback_client_reset_method():
    """测试28: HashChainFallbackClient._reset 清空内部状态"""
    client = HashChainFallbackClient()
    await client.submit_audit_entry(_make_entry(entry_id="r1"))
    assert await client.get_chain_height() == 1

    client._reset()
    assert await client.get_chain_height() == 0
    assert client._last_hash is None


async def test_abstract_class_cannot_instantiate():
    """测试29: BlockchainAuditClient 抽象类不能直接实例化"""
    with pytest.raises(TypeError):
        BlockchainAuditClient()  # type: ignore[abstract]
