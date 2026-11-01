"""
知识分块持久化与水合（知识库改进 / 解决评估报告 E-04"重启知识归零"）
======================================================================

链路（WeKnora 异步流水线模式适配）：

    摄取（ingestion 分块）→ 向量化（EmbeddingProvider，异步流水线）
    → knowledge_chunks 落库 → 启动水合（重建 TF-IDF/BM25/语义索引）

语义向量以 float32 序列化 BLOB 存 MySQL（无向量库运维面；
万级 chunk 内内存余弦检索性价比最优），未向量化 chunk 为 NULL，
语义通道自动跳过、BM25/TF-IDF 通道不受影响。
"""
from __future__ import annotations

import struct
from typing import Optional

import structlog

from app.core.knowledge.rag import Document, vector_store

logger = structlog.get_logger()


def _vec_to_blob(vec: Optional[list[float]]) -> Optional[bytes]:
    if not vec:
        return None
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_vec(blob: Optional[bytes]) -> Optional[list[float]]:
    if not blob:
        return None
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


async def save_chunks(parent_doc_id: str, docs: list[Document],
                      embeddings: Optional[list[Optional[list[float]]]] = None,
                      embedding_model: str = "") -> int:
    """分块 + 向量落库（upsert，按 chunk_id 幂等）。"""
    from app.db.repositories.base import has_db, session_scope

    if not has_db() or not docs:
        return 0
    try:
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        from app.db.models import knowledge_chunks
        from app.db.repositories.base import dialect_upsert_stmt

        count = 0
        for i, doc in enumerate(docs):
            vec = embeddings[i] if embeddings and i < len(embeddings) else None
            meta = doc.metadata or {}
            values = dict(
                chunk_id=doc.doc_id,
                parent_doc_id=meta.get("parent_doc_id", parent_doc_id),
                chunk_index=int(meta.get("chunk_index", i)),
                title=str(meta.get("filename") or meta.get("title") or "")[:256] or None,
                content=doc.content,
                embedding=_vec_to_blob(vec),
                embedding_model=embedding_model if vec else None,
                source=str(meta.get("filename") or "")[:256] or None,
                permission_level=meta.get("permission_level", "internal"),
                department=meta.get("department", "general"),
                tags=meta.get("tags"),
            )
            async with session_scope() as session:
                await session.execute(
                    dialect_upsert_stmt(knowledge_chunks, values,
                                        conflict_cols=["chunk_id"])
                )
            count += 1
        logger.info("knowledge_persistence.saved",
                    parent=parent_doc_id, chunks=count,
                    vectorized=sum(1 for v in (embeddings or []) if v))
        return count
    except Exception as e:  # noqa: BLE001
        logger.error("knowledge_persistence.save_failed",
                     parent=parent_doc_id, error=str(e))
        return 0


async def delete_by_parent(parent_doc_id: str) -> int:
    """删除某文档全部分块（文档更新/删除时调用）。"""
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return 0
    try:
        from sqlalchemy import delete

        from app.db.models import knowledge_chunks

        async with session_scope() as session:
            result = await session.execute(
                delete(knowledge_chunks).where(
                    knowledge_chunks.c.parent_doc_id == parent_doc_id)
            )
            return result.rowcount or 0
    except Exception as e:  # noqa: BLE001
        logger.warning("knowledge_persistence.delete_failed", error=str(e))
        return 0


async def load_all_chunks(limit: int = 20000) -> list[dict]:
    """读取全部分块（水合用）。"""
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return []
    try:
        from sqlalchemy import select

        from app.db.models import knowledge_chunks

        async with session_scope() as session:
            rows = (await session.execute(
                select(knowledge_chunks).limit(limit)
            )).all()
        return [
            {
                "chunk_id": r.chunk_id,
                "parent_doc_id": r.parent_doc_id,
                "chunk_index": r.chunk_index,
                "title": r.title,
                "content": r.content,
                "embedding": _blob_to_vec(r.embedding),
                "embedding_model": r.embedding_model,
                "source": r.source,
                "permission_level": r.permission_level,
                "department": r.department,
                "tags": r.tags,
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        logger.error("knowledge_persistence.load_failed", error=str(e))
        return []


async def hydrate_knowledge_from_db() -> dict:
    """
    启动水合：从 knowledge_chunks 重建内存检索索引
    （TF-IDF 向量库 + BM25 + 语义向量），解决"重启知识归零"（E-04）。

    Returns:
        {"chunks": int, "vectorized": int, "documents": int}
    """
    from app.core.knowledge.hybrid import hybrid_engine

    chunks = await load_all_chunks()
    if not chunks:
        logger.info("knowledge_persistence.hydrate_empty")
        return {"chunks": 0, "vectorized": 0, "documents": 0}

    docs = [
        Document(
            doc_id=c["chunk_id"],
            content=c["content"],
            metadata={
                "parent_doc_id": c["parent_doc_id"],
                "chunk_index": c["chunk_index"],
                "title": c["title"] or "",
                "filename": c["source"] or "",
                "source": c["source"] or "",
                "permission_level": c["permission_level"],
                "department": c["department"] or "general",
                "tags": c["tags"] or {},
            },
            embedding=c["embedding"],
        )
        for c in chunks
    ]
    await vector_store.add_documents(docs)
    hybrid_engine.sync_bm25_index()

    vectorized = sum(1 for d in docs if d.embedding)
    logger.info(
        "knowledge_persistence.hydrated",
        chunks=len(chunks), vectorized=vectorized,
        docs=len({d.metadata["parent_doc_id"] for d in docs}),
    )
    return {"chunks": len(chunks), "vectorized": vectorized,
            "documents": len({d.metadata["parent_doc_id"] for d in docs})}
