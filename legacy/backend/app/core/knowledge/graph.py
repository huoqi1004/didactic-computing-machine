"""
企业知识图谱 - 抽象接口与内存实现
==================================

设计目标
--------
- 内存实现，零外部依赖，开发/测试默认可用
- 接口兼容 Neo4j，未来可通过实现同一抽象类无缝切换到图数据库
- 支持简单的类 Cypher 子集查询语法，便于 Agent 自然语言转译

支持的节点类型 (node_type)：
- product    产品
- supplier   供应商
- customer   客户
- account    会计科目
- warehouse  仓库
- employee   员工

支持的关系类型 (edge_type)：
- supplies_to  供应商 → 产品 / 供应商 → 客户
- belongs_to   产品 → 仓库 / 凭证 → 科目
- manages      员工 → 仓库 / 员工 → 产品线
- reports_to   员工 → 员工（汇报关系）
- contains     仓库 → 产品 / 部门 → 员工

简单查询语法
------------
``node_type:product[category=电子元件]``
- 前缀 ``node_type:`` 指定节点类型
- 方括号内为属性过滤，支持多个 key=value 用逗号分隔
- 不支持嵌套关系遍历（如需请使用 get_neighbors 链式调用）
"""
import re
import structlog
from typing import Optional
from uuid import uuid4

logger = structlog.get_logger()


# 允许的节点类型白名单
VALID_NODE_TYPES = frozenset({
    "product",
    "supplier",
    "customer",
    "account",
    "warehouse",
    "employee",
})

# 允许的关系类型白名单
VALID_EDGE_TYPES = frozenset({
    "supplies_to",
    "belongs_to",
    "manages",
    "reports_to",
    "contains",
})


class KnowledgeNode:
    """知识图谱节点

    Attributes:
        node_id: 节点唯一标识（UUID 字符串）
        node_type: 节点类型（见 VALID_NODE_TYPES）
        properties: 节点属性字典（任意键值对）
    """

    __slots__ = ("node_id", "node_type", "properties")

    def __init__(self, node_id: str, node_type: str, properties: Optional[dict] = None):
        self.node_id = node_id
        self.node_type = node_type
        self.properties = dict(properties) if properties else {}

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeNode":
        return cls(
            node_id=data["node_id"],
            node_type=data["node_type"],
            properties=data.get("properties", {}),
        )

    def __repr__(self) -> str:
        return f"KnowledgeNode(id={self.node_id[:8]}, type={self.node_type})"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, KnowledgeNode)
            and self.node_id == other.node_id
            and self.node_type == other.node_type
        )

    def __hash__(self) -> int:
        return hash(self.node_id)


class KnowledgeEdge:
    """知识图谱边（关系）

    Attributes:
        edge_id: 边唯一标识（UUID 字符串）
        source_id: 起点节点 ID
        target_id: 终点节点 ID
        edge_type: 关系类型（见 VALID_EDGE_TYPES）
        properties: 边属性字典
    """

    __slots__ = ("edge_id", "source_id", "target_id", "edge_type", "properties")

    def __init__(
        self,
        edge_id: str,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: Optional[dict] = None,
    ):
        self.edge_id = edge_id
        self.source_id = source_id
        self.target_id = target_id
        self.edge_type = edge_type
        self.properties = dict(properties) if properties else {}

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type,
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeEdge":
        return cls(
            edge_id=data["edge_id"],
            source_id=data["source_id"],
            target_id=data["target_id"],
            edge_type=data["edge_type"],
            properties=data.get("properties", {}),
        )

    def __repr__(self) -> str:
        return (
            f"KnowledgeEdge(id={self.edge_id[:8]}, "
            f"{self.source_id[:8]}-{self.edge_type}->{self.target_id[:8]})"
        )


class KnowledgeGraph:
    """
    企业知识图谱 - 内存实现

    接口设计兼容 Neo4j，关键方法签名对齐 Neo4j Python Driver 的同步 API：
    - add_node / add_edge 对应 CREATE 子句
    - get_node / find_nodes 对应 MATCH ... RETURN
    - get_neighbors 对应 MATCH (n)-[r]-(m) RETURN r, m
    - query 对应 MATCH (n:Label {prop: value}) RETURN n

    未来切换到 Neo4j 时，只需实现一个 Neo4jKnowledgeGraph 子类覆盖这些方法，
    全局单例 knowledge_graph 替换为 Neo4j 实现即可。
    """

    def __init__(self):
        # 节点存储：node_id -> KnowledgeNode
        self._nodes: dict[str, KnowledgeNode] = {}
        # 边存储：edge_id -> KnowledgeEdge
        self._edges: dict[str, KnowledgeEdge] = {}
        # 邻接索引：node_id -> list[edge_id]
        # 出边与入边统一存储，get_neighbors 可双向查询
        self._adjacency: dict[str, list[str]] = {}
        # 按节点类型索引：node_type -> set(node_id)
        self._type_index: dict[str, set[str]] = {
            t: set() for t in VALID_NODE_TYPES
        }

    # ===================================================================
    # 节点操作
    # ===================================================================

    def add_node(self, node_type: str, properties: Optional[dict] = None) -> str:
        """添加节点

        Args:
            node_type: 节点类型（必须为 VALID_NODE_TYPES 之一）
            properties: 节点属性字典

        Returns:
            新建节点的 node_id

        Raises:
            ValueError: node_type 不在白名单中
        """
        if node_type not in VALID_NODE_TYPES:
            raise ValueError(
                f"Invalid node_type: {node_type}. "
                f"Allowed: {sorted(VALID_NODE_TYPES)}"
            )

        node_id = str(uuid4())
        node = KnowledgeNode(node_id, node_type, properties)
        self._nodes[node_id] = node
        self._adjacency.setdefault(node_id, [])
        self._type_index.setdefault(node_type, set()).add(node_id)

        logger.debug(
            "knowledge_graph.node_added",
            node_id=node_id,
            node_type=node_type,
        )
        return node_id

    def get_node(self, node_id: str) -> Optional[KnowledgeNode]:
        """获取节点，不存在返回 None"""
        return self._nodes.get(node_id)

    def find_nodes(
        self,
        node_type: Optional[str] = None,
        filter_dict: Optional[dict] = None,
    ) -> list[KnowledgeNode]:
        """查找节点

        Args:
            node_type: 节点类型过滤；为 None 时不过滤类型
            filter_dict: 属性过滤字典；为空时返回所有匹配类型的节点

        Returns:
            匹配的节点列表
        """
        if node_type:
            if node_type not in VALID_NODE_TYPES:
                return []
            candidate_ids = self._type_index.get(node_type, set())
        else:
            candidate_ids = self._nodes.keys()

        results = []
        for nid in candidate_ids:
            node = self._nodes.get(nid)
            if node is None:
                continue
            if filter_dict and not self._match_properties(node, filter_dict):
                continue
            results.append(node)
        return results

    def remove_node(self, node_id: str) -> bool:
        """删除节点及其关联的所有边

        Returns:
            True 表示删除成功，False 表示节点不存在
        """
        if node_id not in self._nodes:
            return False

        # 删除关联边
        related_edge_ids = list(self._adjacency.get(node_id, []))
        for eid in related_edge_ids:
            edge = self._edges.get(eid)
            if edge is None:
                continue
            # 从对端节点的邻接表中移除
            other_id = edge.target_id if edge.source_id == node_id else edge.source_id
            if other_id in self._adjacency and eid in self._adjacency[other_id]:
                self._adjacency[other_id].remove(eid)
            self._edges.pop(eid, None)

        node = self._nodes.pop(node_id)
        self._adjacency.pop(node_id, None)
        if node.node_type in self._type_index:
            self._type_index[node.node_type].discard(node_id)

        logger.debug(
            "knowledge_graph.node_removed",
            node_id=node_id,
            removed_edges=len(related_edge_ids),
        )
        return True

    # ===================================================================
    # 边操作
    # ===================================================================

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: Optional[dict] = None,
    ) -> str:
        """添加边

        Args:
            source_id: 起点节点 ID（必须存在）
            target_id: 终点节点 ID（必须存在）
            edge_type: 关系类型（必须为 VALID_EDGE_TYPES 之一）
            properties: 边属性字典

        Returns:
            新建边的 edge_id

        Raises:
            ValueError: edge_type 非法或节点不存在
        """
        if edge_type not in VALID_EDGE_TYPES:
            raise ValueError(
                f"Invalid edge_type: {edge_type}. "
                f"Allowed: {sorted(VALID_EDGE_TYPES)}"
            )
        if source_id not in self._nodes:
            raise ValueError(f"Source node not found: {source_id}")
        if target_id not in self._nodes:
            raise ValueError(f"Target node not found: {target_id}")

        edge_id = str(uuid4())
        edge = KnowledgeEdge(edge_id, source_id, target_id, edge_type, properties)
        self._edges[edge_id] = edge
        # 双向索引：get_neighbors 支持双向遍历
        self._adjacency.setdefault(source_id, []).append(edge_id)
        self._adjacency.setdefault(target_id, []).append(edge_id)

        logger.debug(
            "knowledge_graph.edge_added",
            edge_id=edge_id,
            edge_type=edge_type,
            source_id=source_id,
            target_id=target_id,
        )
        return edge_id

    def get_edge(self, edge_id: str) -> Optional[KnowledgeEdge]:
        """获取边，不存在返回 None"""
        return self._edges.get(edge_id)

    def get_neighbors(
        self,
        node_id: str,
        edge_type: Optional[str] = None,
    ) -> list[tuple[KnowledgeEdge, KnowledgeNode]]:
        """获取邻居节点

        Args:
            node_id: 中心节点 ID
            edge_type: 可选的关系类型过滤

        Returns:
            列表元素为 (edge, neighbor_node) 元组
        """
        if node_id not in self._nodes:
            return []

        results = []
        seen_neighbors = set()
        for eid in self._adjacency.get(node_id, []):
            edge = self._edges.get(eid)
            if edge is None:
                continue
            if edge_type and edge.edge_type != edge_type:
                continue
            # 确定邻居节点（与中心节点相对的一端）
            neighbor_id = (
                edge.target_id if edge.source_id == node_id else edge.source_id
            )
            neighbor = self._nodes.get(neighbor_id)
            if neighbor is None:
                continue
            # 同一邻居去重（避免双向索引重复返回）
            key = (eid, neighbor_id)
            if key in seen_neighbors:
                continue
            seen_neighbors.add(key)
            results.append((edge, neighbor))
        return results

    # ===================================================================
    # 简单查询语言
    # ===================================================================

    # 查询语法：node_type:product[category=电子元件, brand=华为]
    # 其中 node_type 和方括号过滤都是可选的
    _QUERY_PATTERN = re.compile(
        r"^\s*(?:node_type:(?P<type>\w+))?"
        r"\s*(?:\[(?P<filters>[^\]]*)\])?\s*$"
    )

    def query(self, query_str: str) -> list[dict]:
        """简单查询语言

        支持语法：
        - ``node_type:product``  按类型查询
        - ``node_type:product[category=电子元件]``  类型 + 属性过滤
        - ``[name=某仓库]``  仅属性过滤
        - 空字符串返回所有节点

        Args:
            query_str: 查询字符串

        Returns:
            节点字典列表
        """
        if not query_str or not query_str.strip():
            return [n.to_dict() for n in self._nodes.values()]

        match = self._QUERY_PATTERN.match(query_str)
        if not match:
            logger.warning("knowledge_graph.query_parse_failed", query=query_str)
            return []

        node_type = match.group("type")
        filters_str = match.group("filters") or ""

        filter_dict: dict[str, str] = {}
        if filters_str.strip():
            for pair in filters_str.split(","):
                pair = pair.strip()
                if not pair or "=" not in pair:
                    continue
                key, _, value = pair.partition("=")
                filter_dict[key.strip()] = value.strip()

        nodes = self.find_nodes(
            node_type=node_type if node_type else None,
            filter_dict=filter_dict if filter_dict else None,
        )
        return [n.to_dict() for n in nodes]

    # ===================================================================
    # 序列化与统计
    # ===================================================================

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeGraph":
        """从字典反序列化"""
        graph = cls()
        for node_data in data.get("nodes", []):
            node = KnowledgeNode.from_dict(node_data)
            graph._nodes[node.node_id] = node
            graph._adjacency.setdefault(node.node_id, [])
            graph._type_index.setdefault(node.node_type, set()).add(node.node_id)
        for edge_data in data.get("edges", []):
            edge = KnowledgeEdge.from_dict(edge_data)
            graph._edges[edge.edge_id] = edge
            graph._adjacency.setdefault(edge.source_id, []).append(edge.edge_id)
            graph._adjacency.setdefault(edge.target_id, []).append(edge.edge_id)
        return graph

    def get_stats(self) -> dict:
        """获取统计信息"""
        type_dist = {t: len(ids) for t, ids in self._type_index.items()}
        edge_type_dist: dict[str, int] = {}
        for edge in self._edges.values():
            edge_type_dist[edge.edge_type] = edge_type_dist.get(edge.edge_type, 0) + 1
        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "node_type_distribution": type_dist,
            "edge_type_distribution": edge_type_dist,
        }

    def clear(self) -> None:
        """清空图（测试与重置使用）"""
        self._nodes.clear()
        self._edges.clear()
        self._adjacency.clear()
        self._type_index = {t: set() for t in VALID_NODE_TYPES}

    # ===================================================================
    # 内部工具
    # ===================================================================

    @staticmethod
    def _match_properties(node: KnowledgeNode, filter_dict: dict) -> bool:
        """检查节点是否匹配所有过滤条件

        匹配规则：
        - 字符串值进行弱类型比较（str(node_val) == str(filter_val)）
        - 数值类型直接比较
        """
        for key, expected in filter_dict.items():
            actual = node.properties.get(key)
            if actual is None:
                return False
            if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                if actual != expected:
                    return False
            elif str(actual) != str(expected):
                return False
        return True


# 全局单例 - 与 event_bus / audit_chain 等保持一致的架构
knowledge_graph = KnowledgeGraph()
