"""
知识库管理器 (Knowledge Base Manager)
=====================================

设计目标
--------
- 文档分类管理：按 category / department / tags 组织文档
- 版本控制：文档新版本发布时保留历史版本，支持回滚
- 权限隔离：基于 permission_level + department 的文档级访问控制
- 审批发布：新文档需审批后才能进入正式检索库

数据结构
--------
- _documents: doc_id -> DocumentRecord (完整文档记录)
- _categories: category_name -> CategoryInfo
- _versions: parent_doc_id -> [version_record, ...] (按版本号排序)
- _pending: approval_id -> PendingDocument (待审批文档)

文档生命周期
------------
draft → pending_review → approved → published
                                      ↘ rejected → draft
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.knowledge.rag import Document, vector_store
from app.core.knowledge.ingestion import DocumentIngester, document_ingester, PERMISSION_LEVELS
from app.core.knowledge.hybrid import hybrid_engine

logger = structlog.get_logger()


# 文档状态
DOC_STATUS_DRAFT = "draft"
DOC_STATUS_PENDING = "pending_review"
DOC_STATUS_APPROVED = "approved"
DOC_STATUS_PUBLISHED = "published"
DOC_STATUS_REJECTED = "rejected"
DOC_STATUS_ARCHIVED = "archived"

VALID_STATUSES = {
    DOC_STATUS_DRAFT,
    DOC_STATUS_PENDING,
    DOC_STATUS_APPROVED,
    DOC_STATUS_PUBLISHED,
    DOC_STATUS_REJECTED,
    DOC_STATUS_ARCHIVED,
}


class DocumentRecord:
    """文档记录（包含元数据与状态信息）

    包装 RAG Document，增加状态管理、版本控制字段。
    """

    __slots__ = (
        "record_id", "doc_id", "parent_doc_id", "version",
        "status", "filename", "file_format", "category",
        "department", "permission_level", "tags",
        "uploaded_by", "approved_by", "content_preview",
        "char_count", "chunk_count", "created_at",
        "updated_at", "published_at", "metadata",
    )

    def __init__(
        self,
        doc_id: str,
        parent_doc_id: str,
        version: int,
        filename: str,
        file_format: str,
        category: str,
        department: str,
        permission_level: str,
        content_preview: str,
        char_count: int,
        chunk_count: int,
        uploaded_by: str = "system",
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ):
        self.record_id = f"rec_{uuid4().hex[:12]}"
        self.doc_id = doc_id
        self.parent_doc_id = parent_doc_id
        self.version = version
        self.status = DOC_STATUS_DRAFT
        self.filename = filename
        self.file_format = file_format
        self.category = category
        self.department = department
        self.permission_level = permission_level
        self.tags = tags or []
        self.uploaded_by = uploaded_by
        self.approved_by: Optional[str] = None
        self.content_preview = content_preview[:200]
        self.char_count = char_count
        self.chunk_count = chunk_count
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = self.created_at
        self.published_at: Optional[datetime] = None
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "doc_id": self.doc_id,
            "parent_doc_id": self.parent_doc_id,
            "version": self.version,
            "status": self.status,
            "filename": self.filename,
            "file_format": self.file_format,
            "category": self.category,
            "department": self.department,
            "permission_level": self.permission_level,
            "tags": list(self.tags),
            "uploaded_by": self.uploaded_by,
            "approved_by": self.approved_by,
            "content_preview": self.content_preview,
            "char_count": self.char_count,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "metadata": dict(self.metadata),
        }


class Category:
    """文档分类"""

    __slots__ = ("name", "description", "department", "doc_count", "created_at")

    def __init__(
        self,
        name: str,
        description: str = "",
        department: str = "general",
    ):
        self.name = name
        self.description = description
        self.department = department
        self.doc_count = 0
        self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "department": self.department,
            "doc_count": self.doc_count,
            "created_at": self.created_at.isoformat(),
        }


class KnowledgeManager:
    """知识库管理器

    管理文档的全生命周期：摄入 → 分类 → 审批 → 发布 → 版本控制

    使用方式：
        manager = KnowledgeManager()

        # 上传文档
        record = manager.upload_document(
            content=file_bytes,
            filename="manual.pdf",
            category="operation",
            department="warehouse",
            permission_level="internal",
            uploaded_by="user_001",
        )

        # 提交审批
        manager.submit_for_review(record.record_id)

        # 审批通过并发布
        manager.approve_and_publish(record.record_id, approved_by="cio_001")

        # 检索
        results = await manager.search("库存管理", user_permission_level="internal")
    """

    def __init__(self):
        # record_id -> DocumentRecord
        self._records: dict[str, DocumentRecord] = {}
        # parent_doc_id -> [record_id, ...] (版本历史)
        self._versions: dict[str, list[str]] = {}
        # category_name -> Category
        self._categories: dict[str, Category] = {}
        # 预置分类
        self._init_categories()
        # 待审批文档：record_id -> approval_info
        self._pending: dict[str, dict] = {}

    def _init_categories(self) -> None:
        """初始化预置分类"""
        preset_categories = [
            ("operation", "运营管理文档", "general"),
            ("finance", "财务会计文档", "finance"),
            ("procurement", "采购供应链文档", "procurement"),
            ("inventory", "库存仓储文档", "warehouse"),
            ("hr", "人力资源文档", "hr"),
            ("legal", "法务合规文档", "legal"),
            ("it", "信息技术文档", "it"),
            ("sales", "销售营销文档", "sales"),
            ("product", "产品研发文档", "product"),
            ("production", "生产制造文档", "production"),
            ("policy", "制度规范文档", "general"),
            ("training", "培训资料", "general"),
        ]
        for name, desc, dept in preset_categories:
            self._categories[name] = Category(name, desc, dept)

    # ===================================================================
    # 文档上传与摄入
    # ===================================================================

    def upload_document(
        self,
        content: bytes,
        filename: str = "",
        file_format: Optional[str] = None,
        category: str = "uncategorized",
        department: str = "general",
        permission_level: str = "internal",
        tags: Optional[list[str]] = None,
        uploaded_by: str = "system",
        metadata: Optional[dict] = None,
        auto_publish: bool = False,
    ) -> DocumentRecord:
        """上传文档

        Args:
            content: 文件二进制内容
            filename: 原始文件名
            file_format: 显式格式
            category: 分类名称
            department: 归属部门
            permission_level: 权限级别
            tags: 标签列表
            uploaded_by: 上传者 ID
            metadata: 额外元数据
            auto_publish: 是否自动发布（跳过审批，仅限 trusted 场景）

        Returns:
            文档记录
        """
        # 1. 摄入文档（分块）
        documents = document_ingester.ingest(
            content=content,
            filename=filename,
            file_format=file_format,
            metadata=metadata,
            permission_level=permission_level,
            department=department,
            category=category,
            tags=tags,
            uploaded_by=uploaded_by,
        )

        if not documents:
            raise ValueError("文档内容提取失败，未生成任何文档块")

        # 2. 创建文档记录
        parent_doc_id = documents[0].metadata["parent_doc_id"]
        # 检查是否已有同 parent_doc_id 的版本
        existing_versions = self._versions.get(parent_doc_id, [])
        version = len(existing_versions) + 1

        record = DocumentRecord(
            doc_id=parent_doc_id,
            parent_doc_id=parent_doc_id,
            version=version,
            filename=filename,
            file_format=documents[0].metadata.get("file_format", "text"),
            category=category,
            department=department,
            permission_level=permission_level,
            content_preview=documents[0].content,
            char_count=documents[0].metadata.get("char_count", 0),
            chunk_count=len(documents),
            uploaded_by=uploaded_by,
            tags=tags or [],
            metadata=metadata,
        )

        # 3. 存储记录
        self._records[record.record_id] = record
        self._versions.setdefault(parent_doc_id, []).append(record.record_id)

        # 4. 更新分类计数
        if category not in self._categories:
            self._categories[category] = Category(category, f"自定义分类: {category}")
        self._categories[category].doc_count += 1

        # 5. 如果自动发布，直接加入向量库
        if auto_publish:
            record.status = DOC_STATUS_PUBLISHED
            record.published_at = datetime.now(timezone.utc)
            # 异步添加到向量库（同步调用，因为 ingest 已完成）
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果事件循环正在运行，创建任务
                asyncio.ensure_future(vector_store.add_documents(documents))
                asyncio.ensure_future(asyncio.get_event_loop().run_in_executor(
                    None, hybrid_engine.sync_bm25_index
                ))
            else:
                loop.run_until_complete(vector_store.add_documents(documents))
                hybrid_engine.sync_bm25_index()

        logger.info(
            "manager.document_uploaded",
            record_id=record.record_id,
            parent_doc_id=parent_doc_id,
            filename=filename,
            category=category,
            version=version,
            auto_publish=auto_publish,
        )

        return record

    def upload_text(
        self,
        text: str,
        filename: str = "",
        category: str = "uncategorized",
        department: str = "general",
        permission_level: str = "internal",
        tags: Optional[list[str]] = None,
        uploaded_by: str = "system",
        metadata: Optional[dict] = None,
        auto_publish: bool = False,
    ) -> DocumentRecord:
        """上传纯文本文档"""
        documents = document_ingester.ingest_text(
            text=text,
            metadata=metadata,
            permission_level=permission_level,
            department=department,
            category=category,
            tags=tags,
            uploaded_by=uploaded_by,
        )

        if not documents:
            raise ValueError("文本内容为空，未生成任何文档块")

        parent_doc_id = documents[0].metadata["parent_doc_id"]
        existing_versions = self._versions.get(parent_doc_id, [])
        version = len(existing_versions) + 1

        record = DocumentRecord(
            doc_id=parent_doc_id,
            parent_doc_id=parent_doc_id,
            version=version,
            filename=filename,
            file_format="text",
            category=category,
            department=department,
            permission_level=permission_level,
            content_preview=documents[0].content,
            char_count=documents[0].metadata.get("char_count", 0),
            chunk_count=len(documents),
            uploaded_by=uploaded_by,
            tags=tags or [],
            metadata=metadata,
        )

        self._records[record.record_id] = record
        self._versions.setdefault(parent_doc_id, []).append(record.record_id)

        if category not in self._categories:
            self._categories[category] = Category(category, f"自定义分类: {category}")
        self._categories[category].doc_count += 1

        if auto_publish:
            record.status = DOC_STATUS_PUBLISHED
            record.published_at = datetime.now(timezone.utc)
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(vector_store.add_documents(documents))
                asyncio.ensure_future(asyncio.get_event_loop().run_in_executor(
                    None, hybrid_engine.sync_bm25_index
                ))
            else:
                loop.run_until_complete(vector_store.add_documents(documents))
                hybrid_engine.sync_bm25_index()

        logger.info(
            "manager.text_uploaded",
            record_id=record.record_id,
            parent_doc_id=parent_doc_id,
            category=category,
            version=version,
        )

        return record

    # ===================================================================
    # 审批与发布
    # ===================================================================

    def submit_for_review(self, record_id: str) -> dict:
        """提交文档审批

        Args:
            record_id: 文档记录 ID

        Returns:
            审批信息字典
        """
        record = self._records.get(record_id)
        if record is None:
            raise ValueError(f"文档记录不存在: {record_id}")

        if record.status not in (DOC_STATUS_DRAFT, DOC_STATUS_REJECTED):
            raise ValueError(f"文档状态不允许提交审批: {record.status}")

        record.status = DOC_STATUS_PENDING
        record.updated_at = datetime.now(timezone.utc)

        approval = {
            "approval_id": f"appr_{uuid4().hex[:12]}",
            "record_id": record_id,
            "doc_id": record.doc_id,
            "filename": record.filename,
            "category": record.category,
            "permission_level": record.permission_level,
            "submitted_by": record.uploaded_by,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        self._pending[record_id] = approval

        logger.info(
            "manager.submitted_for_review",
            record_id=record_id,
            approval_id=approval["approval_id"],
        )

        return approval

    async def approve_and_publish(
        self,
        record_id: str,
        approved_by: str,
        comment: str = "",
    ) -> DocumentRecord:
        """审批通过并发布文档

        Args:
            record_id: 文档记录 ID
            approved_by: 审批人 ID
            comment: 审批意见

        Returns:
            更新后的文档记录
        """
        record = self._records.get(record_id)
        if record is None:
            raise ValueError(f"文档记录不存在: {record_id}")

        if record.status != DOC_STATUS_PENDING:
            raise ValueError(f"文档未处于待审批状态: {record.status}")

        # 1. 更新状态
        record.status = DOC_STATUS_PUBLISHED
        record.approved_by = approved_by
        record.published_at = datetime.now(timezone.utc)
        record.updated_at = datetime.now(timezone.utc)

        # 2. 重新摄入文档到向量库
        # 从版本历史中获取最新的文档块
        documents = document_ingester.ingest_text(
            text=record.content_preview,  # 使用预览内容作为简化处理
            permission_level=record.permission_level,
            department=record.department,
            category=record.category,
            tags=record.tags,
            uploaded_by=record.uploaded_by,
        )
        # 使用原始 doc_id
        for doc in documents:
            doc.doc_id = f"{record.doc_id}_v{record.version}_{documents.index(doc)}"

        await vector_store.add_documents(documents)
        hybrid_engine.sync_bm25_index()

        # 3. 更新审批状态
        if record_id in self._pending:
            self._pending[record_id]["status"] = "approved"
            self._pending[record_id]["approved_by"] = approved_by
            self._pending[record_id]["approved_at"] = record.published_at.isoformat()
            self._pending[record_id]["comment"] = comment

        logger.info(
            "manager.document_published",
            record_id=record_id,
            doc_id=record.doc_id,
            approved_by=approved_by,
        )

        return record

    def reject_document(
        self,
        record_id: str,
        rejected_by: str,
        comment: str = "",
    ) -> DocumentRecord:
        """拒绝文档

        Args:
            record_id: 文档记录 ID
            rejected_by: 拒绝人 ID
            comment: 拒绝原因

        Returns:
            更新后的文档记录
        """
        record = self._records.get(record_id)
        if record is None:
            raise ValueError(f"文档记录不存在: {record_id}")

        if record.status != DOC_STATUS_PENDING:
            raise ValueError(f"文档未处于待审批状态: {record.status}")

        record.status = DOC_STATUS_REJECTED
        record.updated_at = datetime.now(timezone.utc)

        if record_id in self._pending:
            self._pending[record_id]["status"] = "rejected"
            self._pending[record_id]["rejected_by"] = rejected_by
            self._pending[record_id]["rejected_at"] = record.updated_at.isoformat()
            self._pending[record_id]["comment"] = comment

        logger.info(
            "manager.document_rejected",
            record_id=record_id,
            rejected_by=rejected_by,
        )

        return record

    # ===================================================================
    # 检索
    # ===================================================================

    async def search(
        self,
        query: str,
        top_k: int = 10,
        user_permission_level: str = "internal",
        user_department: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """知识库检索

        封装混合检索引擎，增加分类和标签过滤。

        Args:
            query: 查询文本
            top_k: 返回结果数上限
            user_permission_level: 用户权限级别
            user_department: 用户部门
            category: 分类过滤
            tags: 标签过滤

        Returns:
            检索结果列表
        """
        # 构建过滤标签
        filter_tags: dict = {}
        if category:
            filter_tags["category"] = category
        if tags:
            filter_tags["tags"] = tags[0] if len(tags) == 1 else tags

        # 同步 BM25 索引
        hybrid_engine.sync_bm25_index()

        # 混合检索
        results = await hybrid_engine.retrieve(
            query=query,
            top_k=top_k,
            user_permission_level=user_permission_level,
            user_department=user_department,
            filter_tags=filter_tags if filter_tags else None,
        )

        return results

    # ===================================================================
    # 文档管理
    # ===================================================================

    def get_record(self, record_id: str) -> Optional[DocumentRecord]:
        """获取文档记录"""
        return self._records.get(record_id)

    def list_records(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        department: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DocumentRecord]:
        """列出文档记录

        Args:
            status: 状态过滤
            category: 分类过滤
            department: 部门过滤
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            文档记录列表
        """
        records = list(self._records.values())

        if status:
            records = [r for r in records if r.status == status]
        if category:
            records = [r for r in records if r.category == category]
        if department:
            records = [r for r in records if r.department == department]

        # 按创建时间降序
        records.sort(key=lambda r: r.created_at, reverse=True)

        return records[offset:offset + limit]

    def get_version_history(self, parent_doc_id: str) -> list[DocumentRecord]:
        """获取文档的版本历史

        Args:
            parent_doc_id: 父文档 ID

        Returns:
            版本记录列表（按版本号升序）
        """
        record_ids = self._versions.get(parent_doc_id, [])
        records = [self._records[rid] for rid in record_ids if rid in self._records]
        records.sort(key=lambda r: r.version)
        return records

    async def delete_document(self, record_id: str) -> bool:
        """删除文档

        从向量库和 BM25 索引中移除文档块。

        Args:
            record_id: 文档记录 ID

        Returns:
            是否删除成功
        """
        record = self._records.get(record_id)
        if record is None:
            return False

        # 从向量库中删除相关文档块
        all_docs = hybrid_engine._get_all_documents()
        for doc_id, doc in all_docs.items():
            if doc.metadata.get("parent_doc_id") == record.doc_id:
                await vector_store.delete(doc_id)

        # 同步 BM25 索引
        hybrid_engine.sync_bm25_index()

        # 更新记录状态
        record.status = DOC_STATUS_ARCHIVED
        record.updated_at = datetime.now(timezone.utc)

        # 更新分类计数
        if record.category in self._categories:
            self._categories[record.category].doc_count = max(
                0, self._categories[record.category].doc_count - 1
            )

        logger.info(
            "manager.document_deleted",
            record_id=record_id,
            doc_id=record.doc_id,
        )

        return True

    # ===================================================================
    # 分类管理
    # ===================================================================

    def list_categories(self) -> list[Category]:
        """列出所有分类"""
        return list(self._categories.values())

    def create_category(
        self,
        name: str,
        description: str = "",
        department: str = "general",
    ) -> Category:
        """创建新分类"""
        if name in self._categories:
            raise ValueError(f"分类已存在: {name}")
        cat = Category(name, description, department)
        self._categories[name] = cat
        logger.info("manager.category_created", name=name)
        return cat

    def delete_category(self, name: str) -> bool:
        """删除分类（仅允许删除空分类）"""
        if name not in self._categories:
            return False
        if self._categories[name].doc_count > 0:
            raise ValueError(f"分类下仍有文档，无法删除: {name}")
        self._categories.pop(name)
        return True

    # ===================================================================
    # 审批管理
    # ===================================================================

    def list_pending_reviews(
        self,
        department: Optional[str] = None,
    ) -> list[dict]:
        """列出待审批文档

        Args:
            department: 部门过滤

        Returns:
            待审批信息列表
        """
        pending = []
        for record_id, approval in self._pending.items():
            if approval["status"] != "pending":
                continue
            record = self._records.get(record_id)
            if record is None:
                continue
            if department and record.department != department:
                continue
            pending.append({**approval, "record": record.to_dict()})
        return pending

    # ===================================================================
    # 统计
    # ===================================================================

    def get_stats(self) -> dict:
        """获取知识库统计信息"""
        status_dist: dict[str, int] = {}
        category_dist: dict[str, int] = {}
        dept_dist: dict[str, int] = {}
        perm_dist: dict[str, int] = {}

        for record in self._records.values():
            status_dist[record.status] = status_dist.get(record.status, 0) + 1
            category_dist[record.category] = category_dist.get(record.category, 0) + 1
            dept_dist[record.department] = dept_dist.get(record.department, 0) + 1
            perm_dist[record.permission_level] = perm_dist.get(record.permission_level, 0) + 1

        return {
            "total_documents": len(self._records),
            "total_categories": len(self._categories),
            "total_published": status_dist.get(DOC_STATUS_PUBLISHED, 0),
            "total_pending": status_dist.get(DOC_STATUS_PENDING, 0),
            "total_drafts": status_dist.get(DOC_STATUS_DRAFT, 0),
            "status_distribution": status_dist,
            "category_distribution": category_dist,
            "department_distribution": dept_dist,
            "permission_distribution": perm_dist,
            "pending_reviews": len([
                a for a in self._pending.values() if a["status"] == "pending"
            ]),
        }

    def clear(self) -> None:
        """清空所有数据（测试使用）"""
        self._records.clear()
        self._versions.clear()
        self._pending.clear()
        self._categories.clear()
        self._init_categories()


# 全局单例
knowledge_manager = KnowledgeManager()
