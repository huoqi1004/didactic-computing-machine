"""
DID身份系统测试
验证Agent身份注册、验证、吊销机制
"""
import pytest

from app.core.security.did import DIDRegistry, AgentIdentity


# ========== test_register_agent ==========

def test_register_agent(fresh_registry):
    """注册Agent身份"""
    registry = fresh_registry

    identity = registry.register_agent(
        name="库存守护Agent",
        domain="inventory",
        capabilities=["inventory_monitoring", "demand_forecasting", "general"],
        security_clearance="internal",
    )

    # 验证返回的身份对象
    assert isinstance(identity, AgentIdentity)
    assert identity.did.startswith("did:erp:inventory:")
    assert identity.name == "库存守护Agent"
    assert identity.domain == "inventory"
    assert "inventory_monitoring" in identity.capabilities
    assert "general" in identity.capabilities
    assert identity.security_clearance == "internal"
    assert identity.status == "active"
    assert identity.public_key  # 公钥非空
    assert len(identity.public_key) == 64  # SHA-256 hex

    # 验证DID唯一性
    identity2 = registry.register_agent(
        name="记账Agent",
        domain="accounting",
        capabilities=["bookkeeping", "general"],
        security_clearance="confidential",
    )
    assert identity.did != identity2.did

    # 验证注册后可查询
    found = registry.get_identity(identity.did)
    assert found is not None
    assert found.name == "库存守护Agent"

    # 验证统计
    stats = registry.get_stats()
    assert stats["total"] == 2
    assert stats["active"] == 2
    assert stats["revoked"] == 0


# ========== test_verify_identity ==========

def test_verify_identity(fresh_registry):
    """验证有效身份"""
    registry = fresh_registry

    identity = registry.register_agent(
        name="验证测试Agent",
        domain="test",
        capabilities=["general"],
        security_clearance="internal",
    )

    # 有效身份验证通过
    assert registry.verify_identity(identity.did) is True

    # 更新最后活跃时间
    old_active = identity.last_active
    registry.update_last_active(identity.did)
    assert identity.last_active >= old_active

    # 不存在的DID验证失败
    assert registry.verify_identity("did:erp:test:nonexistent") is False


# ========== test_revoke_identity ==========

def test_revoke_identity(fresh_registry):
    """吊销身份后验证失败"""
    registry = fresh_registry

    identity = registry.register_agent(
        name="待吊销Agent",
        domain="test",
        capabilities=["general"],
        security_clearance="confidential",
    )

    # 吊销前验证通过
    assert registry.verify_identity(identity.did) is True
    assert identity.status == "active"

    # 吊销身份
    registry.revoke_identity(identity.did, reason="安全违规")

    # 吊销后验证失败
    assert registry.verify_identity(identity.did) is False
    assert identity.status == "revoked"

    # 统计反映吊销状态
    stats = registry.get_stats()
    assert stats["total"] == 1
    assert stats["active"] == 0
    assert stats["revoked"] == 1


# ========== 额外测试：DID格式 ==========

def test_did_format(fresh_registry):
    """DID标识符格式正确"""
    registry = fresh_registry

    did = registry.generate_did("inventory")
    assert did.startswith("did:erp:inventory:")

    # 不同域生成不同前缀
    did_acct = registry.generate_did("accounting")
    assert did_acct.startswith("did:erp:accounting:")

    # 每次生成唯一
    did1 = registry.generate_did("test")
    did2 = registry.generate_did("test")
    assert did1 != did2


# ========== 额外测试：密钥对生成 ==========

def test_keypair_generation(fresh_registry):
    """密钥对生成正确"""
    registry = fresh_registry

    identity = registry.register_agent(
        name="密钥测试Agent",
        domain="test",
        capabilities=["general"],
        security_clearance="internal",
    )

    # 公钥是私钥的SHA-256哈希
    private_key = registry._private_keys[identity.did]
    import hashlib
    expected_public = hashlib.sha256(private_key.encode()).hexdigest()
    assert identity.public_key == expected_public

    # 私钥长度为64字符（32字节hex）
    assert len(private_key) == 64


# ========== 额外测试：列出所有身份 ==========

def test_list_identities(fresh_registry):
    """列出所有身份"""
    registry = fresh_registry

    registry.register_agent("Agent-A", "domain-a", ["general"], "internal")
    registry.register_agent("Agent-B", "domain-b", ["general"], "confidential")

    identities = registry.list_identities()
    assert len(identities) == 2

    for identity_dict in identities:
        assert "did" in identity_dict
        assert "name" in identity_dict
        assert "capabilities" in identity_dict
        assert "security_clearance" in identity_dict
        assert "status" in identity_dict
        # 公钥在 to_dict 中被截断
        assert identity_dict["public_key"].endswith("...")


# ========== 额外测试：默认安全许可 ==========

def test_default_security_clearance(fresh_registry):
    """未指定安全许可时默认为internal"""
    registry = fresh_registry

    identity = registry.register_agent(
        name="默认许可Agent",
        domain="test",
        capabilities=["general"],
    )
    assert identity.security_clearance == "internal"
