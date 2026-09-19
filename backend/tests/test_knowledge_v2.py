"""
知识库改进测试（WeKnora 模式适配：语义通道/持久化/工具）
========================================================
1. EmbeddingProvider：hashing 确定性与维度、openai mock 解析、工厂降级；
2. 混合检索语义通道：向量注入后 semantic_score 生效、命中排序改善；
3. 持久化与重启一致性：摄取 → 落库 → 新实例水合 → 检索命中（E-04）；
4. 治理面工具：knowledge_search/answer（RBAC 放行下真实检索+引用）。
"""
import asyncio
import tempfile
import pathlib

import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.knowledge.embeddings import (
    HashingEmbeddingProvider,
    get_embedding_provider,
)


# ===========================================================================
# 1. EmbeddingProvider
# ===========================================================================

async def test_hashing_provider_deterministic_and_normalized():
    p = HashingEmbeddingProvider(dims=256)
    v1 = (await p.embed(["ERP备份策略保留30天"]))[0]
    v2 = (await p.embed(["ERP备份策略保留30天"]))[0]
    assert v1 == v2, "同文本向量必须一致（确定性）"
    assert len(v1) == 256
    norm = sum(v * v for v in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6, "向量应 L2 归一化"
    # 空文本安全
    v3 = (await p.embed([""]))[0]
    assert len(v3) == 256 and all(x == 0 for x in v3)


async def test_openai_provider_parsing(monkeypatch):
    """OpenAI 兼容 /embeddings：响应解析与批处理（mock HTTP）。"""
    import httpx

    from app.core.knowledge.embeddings import OpenAIEmbeddingProvider

    captured = {}

    def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        texts = kwargs["json"]["input"]
        captured["calls"] = captured.get("calls", 0) + 1

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [
                    {"index": i, "embedding": [0.5] * 8} for i in range(len(texts))
                ]}

        async def _async_post(*a, **kw):
            return R()

        return _async_post()

    # fastmcp 4 引入 httpx2 分叉，兼容两种客户端模块
    patched = False
    for mod_name in ("httpx", "httpx2"):
        try:
            mod = __import__(mod_name, fromlist=["AsyncClient"])
            monkeypatch.setattr(mod.AsyncClient, "post", fake_post)
            patched = True
        except (ImportError, AttributeError):
            continue
    assert patched, "httpx/httpx2 均不可 patch"

    provider = OpenAIEmbeddingProvider(
        base_url="https://api.example.com/v1", api_key="sk-test",
        model="BAAI/bge-m3", batch_size=2,
    )
    vecs = await provider.embed(["a", "b", "c", "d", "e"])
    assert len(vecs) == 5
    assert all(len(v) == 8 for v in vecs)
    assert captured["url"].endswith("/embeddings")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["calls"] == 3, "batch_size=2 时 5 条应分 3 批"


async def test_provider_factory_fallback(monkeypatch):
    """工厂：openai 模式无 key → 降级 hashing（显式告警）。"""
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    p = get_embedding_provider()
    assert p.name == "hashing"


# ===========================================================================
# 2/3. 混合检索语义通道 + 持久化重启一致性
# ===========================================================================

SAMPLE_DOCS = [
    "ERP系统备份策略：每日凌晨2点执行全量备份，每周日执行增量备份，备份文件保留30天后自动清理。",
    "报销制度：差旅费需提供行程单与发票，单笔超过5000元需部门总监审批，餐费每日上限200元。",
    "合同管理规定：合同签署前必须完成法务审查，金额超过100万的合同需分管副总裁会签。",
]


@pytest_asyncio.fixture
async def kb_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "kb_v2.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


async def _ingest_samples():
    from app.core.knowledge.ingestion import document_ingester

    all_docs = []
    for i, text in enumerate(SAMPLE_DOCS):
        docs = document_ingester.ingest_text(
            text, permission_level="internal",
            metadata={"filename": f"制度文档{i + 1}.md"},
        )
        all_docs.extend(docs)
    await asyncio.sleep(0.6)  # 等异步向量化流水线
    return all_docs


async def test_hybrid_semantic_channel_effective(kb_env, monkeypatch):
    """语义通道：向量注入后 semantic_score 生效，含语义分参与融合。"""
    from app.core.knowledge.hybrid import hybrid_engine

    docs = await _ingest_samples()
    assert docs, "样例摄取失败"
    hybrid_engine.semantic_vectors.clear()
    for d in docs:
        if d.embedding:
            hybrid_engine.semantic_vectors[d.doc_id] = d.embedding

    results = await hybrid_engine.retrieve("备份保留多少天", top_k=3)
    assert results, "应有检索结果"
    top = results[0]
    assert "备份" in top["content"], f"语义+关键词融合应命中备份文档：{top['content'][:80]}"
    assert top["semantic_score"] > 0, "注入向量后语义分应生效"


async def test_persistence_restart_consistency(kb_env):
    """E-04 修复验证：摄取落库 → 全新引擎状态（清空内存索引）→ 水合恢复 → 检索命中。"""
    from app.core.knowledge.hybrid import hybrid_engine
    from app.core.knowledge.ingestion import document_ingester
    from app.core.knowledge.persistence import hydrate_knowledge_from_db

    docs = await _ingest_samples()
    assert docs
    info = await hydrate_knowledge_from_db()
    assert info["chunks"] >= 3 and info["vectorized"] >= 3

    # 模拟重启：清空全部内存索引（vector_store/BM25/语义）
    from app.core.knowledge.rag import vector_store

    vector_store.clear()
    hybrid_engine.semantic_vectors.clear()
    assert (await hybrid_engine.retrieve("备份", top_k=3)) == [], "清空后应无结果"

    # 水合恢复
    info = await hydrate_knowledge_from_db()
    assert info["chunks"] >= 3
    results = await hybrid_engine.retrieve("报销 审批 阈值", top_k=3)
    assert results, "水合后应恢复检索能力"
    assert any("报销" in r["content"] or "合同" in r["content"] for r in results)


# ===========================================================================
# 4. 治理面工具
# ===========================================================================

async def test_knowledge_tools_governed(kb_env, monkeypatch):
    """knowledge_search/answer：治理面调用（RBAC 放行）真实检索 + 引用溯源。"""
    import app.agents.knowledge.tools  # noqa: F401
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.orchestrator import orchestrator
    from app.core.agent.tools_v2 import ToolContext
    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    from app.agents.knowledge.agent import KnowledgeAgent

    ka = KnowledgeAgent()
    ka.identity = did_register(ka)
    await orchestrator.register_agent(ka)

    await _ingest_samples()

    gov = AgentGovernor()
    ctx = ToolContext(user_id="user_admin", username="admin",
                      agent_key="knowledge", thread_id="kb-tools-1")

    r1 = await gov.execute("knowledge_search", {"query": "备份策略"}, ctx)
    assert r1.ok and r1.data["total"] >= 1
    assert r1.data["results"][0]["snippet"]

    r2 = await gov.execute("knowledge_answer", {"question": "报销审批流程是什么"}, ctx)
    assert r2.ok
    assert r2.data["citations"], "answer 应携带引用"
    assert "[1]" in r2.data["answer_material"]


def did_register(agent):
    from app.core.security.did import did_registry

    return did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
