"""
混合检索引擎 (Hybrid Retrieval Engine)
======================================

设计目标
--------
融合三种检索策略，提升召回率与准确率：
1. 向量语义检索（TF-IDF 余弦相似度）—— 捕捉语义相关性
2. 关键词检索（BM25 风格）—— 精确匹配关键术语
3. 知识图谱遍历 —— 结构化关联推理

融合策略
--------
- 加权分数融合（Weighted Score Fusion）
- 可配置各通道权重：vector_weight / keyword_weight / graph_weight
- 归一化各通道分数到 [0, 1] 后加权求和
- 支持权限过滤：基于 permission_level 和 department

使用方式
--------
    engine = HybridRetrievalEngine()
    results = await engine.retrieve(
        query="库存管理流程",
        top_k=10,
        user_permission_level="internal",
        user_department="warehouse",
    )
"""
import math
import re
import structlog
from typing import Optional
from collections import Counter

from app.core.knowledge.rag import Document, vector_store, _tokenize_zh
from app.core.knowledge.graph import knowledge_graph
from app.core.knowledge.ingestion import PERMISSION_LEVELS

logger = structlog.get_logger()


# =============================================================================
# BM25 关键词检索器
# =============================================================================

class BM25Retriever:
    """BM25 关键词检索器（内存实现）

    公式：
        score(D, Q) = Σ IDF(qi) * (f(qi, D) * (k1 + 1)) /
                      (f(qi, D) + k1 * (1 - b + b * |D| / avgdl))

    其中：
        f(qi, D):  词 qi 在文档 D 中的词频
        |D|:       文档 D 的长度
        avgdl:     所有文档的平均长度
        k1:        词频饱和参数（默认 1.5）
        b:         长度归一化参数（默认 0.75）
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        # doc_id -> token frequency dict
        self._doc_freqs: dict[str, Counter] = {}
        # doc_id -> doc length (token count)
        self._doc_lengths: dict[str, int] = {}
        # token -> document frequency (number of docs containing token)
        self._df: dict[str, int] = {}
        self._total_docs = 0
        self._avgdl = 0.0

    def add_document(self, doc_id: str, content: str) -> None:
        """添加文档到 BM25 索引"""
        tokens = _tokenize_zh(content)
        freq = Counter(tokens)

        # 移除旧索引（如果存在）
        if doc_id in self._doc_freqs:
            old_freq = self._doc_freqs[doc_id]
            for token in old_freq:
                self._df[token] = max(0, self._df.get(token, 0) - 1)

        self._doc_freqs[doc_id] = freq
        self._doc_lengths[doc_id] = len(tokens)
        for token in freq:
            self._df[token] = self._df.get(token, 0) + 1
        self._total_docs = len(self._doc_freqs)
        self._avgdl = (
            sum(self._doc_lengths.values()) / self._total_docs
            if self._total_docs > 0 else 0.0
        )

    def remove_document(self, doc_id: str) -> None:
        """从 BM25 索引中移除文档"""
        if doc_id not in self._doc_freqs:
            return
        freq = self._doc_freqs.pop(doc_id)
        for token in freq:
            self._df[token] = max(0, self._df.get(token, 0) - 1)
            if self._df[token] == 0:
                self._df.pop(token, None)
        self._doc_lengths.pop(doc_id, None)
        self._total_docs = len(self._doc_freqs)
        self._avgdl = (
            sum(self._doc_lengths.values()) / self._total_docs
            if self._total_docs > 0 else 0.0
        )

    def search(
        self,
        query: str,
        top_k: int = 10,
        candidate_ids: Optional[set[str]] = None,
    ) -> list[tuple[str, float]]:
        """BM25 检索

        Args:
            query: 查询文本
            top_k: 返回结果数上限
            candidate_ids: 候选文档 ID 集合（用于与向量检索结果取交集）

        Returns:
            (doc_id, bm25_score) 列表，按分数降序
        """
        if self._total_docs == 0 or not query.strip():
            return []

        query_tokens = _tokenize_zh(query)
        if not query_tokens:
            return []

        # 确定候选集
        if candidate_ids is None:
            candidate_ids = set(self._doc_freqs.keys())

        scored: list[tuple[str, float]] = []
        for doc_id in candidate_ids:
            if doc_id not in self._doc_freqs:
                continue
            freq = self._doc_freqs[doc_id]
            doc_len = self._doc_lengths.get(doc_id, 0)
            score = 0.0
            for token in query_tokens:
                tf = freq.get(token, 0)
                if tf == 0:
                    continue
                df = self._df.get(token, 0)
                if df == 0:
                    continue
                # IDF
                idf = math.log((self._total_docs - df + 0.5) / (df + 0.5) + 1.0)
                # BM25 score
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / max(self._avgdl, 1.0))
                score += idf * (numerator / denominator)
            if score > 0:
                scored.append((doc_id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def clear(self) -> None:
        """清空索引"""
        self._doc_freqs.clear()
        self._doc_lengths.clear()
        self._df.clear()
        self._total_docs = 0
        self._avgdl = 0.0

    @property
    def doc_count(self) -> int:
        return self._total_docs


# =============================================================================
# 分数归一化器
# =============================================================================

def _normalize_scores(scores: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """将分数归一化到 [0, 1] 区间（Min-Max 归一化）"""
    if not scores:
        return []
    raw_values = [s for _, s in scores]
    min_val = min(raw_values)
    max_val = max(raw_values)
    if max_val == min_val:
        # 所有分数相同，归一化到 0.5
        return [(doc_id, 0.5) for doc_id, _ in scores]
    return [
        (doc_id, (score - min_val) / (max_val - min_val))
        for doc_id, score in scores
    ]


# =============================================================================
# 混合检索引擎
# =============================================================================

class HybridRetrievalEngine:
    """混合检索引擎

    融合向量检索 + BM25 关键词检索 + 知识图谱遍历，
    通过加权分数融合产出最终排序结果。

    配置参数：
        vector_weight:   向量检索权重（默认 0.4）
        keyword_weight:  关键词检索权重（默认 0.4）
        graph_weight:    知识图谱权重（默认 0.2）
        top_k:           默认返回结果数（默认 10）
    """

    def __init__(
        self,
        vector_weight: float = 0.4,
        keyword_weight: float = 0.4,
        graph_weight: float = 0.2,
        top_k: int = 10,
    ):
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.graph_weight = graph_weight
        self.semantic_weight = 0.25
        self.top_k = top_k
        self.bm25 = BM25Retriever()
        # 语义通道（知识库改进）：chunk_id -> L2 归一化向量（水合/摄取时注入）。
        # embedding 提供方由 retrieve 时惰性获取；无向量时该通道自动跳过。
        self.semantic_vectors: dict[str, list[float]] = {}

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        user_permission_level: str = "internal",
        user_department: Optional[str] = None,
        filter_tags: Optional[dict] = None,
        enable_graph: bool = True,
    ) -> list[dict]:
        """混合检索

        Args:
            query: 查询文本
            top_k: 返回结果数上限
            user_permission_level: 用户权限级别（用于权限过滤）
            user_department: 用户部门（用于部门隔离）
            filter_tags: 额外的元数据过滤标签
            enable_graph: 是否启用知识图谱检索

        Returns:
            检索结果列表，每个元素是一个字典：
            {
                "doc_id": str,
                "content": str,
                "metadata": dict,
                "score": float,          # 融合后的总分 [0, 1]
                "vector_score": float,   # 向量检索分数
                "keyword_score": float,  # 关键词检索分数
                "graph_score": float,    # 知识图谱分数
            }
        """
        top_k = top_k or self.top_k
        user_rank = PERMISSION_LEVELS.get(user_permission_level, 1)

        # 构建权限过滤标签
        perm_filter = dict(filter_tags or {})
        # 不在 filter_tags 中设置 permission_level，而是在结果中过滤

        # ---- 通道 1: 向量语义检索 ----
        vector_results = await vector_store.search(
            query=query,
            top_k=top_k * 3,  # 多取一些用于融合
            filter_tags=perm_filter if perm_filter else None,
        )
        vector_scores: dict[str, float] = {}
        for doc, score in vector_results:
            vector_scores[doc.doc_id] = score

        # ---- 通道 2: BM25 关键词检索 ----
        # 在向量检索的候选集中进行 BM25 检索
        candidate_ids = set(vector_scores.keys())
        # 也加入包含查询关键词的文档
        query_tokens = _tokenize_zh(query)
        for token in query_tokens:
            # 从 BM25 索引中找到包含该 token 的文档
            for doc_id in self.bm25._doc_freqs:
                if token in self.bm25._doc_freqs[doc_id]:
                    candidate_ids.add(doc_id)

        bm25_results = self.bm25.search(query, top_k=top_k * 3, candidate_ids=candidate_ids)
        keyword_scores: dict[str, float] = {}
        for doc_id, score in bm25_results:
            keyword_scores[doc_id] = score

        # ---- 通道 3: 知识图谱检索 ----
        graph_scores: dict[str, float] = {}
        if enable_graph:
            graph_scores = self._graph_search(query, top_k=top_k * 3)

        # ---- 权限过滤 ----
        all_doc_ids = set(vector_scores.keys()) | set(keyword_scores.keys()) | set(graph_scores.keys())
        filtered_doc_ids = set()
        for doc_id in all_doc_ids:
            doc = self._get_document(doc_id)
            if doc is None:
                continue
            # 权限级别过滤：文档权限级别不能超过用户权限级别
            doc_perm = doc.metadata.get("permission_level", "internal")
            doc_rank = PERMISSION_LEVELS.get(doc_perm, 1)
            if doc_rank > user_rank:
                continue
            # 部门隔离：如果设置了部门，只返回该部门或公开部门的文档
            if user_department:
                doc_dept = doc.metadata.get("department", "general")
                if doc_dept != "general" and doc_dept != user_department:
                    continue
            filtered_doc_ids.add(doc_id)

        # ---- 通道 4: 语义向量检索（EmbeddingProvider 余弦；无向量时跳过）----
        semantic_scores: dict[str, float] = {}
        if self.semantic_vectors and filtered_doc_ids:
            try:
                from app.core.knowledge.embeddings import get_embedding_provider

                provider = get_embedding_provider()
                qvec = (await provider.embed([query]))[0]
                for doc_id, vec in self.semantic_vectors.items():
                    if doc_id in filtered_doc_ids:
                        semantic_scores[doc_id] = sum(
                            a * b for a, b in zip(qvec, vec)
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning("hybrid.semantic_channel_failed", error=str(e))

        # ---- 分数归一化与融合 ----
        vec_normalized = _normalize_scores(
            [(did, vector_scores[did]) for did in filtered_doc_ids if did in vector_scores]
        )
        kw_normalized = _normalize_scores(
            [(did, keyword_scores[did]) for did in filtered_doc_ids if did in keyword_scores]
        )
        graph_normalized = _normalize_scores(
            [(did, graph_scores[did]) for did in filtered_doc_ids if did in graph_scores]
        )
        semantic_normalized = _normalize_scores(
            [(did, semantic_scores[did]) for did in filtered_doc_ids if did in semantic_scores]
        )

        # 构建最终分数
        vec_map = {did: score for did, score in vec_normalized}
        kw_map = {did: score for did, score in kw_normalized}
        graph_map = {did: score for did, score in graph_normalized}
        semantic_map = {did: score for did, score in semantic_normalized}

        results: list[dict] = []
        for doc_id in filtered_doc_ids:
            doc = self._get_document(doc_id)
            if doc is None:
                continue

            v_score = vec_map.get(doc_id, 0.0)
            k_score = kw_map.get(doc_id, 0.0)
            g_score = graph_map.get(doc_id, 0.0)
            s_score = semantic_map.get(doc_id, 0.0)

            # 加权融合（语义通道有向量时归一化四路权重；否则退回三路）
            if semantic_scores:
                weights_sum = (self.vector_weight + self.keyword_weight
                               + self.graph_weight + self.semantic_weight)
                fused_score = (
                    v_score * self.vector_weight
                    + k_score * self.keyword_weight
                    + g_score * self.graph_weight
                    + s_score * self.semantic_weight
                ) / (weights_sum or 1.0)
            else:
                total_weight = self.vector_weight + self.keyword_weight + self.graph_weight
                if total_weight == 0:
                    total_weight = 1.0
                fused_score = (
                    v_score * self.vector_weight
                    + k_score * self.keyword_weight
                    + g_score * self.graph_weight
                ) / total_weight

            results.append({
                "doc_id": doc_id,
                "content": doc.content,
                "metadata": doc.metadata,
                "score": round(fused_score, 4),
                "vector_score": round(v_score, 4),
                "keyword_score": round(k_score, 4),
                "graph_score": round(g_score, 4),
                "semantic_score": round(s_score, 4),
            })

        # 按融合分数降序排列
        results.sort(key=lambda x: x["score"], reverse=True)

        logger.debug(
            "hybrid.retrieve",
            query=query[:50],
            total_candidates=len(all_doc_ids),
            filtered=len(filtered_doc_ids),
            returned=len(results[:top_k]),
        )

        return results[:top_k]

    def _graph_search(self, query: str, top_k: int = 10) -> dict[str, float]:
        """知识图谱检索

        在知识图谱中查找与查询关键词相关的节点，
        返回与这些节点关联的文档的分数。

        分数策略：
        - 直接匹配节点属性 → 1.0
        - 邻居节点 → 0.5
        - 二度邻居 → 0.25
        """
        scores: dict[str, float] = {}
        query_lower = query.lower()

        # 遍历所有节点，查找属性匹配
        all_nodes = knowledge_graph.find_nodes()
        matched_node_ids: set[str] = set()

        for node in all_nodes:
            for key, val in node.properties.items():
                if isinstance(val, str) and val.lower() in query_lower:
                    matched_node_ids.add(node.node_id)
                    # 找到与该节点关联的文档
                    for doc_id, doc in self._get_all_documents().items():
                        node_ref = doc.metadata.get("graph_node_id")
                        if node_ref == node.node_id:
                            scores[doc_id] = max(scores.get(doc_id, 0.0), 1.0)
                    break

        # 一度邻居
        for node_id in matched_node_ids:
            neighbors = knowledge_graph.get_neighbors(node_id)
            for edge, neighbor in neighbors:
                for doc_id, doc in self._get_all_documents().items():
                    node_ref = doc.metadata.get("graph_node_id")
                    if node_ref == neighbor.node_id:
                        scores[doc_id] = max(scores.get(doc_id, 0.0), 0.5)

        return scores

    def _get_document(self, doc_id: str) -> Optional[Document]:
        """从向量存储中获取文档"""
        # InMemoryVectorStore 的 _documents 字段
        if hasattr(vector_store, "_documents"):
            return vector_store._documents.get(doc_id)
        return None

    def _get_all_documents(self) -> dict[str, Document]:
        """获取所有文档"""
        if hasattr(vector_store, "_documents"):
            return vector_store._documents
        return {}

    def sync_bm25_index(self) -> int:
        """同步 BM25 索引与向量存储中的文档

        在文档新增/删除后调用，确保 BM25 索引与向量存储一致。

        Returns:
            同步的文档数量
        """
        all_docs = self._get_all_documents()
        # 找出需要新增的文档
        existing_ids = set(self.bm25._doc_freqs.keys())
        current_ids = set(all_docs.keys())

        # 新增
        for doc_id in current_ids - existing_ids:
            self.bm25.add_document(doc_id, all_docs[doc_id].content)

        # 删除
        for doc_id in existing_ids - current_ids:
            self.bm25.remove_document(doc_id)

        synced = len(current_ids - existing_ids) + len(existing_ids - current_ids)
        if synced > 0:
            logger.info("hybrid.bm25_synced", synced=synced, total=len(current_ids))

        return synced

    def get_stats(self) -> dict:
        """获取检索引擎统计信息"""
        all_docs = self._get_all_documents()
        categories = {}
        departments = {}
        permission_dist = {}
        for doc in all_docs.values():
            cat = doc.metadata.get("category", "uncategorized")
            categories[cat] = categories.get(cat, 0) + 1
            dept = doc.metadata.get("department", "general")
            departments[dept] = departments.get(dept, 0) + 1
            perm = doc.metadata.get("permission_level", "internal")
            permission_dist[perm] = permission_dist.get(perm, 0) + 1

        return {
            "total_documents": len(all_docs),
            "bm25_indexed": self.bm25.doc_count,
            "categories": categories,
            "departments": departments,
            "permission_distribution": permission_dist,
            "weights": {
                "vector": self.vector_weight,
                "keyword": self.keyword_weight,
                "graph": self.graph_weight,
            },
        }

    def clear(self) -> None:
        """清空所有索引"""
        self.bm25.clear()


# 全局单例
hybrid_engine = HybridRetrievalEngine()
