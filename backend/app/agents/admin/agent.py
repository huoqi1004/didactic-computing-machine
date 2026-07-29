"""
管理Agent
负责系统配置、Agent监控、通知管理与公告管理。
监听Agent注册、状态变更、安全告警与审计日志事件，
自动维护Agent运行状态、发送通知并评估系统健康度。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class AdminAgent(BaseAgent):
    """
    管理Agent - 系统管理与运维

    职责：
    - 系统配置：管理系统级配置项（按分类组织）
    - Agent监控：跟踪所有注册Agent的运行状态
    - 通知管理：向用户发送系统通知并跟踪阅读状态
    - 公告管理：管理系统公告的创建、发布与过期
    - 系统健康度：聚合各服务状态，评估系统健康度
    """

    def __init__(self):
        super().__init__(
            name="管理Agent",
            domain="admin",
            capabilities=[
                "system_config",                # 系统配置
                "agent_monitoring",             # Agent监控
                "notification_management",      # 通知管理
                "announcement_management",      # 公告管理
                "general",
            ],
            security_clearance="restricted",
        )
        # 系统配置: config_key -> dict
        self._configs: dict[str, dict] = {}
        # 通知: notification_id -> dict
        self._notifications: dict[str, dict] = {}
        # 公告: announcement_id -> dict
        self._announcements: dict[str, dict] = {}
        # Agent监控状态: agent_id -> dict
        self._agent_status: dict[str, dict] = {}
        # 通知编号计数器
        self._notification_seq: int = 0
        # 公告编号计数器
        self._announcement_seq: int = 0
        # 安全告警计数器
        self._security_alert_count: int = 0
        # 审计日志计数器
        self._audit_log_count: int = 0
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "系统配置、Agent监控、通知管理与公告管理"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅管理相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.AGENT_REGISTERED,
                EventType.AGENT_STATE_CHANGED,
                EventType.SECURITY_ALERT,
                EventType.AUDIT_LOG_CREATED,
            ],
            handler=self._handle_event,
        )
        logger.info("admin_agent.initialized", agent=self.name,
                    configs=len(self._configs),
                    announcements=len(self._announcements))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("admin_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.AGENT_REGISTERED:
            await self._handle_agent_registered(data)
        elif event.event_type == EventType.AGENT_STATE_CHANGED:
            await self._handle_agent_state_changed(data)
        elif event.event_type == EventType.SECURITY_ALERT:
            await self._handle_security_alert(data)
        elif event.event_type == EventType.AUDIT_LOG_CREATED:
            await self._handle_audit_log(data)

    async def _handle_agent_registered(self, data: dict):
        """处理Agent注册事件"""
        agent_id = data.get("agent_id", "")
        if not agent_id:
            return
        self._agent_status[agent_id] = {
            "agent_id": agent_id,
            "name": data.get("name", ""),
            "domain": data.get("domain", ""),
            "capabilities": data.get("capabilities", []),
            "did": data.get("did", ""),
            "state": "running",
            "health": "healthy",
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_state_change": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("admin_agent.agent_registered",
                    agent_id=agent_id, name=data.get("name", ""))

    async def _handle_agent_state_changed(self, data: dict):
        """处理Agent状态变更事件"""
        agent_id = data.get("agent_id", "")
        if not agent_id or agent_id not in self._agent_status:
            return
        self._agent_status[agent_id]["state"] = data.get("state", "unknown")
        self._agent_status[agent_id]["last_state_change"] = (
            datetime.now(timezone.utc).isoformat()
        )
        # 状态映射健康度
        state = data.get("state", "")
        if state in ("running",):
            self._agent_status[agent_id]["health"] = "healthy"
        elif state in ("paused", "degraded"):
            self._agent_status[agent_id]["health"] = "degraded"
        elif state in ("fused", "terminated", "error"):
            self._agent_status[agent_id]["health"] = "unhealthy"
        logger.info("admin_agent.agent_state_changed",
                    agent_id=agent_id, state=state)

    async def _handle_security_alert(self, data: dict):
        """处理安全告警事件"""
        self._security_alert_count += 1
        alert = {
            "alert_id": data.get("alert_id", str(uuid4())),
            "severity": data.get("severity", "medium"),
            "type": data.get("type", ""),
            "description": data.get("description", ""),
            "source": data.get("source", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.warning("admin_agent.security_alert",
                       severity=alert["severity"],
                       type=alert["type"])
        # 高危告警自动发送通知
        if alert["severity"] in ("high", "critical"):
            await self.send_notification(
                user_id="admin",
                title=f"安全告警: {alert['type']}",
                content=alert["description"],
                category="security",
                priority="urgent",
            )

    async def _handle_audit_log(self, data: dict):
        """处理审计日志创建事件"""
        self._audit_log_count += 1

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出管理决策"""
        action = context.get("action", "")

        if action == "set_config":
            return await self._decide_set_config(context)
        elif action == "publish_announcement":
            return await self._decide_publish_announcement(context)
        elif action == "send_notification":
            return await self._decide_send_notification(context)

        return AgentDecision(
            decision_type="admin_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_set_config(self, context: dict) -> AgentDecision:
        """系统配置变更决策"""
        key = context.get("key", "")
        if not key:
            return AgentDecision(
                decision_type="system_config",
                reasoning="配置键不能为空",
                action="拒绝变更",
                confidence=1.0,
                context=context,
            )
        # 关键配置变更需人工审批
        critical_keys = ("security_level", "auth_mode", "database_url",
                         "encryption_key", "admin_password")
        if key in critical_keys:
            return AgentDecision(
                decision_type="system_config",
                reasoning=f"关键配置项'{key}'变更需管理员审批",
                action="转人工审批",
                confidence=0.8,
                requires_human_approval=True,
                context=context,
            )
        return AgentDecision(
            decision_type="system_config",
            reasoning=f"更新配置项'{key}'",
            action="更新配置",
            confidence=0.9,
            context=context,
        )

    async def _decide_publish_announcement(self, context: dict) -> AgentDecision:
        """公告发布决策"""
        announcement_id = context.get("announcement_id", "")
        announcement = self._announcements.get(announcement_id)
        if not announcement:
            return AgentDecision(
                decision_type="announcement_publish",
                reasoning=f"公告不存在: {announcement_id}",
                action="拒绝发布",
                confidence=1.0,
                context=context,
            )
        if announcement.get("status") == "published":
            return AgentDecision(
                decision_type="announcement_publish",
                reasoning=f"公告{announcement_id}已发布",
                action="拒绝重复发布",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="announcement_publish",
            reasoning=f"发布公告: {announcement.get('title', '')}",
            action="发布公告",
            confidence=0.9,
            context=context,
        )

    async def _decide_send_notification(self, context: dict) -> AgentDecision:
        """通知发送决策"""
        user_id = context.get("user_id", "")
        if not user_id:
            return AgentDecision(
                decision_type="notification_send",
                reasoning="通知接收人不能为空",
                action="拒绝发送",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="notification_send",
            reasoning=f"向用户{user_id}发送通知",
            action="发送通知",
            confidence=0.9,
            context=context,
        )

    # ========== 公开 API ==========

    def _generate_notification_id(self) -> str:
        """生成通知编号"""
        self._notification_seq += 1
        return f"NTF{self._notification_seq:04d}"

    def _generate_announcement_id(self) -> str:
        """生成公告编号"""
        self._announcement_seq += 1
        return f"ANN{self._announcement_seq:04d}"

    def get_config(self, category: Optional[str] = None) -> dict:
        """获取系统配置，可按分类过滤"""
        if category:
            return {
                k: v for k, v in self._configs.items()
                if v.get("category") == category
            }
        return dict(self._configs)

    def set_config(self, key: str, value, category: str,
                   updated_by: str) -> dict:
        """设置系统配置"""
        now = datetime.now(timezone.utc).isoformat()
        old_value = self._configs.get(key, {}).get("value")
        config = {
            "key": key,
            "value": value,
            "category": category,
            "updated_by": updated_by,
            "updated_at": now,
            "old_value": old_value,
        }
        self._configs[key] = config
        logger.info("admin_agent.config_set",
                    key=key, category=category, updated_by=updated_by)
        return config

    def get_announcements(self, status: Optional[str] = None) -> list[dict]:
        """获取公告列表，可按状态过滤"""
        announcements = list(self._announcements.values())
        if status:
            announcements = [a for a in announcements if a.get("status") == status]
        return announcements

    def create_announcement(self, title: str, content: str,
                            target_audience: str, priority: str,
                            expires_at: str) -> dict:
        """创建公告"""
        announcement_id = self._generate_announcement_id()
        now = datetime.now(timezone.utc).isoformat()
        announcement = {
            "announcement_id": announcement_id,
            "title": title,
            "content": content,
            "target_audience": target_audience,  # all/department/role
            "priority": priority,                # low/normal/high/urgent
            "expires_at": expires_at,
            "status": "draft",                   # draft/published/expired
            "created_by": "admin",
            "created_at": now,
            "published_at": "",
        }
        self._announcements[announcement_id] = announcement
        logger.info("admin_agent.announcement_created",
                    announcement_id=announcement_id, title=title)
        return announcement

    async def publish_announcement(self, announcement_id: str) -> dict:
        """发布公告"""
        announcement = self._announcements.get(announcement_id)
        if not announcement:
            return {"error": f"公告不存在: {announcement_id}"}
        announcement["status"] = "published"
        announcement["published_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("admin_agent.announcement_published",
                    announcement_id=announcement_id)
        return announcement

    def get_notifications(self, user_id: Optional[str] = None,
                          status: Optional[str] = None) -> list[dict]:
        """获取通知列表，可按用户和状态过滤"""
        notifications = list(self._notifications.values())
        if user_id:
            notifications = [n for n in notifications if n.get("user_id") == user_id]
        if status:
            notifications = [n for n in notifications if n.get("status") == status]
        # 按创建时间倒序
        notifications.sort(key=lambda n: n.get("created_at", ""),
                           reverse=True)
        return notifications

    async def send_notification(self, user_id: str, title: str,
                                content: str, category: str,
                                priority: str) -> dict:
        """发送通知"""
        notification_id = self._generate_notification_id()
        now = datetime.now(timezone.utc).isoformat()
        notification = {
            "notification_id": notification_id,
            "user_id": user_id,
            "title": title,
            "content": content,
            "category": category,      # system/security/business
            "priority": priority,      # low/normal/high/urgent
            "status": "unread",        # unread/read
            "created_at": now,
            "read_at": "",
        }
        self._notifications[notification_id] = notification
        logger.info("admin_agent.notification_sent",
                    notification_id=notification_id,
                    user_id=user_id, category=category)
        return notification

    def mark_notification_read(self, notification_id: str) -> dict:
        """标记通知为已读"""
        notification = self._notifications.get(notification_id)
        if not notification:
            return {"error": f"通知不存在: {notification_id}"}
        notification["status"] = "read"
        notification["read_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("admin_agent.notification_read",
                    notification_id=notification_id)
        return notification

    def get_agent_status(self) -> list[dict]:
        """获取所有Agent运行状态概要"""
        return list(self._agent_status.values())

    def get_system_health(self) -> dict:
        """获取系统健康度，含各服务状态"""
        all_agents = list(self._agent_status.values())
        total = len(all_agents)
        healthy = sum(1 for a in all_agents if a.get("health") == "healthy")
        degraded = sum(1 for a in all_agents if a.get("health") == "degraded")
        unhealthy = sum(1 for a in all_agents if a.get("health") == "unhealthy")

        # Agent服务健康度
        agent_health = 100.0 if total == 0 else (healthy / total) * 100

        # 通知服务健康度（基于待处理通知量）
        unread = sum(1 for n in self._notifications.values()
                     if n.get("status") == "unread")
        notification_health = max(0, 100 - unread * 2)

        # 安全健康度（基于安全告警数）
        security_health = max(0, 100 - self._security_alert_count * 10)

        # 公告服务健康度
        published = sum(1 for a in self._announcements.values()
                        if a.get("status") == "published")
        announcement_health = 100.0

        # 综合健康度
        overall = (
            agent_health * 0.4 +
            notification_health * 0.2 +
            security_health * 0.3 +
            announcement_health * 0.1
        )

        if overall >= 90:
            status = "healthy"
        elif overall >= 70:
            status = "degraded"
        else:
            status = "unhealthy"

        return {
            "overall_score": round(overall, 1),
            "status": status,
            "services": {
                "agent_service": {
                    "status": "healthy" if agent_health >= 90 else (
                        "degraded" if agent_health >= 70 else "unhealthy"
                    ),
                    "score": round(agent_health, 1),
                    "total_agents": total,
                    "healthy": healthy,
                    "degraded": degraded,
                    "unhealthy": unhealthy,
                },
                "notification_service": {
                    "status": "healthy" if notification_health >= 90 else (
                        "degraded" if notification_health >= 70 else "unhealthy"
                    ),
                    "score": round(notification_health, 1),
                    "total_notifications": len(self._notifications),
                    "unread": unread,
                },
                "security_service": {
                    "status": "healthy" if security_health >= 90 else (
                        "degraded" if security_health >= 70 else "unhealthy"
                    ),
                    "score": round(security_health, 1),
                    "security_alerts": self._security_alert_count,
                    "audit_logs": self._audit_log_count,
                },
                "announcement_service": {
                    "status": "healthy",
                    "score": round(announcement_health, 1),
                    "total_announcements": len(self._announcements),
                    "published": published,
                },
            },
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_stats(self) -> dict:
        """获取管理统计信息"""
        all_configs = list(self._configs.values())
        by_config_category: dict[str, int] = {}
        for c in all_configs:
            cat = c.get("category", "unknown")
            by_config_category[cat] = by_config_category.get(cat, 0) + 1

        all_notifications = list(self._notifications.values())
        by_notification_status: dict[str, int] = {}
        for n in all_notifications:
            s = n.get("status", "unknown")
            by_notification_status[s] = by_notification_status.get(s, 0) + 1

        all_announcements = list(self._announcements.values())
        by_announcement_status: dict[str, int] = {}
        for a in all_announcements:
            s = a.get("status", "unknown")
            by_announcement_status[s] = by_announcement_status.get(s, 0) + 1

        all_agents = list(self._agent_status.values())
        by_agent_health: dict[str, int] = {}
        for a in all_agents:
            h = a.get("health", "unknown")
            by_agent_health[h] = by_agent_health.get(h, 0) + 1

        return {
            "total_configs": len(all_configs),
            "by_config_category": by_config_category,
            "total_notifications": len(all_notifications),
            "by_notification_status": by_notification_status,
            "total_announcements": len(all_announcements),
            "by_announcement_status": by_announcement_status,
            "total_monitored_agents": len(all_agents),
            "by_agent_health": by_agent_health,
            "security_alert_count": self._security_alert_count,
            "audit_log_count": self._audit_log_count,
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：系统配置项和3条公告"""
        now = datetime.now(timezone.utc).isoformat()

        # 系统配置项
        seed_configs = [
            {
                "key": "system_name",
                "value": "企业数字化大脑",
                "category": "general",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "system_version",
                "value": "2.0.0",
                "category": "general",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "language",
                "value": "zh-CN",
                "category": "general",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "timezone",
                "value": "Asia/Shanghai",
                "category": "general",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "auth_mode",
                "value": "jwt",
                "category": "security",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "security_level",
                "value": "confidential",
                "category": "security",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "session_timeout",
                "value": 3600,
                "category": "security",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "max_agents",
                "value": 50,
                "category": "system",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "event_bus_type",
                "value": "in_memory",
                "category": "system",
                "updated_by": "system",
                "updated_at": now,
            },
            {
                "key": "llm_enabled",
                "value": False,
                "category": "system",
                "updated_by": "system",
                "updated_at": now,
            },
        ]
        for config in seed_configs:
            self._configs[config["key"]] = config

        # 3条公告
        seed_announcements = [
            {
                "announcement_id": "ANN0001",
                "title": "系统升级公告",
                "content": "企业数字化大脑系统将于2026年7月30日凌晨2:00-4:00进行升级维护，届时系统将暂停服务，请提前做好安排。",
                "target_audience": "all",
                "priority": "high",
                "expires_at": "2026-08-15T00:00:00+00:00",
                "status": "published",
                "created_by": "admin",
                "created_at": now,
                "published_at": now,
            },
            {
                "announcement_id": "ANN0002",
                "title": "新功能上线：ESG管理模块",
                "content": "ESG管理模块已正式上线，支持碳排放跟踪、ESG指标管理与报告生成，欢迎各部门使用。",
                "target_audience": "all",
                "priority": "normal",
                "expires_at": "2026-12-31T00:00:00+00:00",
                "status": "published",
                "created_by": "admin",
                "created_at": now,
                "published_at": now,
            },
            {
                "announcement_id": "ANN0003",
                "title": "安全策略更新通知",
                "content": "为提升系统安全性，即日起所有关键配置变更需经过管理员审批，请知悉。",
                "target_audience": "all",
                "priority": "urgent",
                "expires_at": "2026-09-30T00:00:00+00:00",
                "status": "draft",
                "created_by": "admin",
                "created_at": now,
                "published_at": "",
            },
        ]
        for announcement in seed_announcements:
            self._announcements[announcement["announcement_id"]] = announcement
        self._announcement_seq = 3

        logger.info("admin_agent.seed_data_loaded",
                    configs=len(self._configs),
                    announcements=len(self._announcements))
