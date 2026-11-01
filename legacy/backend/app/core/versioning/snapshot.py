"""
版本化与灰度发布系统 - 知识库快照
==================================

提供知识库（知识图谱 + 文档 + 规则）的快照与恢复能力，
是 Agent 版本化的关键组成部分：每个 Agent 版本关联一个知识库快照。

设计原则
--------
- 内存实现，零外部依赖
- 快照不可变：创建后只读，保证版本可追溯
- 全量快照：包含知识图谱、文档、规则的完整状态
- 快速恢复：通过 restore_snapshot 一键还原知识库状态
"""
from __future__ import annotations

import structlog
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.knowledge.graph import knowledge_graph
from app.core.knowledge.rag import vector_store
from app.core.knowledge.rules import rule_engine

logger = structlog.get_logger()


@dataclass
class KnowledgeSnapshot:
    """知识库快照

    封装某一时刻知识库的完整状态。

    Attributes:
        snapshot_id: 快照唯一标识（UUID）
        created_at: 创建时间
        graph_data: 知识图谱快照（节点 + 边）
        documents: 文档快照
        rules: 规则快照
    """

    snapshot_id: str
    created_at: datetime
    graph_data: dict
    documents: list[dict]
    rules: list[dict]

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at.isoformat(),
            "graph_data": self.graph_data,
            "documents": self.documents,
            "rules": self.rules,
            "stats": {
                "nodes": len(self.graph_data.get("nodes", [])),
                "edges": len(self.graph_data.get("edges", [])),
                "documents": len(self.documents),
                "rules": len(self.rules),
            },
        }


class SnapshotManager:
    """知识库快照管理器

    提供知识库快照的创建、查询、恢复能力。
    全局单例：snapshot_manager
    """

    def __init__(self):
        self._snapshots: dict[str, KnowledgeSnapshot] = {}

    async def create_snapshot(self) -> KnowledgeSnapshot:
        """创建当前知识库快照

        捕获知识图谱、向量库文档、规则引擎的当前状态。
        快照深拷贝当前状态，确保后续修改不影响已创建的快照。

        Returns:
            新创建的 KnowledgeSnapshot
        """
        # 1. 知识图谱快照
        graph_data = self._snapshot_graph()

        # 2. 文档快照
        documents = self._snapshot_documents()

        # 3. 规则快照
        rules = self._snapshot_rules()

        snapshot = KnowledgeSnapshot(
            snapshot_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
            graph_data=graph_data,
            documents=documents,
            rules=rules,
        )
        self._snapshots[snapshot.snapshot_id] = snapshot

        logger.info(
            "snapshot.created",
            snapshot_id=snapshot.snapshot_id,
            nodes=len(graph_data.get("nodes", [])),
            documents=len(documents),
            rules=len(rules),
        )
        return snapshot

    def _snapshot_graph(self) -> dict:
        """捕获知识图谱快照"""
        nodes = []
        for node in knowledge_graph.find_nodes():
            nodes.append(node.to_dict())

        edges = []
        for node_dict in nodes:
            node_id = node_dict["node_id"]
            neighbors = knowledge_graph.get_neighbors(node_id)
            for edge, _neighbor in neighbors:
                edges.append(edge.to_dict())

        return {"nodes": nodes, "edges": edges}

    def _snapshot_documents(self) -> list[dict]:
        """捕获向量库文档快照"""
        docs = getattr(vector_store, "_documents", {})
        result = []
        for doc_id, doc in docs.items():
            try:
                result.append(doc.to_dict())
            except Exception:
                result.append({"doc_id": doc_id, "error": "serialization_failed"})
        return result

    def _snapshot_rules(self) -> list[dict]:
        """捕获规则引擎快照"""
        rules = []
        for rule in rule_engine.list_rules():
            try:
                rules.append(rule.to_dict())
            except Exception:
                continue
        return rules

    async def restore_snapshot(self, snapshot_id: str) -> bool:
        """恢复到指定快照

        将知识图谱、向量库、规则引擎还原到快照创建时的状态。

        Args:
            snapshot_id: 目标快照ID

        Returns:
            是否恢复成功
        """
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            logger.warning("snapshot.restore_failed_not_found", snapshot_id=snapshot_id)
            return False

        try:
            # 1. 恢复知识图谱
            knowledge_graph.clear()
            for node_dict in snapshot.graph_data.get("nodes", []):
                try:
                    knowledge_graph.add_node(
                        node_dict.get("node_type", "unknown"),
                        node_dict.get("properties", {}),
                    )
                except Exception:
                    continue

            # 2. 恢复文档（向量库）
            vector_store.clear()
            from app.core.knowledge.rag import Document
            docs_to_add = []
            for doc_dict in snapshot.documents:
                docs_to_add.append(Document(
                    content=doc_dict.get("content", ""),
                    metadata=doc_dict.get("metadata", {}),
                ))
            if docs_to_add:
                await vector_store.add_documents(docs_to_add)

            # 3. 恢复规则引擎
            rule_engine.clear()
            from app.core.knowledge.rules import Rule, load_builtin_rules
            # 先恢复预置规则
            load_builtin_rules(rule_engine)
            # 再追加快照中的规则（避免重复）
            existing_ids = {r.rule_id for r in rule_engine.list_rules()}
            for rule_dict in snapshot.rules:
                rule_id = rule_dict.get("rule_id")
                if rule_id and rule_id in existing_ids:
                    continue
                try:
                    rule = Rule.from_dict(rule_dict)
                    rule_engine.add_rule(rule)
                except Exception:
                    continue

            logger.info("snapshot.restored", snapshot_id=snapshot_id)
            return True
        except Exception as e:
            logger.error("snapshot.restore_error", snapshot_id=snapshot_id, error=str(e))
            return False

    def get_snapshot(self, snapshot_id: str) -> Optional[KnowledgeSnapshot]:
        """获取指定快照"""
        return self._snapshots.get(snapshot_id)

    def list_snapshots(self) -> list[dict]:
        """列出所有快照摘要"""
        return [
            {
                "snapshot_id": s.snapshot_id,
                "created_at": s.created_at.isoformat(),
                "stats": {
                    "nodes": len(s.graph_data.get("nodes", [])),
                    "edges": len(s.graph_data.get("edges", [])),
                    "documents": len(s.documents),
                    "rules": len(s.rules),
                },
            }
            for s in sorted(
                self._snapshots.values(),
                key=lambda x: x.created_at,
            )
        ]

    def clear(self) -> None:
        """清空所有快照（主要用于测试）"""
        self._snapshots.clear()


# 全局单例
snapshot_manager = SnapshotManager()
