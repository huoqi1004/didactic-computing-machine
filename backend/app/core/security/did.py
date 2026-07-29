"""
Agent DID身份系统
每个Agent拥有基于DID的唯一数字身份，私钥安全存储
通信强制mTLS，实现零信任身份验证
"""
import hashlib
import secrets
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.config import settings

logger = structlog.get_logger()


class AgentIdentity:
    """Agent数字身份"""
    def __init__(self, did: str, name: str, domain: str, public_key: str,
                 capabilities: list[str], security_clearance: str):
        self.did = did                      # 去中心化标识符
        self.name = name                    # Agent名称
        self.domain = domain                # 业务域
        self.public_key = public_key        # 公钥
        self.capabilities = capabilities    # 能力列表
        self.security_clearance = security_clearance  # 安全许可等级
        self.created_at = datetime.now(timezone.utc)
        self.last_active = self.created_at
        self.status = "active"

    def to_dict(self) -> dict:
        return {
            "did": self.did,
            "name": self.name,
            "domain": self.domain,
            "public_key": self.public_key[:16] + "...",
            "capabilities": self.capabilities,
            "security_clearance": self.security_clearance,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "status": self.status,
        }


class DIDRegistry:
    """
    Agent身份注册中心
    管理所有Agent的DID身份、密钥对和证书

    特性：
    - DID唯一性保证
    - 密钥对生成与安全存储
    - 身份验证与鉴权
    - 身份生命周期管理
    """

    def __init__(self):
        self._identities: dict[str, AgentIdentity] = {}
        self._private_keys: dict[str, str] = {}  # 实际应由HSM管理
        self._revoked: set[str] = set()

    def generate_did(self, domain: str) -> str:
        """生成DID标识符"""
        unique = secrets.token_hex(16)
        return f"did:erp:{domain}:{unique}"

    def _generate_keypair(self) -> tuple[str, str]:
        """生成密钥对（开发环境模拟，生产环境使用HSM）"""
        private_key = secrets.token_hex(32)
        public_key = hashlib.sha256(private_key.encode()).hexdigest()
        return private_key, public_key

    def register_agent(
        self,
        name: str,
        domain: str,
        capabilities: list[str],
        security_clearance: str = "internal"
    ) -> AgentIdentity:
        """注册新Agent身份"""
        did = self.generate_did(domain)
        private_key, public_key = self._generate_keypair()

        identity = AgentIdentity(
            did=did,
            name=name,
            domain=domain,
            public_key=public_key,
            capabilities=capabilities,
            security_clearance=security_clearance,
        )

        self._identities[did] = identity
        self._private_keys[did] = private_key  # 生产环境存入HSM

        logger.info("did.agent_registered",
                     did=did, name=name, domain=domain,
                     capabilities=capabilities)
        return identity

    def verify_identity(self, did: str) -> bool:
        """验证Agent身份是否有效"""
        if did in self._revoked:
            return False
        identity = self._identities.get(did)
        if not identity or identity.status != "active":
            return False
        return True

    def get_identity(self, did: str) -> Optional[AgentIdentity]:
        """获取Agent身份信息"""
        return self._identities.get(did)

    def revoke_identity(self, did: str, reason: str = ""):
        """吊销Agent身份"""
        if did in self._identities:
            self._identities[did].status = "revoked"
            self._revoked.add(did)
            logger.warning("did.identity_revoked", did=did, reason=reason)

    def update_last_active(self, did: str):
        """更新最后活跃时间"""
        identity = self._identities.get(did)
        if identity:
            identity.last_active = datetime.now(timezone.utc)

    def list_identities(self) -> list[dict]:
        """列出所有身份"""
        return [identity.to_dict() for identity in self._identities.values()]

    def get_stats(self) -> dict:
        return {
            "total": len(self._identities),
            "active": sum(1 for i in self._identities.values() if i.status == "active"),
            "revoked": len(self._revoked),
        }


# 全局DID注册中心单例
did_registry = DIDRegistry()
