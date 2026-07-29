"""
认知与知识层 (Cognition & Knowledge Layer)
=========================================

三域隔离架构中的「数据与知识域」核心组件，为 Agent 提供结构化知识与
非结构化文档检索能力。包含三个子模块：

1. 企业知识图谱 (graph.py)
   - 节点：产品 / 供应商 / 客户 / 账户 / 仓库 / 员工
   - 关系：supplies_to / belongs_to / manages / reports_to / contains
   - 内存实现，接口兼容 Neo4j，未来可平滑切换

2. RAG 检索框架 (rag.py)
   - Document 模型 + VectorStore 抽象接口
   - InMemoryVectorStore：基于 TF-IDF + 余弦相似度，零外部依赖
   - 接口兼容 Milvus，未来可切换至向量数据库

3. 业务规则引擎 (rules.py)
   - Rule 模型 + RuleEngine
   - 安全沙箱评估表达式（仅允许比较与逻辑运算符）
   - 预置业务规则：库存补货 / 大额共识 / 非工作时间转账 / 供应商信用

设计原则
--------
- 全部使用内存实现，不依赖 Neo4j / Milvus 等外部服务
- 接口设计兼容未来切换到生产级后端
- 全局单例模式，与 event_bus / audit_chain 等模块保持一致
"""
from app.core.knowledge.graph import (
    KnowledgeGraph,
    KnowledgeNode,
    KnowledgeEdge,
    knowledge_graph,
)
from app.core.knowledge.rag import (
    Document,
    VectorStore,
    InMemoryVectorStore,
    vector_store,
)
from app.core.knowledge.rules import (
    Rule,
    RuleEngine,
    rule_engine,
)
from app.core.knowledge.ingestion import (
    DocumentIngester,
    document_ingester,
    extract_text,
    chunk_text,
    SUPPORTED_FORMATS,
    PERMISSION_LEVELS,
)
from app.core.knowledge.hybrid import (
    HybridRetrievalEngine,
    BM25Retriever,
    hybrid_engine,
)
from app.core.knowledge.manager import (
    KnowledgeManager,
    DocumentRecord,
    Category,
    knowledge_manager,
)
from app.core.knowledge.initializer import init_knowledge_base

__all__ = [
    # 知识图谱
    "KnowledgeGraph",
    "KnowledgeNode",
    "KnowledgeEdge",
    "knowledge_graph",
    # RAG 检索
    "Document",
    "VectorStore",
    "InMemoryVectorStore",
    "vector_store",
    # 规则引擎
    "Rule",
    "RuleEngine",
    "rule_engine",
    # 文档摄入
    "DocumentIngester",
    "document_ingester",
    "extract_text",
    "chunk_text",
    "SUPPORTED_FORMATS",
    "PERMISSION_LEVELS",
    # 混合检索
    "HybridRetrievalEngine",
    "BM25Retriever",
    "hybrid_engine",
    # 知识管理
    "KnowledgeManager",
    "DocumentRecord",
    "Category",
    "knowledge_manager",
    # 初始化
    "init_knowledge_base",
]
