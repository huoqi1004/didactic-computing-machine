"""
意图级动态授权引擎
权限控制基于实时评估：Agent身份 + 任务意图 + 数据敏感等级 + 上下文风险评分
任务结束即回收权限，实现零信任动态授权
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

from app.core.config import SecurityLevel, Permission
from app.core.security.did import did_registry
from app.core.security.audit import audit_chain

logger = structlog.get_logger()


class RiskLevel(str, Enum):
    """风险等级"""
    LOW = "low"        # 风险评分 0-25
    MEDIUM = "medium"  # 风险评分 26-50
    HIGH = "high"      # 风险评分 51-75
    CRITICAL = "critical"  # 风险评分 76-100


class AuthorizationRequest:
    """授权请求"""
    def __init__(self, agent_did: str, intent: str, target_resource: str,
                 data_security_level: SecurityLevel, context: dict,
                 requested_permissions: list[Permission]):
        self.agent_did = agent_did
        self.intent = intent                  # 任务意图描述
        self.target_resource = target_resource  # 目标资源
        self.data_security_level = data_security_level
        self.context = context                # 上下文信息
        self.requested_permissions = requested_permissions
        self.timestamp = datetime.now(timezone.utc)


class AuthorizationResult:
    """授权结果"""
    def __init__(self, granted: bool, granted_permissions: list[Permission],
                 risk_score: int, risk_level: RiskLevel, reason: str,
                 session_id: Optional[str] = None, expires_in: Optional[int] = None):
        self.granted = granted
        self.granted_permissions = granted_permissions
        self.risk_score = risk_score
        self.risk_level = risk_level
        self.reason = reason
        self.session_id = session_id
        self.expires_in = expires_in
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "granted": self.granted,
            "granted_permissions": [p.value for p in self.granted_permissions],
            "risk_score": self.risk_score,
            "risk_level": self.risk_level.value,
            "reason": self.reason,
            "session_id": self.session_id,
            "expires_in": self.expires_in,
        }


class AuthorizationEngine:
    """
    意图级动态授权引擎

    评估四要素：
    1. Agent身份 - DID验证 + 安全许可等级
    2. 任务意图 - 意图合法性 + 与Agent能力匹配
    3. 数据敏感等级 - Agent许可等级 vs 数据安全等级
    4. 上下文风险评分 - 时间、频率、操作模式等

    高风险操作自动升级人类审批
    """

    # 安全许可等级与数据安全等级的映射
    CLEARANCE_MATRIX = {
        SecurityLevel.PUBLIC: 0,
        SecurityLevel.INTERNAL: 1,
        SecurityLevel.CONFIDENTIAL: 2,
        SecurityLevel.TOP_SECRET: 3,
    }

    def __init__(self, registry=None, audit=None):
        self._registry = registry or did_registry
        self._audit = audit or audit_chain
        self._active_sessions: dict[str, dict] = {}  # session_id -> session info
        self._denied_count = 0
        self._granted_count = 0

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """评估授权请求"""
        # 1. 验证Agent身份
        if not self._registry.verify_identity(request.agent_did):
            self._denied_count += 1
            result = AuthorizationResult(
                granted=False, granted_permissions=[], risk_score=100,
                risk_level=RiskLevel.CRITICAL,
                reason="Agent身份验证失败"
            )
            await self._audit.log(
                actor_did=request.agent_did,
                action=f"authorize.{request.intent}",
                resource=request.target_resource,
                result="denied",
                details=result.to_dict(),
                risk_level="critical",
            )
            return result

        identity = self._registry.get_identity(request.agent_did)
        if not identity:
            self._denied_count += 1
            result = AuthorizationResult(
                granted=False, granted_permissions=[], risk_score=100,
                risk_level=RiskLevel.CRITICAL,
                reason="Agent身份不存在"
            )
            await self._audit.log(
                actor_did=request.agent_did,
                action=f"authorize.{request.intent}",
                resource=request.target_resource,
                result="denied",
                details=result.to_dict(),
                risk_level="critical",
            )
            return result

        # 2. 评估风险评分
        risk_score = self._calculate_risk_score(request, identity)
        risk_level = self._score_to_level(risk_score)

        # 3. 检查安全许可等级
        clearance_level = self.CLEARANCE_MATRIX.get(
            SecurityLevel(identity.security_clearance), 0
        )
        data_level = self.CLEARANCE_MATRIX.get(request.data_security_level, 0)

        if clearance_level < data_level:
            self._denied_count += 1
            logger.warning("authorization.denied_clearance",
                          agent=identity.name,
                          clearance=identity.security_clearance,
                          data_level=request.data_security_level.value)
            result = AuthorizationResult(
                granted=False, granted_permissions=[], risk_score=risk_score,
                risk_level=risk_level,
                reason=f"安全许可等级不足：需要{request.data_security_level.value}，当前{identity.security_clearance}"
            )
            await self._audit.log(
                actor_did=request.agent_did,
                action=f"authorize.{request.intent}",
                resource=request.target_resource,
                result="denied",
                details=result.to_dict(),
                risk_level=risk_level.value,
            )
            return result

        # 4. 检查意图与能力匹配
        if not self._check_capability_match(request.intent, identity.capabilities):
            self._denied_count += 1
            result = AuthorizationResult(
                granted=False, granted_permissions=[], risk_score=risk_score,
                risk_level=risk_level,
                reason=f"任务意图'{request.intent}'不在Agent能力范围内"
            )
            await self._audit.log(
                actor_did=request.agent_did,
                action=f"authorize.{request.intent}",
                resource=request.target_resource,
                result="denied",
                details=result.to_dict(),
                risk_level=risk_level.value,
            )
            return result

        # 5. 极高风险自动升级人类审批
        # CRITICAL等级（76-100）升级人类审批；HIGH等级（51-75）授权但缩短会话
        if risk_score > 75:
            self._denied_count += 1
            logger.warning("authorization.escalated",
                          agent=identity.name, risk_score=risk_score,
                          intent=request.intent)
            result = AuthorizationResult(
                granted=False, granted_permissions=[], risk_score=risk_score,
                risk_level=RiskLevel.CRITICAL,
                reason=f"风险评分{risk_score}过高，已升级人类审批"
            )
            await self._audit.log(
                actor_did=request.agent_did,
                action=f"authorize.{request.intent}",
                resource=request.target_resource,
                result="escalated",
                details=result.to_dict(),
                risk_level="critical",
            )
            return result

        # 6. 授予权限
        import secrets
        session_id = secrets.token_hex(16)
        expires_in = 300 if risk_level == RiskLevel.HIGH else 1800  # 高风险5分钟，其他30分钟

        self._active_sessions[session_id] = {
            "agent_did": request.agent_did,
            "intent": request.intent,
            "permissions": [p.value for p in request.requested_permissions],
            "risk_score": risk_score,
            "created_at": datetime.now(timezone.utc),
            "expires_in": expires_in,
        }

        self._granted_count += 1
        self._registry.update_last_active(request.agent_did)

        logger.info("authorization.granted",
                    agent=identity.name, intent=request.intent,
                    risk_score=risk_score, risk_level=risk_level.value,
                    permissions=[p.value for p in request.requested_permissions],
                    expires_in=expires_in)

        result = AuthorizationResult(
            granted=True,
            granted_permissions=request.requested_permissions,
            risk_score=risk_score,
            risk_level=risk_level,
            reason="授权通过",
            session_id=session_id,
            expires_in=expires_in,
        )
        await self._audit.log(
            actor_did=request.agent_did,
            action=f"authorize.{request.intent}",
            resource=request.target_resource,
            result="success",
            details=result.to_dict(),
            risk_level=risk_level.value,
        )
        return result

    def _calculate_risk_score(self, request: AuthorizationRequest, identity) -> int:
        """计算上下文风险评分（0-100）"""
        score = 0

        # 数据敏感等级权重
        level_weights = {
            SecurityLevel.PUBLIC: 0,
            SecurityLevel.INTERNAL: 10,
            SecurityLevel.CONFIDENTIAL: 30,
            SecurityLevel.TOP_SECRET: 60,
        }
        score += level_weights.get(request.data_security_level, 0)

        # 操作权限权重
        perm_weights = {
            Permission.READ: 5,
            Permission.WRITE: 15,
            Permission.EXECUTE: 20,
            Permission.APPROVE: 25,
            Permission.DELETE: 30,
        }
        for perm in request.requested_permissions:
            score += perm_weights.get(perm, 0)

        # 上下文风险因子
        context = request.context or {}

        # 非工作时间操作
        hour = request.timestamp.hour
        if hour < 8 or hour > 20:
            score += 10

        # 高频操作
        operation_count = context.get("operation_count_1h", 0)
        if operation_count > 100:
            score += 15
        elif operation_count > 50:
            score += 8

        # 跨域操作
        if context.get("cross_domain", False):
            score += 10

        # 金额风险
        amount = context.get("amount", 0)
        if amount > 1000000:
            score += 20
        elif amount > 100000:
            score += 10

        return min(score, 100)

    def _score_to_level(self, score: int) -> RiskLevel:
        """风险评分转等级"""
        if score <= 25:
            return RiskLevel.LOW
        elif score <= 50:
            return RiskLevel.MEDIUM
        elif score <= 75:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    def _check_capability_match(self, intent: str, capabilities: list[str]) -> bool:
        """检查意图是否在Agent能力范围内"""
        intent_lower = intent.lower()
        for cap in capabilities:
            if cap.lower() in intent_lower or intent_lower in cap.lower():
                return True
        # 默认允许通用能力
        if "general" in capabilities or "*" in capabilities:
            return True
        return False

    def revoke_session(self, session_id: str):
        """回收权限（任务结束）"""
        session = self._active_sessions.pop(session_id, None)
        if session:
            logger.info("authorization.session_revoked",
                        session_id=session_id,
                        agent_did=session.get("agent_did"))

    def cleanup_expired_sessions(self):
        """清理过期会话"""
        now = datetime.now(timezone.utc)
        expired = []
        for sid, session in self._active_sessions.items():
            created = session["created_at"]
            elapsed = (now - created).total_seconds()
            if elapsed > session["expires_in"]:
                expired.append(sid)
        for sid in expired:
            self.revoke_session(sid)
        if expired:
            logger.info("authorization.sessions_expired", count=len(expired))

    def get_stats(self) -> dict:
        return {
            "active_sessions": len(self._active_sessions),
            "granted": self._granted_count,
            "denied": self._denied_count,
            "grant_rate": self._granted_count / (self._granted_count + self._denied_count)
                          if (self._granted_count + self._denied_count) > 0 else 0,
        }


# 全局授权引擎单例
authz_engine = AuthorizationEngine()
