"""
API端点测试
验证健康检查、系统统计、Agent管理操作
使用 httpx.AsyncClient + ASGITransport 在同一事件循环中测试ASGI应用
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes import router
from app.core.agent.orchestrator import orchestrator
from app.core.security.did import did_registry
from app.core.config import AgentState
from app.agents.inventory.agent import InventoryGuardianAgent
from app.agents.accounting.agent import BookkeepingAgent


# ========== API客户端夹具 ==========

@pytest_asyncio.fixture
async def api_client():
    """创建API测试客户端（基于ASGITransport，不触发lifespan）"""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ========== 辅助函数 ==========

async def _register_inventory_agent() -> InventoryGuardianAgent:
    """创建并注册库存Agent到编排器（不启动主循环）"""
    agent = InventoryGuardianAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await orchestrator.register_agent(agent)
    return agent


async def _register_bookkeeping_agent() -> BookkeepingAgent:
    """创建并注册记账Agent到编排器（不启动主循环）"""
    agent = BookkeepingAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await orchestrator.register_agent(agent)
    return agent


# ========== test_health_check ==========

async def test_health_check(api_client):
    """健康检查端点"""
    response = await api_client.get("/api/v1/health")

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert "version" in data
    assert "domain" in data


# ========== test_system_stats ==========

async def test_system_stats(api_client):
    """系统统计端点"""
    response = await api_client.get("/api/v1/system/stats")

    assert response.status_code == 200

    data = response.json()

    # 验证所有子系统统计都存在
    assert "event_bus" in data
    assert "event_store" in data
    assert "saga_coordinator" in data
    assert "did_registry" in data
    assert "authorization" in data
    assert "audit_chain" in data
    assert "guardrails" in data
    assert "orchestrator" in data
    assert "tool_registry" in data

    # 验证事件总线统计结构
    assert "published" in data["event_bus"]
    assert "delivered" in data["event_bus"]
    assert "failed" in data["event_bus"]
    assert "subscriptions" in data["event_bus"]

    # 验证DID注册中心统计结构
    assert "total" in data["did_registry"]
    assert "active" in data["did_registry"]
    assert "revoked" in data["did_registry"]

    # 验证授权引擎统计结构
    assert "active_sessions" in data["authorization"]
    assert "granted" in data["authorization"]
    assert "denied" in data["authorization"]

    # 验证审计链统计结构
    assert "total_entries" in data["audit_chain"]
    assert "integrity_valid" in data["audit_chain"]

    # 验证护栏统计结构
    assert "allowed" in data["guardrails"]
    assert "blocked" in data["guardrails"]
    assert "sanitized" in data["guardrails"]

    # 验证编排器统计结构
    assert "total_agents" in data["orchestrator"]
    assert "state_distribution" in data["orchestrator"]


# ========== test_agent_management ==========

async def test_agent_management(api_client):
    """Agent管理操作：注册→列表→详情→暂停→恢复"""
    # 1. 注册Agent到编排器
    agent = await _register_inventory_agent()

    # 2. 列出所有Agent
    response = await api_client.get("/api/v1/agents")
    assert response.status_code == 200

    data = response.json()
    assert len(data["agents"]) == 1
    agent_info = data["agents"][0]
    assert agent_info["name"] == "库存守护Agent"
    assert agent_info["domain"] == "inventory"
    assert "capabilities" in agent_info

    # 3. 获取Agent详情
    agent_id = agent.agent_id
    response = await api_client.get(f"/api/v1/agents/{agent_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["agent_id"] == agent_id
    assert data["name"] == "库存守护Agent"
    assert data["domain"] == "inventory"
    assert data["did"] is not None
    assert "circuit_breaker" in data
    assert "stats" in data

    # 4. 暂停Agent
    response = await api_client.post(f"/api/v1/agents/{agent_id}/pause")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "paused"
    assert data["agent_id"] == agent_id

    # 验证Agent状态确实已改变
    assert agent.state == AgentState.PAUSED

    # 5. 恢复Agent
    response = await api_client.post(f"/api/v1/agents/{agent_id}/resume")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "running"
    assert data["agent_id"] == agent_id

    # 验证Agent状态已恢复
    assert agent.state == AgentState.RUNNING


# ========== 额外测试：获取不存在的Agent返回404 ==========

async def test_get_nonexistent_agent(api_client):
    """获取不存在的Agent返回404"""
    response = await api_client.get("/api/v1/agents/nonexistent-agent-id")
    assert response.status_code == 404

    data = response.json()
    assert "detail" in data
    assert "not found" in data["detail"].lower()


# ========== 额外测试：暂停不存在的Agent返回404 ==========

async def test_pause_nonexistent_agent(api_client):
    """暂停不存在的Agent返回404"""
    response = await api_client.post("/api/v1/agents/nonexistent-id/pause")
    assert response.status_code == 404


# ========== 额外测试：恢复不存在的Agent返回404 ==========

async def test_resume_nonexistent_agent(api_client):
    """恢复不存在的Agent返回404"""
    response = await api_client.post("/api/v1/agents/nonexistent-id/resume")
    assert response.status_code == 404


# ========== 额外测试：空Agent列表 ==========

async def test_empty_agent_list(api_client):
    """无Agent时列表为空"""
    response = await api_client.get("/api/v1/agents")
    assert response.status_code == 200

    data = response.json()
    assert data["agents"] == []


# ========== 额外测试：人类接管与释放 ==========

async def test_human_takeover_and_release(api_client):
    """人类接管与释放Agent"""
    agent = await _register_inventory_agent()
    agent_id = agent.agent_id

    # 人类接管
    response = await api_client.post(f"/api/v1/agents/{agent_id}/takeover")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "human_takeover"
    assert agent._human_override is True

    # 释放控制
    response = await api_client.post(f"/api/v1/agents/{agent_id}/release")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "released"
    assert agent._human_override is False


# ========== 额外测试：多Agent列表 ==========

async def test_multiple_agents(api_client):
    """多Agent注册后列表正确"""
    agent1 = await _register_inventory_agent()
    agent2 = await _register_bookkeeping_agent()

    response = await api_client.get("/api/v1/agents")
    assert response.status_code == 200

    data = response.json()
    assert len(data["agents"]) == 2

    agent_names = [a["name"] for a in data["agents"]]
    assert "库存守护Agent" in agent_names
    assert "记账Agent" in agent_names

    # 验证两个Agent的domain不同
    domains = [a["domain"] for a in data["agents"]]
    assert "inventory" in domains
    assert "accounting" in domains


# ========== 额外测试：安全相关端点 ==========

async def test_security_endpoints(api_client):
    """安全相关API端点"""
    # 注册身份
    did_registry.register_agent(
        name="API测试Agent",
        domain="test",
        capabilities=["general"],
        security_clearance="internal",
    )

    # 1. 列出所有身份
    response = await api_client.get("/api/v1/security/identities")
    assert response.status_code == 200

    data = response.json()
    assert "identities" in data
    assert len(data["identities"]) >= 1

    # 2. 安全统计
    response = await api_client.get("/api/v1/security/stats")
    assert response.status_code == 200

    data = response.json()
    assert "did_registry" in data
    assert "authorization" in data
    assert "audit_chain" in data
    assert "guardrails" in data

    # 3. 审计日志查询
    response = await api_client.get("/api/v1/security/audit")
    assert response.status_code == 200

    data = response.json()
    assert "entries" in data
    assert "count" in data

    # 4. 审计链完整性验证
    response = await api_client.get("/api/v1/security/audit/verify")
    assert response.status_code == 200

    data = response.json()
    assert "integrity_valid" in data
    assert data["integrity_valid"] is True


# ========== 额外测试：工具列表端点 ==========

async def test_tools_endpoint(api_client):
    """工具列表端点"""
    response = await api_client.get("/api/v1/tools")
    assert response.status_code == 200

    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
