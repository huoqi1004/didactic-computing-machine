# -*- coding: utf-8 -*-
"""
管理Agent测试
验证系统配置、Agent监控、通知管理、公告管理、系统健康度与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.admin.agent import AdminAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> AdminAgent:
    """创建并初始化管理Agent（不启动主循环，仅注册身份和订阅）"""
    agent = AdminAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_agent_registered_event(
    agent_id: str = "agt-test-001",
    name: str = "测试Agent",
    domain: str = "test",
) -> Event:
    """创建Agent注册事件"""
    return Event(
        event_type=EventType.AGENT_REGISTERED,
        aggregate_id=agent_id,
        aggregate_type="agent",
        event_data={
            "agent_id": agent_id,
            "name": name,
            "domain": domain,
            "capabilities": ["general"],
            "did": "did:erp:test:abc123",
        },
    )


def _make_agent_state_changed_event(
    agent_id: str = "agt-test-001",
    state: str = "paused",
) -> Event:
    """创建Agent状态变更事件"""
    return Event(
        event_type=EventType.AGENT_STATE_CHANGED,
        aggregate_id=agent_id,
        aggregate_type="agent",
        event_data={
            "agent_id": agent_id,
            "state": state,
            "reason": "manual",
        },
    )


def _make_security_alert_event(
    alert_id: str = "ALT-001",
    severity: str = "high",
    type: str = "unauthorized_access",
    description: str = "检测到未授权访问",
) -> Event:
    """创建安全告警事件"""
    return Event(
        event_type=EventType.SECURITY_ALERT,
        aggregate_id=alert_id,
        aggregate_type="security",
        event_data={
            "alert_id": alert_id,
            "severity": severity,
            "type": type,
            "description": description,
            "source": "ids",
        },
    )


def _make_audit_log_event(
    log_id: str = "LOG-001",
) -> Event:
    """创建审计日志事件"""
    return Event(
        event_type=EventType.AUDIT_LOG_CREATED,
        aggregate_id=log_id,
        aggregate_type="audit",
        event_data={
            "log_id": log_id,
            "action": "config.update",
            "actor": "admin",
            "resource": "system:config",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "管理Agent"
    assert agent.domain == "admin"
    assert agent.security_clearance == "restricted"
    assert "system_config" in agent.capabilities
    assert "agent_monitoring" in agent.capabilities
    assert "notification_management" in agent.capabilities
    assert "announcement_management" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载：10个配置项和3条公告"""
    agent = await _setup_agent()

    configs = agent.get_config()
    assert len(configs) == 10

    announcements = agent.get_announcements()
    assert len(announcements) == 3

    # 验证关键配置项存在
    assert "system_name" in configs
    assert "auth_mode" in configs
    assert "llm_enabled" in configs

    # 验证公告ID
    ann_ids = [a["announcement_id"] for a in announcements]
    assert "ANN0001" in ann_ids
    assert "ANN0002" in ann_ids
    assert "ANN0003" in ann_ids

    # 验证公告状态：2条已发布，1条草稿
    published = agent.get_announcements(status="published")
    assert len(published) == 2
    drafts = agent.get_announcements(status="draft")
    assert len(drafts) == 1


# ========== test_agent_registered_event ==========

async def test_agent_registered_event():
    """Agent注册事件记录监控状态"""
    agent = await _setup_agent()

    event = _make_agent_registered_event(agent_id="agt-test-001", name="库存Agent")
    await agent.process_event(event)

    status = agent.get_agent_status()
    assert len(status) == 1
    assert status[0]["agent_id"] == "agt-test-001"
    assert status[0]["name"] == "库存Agent"
    assert status[0]["state"] == "running"
    assert status[0]["health"] == "healthy"


# ========== test_agent_state_changed_event ==========

async def test_agent_state_changed_event():
    """Agent状态变更事件更新健康度"""
    agent = await _setup_agent()

    # 先注册Agent
    await agent.process_event(
        _make_agent_registered_event(agent_id="agt-test-001")
    )

    # 发送状态变更：paused -> degraded
    await agent.process_event(
        _make_agent_state_changed_event(agent_id="agt-test-001", state="paused")
    )

    status = agent.get_agent_status()
    assert status[0]["state"] == "paused"
    assert status[0]["health"] == "degraded"

    # 发送状态变更：error -> unhealthy
    await agent.process_event(
        _make_agent_state_changed_event(agent_id="agt-test-001", state="error")
    )
    status = agent.get_agent_status()
    assert status[0]["state"] == "error"
    assert status[0]["health"] == "unhealthy"


# ========== test_security_alert_high_severity_notification ==========

async def test_security_alert_high_severity_notification():
    """高危安全告警自动发送通知"""
    agent = await _setup_agent()

    # 发送高危告警
    await agent.process_event(
        _make_security_alert_event(severity="high", description="检测到SQL注入攻击")
    )

    # 应自动发送通知给admin
    notifications = agent.get_notifications(user_id="admin")
    assert len(notifications) >= 1
    assert notifications[0]["category"] == "security"
    assert notifications[0]["priority"] == "urgent"
    assert "SQL注入" in notifications[0]["content"]

    # 安全告警计数应增加
    assert agent._security_alert_count == 1


# ========== test_audit_log_count ==========

async def test_audit_log_count():
    """审计日志事件递增计数器"""
    agent = await _setup_agent()

    assert agent._audit_log_count == 0

    # 发送3条审计日志事件
    for i in range(3):
        await agent.process_event(
            _make_audit_log_event(log_id=f"LOG-{i:03d}")
        )

    assert agent._audit_log_count == 3


# ========== test_make_decision_set_config ==========

async def test_make_decision_set_config():
    """普通配置变更决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "set_config",
        "key": "session_timeout",
        "value": 7200,
    })
    assert decision.decision_type == "system_config"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is False
    assert "更新配置" in decision.action


# ========== test_make_decision_critical_config ==========

async def test_make_decision_critical_config():
    """关键配置变更需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "set_config",
        "key": "security_level",
        "value": "top_secret",
    })
    assert decision.decision_type == "system_config"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.8
    assert "人工审批" in decision.action


# ========== test_make_decision_publish_announcement ==========

async def test_make_decision_publish_announcement():
    """公告发布决策"""
    agent = await _setup_agent()

    # ANN0003为草稿状态，可发布
    decision = await agent.make_decision({
        "action": "publish_announcement",
        "announcement_id": "ANN0003",
    })
    assert decision.decision_type == "announcement_publish"
    assert decision.confidence == 0.9
    assert "发布公告" in decision.action


# ========== test_make_decision_send_notification ==========

async def test_make_decision_send_notification():
    """通知发送决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "send_notification",
        "user_id": "user-001",
    })
    assert decision.decision_type == "notification_send"
    assert decision.confidence == 0.9
    assert "发送通知" in decision.action


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作返回拒绝决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "admin_unknown"
    assert decision.confidence == 0.0
    assert "拒绝执行" in decision.action


# ========== test_config_management ==========

async def test_config_management():
    """系统配置管理：按分类查询与设置"""
    agent = await _setup_agent()

    # 按分类过滤
    general_configs = agent.get_config(category="general")
    assert len(general_configs) >= 4
    assert "system_name" in general_configs

    security_configs = agent.get_config(category="security")
    assert len(security_configs) >= 3
    assert "auth_mode" in security_configs

    # 设置新配置
    config = agent.set_config(
        key="test_config",
        value="test_value",
        category="test_category",
        updated_by="tester",
    )
    assert config["key"] == "test_config"
    assert config["value"] == "test_value"
    assert config["old_value"] is None

    # 更新已有配置（保留旧值）
    updated = agent.set_config(
        key="test_config",
        value="new_value",
        category="test_category",
        updated_by="tester2",
    )
    assert updated["old_value"] == "test_value"

    # 验证可查询到新配置
    test_configs = agent.get_config(category="test_category")
    assert "test_config" in test_configs


# ========== test_announcement_management ==========

async def test_announcement_management():
    """公告管理：创建、发布与查询"""
    agent = await _setup_agent()

    # 创建公告
    announcement = agent.create_announcement(
        title="测试公告",
        content="这是一条测试公告内容",
        target_audience="all",
        priority="normal",
        expires_at="2026-12-31T00:00:00+00:00",
    )
    assert announcement["status"] == "draft"
    assert announcement["title"] == "测试公告"
    assert announcement["announcement_id"].startswith("ANN")

    # 发布公告
    published = await agent.publish_announcement(announcement["announcement_id"])
    assert published["status"] == "published"
    assert published["published_at"] != ""

    # 按状态查询
    all_published = agent.get_announcements(status="published")
    assert len(all_published) == 3  # 2条种子 + 1条新建

    drafts = agent.get_announcements(status="draft")
    assert len(drafts) == 1  # 仅ANN0003


# ========== test_notification_management ==========

async def test_notification_management():
    """通知管理：发送、查询与标记已读"""
    agent = await _setup_agent()

    # 发送通知
    notification = await agent.send_notification(
        user_id="user-001",
        title="测试通知",
        content="测试通知内容",
        category="system",
        priority="normal",
    )
    assert notification["status"] == "unread"
    assert notification["user_id"] == "user-001"
    assert notification["notification_id"].startswith("NTF")

    # 查询通知
    user_notifications = agent.get_notifications(user_id="user-001")
    assert len(user_notifications) >= 1

    unread = agent.get_notifications(user_id="user-001", status="unread")
    assert len(unread) >= 1

    # 标记已读
    read = agent.mark_notification_read(notification["notification_id"])
    assert read["status"] == "read"
    assert read["read_at"] != ""

    read_notifications = agent.get_notifications(user_id="user-001", status="read")
    assert len(read_notifications) >= 1


# ========== test_system_health ==========

async def test_system_health():
    """系统健康度评估"""
    agent = await _setup_agent()

    # 注册几个Agent
    await agent.process_event(
        _make_agent_registered_event(agent_id="agt-001", name="Agent1")
    )
    await agent.process_event(
        _make_agent_registered_event(agent_id="agt-002", name="Agent2")
    )

    health = agent.get_system_health()
    assert "overall_score" in health
    assert "status" in health
    assert "services" in health

    services = health["services"]
    assert "agent_service" in services
    assert "notification_service" in services
    assert "security_service" in services
    assert "announcement_service" in services

    # 2个Agent都健康
    assert services["agent_service"]["total_agents"] == 2
    assert services["agent_service"]["healthy"] == 2

    # 初始无安全告警，安全健康度满
    assert services["security_service"]["security_alerts"] == 0

    # 发送安全告警后安全健康度下降
    await agent.process_event(
        _make_security_alert_event(severity="high")
    )
    health2 = agent.get_system_health()
    assert health2["services"]["security_service"]["security_alerts"] == 1
    assert health2["services"]["security_service"]["score"] < 100


# ========== test_get_stats ==========

async def test_get_stats():
    """管理统计信息"""
    agent = await _setup_agent()

    # 发送安全告警和审计日志
    await agent.process_event(_make_security_alert_event())
    await agent.process_event(_make_audit_log_event())

    stats = agent.get_stats()
    assert stats["total_configs"] == 10
    assert stats["total_announcements"] == 3
    assert stats["security_alert_count"] == 1
    assert stats["audit_log_count"] == 1
    assert stats["total_monitored_agents"] == 0
    assert "by_config_category" in stats
    assert "by_announcement_status" in stats
    assert "by_agent_health" in stats
