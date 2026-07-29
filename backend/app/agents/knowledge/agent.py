"""
知识Agent (KnowledgeAgent)
===========================

统一知识检索服务，为所有业务Agent提供：
- 文档检索（向量 + 关键词 + 图谱三路混合检索）
- 知识图谱查询
- 业务规则评估
- 文档摄入与管理

设计原则
--------
- KnowledgeAgent.retrieve() 是所有Agent获取知识的标准入口
- 权限感知：根据调用者的权限级别和部门过滤结果
- 零依赖启动：不需要外部向量数据库即可工作
- 可扩展：未来可切换到 Milvus / Neo4j 后端
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType
from app.core.knowledge.rag import vector_store, Document
from app.core.knowledge.graph import knowledge_graph
from app.core.knowledge.rules import rule_engine
from app.core.knowledge.hybrid import hybrid_engine
from app.core.knowledge.manager import knowledge_manager

logger = structlog.get_logger()


class KnowledgeAgent(BaseAgent):
    """知识Agent

    为所有业务Agent提供统一的知识检索服务。

    核心接口：
    - retrieve(query, ...) → 混合检索文档
    - query_graph(query_str) → 知识图谱查询
    - evaluate_rules(context) → 业务规则评估
    - ingest_document(...) → 文档摄入
    """

    def __init__(self):
        super().__init__(
            name="知识Agent",
            domain="knowledge",
            capabilities=[
                "document_retrieval",      # 文档检索
                "knowledge_graph_query",   # 知识图谱查询
                "rule_evaluation",         # 规则评估
                "document_ingestion",      # 文档摄入
                "knowledge_management",    # 知识管理
            ],
            security_clearance="confidential",
        )
        self._retrieve_count = 0
        self._ingest_count = 0

    @property
    def description(self) -> str:
        return (
            "知识Agent - 统一知识检索服务，提供文档检索、知识图谱查询、"
            "业务规则评估和文档管理能力，支持权限感知的混合检索。"
        )

    async def initialize(self):
        """初始化知识Agent"""
        # 订阅知识相关事件
        self.subscribe_events(
            [EventType.AGENT_DECISION_MADE],
            self._handle_knowledge_request,
        )
        logger.info("knowledge_agent.initialized")

    async def process_event(self, event: Event):
        """处理事件"""
        # 知识Agent主要被动响应检索请求
        pass

    async def make_decision(self, context: dict) -> AgentDecision:
        """决策"""
        return AgentDecision(
            decision_type="knowledge_retrieval",
            reasoning="知识检索请求",
            action="retrieve_knowledge",
            confidence=0.95,
            context=context,
        )

    async def on_tick(self):
        """周期性任务：同步BM25索引"""
        try:
            hybrid_engine.sync_bm25_index()
        except Exception as e:
            logger.debug("knowledge_agent.sync_failed", error=str(e))

    # ===================================================================
    # 核心检索接口
    # ===================================================================

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        caller_permission_level: str = "internal",
        caller_department: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """知识检索（所有Agent的标准入口）

        Args:
            query: 查询文本
            top_k: 返回结果数上限
            caller_permission_level: 调用者权限级别
            caller_department: 调用者部门
            category: 分类过滤
            tags: 标签过滤

        Returns:
            检索结果列表，每个元素包含：
            - doc_id: 文档ID
            - content: 文档内容
            - metadata: 元数据
            - score: 融合分数
            - vector_score: 向量分数
            - keyword_score: 关键词分数
            - graph_score: 图谱分数
        """
        self._retrieve_count += 1

        # 使用知识管理器进行检索
        results = await knowledge_manager.search(
            query=query,
            top_k=top_k,
            user_permission_level=caller_permission_level,
            user_department=caller_department,
            category=category,
            tags=tags,
        )

        # 记录决策
        decision = AgentDecision(
            decision_type="knowledge_retrieval",
            reasoning=f"检索查询: {query[:50]}",
            action="retrieve",
            confidence=0.95,
            context={
                "query": query[:100],
                "top_k": top_k,
                "results_count": len(results),
                "caller_permission": caller_permission_level,
            },
        )
        await self.record_decision(decision)

        logger.info(
            "knowledge_agent.retrieve",
            query=query[:50],
            results=len(results),
            caller_permission=caller_permission_level,
        )

        return results

    async def retrieve_context(
        self,
        query: str,
        top_k: int = 3,
        caller_permission_level: str = "internal",
        caller_department: Optional[str] = None,
    ) -> str:
        """检索并返回拼接的上下文文本（供LLM使用）

        将检索到的文档内容拼接为一段文本，
        可直接作为LLM的上下文输入。

        Args:
            同 retrieve()

        Returns:
            拼接的文档内容文本
        """
        results = await self.retrieve(
            query=query,
            top_k=top_k,
            caller_permission_level=caller_permission_level,
            caller_department=caller_department,
        )

        if not results:
            return ""

        context_parts = []
        for i, result in enumerate(results, 1):
            content = result.get("content", "")
            source = result.get("metadata", {}).get("filename", "unknown")
            score = result.get("score", 0)
            context_parts.append(
                f"[文档{i}] (来源: {source}, 相关度: {score:.2f})\n{content}"
            )

        return "\n\n---\n\n".join(context_parts)

    # ===================================================================
    # 知识图谱查询
    # ===================================================================

    def query_graph(self, query_str: str) -> list[dict]:
        """知识图谱查询

        支持简单查询语法：
        - node_type:product  按类型查询
        - node_type:product[category=电子元件]  类型+属性过滤
        - [name=某仓库]  仅属性过滤

        Args:
            query_str: 查询字符串

        Returns:
            节点字典列表
        """
        return knowledge_graph.query(query_str)

    def get_graph_stats(self) -> dict:
        """获取知识图谱统计信息"""
        return knowledge_graph.get_stats()

    def get_neighbors(
        self,
        node_id: str,
        edge_type: Optional[str] = None,
    ) -> list[dict]:
        """获取节点的邻居

        Args:
            node_id: 节点ID
            edge_type: 关系类型过滤

        Returns:
            邻居节点列表
        """
        neighbors = knowledge_graph.get_neighbors(node_id, edge_type)
        return [
            {
                "edge": edge.to_dict(),
                "node": neighbor.to_dict(),
            }
            for edge, neighbor in neighbors
        ]

    # ===================================================================
    # 业务规则评估
    # ===================================================================

    async def evaluate_rules(self, context: dict) -> list[dict]:
        """评估业务规则

        Args:
            context: 评估上下文（如 {"amount": 150000, "hour": 22}）

        Returns:
            匹配的规则列表，包含规则ID、名称、动作、优先级
        """
        matched = await rule_engine.evaluate(context)
        return [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "condition": r.condition,
                "action": r.action,
                "priority": r.priority,
                "description": r.description,
            }
            for r in matched
        ]

    async def execute_rules(self, context: dict) -> list[dict]:
        """执行匹配规则的动作

        Args:
            context: 评估上下文

        Returns:
            执行结果列表
        """
        return await rule_engine.execute(context)

    # ===================================================================
    # 文档摄入
    # ===================================================================

    async def ingest_text(
        self,
        text: str,
        category: str = "uncategorized",
        department: str = "general",
        permission_level: str = "internal",
        tags: Optional[list[str]] = None,
        uploaded_by: str = "system",
        auto_publish: bool = True,
    ) -> dict:
        """摄入纯文本文档到知识库

        Args:
            text: 纯文本内容
            category: 分类
            department: 部门
            permission_level: 权限级别
            tags: 标签
            uploaded_by: 上传者
            auto_publish: 是否自动发布

        Returns:
            摄入结果，包含记录信息和文档块数量
        """
        self._ingest_count += 1

        record = knowledge_manager.upload_text(
            text=text,
            category=category,
            department=department,
            permission_level=permission_level,
            tags=tags,
            uploaded_by=uploaded_by,
            auto_publish=auto_publish,
        )

        logger.info(
            "knowledge_agent.ingest_text",
            record_id=record.record_id,
            category=category,
            chunks=record.chunk_count,
        )

        return record.to_dict()

    async def ingest_document(
        self,
        content: bytes,
        filename: str = "",
        category: str = "uncategorized",
        department: str = "general",
        permission_level: str = "internal",
        tags: Optional[list[str]] = None,
        uploaded_by: str = "system",
        auto_publish: bool = True,
    ) -> dict:
        """摄入文档到知识库

        Args:
            content: 文件二进制内容
            filename: 文件名
            其余参数同 ingest_text

        Returns:
            摄入结果
        """
        self._ingest_count += 1

        record = knowledge_manager.upload_document(
            content=content,
            filename=filename,
            category=category,
            department=department,
            permission_level=permission_level,
            tags=tags,
            uploaded_by=uploaded_by,
            auto_publish=auto_publish,
        )

        logger.info(
            "knowledge_agent.ingest_document",
            record_id=record.record_id,
            filename=filename,
            category=category,
            chunks=record.chunk_count,
        )

        return record.to_dict()

    # ===================================================================
    # 知识库管理
    # ===================================================================

    def get_knowledge_stats(self) -> dict:
        """获取知识库统计信息"""
        manager_stats = knowledge_manager.get_stats()
        hybrid_stats = hybrid_engine.get_stats()
        graph_stats = knowledge_graph.get_stats()
        rule_stats = rule_engine.get_stats()
        vector_stats = vector_store.get_stats()

        return {
            "knowledge_manager": manager_stats,
            "hybrid_retrieval": hybrid_stats,
            "knowledge_graph": graph_stats,
            "rule_engine": rule_stats,
            "vector_store": vector_stats,
            "agent_stats": {
                "retrieve_count": self._retrieve_count,
                "ingest_count": self._ingest_count,
            },
        }

    def list_categories(self) -> list[dict]:
        """列出所有文档分类"""
        return [cat.to_dict() for cat in knowledge_manager.list_categories()]

    def list_documents(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        department: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """列出文档记录"""
        records = knowledge_manager.list_records(
            status=status,
            category=category,
            department=department,
            limit=limit,
            offset=offset,
        )
        return [r.to_dict() for r in records]

    async def _handle_knowledge_request(self, event: Event):
        """处理知识检索请求事件"""
        try:
            data = event.event_data
            query = data.get("query", "")
            if not query:
                return

            await self.retrieve(
                query=query,
                top_k=data.get("top_k", 5),
                caller_permission_level=data.get("permission_level", "internal"),
                caller_department=data.get("department"),
            )
        except Exception as e:
            logger.error(
                "knowledge_agent.handle_request_failed",
                error=str(e),
                event_id=event.event_id,
            )

    def get_status(self) -> dict:
        """获取Agent状态（扩展基类）"""
        status = super().get_status()
        status["knowledge_stats"] = {
            "retrieve_count": self._retrieve_count,
            "ingest_count": self._ingest_count,
        }
        return status
