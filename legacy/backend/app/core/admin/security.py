"""
安全控制模块
CIO管理员系统的安全管控中心，提供安全策略、审计、事件响应等全面安全能力

特性：
- 安全策略管理（密码策略、会话策略、API限流策略）
- 安全审计日志查询
- 安全事件响应（锁定、解锁、强制下线）
- IP黑白名单管理
- API密钥管理
- 安全评分计算
- 数据加密状态检查
"""
import hashlib
import secrets
import structlog
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

logger = structlog.get_logger()


class SecurityController:
    """
    安全控制器

    职责：
    - 管理安全策略（密码策略、会话策略、API限流策略）
    - 查询安全审计日志
    - 响应安全事件（账户锁定/解锁、强制下线）
    - 管理IP黑白名单
    - 管理API密钥（创建、吊销）
    - 计算系统安全评分
    - 检查数据加密状态
    """

    def __init__(self):
        # 安全策略存储：{policy_id: policy_dict}
        self._policies: dict[str, dict] = {}
        # 安全审计日志：list[audit_log_dict]
        self._audit_logs: list[dict] = []
        # 安全事件：{event_id: event_dict}
        self._security_events: dict[str, dict] = {}
        # IP规则：{"whitelist": {ip: rule}, "blacklist": {ip: rule}}
        self._ip_rules: dict[str, dict[str, dict]] = {
            "whitelist": {},
            "blacklist": {},
        }
        # API密钥：{key_id: api_key_dict}
        self._api_keys: dict[str, dict] = {}
        # 加密状态
        self._encryption_status: dict = {}
        self._seed_data()

    def _seed_data(self) -> None:
        """加载示例数据"""
        now = datetime.now(timezone.utc)
        # 安全策略
        self._policies = {
            "policy-password": {
                "policy_id": "policy-password",
                "name": "密码策略",
                "type": "password",
                "config": {
                    "min_length": 12,
                    "require_uppercase": True,
                    "require_lowercase": True,
                    "require_digit": True,
                    "require_special": True,
                    "expiry_days": 90,
                    "history_count": 5,
                    "max_attempts": 5,
                    "lockout_duration_minutes": 30,
                },
                "enabled": True,
                "updated_at": now.isoformat(),
            },
            "policy-session": {
                "policy_id": "policy-session",
                "name": "会话策略",
                "type": "session",
                "config": {
                    "timeout_minutes": 60,
                    "max_concurrent_sessions": 3,
                    "require_mfa": True,
                    "idle_timeout_minutes": 15,
                    "token_refresh_interval": 300,
                },
                "enabled": True,
                "updated_at": now.isoformat(),
            },
            "policy-api-rate-limit": {
                "policy_id": "policy-api-rate-limit",
                "name": "API限流策略",
                "type": "rate_limit",
                "config": {
                    "default_rate_per_minute": 1000,
                    "burst_limit": 100,
                    "per_ip_limit": 500,
                    "per_user_limit": 2000,
                    "exempt_ips": ["127.0.0.1"],
                },
                "enabled": True,
                "updated_at": now.isoformat(),
            },
        }
        # 审计日志示例
        for i in range(5):
            self._audit_logs.append({
                "log_id": str(uuid4()),
                "timestamp": (now - timedelta(hours=i)).isoformat(),
                "actor": "admin" if i % 2 == 0 else "user_zhang",
                "actor_type": "user",
                "action": ["login", "config_update", "agent_stop", "login", "data_export"][i],
                "resource": ["auth:login", "config:agent", "agent:procurement-001", "auth:login", "data:finance_report"][i],
                "result": "success" if i != 2 else "denied",
                "ip_address": f"192.168.1.{100 + i}",
                "user_agent": "Mozilla/5.0",
                "details": {"session_id": str(uuid4())},
                "risk_level": ["low", "medium", "high", "low", "medium"][i],
            })
        # 安全事件示例
        self._security_events = {
            "event-001": {
                "event_id": "event-001",
                "timestamp": (now - timedelta(hours=2)).isoformat(),
                "type": "brute_force",
                "severity": "high",
                "description": "检测到暴力破解尝试: 5次失败登录",
                "source_ip": "203.0.113.50",
                "target_user": "admin",
                "status": "active",
                "response_actions": [],
            },
            "event-002": {
                "event_id": "event-002",
                "timestamp": (now - timedelta(hours=1)).isoformat(),
                "type": "suspicious_access",
                "severity": "medium",
                "description": "非工作时间访问机密数据",
                "source_ip": "192.168.1.105",
                "target_user": "user_li",
                "status": "investigating",
                "response_actions": [],
            },
        }
        # IP规则示例
        self._ip_rules["blacklist"]["203.0.113.50"] = {
            "ip": "203.0.113.50",
            "reason": "暴力破解攻击",
            "added_at": now.isoformat(),
            "added_by": "system",
        }
        self._ip_rules["whitelist"]["127.0.0.1"] = {
            "ip": "127.0.0.1",
            "reason": "本地开发环境",
            "added_at": now.isoformat(),
            "added_by": "system",
        }
        # API密钥示例
        api_key_secret = secrets.token_hex(32)
        self._api_keys = {
            "key-001": {
                "key_id": "key-001",
                "name": "ERP-Integration-Key",
                "key_hash": hashlib.sha256(api_key_secret.encode()).hexdigest(),
                "key_prefix": api_key_secret[:8],
                "scopes": ["read:inventory", "write:inventory", "read:finance"],
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=365)).isoformat(),
                "status": "active",
                "last_used": now.isoformat(),
                "created_by": "admin",
            },
        }
        # 加密状态
        self._encryption_status = {
            "data_at_rest": {
                "enabled": True,
                "algorithm": "AES-256-GCM",
                "key_rotation_days": 90,
                "last_rotation": (now - timedelta(days=45)).isoformat(),
            },
            "data_in_transit": {
                "enabled": True,
                "protocol": "TLS 1.3",
                "certificate_valid": True,
                "certificate_expires": (now + timedelta(days=60)).isoformat(),
            },
            "database_encryption": {
                "enabled": True,
                "algorithm": "AES-256",
                "columns_encrypted": ["password_hash", "api_key", "ssn", "bank_account"],
            },
            "backup_encryption": {
                "enabled": True,
                "algorithm": "AES-256-GCM",
            },
        }
        logger.info("security.seeded",
                    policies=len(self._policies),
                    audit_logs=len(self._audit_logs),
                    events=len(self._security_events),
                    api_keys=len(self._api_keys))

    def get_security_policies(self) -> list[dict]:
        """
        获取所有安全策略

        Returns:
            list[dict]: 安全策略列表
        """
        policies = list(self._policies.values())
        logger.info("security.policies_listed", count=len(policies))
        return policies

    def update_security_policy(
        self, policy_id: str, config: dict
    ) -> dict:
        """
        更新安全策略

        参数:
            policy_id: 策略ID
            config: 新的策略配置字典（将合并到现有配置）

        Returns:
            dict: 更新后的策略信息
        """
        policy = self._policies.get(policy_id)
        if not policy:
            return {"error": "policy_not_found", "policy_id": policy_id}

        policy["config"].update(config)
        policy["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 记录审计日志
        self._audit_logs.append({
            "log_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": "admin",
            "actor_type": "user",
            "action": "security_policy_update",
            "resource": f"policy:{policy_id}",
            "result": "success",
            "details": {"updated_fields": list(config.keys())},
            "risk_level": "medium",
        })

        logger.info("security.policy_updated", policy_id=policy_id)
        return {"status": "success", "policy": policy}

    def get_audit_logs(
        self,
        start_time: str,
        end_time: str,
        actor: Optional[str] = None,
        action: Optional[str] = None,
    ) -> list[dict]:
        """
        查询安全审计日志

        参数:
            start_time: 起始时间（ISO 8601格式）
            end_time: 结束时间（ISO 8601格式）
            actor: 可选，按操作者过滤
            action: 可选，按操作类型过滤

        Returns:
            list[dict]: 审计日志列表
        """
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError as e:
            logger.error("security.invalid_time_format", error=str(e))
            return []

        results = []
        for log in self._audit_logs:
            try:
                log_dt = datetime.fromisoformat(
                    log["timestamp"].replace("Z", "+00:00")
                )
            except (ValueError, KeyError):
                continue
            if not (start_dt <= log_dt <= end_dt):
                continue
            if actor and log.get("actor") != actor:
                continue
            if action and log.get("action") != action:
                continue
            results.append(log)

        logger.info("security.audit_logs_queried",
                    count=len(results), actor=actor, action=action)
        return results

    def respond_security_event(
        self,
        event_id: str,
        action: str,
        notes: str = "",
    ) -> dict:
        """
        响应安全事件

        参数:
            event_id: 安全事件ID
            action: 响应动作（lock_account/unlock_account/force_logout/block_ip/dismiss/escalate）
            notes: 处理备注

        Returns:
            dict: 响应结果
        """
        event = self._security_events.get(event_id)
        if not event:
            return {"error": "event_not_found", "event_id": event_id}

        now = datetime.now(timezone.utc)
        valid_actions = [
            "lock_account", "unlock_account", "force_logout",
            "block_ip", "dismiss", "escalate",
        ]
        if action not in valid_actions:
            return {"error": "invalid_action", "action": action,
                    "valid_actions": valid_actions}

        # 执行响应动作
        response_record = {
            "action": action,
            "notes": notes,
            "timestamp": now.isoformat(),
            "performed_by": "admin",
        }
        event["response_actions"].append(response_record)

        # 根据动作更新事件状态
        if action in ("lock_account", "block_ip"):
            event["status"] = "mitigated"
            if action == "block_ip" and "source_ip" in event:
                self._ip_rules["blacklist"][event["source_ip"]] = {
                    "ip": event["source_ip"],
                    "reason": f"安全事件响应: {event['description']}",
                    "added_at": now.isoformat(),
                    "added_by": "admin",
                }
        elif action == "dismiss":
            event["status"] = "dismissed"
        elif action == "escalate":
            event["status"] = "escalated"
            event["severity"] = "critical"
        else:
            event["status"] = "resolved"

        # 记录审计日志
        self._audit_logs.append({
            "log_id": str(uuid4()),
            "timestamp": now.isoformat(),
            "actor": "admin",
            "actor_type": "user",
            "action": f"security_event_response:{action}",
            "resource": f"event:{event_id}",
            "result": "success",
            "details": response_record,
            "risk_level": "high",
        })

        logger.info("security.event_responded",
                    event_id=event_id, action=action)
        return {"status": "success", "event": event}

    def get_ip_rules(self) -> dict:
        """
        获取IP黑白名单规则

        Returns:
            dict: 包含whitelist和blacklist的IP规则字典
        """
        return {
            "whitelist": list(self._ip_rules["whitelist"].values()),
            "blacklist": list(self._ip_rules["blacklist"].values()),
        }

    def add_ip_rule(
        self, ip: str, rule_type: str, reason: str = ""
    ) -> dict:
        """
        添加IP规则

        参数:
            ip: IP地址
            rule_type: 规则类型（whitelist/blacklist）
            reason: 添加原因

        Returns:
            dict: 添加结果
        """
        if rule_type not in ("whitelist", "blacklist"):
            return {"error": "invalid_rule_type", "rule_type": rule_type}

        rule = {
            "ip": ip,
            "reason": reason,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "added_by": "admin",
        }
        self._ip_rules[rule_type][ip] = rule

        # 记录审计日志
        self._audit_logs.append({
            "log_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": "admin",
            "actor_type": "user",
            "action": f"ip_rule_add:{rule_type}",
            "resource": f"ip:{ip}",
            "result": "success",
            "details": rule,
            "risk_level": "medium",
        })

        logger.info("security.ip_rule_added", ip=ip, rule_type=rule_type)
        return {"status": "success", "rule": rule}

    def remove_ip_rule(self, ip: str) -> dict:
        """
        移除IP规则

        参数:
            ip: IP地址

        Returns:
            dict: 移除结果
        """
        for rule_type in ("whitelist", "blacklist"):
            if ip in self._ip_rules[rule_type]:
                removed = self._ip_rules[rule_type].pop(ip)
                logger.info("security.ip_rule_removed", ip=ip, rule_type=rule_type)
                return {
                    "status": "success",
                    "removed_rule": removed,
                    "rule_type": rule_type,
                }
        return {"error": "ip_rule_not_found", "ip": ip}

    def get_api_keys(self) -> list[dict]:
        """
        获取所有API密钥列表（不包含密钥明文）

        Returns:
            list[dict]: API密钥信息列表
        """
        keys = []
        for key_info in self._api_keys.values():
            keys.append({
                "key_id": key_info["key_id"],
                "name": key_info["name"],
                "key_prefix": key_info["key_prefix"],
                "scopes": key_info["scopes"],
                "created_at": key_info["created_at"],
                "expires_at": key_info["expires_at"],
                "status": key_info["status"],
                "last_used": key_info["last_used"],
                "created_by": key_info["created_by"],
            })
        logger.info("security.api_keys_listed", count=len(keys))
        return keys

    def create_api_key(
        self,
        name: str,
        scopes: list[str],
        expires_in_days: int = 365,
    ) -> dict:
        """
        创建新的API密钥

        参数:
            name: 密钥名称
            scopes: 权限范围列表
            expires_in_days: 过期天数，默认365天

        Returns:
            dict: 创建结果，包含密钥明文（仅此一次返回）和密钥信息
        """
        key_id = f"key-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc)
        raw_key = secrets.token_hex(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        key_info = {
            "key_id": key_id,
            "name": name,
            "key_hash": key_hash,
            "key_prefix": raw_key[:8],
            "scopes": scopes,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=expires_in_days)).isoformat(),
            "status": "active",
            "last_used": now.isoformat(),
            "created_by": "admin",
        }
        self._api_keys[key_id] = key_info

        # 记录审计日志
        self._audit_logs.append({
            "log_id": str(uuid4()),
            "timestamp": now.isoformat(),
            "actor": "admin",
            "actor_type": "user",
            "action": "api_key_create",
            "resource": f"key:{key_id}",
            "result": "success",
            "details": {"name": name, "scopes": scopes},
            "risk_level": "high",
        })

        logger.info("security.api_key_created", key_id=key_id, name=name)
        return {
            "status": "success",
            "key_id": key_id,
            "api_key": raw_key,  # 明文密钥仅此一次返回
            "key_info": {
                "name": name,
                "key_prefix": key_info["key_prefix"],
                "scopes": scopes,
                "expires_at": key_info["expires_at"],
            },
            "warning": "请妥善保管API密钥明文，此为唯一一次显示",
        }

    def revoke_api_key(self, key_id: str) -> dict:
        """
        吊销API密钥

        参数:
            key_id: 密钥ID

        Returns:
            dict: 吊销结果
        """
        key_info = self._api_keys.get(key_id)
        if not key_info:
            return {"error": "api_key_not_found", "key_id": key_id}

        key_info["status"] = "revoked"
        key_info["revoked_at"] = datetime.now(timezone.utc).isoformat()

        # 记录审计日志
        self._audit_logs.append({
            "log_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": "admin",
            "actor_type": "user",
            "action": "api_key_revoke",
            "resource": f"key:{key_id}",
            "result": "success",
            "details": {"name": key_info["name"]},
            "risk_level": "high",
        })

        logger.info("security.api_key_revoked", key_id=key_id)
        return {"status": "success", "key_id": key_id, "revoked_at": key_info["revoked_at"]}

    def get_security_score(self) -> dict:
        """
        计算系统安全评分

        基于以下维度综合评分（0-100）：
        - 密码策略强度
        - 会话安全
        - API限流配置
        - 加密覆盖度
        - 审计日志完整性
        - IP防护规则
        - API密钥管理

        Returns:
            dict: 安全评分及各维度得分详情
        """
        scores = {}
        now = datetime.now(timezone.utc)

        # 密码策略评分（20分）
        pwd_policy = self._policies.get("policy-password", {})
        pwd_config = pwd_policy.get("config", {})
        pwd_score = 0
        if pwd_config.get("min_length", 0) >= 12:
            pwd_score += 5
        if pwd_config.get("require_uppercase"):
            pwd_score += 3
        if pwd_config.get("require_special"):
            pwd_score += 3
        if pwd_config.get("max_attempts", 0) <= 5:
            pwd_score += 4
        if pwd_config.get("expiry_days", 0) <= 90:
            pwd_score += 5
        scores["password_policy"] = {"score": pwd_score, "max": 20}

        # 会话安全评分（15分）
        session_policy = self._policies.get("policy-session", {})
        session_config = session_policy.get("config", {})
        session_score = 0
        if session_config.get("require_mfa"):
            session_score += 8
        if session_config.get("timeout_minutes", 0) <= 60:
            session_score += 4
        if session_config.get("max_concurrent_sessions", 99) <= 3:
            session_score += 3
        scores["session_security"] = {"score": session_score, "max": 15}

        # API限流评分（15分）
        rate_policy = self._policies.get("policy-api-rate-limit", {})
        rate_config = rate_policy.get("config", {})
        rate_score = 0
        if rate_policy.get("enabled"):
            rate_score += 8
        if rate_config.get("per_ip_limit", 0) > 0:
            rate_score += 4
        if rate_config.get("per_user_limit", 0) > 0:
            rate_score += 3
        scores["api_rate_limiting"] = {"score": rate_score, "max": 15}

        # 加密覆盖度评分（20分）
        enc = self._encryption_status
        enc_score = 0
        if enc.get("data_at_rest", {}).get("enabled"):
            enc_score += 6
        if enc.get("data_in_transit", {}).get("enabled"):
            enc_score += 6
        if enc.get("database_encryption", {}).get("enabled"):
            enc_score += 5
        if enc.get("backup_encryption", {}).get("enabled"):
            enc_score += 3
        scores["encryption"] = {"score": enc_score, "max": 20}

        # 审计日志评分（10分）
        audit_score = min(len(self._audit_logs) * 2, 10)
        scores["audit_logging"] = {"score": audit_score, "max": 10}

        # IP防护评分（10分）
        blacklist_count = len(self._ip_rules["blacklist"])
        ip_score = min(blacklist_count * 5, 10)
        scores["ip_protection"] = {"score": ip_score, "max": 10}

        # API密钥管理评分（10分）
        active_keys = sum(
            1 for k in self._api_keys.values() if k["status"] == "active"
        )
        expired_keys = sum(
            1 for k in self._api_keys.values()
            if k["status"] == "active"
            and datetime.fromisoformat(k["expires_at"].replace("Z", "+00:00")) < now
        )
        key_score = max(10 - expired_keys * 5, 0) if active_keys > 0 else 5
        scores["api_key_management"] = {"score": key_score, "max": 10}

        total = sum(s["score"] for s in scores.values())
        max_total = sum(s["max"] for s in scores.values())

        # 安全等级
        percentage = (total / max_total * 100) if max_total > 0 else 0
        if percentage >= 90:
            grade = "A"
        elif percentage >= 80:
            grade = "B"
        elif percentage >= 70:
            grade = "C"
        elif percentage >= 60:
            grade = "D"
        else:
            grade = "F"

        result = {
            "total_score": total,
            "max_score": max_total,
            "percentage": round(percentage, 1),
            "grade": grade,
            "details": scores,
            "evaluated_at": now.isoformat(),
        }
        logger.info("security.score_calculated",
                    score=total, grade=grade, percentage=percentage)
        return result

    def get_encryption_status(self) -> dict:
        """
        获取数据加密状态

        Returns:
            dict: 各加密维度的状态信息
        """
        now = datetime.now(timezone.utc)
        status = dict(self._encryption_status)

        # 检查证书是否即将过期
        transit = status.get("data_in_transit", {})
        if "certificate_expires" in transit:
            try:
                cert_expiry = datetime.fromisoformat(
                    transit["certificate_expires"].replace("Z", "+00:00")
                )
                days_to_expiry = (cert_expiry - now).days
                transit["certificate_days_remaining"] = days_to_expiry
                transit["certificate_warning"] = days_to_expiry < 30
            except (ValueError, TypeError):
                pass

        # 检查密钥轮换
        at_rest = status.get("data_at_rest", {})
        if "last_rotation" in at_rest:
            try:
                last_rot = datetime.fromisoformat(
                    at_rest["last_rotation"].replace("Z", "+00:00")
                )
                rotation_days = at_rest.get("key_rotation_days", 90)
                days_since_rotation = (now - last_rot).days
                at_rest["days_since_rotation"] = days_since_rotation
                at_rest["rotation_needed"] = days_since_rotation > rotation_days
            except (ValueError, TypeError):
                pass

        status["checked_at"] = now.isoformat()
        logger.info("security.encryption_status_checked")
        return status
