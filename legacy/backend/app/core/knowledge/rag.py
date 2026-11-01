"""
RAG 检索框架 - 文档模型与向量存储
==================================

设计目标
--------
- VectorStore 抽象基类定义与 Milvus / Pinecone 等向量数据库兼容的接口
- InMemoryVectorStore 使用 TF-IDF + 余弦相似度实现零依赖检索
- 文本分词优先使用 jieba（如已安装），否则降级到字符级 n-gram
- 支持 metadata 标签过滤（用于基于权限标签的数据隔离）

接口契约
--------
所有 VectorStore 实现必须提供：
- async add_documents(docs) -> list[str]   批量入库，返回 doc_id 列表
- async search(query, top_k, filter_tags) -> list[tuple[Document, float]]
    按相似度从高到低返回，元组第二项为相似度分数 (0.0-1.0)
- async delete(doc_id) -> bool  删除文档，返回是否删除成功

未来切换到 Milvus
------------------
只需创建 MilvusVectorStore(VectorStore) 子类并实现上述三个方法，
全局单例 vector_store 替换为 Milvus 实现即可，业务代码无需改动。
"""
import math
import re
import structlog
from abc import ABC, abstractmethod
from typing import Optional
from uuid import uuid4

logger = structlog.get_logger()


# =============================================================================
# 分词器
# =============================================================================

# 尝试加载 jieba；不可用时降级到字符级 n-gram
_jieba_available = False
try:
    import jieba  # type: ignore
    _jieba_available = True
except ImportError:
    pass


def _tokenize_zh(text: str) -> list[str]:
    """中文分词

    优先使用 jieba；不可用时降级到「字符级 2-gram + 英文单词」混合方案。
    """
    if not text:
        return []

    if _jieba_available:
        # jieba.cut 返回生成器，过滤空白与标点
        return [t.strip() for t in jieba.cut(text) if t.strip()]

    # 降级方案：
    # 1. 提取英文/数字 token（按非字母数字字符分割）
    # 2. 中文部分生成 2-gram（覆盖大部分语义相似度场景）
    tokens: list[str] = []

    # 英文/数字 token
    for word in re.findall(r"[A-Za-z0-9]+", text):
        tokens.append(word.lower())

    # 中文 2-gram
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(chinese_chars) - 1):
        tokens.append(chinese_chars[i] + chinese_chars[i + 1])
    # 单字也作为一个 token（短文本鲁棒性）
    tokens.extend(chinese_chars)

    return tokens


# =============================================================================
# Document 模型
# =============================================================================

class Document:
    """RAG 文档模型

    Attributes:
        doc_id: 文档唯一标识（不指定时自动生成 UUID）
        content: 文档文本内容
        metadata: 元数据字典（可包含 source / tags / permissions 等）
        embedding: 文档向量（可选，外部预计算时填充）
    """

    __slots__ = ("doc_id", "content", "metadata", "embedding")

    def __init__(
        self,
        content: str,
        doc_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        embedding: Optional[list[float]] = None,
    ):
        self.doc_id = doc_id or str(uuid4())
        self.content = content
        self.metadata = dict(metadata) if metadata else {}
        self.embedding = embedding

    def to_dict(self, include_embedding: bool = False) -> dict:
        data = {
            "doc_id": self.doc_id,
            "content": self.content,
            "metadata": dict(self.metadata),
        }
        if include_embedding and self.embedding is not None:
            data["embedding"] = list(self.embedding)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        return cls(
            content=data["content"],
            doc_id=data.get("doc_id"),
            metadata=data.get("metadata"),
            embedding=data.get("embedding"),
        )

    def __repr__(self) -> str:
        preview = self.content[:20].replace("\n", " ")
        return f"Document(id={self.doc_id[:8]}, content='{preview}...')"


# =============================================================================
# VectorStore 抽象接口
# =============================================================================

class VectorStore(ABC):
    """向量存储抽象基类

    所有方法均为 async，与 Milvus / Pinecone / Weaviate 等向量数据库的
    异步客户端保持一致的接口契约。
    """

    @abstractmethod
    async def add_documents(self, docs: list[Document]) -> list[str]:
        """批量添加文档

        Args:
            docs: 待添加的文档列表

        Returns:
            添加成功的 doc_id 列表（顺序与输入一致）
        """
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 5,
        filter_tags: Optional[dict] = None,
    ) -> list[tuple[Document, float]]:
        """向量检索

        Args:
            query: 查询文本
            top_k: 返回结果数上限
            filter_tags: metadata 标签过滤字典；键值都必须精确匹配

        Returns:
            列表元素为 (Document, similarity_score) 元组，按相似度从高到低排序。
            similarity_score 取值范围 [0.0, 1.0]。
        """
        raise NotImplementedError

    @abstractmethod
    async def delete(self, doc_id: str) -> bool:
        """删除文档

        Returns:
            True 表示删除成功，False 表示文档不存在
        """
        raise NotImplementedError


# =============================================================================
# 内存向量库实现
# =============================================================================

class InMemoryVectorStore(VectorStore):
    """内存向量库 - 基于 TF-IDF + 余弦相似度

    特性：
    - 零外部依赖（不依赖 numpy / sentence-transformers / Milvus）
    - 文本分词使用 jieba 或字符级 n-gram 降级方案
    - 支持 metadata 标签过滤（权限隔离）
    - TF-IDF 权重在 add_documents 时增量更新，避免重复计算

    局限：
    - 不支持持久化（进程退出即丢失）
    - TF-IDF 相似度不如深度向量模型精准
    - 适用于开发/测试/小规模数据（< 10K 文档）

    切换到 Milvus：
        class MilvusVectorStore(VectorStore):
            ...
        vector_store = MilvusVectorStore(...)
    """

    def __init__(self):
        # doc_id -> Document
        self._documents: dict[str, Document] = {}
        # doc_id -> token list
        self._doc_tokens: dict[str, list[str]] = {}
        # token -> 出现该 token 的文档集合（用于 IDF 计算）
        self._inverted_index: dict[str, set[str]] = {}
        # doc_id -> token tf dict（token -> term frequency）
        self._tf_index: dict[str, dict[str, float]] = {}
        # doc_id -> 模长（TF-IDF 向量的 L2 范数，用于余弦相似度）
        self._doc_norms: dict[str, float] = {}

    async def add_documents(self, docs: list[Document]) -> list[str]:
        """批量添加文档，增量更新 TF-IDF 索引"""
        added_ids: list[str] = []
        for doc in docs:
            if doc.doc_id in self._documents:
                # 已存在则先删除旧索引
                self._remove_from_index(doc.doc_id)

            self._documents[doc.doc_id] = doc
            tokens = _tokenize_zh(doc.content)
            self._doc_tokens[doc.doc_id] = tokens

            # 计算 TF
            tf: dict[str, float] = {}
            total = len(tokens)
            if total > 0:
                for t in tokens:
                    tf[t] = tf.get(t, 0.0) + 1.0
                # 归一化 TF
                for t in tf:
                    tf[t] /= total
            self._tf_index[doc.doc_id] = tf

            # 更新倒排索引
            for token in tf:
                self._inverted_index.setdefault(token, set()).add(doc.doc_id)

            added_ids.append(doc.doc_id)

        # 全部添加后重新计算 IDF 与文档范数
        self._recompute_idf_and_norms()

        logger.debug(
            "vector_store.documents_added",
            count=len(added_ids),
            total=len(self._documents),
        )
        return added_ids

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filter_tags: Optional[dict] = None,
    ) -> list[tuple[Document, float]]:
        """TF-IDF 余弦相似度检索"""
        if not self._documents:
            return []

        # 计算查询的 TF
        query_tokens = _tokenize_zh(query)
        if not query_tokens:
            return []

        query_tf: dict[str, float] = {}
        for t in query_tokens:
            query_tf[t] = query_tf.get(t, 0.0) + 1.0
        total_q = len(query_tokens)
        for t in query_tf:
            query_tf[t] /= total_q

        # 计算 IDF（与文档端使用同一公式）
        total_docs = len(self._documents)
        idf: dict[str, float] = {}
        for token in query_tf:
            df = len(self._inverted_index.get(token, set()))
            if df == 0:
                continue
            # IDF = log((N + 1) / (df + 1)) + 1  (smoothed)
            idf[token] = math.log((total_docs + 1) / (df + 1)) + 1.0

        # 查询向量的 L2 范数
        query_vec: dict[str, float] = {}
        for token, tf_val in query_tf.items():
            if token in idf:
                query_vec[token] = tf_val * idf[token]
        query_norm = math.sqrt(sum(v * v for v in query_vec.values())) or 1.0

        # 候选文档：包含至少一个查询 token 的文档
        candidate_ids: set[str] = set()
        for token in query_tf:
            candidate_ids.update(self._inverted_index.get(token, set()))

        # 应用 metadata 标签过滤
        if filter_tags:
            candidate_ids = {
                did for did in candidate_ids
                if self._match_tags(self._documents[did], filter_tags)
            }

        # 计算余弦相似度
        scored: list[tuple[str, float]] = []
        for doc_id in candidate_ids:
            doc_tf = self._tf_index[doc_id]
            doc_norm = self._doc_norms.get(doc_id, 0.0)
            if doc_norm == 0:
                continue
            # 点积：仅计算查询 token 中出现在文档里的部分
            dot = 0.0
            for token, q_weight in query_vec.items():
                if token in idf:
                    doc_weight = doc_tf.get(token, 0.0) * idf[token]
                    dot += q_weight * doc_weight
            similarity = dot / (query_norm * doc_norm)
            scored.append((doc_id, similarity))

        # 按相似度降序
        scored.sort(key=lambda x: x[1], reverse=True)

        # 返回 top_k
        results: list[tuple[Document, float]] = []
        for doc_id, score in scored[:top_k]:
            results.append((self._documents[doc_id], max(0.0, min(1.0, score))))
        return results

    async def delete(self, doc_id: str) -> bool:
        """删除文档"""
        if doc_id not in self._documents:
            return False
        self._remove_from_index(doc_id)
        self._recompute_idf_and_norms()
        logger.debug("vector_store.document_deleted", doc_id=doc_id)
        return True

    def get_stats(self) -> dict:
        """统计信息"""
        return {
            "total_documents": len(self._documents),
            "vocabulary_size": len(self._inverted_index),
            "jieba_enabled": _jieba_available,
        }

    def clear(self) -> None:
        """清空所有文档（测试与重置使用）"""
        self._documents.clear()
        self._doc_tokens.clear()
        self._inverted_index.clear()
        self._tf_index.clear()
        self._doc_norms.clear()

    # ===================================================================
    # 内部工具
    # ===================================================================

    def _remove_from_index(self, doc_id: str) -> None:
        """从倒排索引中移除文档（但不清除 IDF 缓存，需要后续重新计算）"""
        doc_tf = self._tf_index.pop(doc_id, {})
        for token in doc_tf:
            posting = self._inverted_index.get(token)
            if posting:
                posting.discard(doc_id)
                if not posting:
                    self._inverted_index.pop(token, None)
        self._documents.pop(doc_id, None)
        self._doc_tokens.pop(doc_id, None)
        self._doc_norms.pop(doc_id, None)

    def _recompute_idf_and_norms(self) -> None:
        """重新计算所有文档的 IDF 权重与 L2 范数"""
        total_docs = len(self._documents)
        if total_docs == 0:
            return

        # 收集所有 token 的 df
        df: dict[str, int] = {}
        for doc_tf in self._tf_index.values():
            for token in doc_tf:
                df[token] = df.get(token, 0) + 1

        # 计算每个文档的 TF-IDF 向量范数
        self._doc_norms.clear()
        for doc_id, doc_tf in self._tf_index.items():
            norm_sq = 0.0
            for token, tf_val in doc_tf.items():
                token_df = df.get(token, 0)
                # IDF = log((N + 1) / (df + 1)) + 1  (smoothed)
                token_idf = math.log((total_docs + 1) / (token_df + 1)) + 1.0
                weight = tf_val * token_idf
                norm_sq += weight * weight
            self._doc_norms[doc_id] = math.sqrt(norm_sq) if norm_sq > 0 else 0.0

    @staticmethod
    def _match_tags(doc: Document, filter_tags: dict) -> bool:
        """检查文档 metadata 是否匹配所有标签

        匹配规则：
        - 如果 filter_tags 中指定了某个 key，文档 metadata 必须包含该 key
        - 值必须相等（字符串比较）
        - 特殊：值为 list 时支持「包含」语义（doc.metadata[key] 是 list 或单值）
        """
        for key, expected in filter_tags.items():
            actual = doc.metadata.get(key)
            if actual is None:
                return False
            if isinstance(actual, list):
                if str(expected) not in [str(x) for x in actual]:
                    return False
            elif str(actual) != str(expected):
                return False
        return True


# 全局单例
vector_store = InMemoryVectorStore()
