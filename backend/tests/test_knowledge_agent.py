# -*- coding: utf-8 -*-
"""
知识库Agent测试
验证文档检索、知识图谱查询、规则评估与文档摄入功能
"""
import asyncio
import pytest

from app.core.security.did import did_registry
from app.core.knowledge.hybrid import hybrid_engine
from app.agents.knowledge.agent import KnowledgeAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> KnowledgeAgent:
    """创建并初始化知识Agent（不启动主循环，仅注册身份和订阅）"""
    agent = KnowledgeAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "知识Agent"
    assert agent.domain == "knowledge"
    assert agent.security_clearance == "confidential"
    assert "document_retrieval" in agent.capabilities
    assert "knowledge_graph_query" in agent.capabilities
    assert "rule_evaluation" in agent.capabilities
    assert "document_ingestion" in agent.capabilities
    assert "knowledge_management" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_ingest_text ==========

async def test_ingest_text():
    """摄入纯文本文档"""
    agent = await _setup_agent()

    result = await agent.ingest_text(
        text="增值税专用发票的税率为13%，适用于一般纳税人销售货物或提供应税劳务。",
        category="tax",
        department="财务部",
        tags=["增值税", "税率"],
        uploaded_by="测试用户",
    )

    assert "record_id" in result
    assert result["category"] == "tax"
    assert result["chunk_count"] > 0
    assert agent._ingest_count == 1


# ========== test_retrieve ==========

async def test_retrieve():
    """知识检索"""
    agent = await _setup_agent()

    # 先摄入文档
    await agent.ingest_text(
        text="企业所得税税率为25%，适用于居民企业。",
        category="tax",
        department="财务部",
        tags=["企业所得税"],
    )
    # 等待异步文档入库完成
    await asyncio.sleep(0.1)
    hybrid_engine.sync_bm25_index()

    results = await agent.retrieve(
        query="企业所得税税率",
        top_k=5,
    )

    assert isinstance(results, list)
    assert len(results) > 0
    assert "content" in results[0]
    assert "score" in results[0]
    assert agent._retrieve_count == 1


# ========== test_retrieve_empty ==========

async def test_retrieve_empty():
    """空知识库检索返回空列表"""
    agent = await _setup_agent()

    results = await agent.retrieve(query="不存在的查询内容xyz123")

    assert isinstance(results, list)
    assert len(results) == 0


# ========== test_retrieve_context ==========

async def test_retrieve_context():
    """检索上下文文本"""
    agent = await _setup_agent()

    await agent.ingest_text(
        text="采购流程包括需求确认、供应商选择、合同签订、验收入库。",
        category="procurement",
        department="采购部",
    )
    # 等待异步文档入库完成
    await asyncio.sleep(0.1)
    hybrid_engine.sync_bm25_index()

    context = await agent.retrieve_context(query="采购流程", top_k=3)

    assert isinstance(context, str)
    assert len(context) > 0


# ========== test_retrieve_context_empty ==========

async def test_retrieve_context_empty():
    """无结果时上下文为空字符串"""
    agent = await _setup_agent()

    context = await agent.retrieve_context(query="不存在的查询内容xyz123")
    assert context == ""


# ========== test_query_graph ==========

async def test_query_graph():
    """知识图谱查询"""
    agent = await _setup_agent()

    # 空图谱查询应返回空列表
    results = agent.query_graph("node_type:product")
    assert isinstance(results, list)


# ========== test_get_graph_stats ==========

async def test_get_graph_stats():
    """获取知识图谱统计"""
    agent = await _setup_agent()

    stats = agent.get_graph_stats()
    assert isinstance(stats, dict)
    assert "nodes" in stats or "node_count" in stats or "total_nodes" in stats


# ========== test_evaluate_rules ==========

async def test_evaluate_rules():
    """业务规则评估"""
    agent = await _setup_agent()

    matched = await agent.evaluate_rules({"amount": 150000, "hour": 22})

    assert isinstance(matched, list)
    # 预置规则可能匹配，也可能不匹配，取决于上下文
    for rule in matched:
        assert "rule_id" in rule
        assert "action" in rule


# ========== test_get_knowledge_stats ==========

async def test_get_knowledge_stats():
    """获取知识库统计"""
    agent = await _setup_agent()

    stats = agent.get_knowledge_stats()
    assert "agent_stats" in stats
    assert stats["agent_stats"]["retrieve_count"] == 0
    assert stats["agent_stats"]["ingest_count"] == 0


# ========== test_make_decision ==========

async def test_make_decision():
    """知识检索决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"query": "增值税税率"})

    assert decision.decision_type == "knowledge_retrieval"
    assert decision.confidence == 0.95
    assert decision.action == "retrieve_knowledge"


# ========== test_get_status ==========

async def test_get_status():
    """获取Agent扩展状态"""
    agent = await _setup_agent()

    status = agent.get_status()
    assert "knowledge_stats" in status
    assert status["knowledge_stats"]["retrieve_count"] == 0


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = KnowledgeAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
