"""
pytest 测试夹具
提供事件总线、事件存储、Agent实例等测试夹具
每个测试前后自动重置全局单例状态，保证测试隔离
"""
import asyncio
import sys
import os

# 确保后端根目录在 Python 路径中
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
import pytest_asyncio

from app.core.event.bus import EventBus, event_bus
from app.core.event.store import EventStore, event_store
from app.core.event.models import Event, EventType, EventSubscription
from app.core.security.did import DIDRegistry, did_registry
from app.core.security.authorization import (
    AuthorizationEngine,
    AuthorizationRequest,
    AuthorizationResult,
    authz_engine,
)
from app.core.security.audit import AuditChain, audit_chain
from app.core.security.guardrails import SecurityGuardrails, guardrails
from app.core.event.saga import Saga, SagaStep, SagaCoordinator, saga_coordinator
from app.core.agent.orchestrator import AgentOrchestrator, orchestrator
from app.core.config import SecurityLevel, Permission
from app.core.knowledge.graph import (
    KnowledgeGraph as CoreKnowledgeGraph,
    knowledge_graph as core_knowledge_graph,
)
from app.core.knowledge.rag import (
    InMemoryVectorStore,
    vector_store as core_vector_store,
)
from app.core.knowledge.rules import (
    RuleEngine as CoreRuleEngine,
    rule_engine as core_rule_engine,
    load_builtin_rules as load_core_builtin_rules,
)


# ========== 全局单例重置 ==========

@pytest.fixture(autouse=True)
def reset_global_singletons():
    """每个测试前自动重置所有全局单例的内部状态，保证测试隔离"""

    # 事件总线
    event_bus._subscriptions.clear()
    event_bus._dead_letter_queue.clear()
    event_bus._stats = {"published": 0, "delivered": 0, "failed": 0}
    event_bus._running = False
    event_bus._dispatcher_task = None
    event_bus._event_queue = asyncio.Queue()

    # 事件存储
    event_store._streams.clear()
    event_store._snapshots.clear()
    event_store._last_hash = None
    event_store._hash_chain.clear()

    # DID 注册中心
    did_registry._identities.clear()
    did_registry._private_keys.clear()
    did_registry._revoked.clear()

    # 授权引擎
    authz_engine._active_sessions.clear()
    authz_engine._denied_count = 0
    authz_engine._granted_count = 0

    # 审计链
    audit_chain._entries.clear()
    audit_chain._last_hash = None
    # 审计链 - 区块链客户端状态（保证 test_blockchain 隔离）
    audit_chain._blockchain_client = None
    audit_chain._blockchain_initialized = False
    audit_chain._blockchain_tx_index.clear()

    # 区块链客户端全局单例（保证 fabric_client 模块状态隔离）
    try:
        from app.core.blockchain.fabric_client import reset_blockchain_client
        reset_blockchain_client()
    except Exception:  # noqa: BLE001
        pass

    # 安全护栏
    guardrails._blocked_count = 0
    guardrails._sanitized_count = 0
    guardrails._allowed_count = 0

    # Saga 协调器
    saga_coordinator._active_sagas.clear()
    saga_coordinator._completed_sagas.clear()

    # Agent 编排器
    orchestrator._agents.clear()
    orchestrator._agents_by_name.clear()
    orchestrator._goals.clear()
    orchestrator._consensus_votes.clear()

    # 采购知识图谱
    from app.agents.procurement.agent import knowledge_graph
    knowledge_graph.clear()

    # 认知与知识层 - 全局单例重置
    # 知识图谱（app.core.knowledge.graph，与采购Agent的轻量级图谱不同）
    core_knowledge_graph.clear()
    # 向量库（RAG 检索）
    core_vector_store.clear()
    # 规则引擎：清空后重新加载预置业务规则
    # （rules.py 模块加载时已注入，此处保证每个测试都拥有干净的默认规则集）
    core_rule_engine.clear()
    load_core_builtin_rules(core_rule_engine)

    # 版本化与灰度发布系统 - 全局单例重置
    from app.core.versioning import (
        version_registry as ver_registry,
        canary_manager as can_mgr,
        digital_twin as dig_twin,
        snapshot_manager as snap_mgr,
    )
    ver_registry.clear()
    can_mgr.clear()
    dig_twin.clear()
    snap_mgr.clear()

    yield


# ========== 全局单例夹具（使用重置后的全局实例） ==========

@pytest.fixture
def bus():
    """全局事件总线单例（已重置）"""
    return event_bus


@pytest.fixture
def store():
    """全局事件存储单例（已重置）"""
    return event_store


@pytest.fixture
def registry():
    """全局DID注册中心单例（已重置）"""
    return did_registry


@pytest.fixture
def authz():
    """全局授权引擎单例（已重置）"""
    return authz_engine


@pytest.fixture
def audit():
    """全局审计链单例（已重置）"""
    return audit_chain


@pytest.fixture
def guardrail():
    """全局安全护栏单例（已重置）"""
    return guardrails


@pytest.fixture
def coordinator():
    """全局Saga协调器单例（已重置）"""
    return saga_coordinator


# ========== 全新实例夹具（用于单元测试隔离） ==========

@pytest.fixture
def fresh_bus():
    """全新事件总线实例（与全局单例隔离）"""
    return EventBus()


@pytest.fixture
def fresh_store():
    """全新事件存储实例"""
    return EventStore()


@pytest.fixture
def fresh_registry():
    """全新DID注册中心实例"""
    return DIDRegistry()


@pytest.fixture
def fresh_guardrails():
    """全新安全护栏实例"""
    return SecurityGuardrails()


@pytest.fixture
def fresh_audit_chain():
    """全新审计链实例"""
    return AuditChain()


@pytest.fixture
def fresh_authz_engine(fresh_registry):
    """全新授权引擎实例（绑定全新的DID注册中心）"""
    return AuthorizationEngine(registry=fresh_registry)


@pytest.fixture
def fresh_coordinator():
    """全新Saga协调器实例"""
    return SagaCoordinator()


@pytest.fixture
def fresh_orchestrator():
    """全新Agent编排器实例"""
    return AgentOrchestrator()


# ========== 认知与知识层夹具 ==========

@pytest.fixture
def fresh_knowledge_graph():
    """全新知识图谱实例（与全局单例隔离）"""
    return CoreKnowledgeGraph()


@pytest.fixture
def fresh_vector_store():
    """全新向量库实例（与全局单例隔离）"""
    return InMemoryVectorStore()


@pytest.fixture
def fresh_rule_engine():
    """全新规则引擎实例（预置规则已注入）"""
    engine = CoreRuleEngine()
    load_core_builtin_rules(engine)
    return engine


@pytest.fixture
def knowledge_graph_global():
    """全局知识图谱单例（已重置）"""
    return core_knowledge_graph


@pytest.fixture
def vector_store_global():
    """全局向量库单例（已重置）"""
    return core_vector_store


@pytest.fixture
def rule_engine_global():
    """全局规则引擎单例（已重置，预置规则已加载）"""
    return core_rule_engine


# ========== 已启动的事件总线夹具 ==========

@pytest_asyncio.fixture
async def started_bus():
    """启动全局事件总线分发器，测试后自动停止"""
    await event_bus.start()
    yield event_bus
    await event_bus.stop()


@pytest_asyncio.fixture
async def started_fresh_bus(fresh_bus):
    """启动全新事件总线实例，测试后自动停止"""
    await fresh_bus.start()
    yield fresh_bus
    await fresh_bus.stop()


# ========== 辅助夹具 ==========

@pytest.fixture
def make_event():
    """事件工厂：快速创建测试事件"""
    def _make(
        event_type: EventType = EventType.INVENTORY_ADJUSTED,
        aggregate_id: str = "test-agg-001",
        aggregate_type: str = "test",
        event_data: dict | None = None,
        agent_id: str | None = None,
        version: int = 1,
    ) -> Event:
        return Event(
            event_type=event_type,
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            event_data=event_data or {"key": "value"},
            agent_id=agent_id,
            version=version,
        )
    return _make


@pytest.fixture
def make_subscription():
    """订阅工厂：快速创建事件订阅"""
    def _make(
        subscriber_id: str = "test-subscriber",
        event_types: list[EventType] | None = None,
        handler=None,
        filter_expr: dict | None = None,
    ) -> EventSubscription:
        return EventSubscription(
            subscriber_id=subscriber_id,
            event_types=event_types or [EventType.INVENTORY_ADJUSTED],
            handler=handler or (lambda e: None),
            filter_expr=filter_expr,
        )
    return _make


@pytest.fixture
def registered_agent(registry):
    """已注册的测试Agent身份"""
    identity = registry.register_agent(
        name="测试Agent",
        domain="test",
        capabilities=["general", "inventory_monitoring", "voucher_generation"],
        security_clearance="confidential",
    )
    return identity


@pytest.fixture
def authz_request_factory():
    """授权请求工厂"""
    def _make(
        agent_did: str,
        intent: str = "inventory_monitoring",
        target_resource: str = "resource:test",
        data_security_level: SecurityLevel = SecurityLevel.INTERNAL,
        context: dict | None = None,
        permissions: list[Permission] | None = None,
    ) -> AuthorizationRequest:
        return AuthorizationRequest(
            agent_did=agent_did,
            intent=intent,
            target_resource=target_resource,
            data_security_level=data_security_level,
            context=context or {},
            requested_permissions=permissions or [Permission.READ],
        )
    return _make
