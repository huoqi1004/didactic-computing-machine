# -*- coding: utf-8 -*-
"""
认知与知识层测试
================

验证知识图谱CRUD、向量库RAG检索、规则引擎评估的完整功能。

测试覆盖：
1. 知识图谱：节点/边CRUD、查询语法、邻居遍历、序列化、统计
2. 向量库：文档添加、TF-IDF检索、标签过滤、删除、统计
3. 规则引擎：规则注册/删除/评估/执行、安全沙箱、预置业务规则
4. 知识库初始化：示例数据注入
5. API端点：知识图谱查询、文档检索、规则评估

共 25+ 测试用例。
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes import router
from app.core.knowledge.graph import (
    KnowledgeGraph,
    KnowledgeNode,
    KnowledgeEdge,
    knowledge_graph,
)
from app.core.knowledge.rag import (
    Document,
    InMemoryVectorStore,
    vector_store,
)
from app.core.knowledge.rules import (
    Rule,
    RuleEngine,
    SafeEvalError,
    safe_eval,
    rule_engine,
    load_builtin_rules,
)
from app.core.knowledge.initializer import init_knowledge_base, reset_initialized


# =============================================================================
# 知识图谱测试
# =============================================================================

def test_add_node_returns_id(fresh_knowledge_graph):
    """添加节点返回唯一ID"""
    kg = fresh_knowledge_graph
    node_id = kg.add_node("product", {"name": "电阻", "sku": "R001"})
    assert node_id is not None
    assert isinstance(node_id, str)
    assert len(node_id) > 0


def test_add_node_invalid_type_raises(fresh_knowledge_graph):
    """添加非法节点类型抛出ValueError"""
    kg = fresh_knowledge_graph
    with pytest.raises(ValueError, match="Invalid node_type"):
        kg.add_node("invalid_type", {"name": "test"})


def test_get_node(fresh_knowledge_graph):
    """获取节点"""
    kg = fresh_knowledge_graph
    node_id = kg.add_node("product", {"name": "电容", "category": "电子元件"})
    node = kg.get_node(node_id)
    assert node is not None
    assert node.node_id == node_id
    assert node.node_type == "product"
    assert node.properties["name"] == "电容"
    assert node.properties["category"] == "电子元件"


def test_get_nonexistent_node_returns_none(fresh_knowledge_graph):
    """获取不存在的节点返回None"""
    kg = fresh_knowledge_graph
    assert kg.get_node("nonexistent-id") is None


def test_find_nodes_by_type(fresh_knowledge_graph):
    """按类型查找节点"""
    kg = fresh_knowledge_graph
    kg.add_node("product", {"name": "电阻"})
    kg.add_node("product", {"name": "电容"})
    kg.add_node("supplier", {"name": "供应商A"})

    products = kg.find_nodes("product")
    assert len(products) == 2
    suppliers = kg.find_nodes("supplier")
    assert len(suppliers) == 1


def test_find_nodes_with_filter(fresh_knowledge_graph):
    """按属性过滤查找节点"""
    kg = fresh_knowledge_graph
    kg.add_node("product", {"name": "电阻", "category": "电子元件"})
    kg.add_node("product", {"name": "芯片", "category": "集成电路"})
    kg.add_node("product", {"name": "电容", "category": "电子元件"})

    # 按category过滤
    filtered = kg.find_nodes("product", {"category": "电子元件"})
    assert len(filtered) == 2
    names = [n.properties["name"] for n in filtered]
    assert "电阻" in names
    assert "电容" in names
    assert "芯片" not in names


def test_add_edge_and_get_neighbors(fresh_knowledge_graph):
    """添加边并获取邻居"""
    kg = fresh_knowledge_graph
    p1 = kg.add_node("product", {"name": "电阻"})
    s1 = kg.add_node("supplier", {"name": "供应商A"})

    edge_id = kg.add_edge(s1, p1, "supplies_to", {"lead_time": 7})
    assert edge_id is not None

    # 获取供应商的邻居
    neighbors = kg.get_neighbors(s1)
    assert len(neighbors) == 1
    edge, neighbor = neighbors[0]
    assert edge.edge_type == "supplies_to"
    assert neighbor.node_id == p1
    assert neighbor.properties["name"] == "电阻"

    # 获取产品的邻居（反向）
    reverse_neighbors = kg.get_neighbors(p1)
    assert len(reverse_neighbors) == 1
    _, reverse_neighbor = reverse_neighbors[0]
    assert reverse_neighbor.node_id == s1


def test_get_neighbors_with_edge_type_filter(fresh_knowledge_graph):
    """按关系类型过滤邻居"""
    kg = fresh_knowledge_graph
    p1 = kg.add_node("product", {"name": "电阻"})
    s1 = kg.add_node("supplier", {"name": "供应商A"})
    w1 = kg.add_node("warehouse", {"name": "主仓库"})

    kg.add_edge(s1, p1, "supplies_to")
    kg.add_edge(p1, w1, "belongs_to")

    # 只查supplies_to关系
    supplies = kg.get_neighbors(p1, edge_type="supplies_to")
    assert len(supplies) == 1
    assert supplies[0][1].node_id == s1

    # 只查belongs_to关系
    belongs = kg.get_neighbors(p1, edge_type="belongs_to")
    assert len(belongs) == 1
    assert belongs[0][1].node_id == w1


def test_add_edge_invalid_type_raises(fresh_knowledge_graph):
    """添加非法关系类型抛出ValueError"""
    kg = fresh_knowledge_graph
    p1 = kg.add_node("product", {"name": "电阻"})
    s1 = kg.add_node("supplier", {"name": "供应商A"})
    with pytest.raises(ValueError, match="Invalid edge_type"):
        kg.add_edge(s1, p1, "invalid_relation")


def test_add_edge_nonexistent_node_raises(fresh_knowledge_graph):
    """添加边时引用不存在的节点抛出ValueError"""
    kg = fresh_knowledge_graph
    p1 = kg.add_node("product", {"name": "电阻"})
    with pytest.raises(ValueError, match="Target node not found"):
        kg.add_edge(p1, "nonexistent", "supplies_to")
    with pytest.raises(ValueError, match="Source node not found"):
        kg.add_edge("nonexistent", p1, "supplies_to")


def test_query_simple_syntax(fresh_knowledge_graph):
    """简单查询语法"""
    kg = fresh_knowledge_graph
    kg.add_node("product", {"name": "电阻", "category": "电子元件"})
    kg.add_node("product", {"name": "芯片", "category": "集成电路"})
    kg.add_node("supplier", {"name": "供应商A"})

    # 按类型查询
    results = kg.query("node_type:product")
    assert len(results) == 2

    # 类型 + 属性过滤
    results = kg.query("node_type:product[category=电子元件]")
    assert len(results) == 1
    assert results[0]["properties"]["name"] == "电阻"

    # 仅属性过滤（无类型）
    results = kg.query("[name=供应商A]")
    assert len(results) == 1
    assert results[0]["node_type"] == "supplier"


def test_remove_node_cascades_edges(fresh_knowledge_graph):
    """删除节点级联删除关联边"""
    kg = fresh_knowledge_graph
    p1 = kg.add_node("product", {"name": "电阻"})
    s1 = kg.add_node("supplier", {"name": "供应商A"})
    edge_id = kg.add_edge(s1, p1, "supplies_to")

    # 删除产品节点
    assert kg.remove_node(p1) is True
    assert kg.get_node(p1) is None
    assert kg.get_edge(edge_id) is None
    # 边应被级联删除
    assert kg.get_neighbors(s1) == []


def test_remove_nonexistent_node_returns_false(fresh_knowledge_graph):
    """删除不存在的节点返回False"""
    kg = fresh_knowledge_graph
    assert kg.remove_node("nonexistent-id") is False


def test_graph_serialization(fresh_knowledge_graph):
    """图谱序列化与反序列化"""
    kg = fresh_knowledge_graph
    p1 = kg.add_node("product", {"name": "电阻", "price": 0.05})
    s1 = kg.add_node("supplier", {"name": "供应商A"})
    kg.add_edge(s1, p1, "supplies_to", {"lead_time": 7})

    # 序列化
    data = kg.to_dict()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1

    # 反序列化
    kg2 = KnowledgeGraph.from_dict(data)
    assert len(kg2.find_nodes("product")) == 1
    assert len(kg2.find_nodes("supplier")) == 1
    # 验证边恢复后邻居关系正常
    neighbors = kg2.get_neighbors(s1)
    assert len(neighbors) == 1


def test_graph_stats(fresh_knowledge_graph):
    """图谱统计信息"""
    kg = fresh_knowledge_graph
    kg.add_node("product", {"name": "电阻"})
    kg.add_node("product", {"name": "电容"})
    kg.add_node("supplier", {"name": "供应商A"})
    p1 = kg.find_nodes("product")[0].node_id
    s1 = kg.find_nodes("supplier")[0].node_id
    kg.add_edge(s1, p1, "supplies_to")

    stats = kg.get_stats()
    assert stats["total_nodes"] == 3
    assert stats["total_edges"] == 1
    assert stats["node_type_distribution"]["product"] == 2
    assert stats["node_type_distribution"]["supplier"] == 1
    assert stats["edge_type_distribution"]["supplies_to"] == 1


# =============================================================================
# 向量库测试
# =============================================================================

async def test_vector_store_add_and_search(fresh_vector_store):
    """添加文档并搜索"""
    vs = fresh_vector_store
    docs = [
        Document(content="ERP库存管理操作手册：入库流程、出库流程、盘点流程", metadata={"category": "inventory"}),
        Document(content="会计准则摘要：存货按成本初始计量，先进先出法", metadata={"category": "accounting"}),
        Document(content="采购流程：供应商选择、采购订单、收货入库", metadata={"category": "procurement"}),
    ]
    doc_ids = await vs.add_documents(docs)
    assert len(doc_ids) == 3

    # 搜索库存相关
    results = await vs.search("库存管理入库流程", top_k=2)
    assert len(results) > 0
    # 最相关的应该是库存管理文档
    top_doc, score = results[0]
    assert "库存" in top_doc.content
    assert 0.0 <= score <= 1.0


async def test_vector_store_search_relevance(fresh_vector_store):
    """搜索结果相关性排序"""
    vs = fresh_vector_store
    await vs.add_documents([
        Document(content="供应商管理政策：A级供应商享受优先付款"),
        Document(content="财务风险控制：大额转账需要多Agent共识"),
        Document(content="供应商信用分低于60发出警告"),
    ])

    # 搜索供应商相关内容
    results = await vs.search("供应商信用分", top_k=3)
    assert len(results) > 0
    # 最相关的应包含"供应商信用分"
    top_content = results[0][0].content
    assert "供应商" in top_content


async def test_vector_store_search_with_filter_tags(fresh_vector_store):
    """带标签过滤的搜索"""
    vs = fresh_vector_store
    await vs.add_documents([
        Document(
            content="公开的库存操作说明",
            metadata={"permission_level": "public"},
        ),
        Document(
            content="机密的财务风险控制规范",
            metadata={"permission_level": "confidential"},
        ),
        Document(
            content="内部的采购流程文档",
            metadata={"permission_level": "internal"},
        ),
    ])

    # 仅搜索内部文档
    results = await vs.search("流程文档", top_k=5, filter_tags={"permission_level": "internal"})
    assert len(results) == 1
    assert results[0][0].metadata["permission_level"] == "internal"

    # 仅搜索机密文档
    results = await vs.search("风险控制", top_k=5, filter_tags={"permission_level": "confidential"})
    assert len(results) == 1
    assert "机密" in results[0][0].content


async def test_vector_store_delete(fresh_vector_store):
    """删除文档"""
    vs = fresh_vector_store
    doc_ids = await vs.add_documents([
        Document(content="文档1内容"),
        Document(content="文档2内容"),
    ])

    # 删除第一个文档
    deleted = await vs.delete(doc_ids[0])
    assert deleted is True

    # 删除不存在的文档
    deleted = await vs.delete("nonexistent-id")
    assert deleted is False

    # 验证剩余文档数
    stats = vs.get_stats()
    assert stats["total_documents"] == 1


async def test_vector_store_empty_search(fresh_vector_store):
    """空向量库搜索返回空列表"""
    vs = fresh_vector_store
    results = await vs.search("任意查询", top_k=5)
    assert results == []


async def test_vector_store_stats(fresh_vector_store):
    """向量库统计信息"""
    vs = fresh_vector_store
    await vs.add_documents([
        Document(content="文档1"),
        Document(content="文档2"),
    ])
    stats = vs.get_stats()
    assert stats["total_documents"] == 2
    assert stats["vocabulary_size"] > 0


# =============================================================================
# 规则引擎测试
# =============================================================================

async def test_rule_engine_add_and_evaluate(fresh_rule_engine):
    """添加规则并评估"""
    engine = fresh_rule_engine
    rule = Rule(
        name="测试规则",
        condition="quantity < 10",
        action="alert_low_stock",
        priority=50,
    )
    engine.add_rule(rule)

    # 匹配条件
    matched = await engine.evaluate({"quantity": 5})
    assert len(matched) == 1
    assert matched[0].name == "测试规则"

    # 不匹配条件
    matched = await engine.evaluate({"quantity": 100})
    assert len(matched) == 0


async def test_rule_engine_priority_ordering(fresh_rule_engine):
    """规则按优先级排序"""
    engine = fresh_rule_engine
    # 先清空预置规则，添加自定义规则测试
    engine.clear()
    engine.add_rule(Rule(name="低优先级", condition="x > 0", action="a1", priority=10))
    engine.add_rule(Rule(name="高优先级", condition="x > 0", action="a2", priority=100))
    engine.add_rule(Rule(name="中优先级", condition="x > 0", action="a3", priority=50))

    matched = await engine.evaluate({"x": 1})
    assert len(matched) == 3
    # 按优先级降序
    assert matched[0].name == "高优先级"
    assert matched[1].name == "中优先级"
    assert matched[2].name == "低优先级"


async def test_rule_engine_remove_rule(fresh_rule_engine):
    """删除规则"""
    engine = fresh_rule_engine
    rule = Rule(name="临时规则", condition="x > 0", action="temp")
    rule_id = engine.add_rule(rule)

    assert engine.remove_rule(rule_id) is True
    assert engine.remove_rule(rule_id) is False  # 已删除
    assert engine.get_rule(rule_id) is None


async def test_rule_engine_disabled_rule_skipped(fresh_rule_engine):
    """禁用的规则被跳过"""
    engine = fresh_rule_engine
    rule = Rule(
        name="禁用规则",
        condition="x > 0",
        action="disabled_action",
        enabled=False,
    )
    engine.add_rule(rule)

    matched = await engine.evaluate({"x": 1})
    # 禁用规则不应匹配
    for r in matched:
        assert r.name != "禁用规则"


async def test_rule_engine_execute(fresh_rule_engine):
    """执行规则动作"""
    engine = fresh_rule_engine
    engine.clear()
    engine.add_rule(Rule(
        name="库存不足",
        condition="quantity < safety_stock",
        action="trigger_replenishment",
        priority=80,
    ))

    results = await engine.execute({"quantity": 5, "safety_stock": 50})
    assert len(results) == 1
    result = results[0]
    assert result["action"] == "trigger_replenishment"
    assert result["rule_name"] == "库存不足"
    assert result["status"] == "triggered"
    assert result["context_snapshot"]["quantity"] == 5


# =============================================================================
# 预置业务规则测试
# =============================================================================

async def test_builtin_rule_low_inventory(rule_engine_global):
    """预置规则：库存低于安全线触发补货"""
    matched = await rule_engine_global.evaluate({
        "quantity": 30,
        "safety_stock": 50,
    })
    rule_names = [r.name for r in matched]
    assert "库存低于安全线触发补货" in rule_names


async def test_builtin_rule_large_amount_consensus(rule_engine_global):
    """预置规则：金额超过10万需多Agent共识"""
    matched = await rule_engine_global.evaluate({"amount": 150000})
    rule_names = [r.name for r in matched]
    assert "大额操作需多Agent共识" in rule_names

    # 金额不超过10万不应触发
    matched = await rule_engine_global.evaluate({"amount": 50000})
    rule_names = [r.name for r in matched]
    assert "大额操作需多Agent共识" not in rule_names


async def test_builtin_rule_off_hours_large_transfer(rule_engine_global):
    """预置规则：非工作时间大额转账拒绝"""
    # 非工作时间（20点）+ 大额（6万）→ 拒绝
    matched = await rule_engine_global.evaluate({"amount": 60000, "hour": 20})
    rule_names = [r.name for r in matched]
    assert "非工作时间大额转账拒绝" in rule_names

    # 工作时间（10点）+ 大额 → 不触发拒绝
    matched = await rule_engine_global.evaluate({"amount": 60000, "hour": 10})
    rule_names = [r.name for r in matched]
    assert "非工作时间大额转账拒绝" not in rule_names

    # 非工作时间 + 小额 → 不触发拒绝
    matched = await rule_engine_global.evaluate({"amount": 30000, "hour": 20})
    rule_names = [r.name for r in matched]
    assert "非工作时间大额转账拒绝" not in rule_names


async def test_builtin_rule_low_supplier_credit(rule_engine_global):
    """预置规则：供应商信用分低于60警告"""
    matched = await rule_engine_global.evaluate({"supplier_credit_score": 55})
    rule_names = [r.name for r in matched]
    assert "供应商信用分低警告" in rule_names

    # 信用分高于60不应触发
    matched = await rule_engine_global.evaluate({"supplier_credit_score": 75})
    rule_names = [r.name for r in matched]
    assert "供应商信用分低警告" not in rule_names


def test_builtin_rules_loaded():
    """预置规则已加载到全局单例"""
    rules = rule_engine.list_rules()
    assert len(rules) >= 4
    rule_names = [r.name for r in rules]
    assert "库存低于安全线触发补货" in rule_names
    assert "大额操作需多Agent共识" in rule_names
    assert "非工作时间大额转账拒绝" in rule_names
    assert "供应商信用分低警告" in rule_names


# =============================================================================
# 安全沙箱测试
# =============================================================================

def test_safe_eval_basic_comparison():
    """安全沙箱：基本比较运算"""
    assert safe_eval("x > 5", {"x": 10}) is True
    assert safe_eval("x > 5", {"x": 3}) is False
    assert safe_eval("x == 100", {"x": 100}) is True
    assert safe_eval("x != 100", {"x": 50}) is True
    assert safe_eval("x <= 10", {"x": 10}) is True


def test_safe_eval_logical_operators():
    """安全沙箱：逻辑运算符"""
    assert safe_eval("x > 0 and x < 100", {"x": 50}) is True
    assert safe_eval("x > 0 and x < 100", {"x": 150}) is False
    assert safe_eval("x > 100 or x < 0", {"x": 50}) is False
    assert safe_eval("not x", {"x": False}) is True


def test_safe_eval_arithmetic():
    """安全沙箱：算术运算"""
    assert safe_eval("a + b > 100", {"a": 60, "b": 50}) is True
    assert safe_eval("a * b > 1000", {"a": 30, "b": 40}) is True
    # 100 * 1.5 = 150，150 > 150 为 False
    assert safe_eval("amount > threshold * 1.5", {"amount": 150, "threshold": 100}) is False
    # 200 > 150 为 True
    assert safe_eval("amount > threshold * 1.5", {"amount": 200, "threshold": 100}) is True


def test_safe_eval_rejects_function_call():
    """安全沙箱：拒绝函数调用"""
    with pytest.raises(SafeEvalError):
        safe_eval("__import__('os').system('ls')", {})


def test_safe_eval_rejects_attribute_access():
    """安全沙箱：拒绝属性访问"""
    with pytest.raises(SafeEvalError):
        safe_eval("x.__class__", {"x": 1})


def test_safe_eval_rejects_undefined_variable():
    """安全沙箱：未定义变量报错"""
    with pytest.raises(SafeEvalError, match="Undefined variable"):
        safe_eval("undefined_var > 0", {})


def test_safe_eval_empty_expression():
    """安全沙箱：空表达式视为True（始终匹配）"""
    assert safe_eval("", {}) is True
    assert safe_eval("   ", {}) is True


def test_safe_eval_chained_comparison():
    """安全沙箱：链式比较 a < b < c"""
    assert safe_eval("0 < x < 100", {"x": 50}) is True
    assert safe_eval("0 < x < 100", {"x": 150}) is False


# =============================================================================
# 知识库初始化测试
# =============================================================================

async def test_init_knowledge_base():
    """知识库初始化注入示例数据"""
    reset_initialized()
    stats = await init_knowledge_base()

    # 验证知识图谱数据
    assert stats["graph"]["nodes"] > 0
    assert stats["graph"]["edges"] > 0
    # 3个产品 + 2个供应商 + 1个仓库 + 3个会计科目 = 9个节点
    assert stats["graph"]["nodes"] == 9

    # 验证文档数据
    assert stats["documents"] >= 3

    # 验证规则数据
    assert stats["rules"] >= 4


async def test_init_knowledge_base_idempotent():
    """知识库初始化幂等性"""
    reset_initialized()
    stats1 = await init_knowledge_base()
    # 再次初始化不应产生重复数据
    reset_initialized()
    stats2 = await init_knowledge_base()

    assert stats1["graph"]["nodes"] == stats2["graph"]["nodes"]
    assert stats1["documents"] == stats2["documents"]
    assert stats1["rules"] == stats2["rules"]


async def test_init_knowledge_base_graph_data():
    """初始化后知识图谱包含正确的示例数据"""
    reset_initialized()
    await init_knowledge_base()

    # 验证产品节点
    products = knowledge_graph.find_nodes("product")
    assert len(products) == 3
    product_categories = [p.properties.get("category") for p in products]
    assert "电子元件" in product_categories
    assert "集成电路" in product_categories

    # 验证供应商节点
    suppliers = knowledge_graph.find_nodes("supplier")
    assert len(suppliers) == 2

    # 验证仓库节点
    warehouses = knowledge_graph.find_nodes("warehouse")
    assert len(warehouses) == 1

    # 验证会计科目节点
    accounts = knowledge_graph.find_nodes("account")
    assert len(accounts) == 3


async def test_init_knowledge_base_documents_searchable():
    """初始化后向量库文档可搜索"""
    reset_initialized()
    await init_knowledge_base()

    # 搜索库存相关文档
    results = await vector_store.search("库存入库流程", top_k=3)
    assert len(results) > 0
    # 至少有一个结果包含库存相关内容
    assert any("库存" in doc.content or "入库" in doc.content for doc, _ in results)


# =============================================================================
# API端点测试
# =============================================================================

@pytest_asyncio.fixture
async def knowledge_api_client():
    """知识层API测试客户端（基于ASGITransport，不触发lifespan）"""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_api_query_graph(knowledge_api_client):
    """API: 查询知识图谱"""
    # 先注入数据
    reset_initialized()
    await init_knowledge_base()

    response = await knowledge_api_client.get("/api/v1/knowledge/graph?node_type=product")
    assert response.status_code == 200

    data = response.json()
    assert data["count"] == 3
    assert data["stats"]["total_nodes"] >= 9


async def test_api_query_graph_with_syntax(knowledge_api_client):
    """API: 使用查询语法查询知识图谱"""
    reset_initialized()
    await init_knowledge_base()

    response = await knowledge_api_client.get(
        "/api/v1/knowledge/graph?q=node_type:product[category=电子元件]"
    )
    assert response.status_code == 200

    data = response.json()
    assert data["count"] == 2  # 电阻和电容


async def test_api_add_graph_node(knowledge_api_client):
    """API: 添加知识图谱节点"""
    response = await knowledge_api_client.post(
        "/api/v1/knowledge/graph/nodes",
        json={
            "node_type": "product",
            "properties": {"name": "测试产品", "sku": "TEST-001"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "node_id" in data
    assert data["node_type"] == "product"


async def test_api_add_graph_node_invalid_type(knowledge_api_client):
    """API: 添加非法节点类型返回400"""
    response = await knowledge_api_client.post(
        "/api/v1/knowledge/graph/nodes",
        json={"node_type": "invalid_type", "properties": {}},
    )
    assert response.status_code == 400


async def test_api_search_documents(knowledge_api_client):
    """API: 搜索文档（RAG检索）"""
    # 注入文档
    reset_initialized()
    await init_knowledge_base()

    response = await knowledge_api_client.get(
        "/api/v1/knowledge/documents?q=库存管理&top_k=3"
    )
    assert response.status_code == 200

    data = response.json()
    assert data["query"] == "库存管理"
    assert len(data["results"]) > 0
    assert "score" in data["results"][0]


async def test_api_add_document(knowledge_api_client):
    """API: 添加文档"""
    response = await knowledge_api_client.post(
        "/api/v1/knowledge/documents",
        json={
            "content": "这是一份测试文档，用于验证API添加功能",
            "metadata": {"source": "test", "category": "test"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "doc_id" in data
    assert data["doc_id"] is not None


async def test_api_list_rules(knowledge_api_client):
    """API: 列出规则"""
    response = await knowledge_api_client.get("/api/v1/knowledge/rules")
    assert response.status_code == 200

    data = response.json()
    assert data["count"] >= 4
    rule_names = [r["name"] for r in data["rules"]]
    assert "库存低于安全线触发补货" in rule_names


async def test_api_evaluate_rules(knowledge_api_client):
    """API: 评估规则"""
    response = await knowledge_api_client.post(
        "/api/v1/knowledge/rules/evaluate",
        json={
            "context": {
                "quantity": 5,
                "safety_stock": 50,
                "amount": 200000,
            },
        },
    )
    assert response.status_code == 200

    data = response.json()
    # 库存低于安全线 + 大额操作 两条规则应同时触发
    assert data["matched_count"] >= 2
    assert len(data["execution_results"]) >= 2

    actions = [r["action"] for r in data["execution_results"]]
    assert "trigger_replenishment" in actions
    assert "require_multi_agent_consensus" in actions


async def test_api_get_graph_node_detail(knowledge_api_client):
    """API: 获取节点详情（含邻居）"""
    reset_initialized()
    await init_knowledge_base()

    # 先获取一个产品节点
    products = knowledge_graph.find_nodes("product")
    product_id = products[0].node_id

    response = await knowledge_api_client.get(f"/api/v1/knowledge/graph/nodes/{product_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["node"]["node_id"] == product_id
    assert len(data["neighbors"]) > 0


async def test_api_get_nonexistent_node_404(knowledge_api_client):
    """API: 获取不存在的节点返回404"""
    response = await knowledge_api_client.get(
        "/api/v1/knowledge/graph/nodes/nonexistent-id"
    )
    assert response.status_code == 404
