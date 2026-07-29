"""
授权引擎测试
验证意图级动态授权：身份验证、安全许可检查、高风险升级、能力匹配
"""
import pytest

from app.core.config import SecurityLevel, Permission
from app.core.security.did import DIDRegistry
from app.core.security.authorization import (
    AuthorizationEngine,
    AuthorizationRequest,
    RiskLevel,
)


# ========== test_grant_authorization ==========

async def test_grant_authorization(fresh_registry, fresh_authz_engine):
    """正常授权通过"""
    registry = fresh_registry
    engine = fresh_authz_engine

    # 注册一个具有足够许可的Agent
    identity = registry.register_agent(
        name="库存守护Agent",
        domain="inventory",
        capabilities=["inventory_monitoring", "general"],
        security_clearance="internal",
    )

    request = AuthorizationRequest(
        agent_did=identity.did,
        intent="inventory_monitoring",
        target_resource="inventory:prod-001",
        data_security_level=SecurityLevel.INTERNAL,
        context={},
        requested_permissions=[Permission.READ],
    )

    result = await engine.authorize(request)

    assert result.granted is True
    assert Permission.READ in result.granted_permissions
    assert result.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)
    assert result.session_id is not None
    assert result.expires_in is not None
    assert "授权通过" in result.reason


# ========== test_deny_insufficient_clearance ==========

async def test_deny_insufficient_clearance(fresh_registry, fresh_authz_engine):
    """安全许可不足拒绝"""
    registry = fresh_registry
    engine = fresh_authz_engine

    # 注册一个低许可等级的Agent
    identity = registry.register_agent(
        name="低权限Agent",
        domain="test",
        capabilities=["general"],
        security_clearance="internal",  # 许可等级1
    )

    # 请求访问机密数据（许可等级2），应被拒绝
    request = AuthorizationRequest(
        agent_did=identity.did,
        intent="general_access",
        target_resource="confidential:data",
        data_security_level=SecurityLevel.CONFIDENTIAL,  # 许可等级2
        context={},
        requested_permissions=[Permission.READ],
    )

    result = await engine.authorize(request)

    assert result.granted is False
    assert result.granted_permissions == []
    assert "安全许可等级不足" in result.reason
    assert "confidential" in result.reason
    assert "internal" in result.reason


# ========== test_high_risk_escalation ==========

async def test_high_risk_escalation(fresh_registry, fresh_authz_engine):
    """高风险升级人类审批"""
    registry = fresh_registry
    engine = fresh_authz_engine

    # 注册一个高许可Agent
    identity = registry.register_agent(
        name="高权限Agent",
        domain="finance",
        capabilities=["delete_sensitive_data", "general"],
        security_clearance="top_secret",  # 最高许可等级3
    )

    # 请求删除绝密数据 - 风险评分应超过75
    # TOP_SECRET数据 = 60分 + DELETE权限 = 30分 = 90分 >= 75
    request = AuthorizationRequest(
        agent_did=identity.did,
        intent="delete_sensitive_data",
        target_resource="top_secret:financial_records",
        data_security_level=SecurityLevel.TOP_SECRET,
        context={"amount": 2000000, "cross_domain": True},
        requested_permissions=[Permission.DELETE],
    )

    result = await engine.authorize(request)

    assert result.granted is False
    assert result.risk_score >= 75
    assert result.risk_level == RiskLevel.CRITICAL
    assert "升级人类审批" in result.reason


# ========== test_capability_mismatch ==========

async def test_capability_mismatch(fresh_registry, fresh_authz_engine):
    """能力不匹配拒绝"""
    registry = fresh_registry
    engine = fresh_authz_engine

    # 注册一个只有库存能力的Agent
    identity = registry.register_agent(
        name="库存专用Agent",
        domain="inventory",
        capabilities=["inventory_monitoring"],  # 只有库存监控能力，没有general
        security_clearance="confidential",
    )

    # 请求执行记账意图 - 能力不匹配
    request = AuthorizationRequest(
        agent_did=identity.did,
        intent="voucher_generation",  # 记账意图
        target_resource="accounting:voucher",
        data_security_level=SecurityLevel.INTERNAL,  # 数据等级低于许可，通过许可检查
        context={},
        requested_permissions=[Permission.WRITE],
    )

    result = await engine.authorize(request)

    assert result.granted is False
    assert "能力范围" in result.reason
    assert "voucher_generation" in result.reason


# ========== 额外测试：无效身份拒绝 ==========

async def test_invalid_identity_denied(fresh_authz_engine):
    """无效DID身份拒绝授权"""
    engine = fresh_authz_engine

    request = AuthorizationRequest(
        agent_did="did:erp:test:nonexistent",
        intent="any_operation",
        target_resource="any:resource",
        data_security_level=SecurityLevel.PUBLIC,
        context={},
        requested_permissions=[Permission.READ],
    )

    result = await engine.authorize(request)

    assert result.granted is False
    assert result.risk_score == 100
    assert result.risk_level == RiskLevel.CRITICAL
    assert "身份验证失败" in result.reason


# ========== 额外测试：会话回收 ==========

async def test_session_revoke(fresh_registry, fresh_authz_engine):
    """任务结束即回收权限"""
    registry = fresh_registry
    engine = fresh_authz_engine

    identity = registry.register_agent(
        name="会话测试Agent",
        domain="test",
        capabilities=["general"],
        security_clearance="internal",
    )

    request = AuthorizationRequest(
        agent_did=identity.did,
        intent="general_operation",
        target_resource="test:resource",
        data_security_level=SecurityLevel.INTERNAL,
        context={},
        requested_permissions=[Permission.READ],
    )

    result = await engine.authorize(request)
    assert result.granted is True
    assert result.session_id is not None

    # 验证活跃会话
    stats = engine.get_stats()
    assert stats["active_sessions"] == 1

    # 回收会话
    engine.revoke_session(result.session_id)

    stats = engine.get_stats()
    assert stats["active_sessions"] == 0


# ========== 额外测试：风险评分计算 ==========

async def test_risk_score_calculation(fresh_registry, fresh_authz_engine):
    """风险评分计算正确"""
    registry = fresh_registry
    engine = fresh_authz_engine

    identity = registry.register_agent(
        name="风险测试Agent",
        domain="test",
        capabilities=["general"],
        security_clearance="confidential",
    )

    # 低风险请求：PUBLIC数据 + READ权限
    low_request = AuthorizationRequest(
        agent_did=identity.did,
        intent="general",
        target_resource="public:info",
        data_security_level=SecurityLevel.PUBLIC,
        context={},
        requested_permissions=[Permission.READ],
    )
    low_result = await engine.authorize(low_request)
    assert low_result.risk_score <= 25  # READ=5
    assert low_result.risk_level == RiskLevel.LOW

    # 中风险请求：INTERNAL数据 + EXECUTE权限
    # INTERNAL=10 + EXECUTE=20 = 30 → MEDIUM (26-50)
    mid_request = AuthorizationRequest(
        agent_did=identity.did,
        intent="general",
        target_resource="internal:info",
        data_security_level=SecurityLevel.INTERNAL,
        context={},
        requested_permissions=[Permission.EXECUTE],
    )
    mid_result = await engine.authorize(mid_request)
    assert 26 <= mid_result.risk_score <= 50  # INTERNAL=10 + EXECUTE=20 = 30
    assert mid_result.risk_level == RiskLevel.MEDIUM


# ========== 额外测试：统计信息 ==========

async def test_authorization_stats(fresh_registry, fresh_authz_engine):
    """授权统计信息正确"""
    registry = fresh_registry
    engine = fresh_authz_engine

    # 授权通过
    identity_ok = registry.register_agent(
        "授权通过Agent", "test", ["general"], "internal"
    )
    req_ok = AuthorizationRequest(
        agent_did=identity_ok.did,
        intent="general",
        target_resource="test:resource",
        data_security_level=SecurityLevel.INTERNAL,
        context={},
        requested_permissions=[Permission.READ],
    )
    await engine.authorize(req_ok)

    # 授权拒绝（许可不足）
    identity_low = registry.register_agent(
        "低权限Agent", "test", ["general"], "internal"
    )
    req_denied = AuthorizationRequest(
        agent_did=identity_low.did,
        intent="general",
        target_resource="secret:data",
        data_security_level=SecurityLevel.TOP_SECRET,
        context={},
        requested_permissions=[Permission.READ],
    )
    await engine.authorize(req_denied)

    stats = engine.get_stats()
    assert stats["granted"] == 1
    assert stats["denied"] == 1
    assert stats["grant_rate"] == 0.5
