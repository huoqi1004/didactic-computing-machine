"""
知识库初始化器
==============

在应用启动时为知识图谱、向量库、规则引擎注入示例数据，
让 Agent 启动后即可使用认知与知识层能力。

数据注入策略：
- 幂等：重复调用不会产生重复数据（先 clear 后注入）
- 隔离：使用全局单例的 clear() 方法重置状态，确保测试可重复
- 错误容忍：单个数据项失败不影响整体初始化

注入内容：
1. 知识图谱：3 个产品 + 2 个供应商 + 1 个仓库 + 3 个会计科目 + 关系
2. 向量库：ERP 操作手册片段 + 会计准则摘要 + 采购流程文档
3. 规则引擎：4 条预置业务规则（rules.py 模块加载时已注入，此处确保存在）
"""
import structlog

from app.core.knowledge.graph import knowledge_graph
from app.core.knowledge.rag import vector_store, Document
from app.core.knowledge.rules import rule_engine, load_builtin_rules

logger = structlog.get_logger()


# 知识库初始化状态标志（避免重复初始化）
_initialized: bool = False


async def init_knowledge_base() -> dict:
    """初始化知识库

    在应用 lifespan 启动阶段调用，向三个核心组件注入示例数据：
    - knowledge_graph: 注入企业主数据
    - vector_store: 注入业务文档
    - rule_engine: 确保预置规则已加载

    Returns:
        初始化统计字典，包含：
        - graph: {nodes: int, edges: int}
        - documents: int
        - rules: int

    Note:
        幂等设计：重复调用会先清空再注入，保证数据一致性。
    """
    global _initialized

    logger.info("knowledge_base.initializing")

    stats = {
        "graph": {"nodes": 0, "edges": 0},
        "documents": 0,
        "rules": 0,
    }

    # 1. 知识图谱：清空后注入示例企业主数据
    try:
        knowledge_graph.clear()
        _init_graph_data()
        g_stats = knowledge_graph.get_stats()
        stats["graph"]["nodes"] = g_stats["total_nodes"]
        stats["graph"]["edges"] = g_stats["total_edges"]
        logger.info(
            "knowledge_base.graph_initialized",
            nodes=g_stats["total_nodes"],
            edges=g_stats["total_edges"],
        )
    except Exception as e:
        logger.error("knowledge_base.graph_init_failed", error=str(e))

    # 2. 向量库：清空后注入示例业务文档
    try:
        vector_store.clear()
        doc_count = await _init_vector_store_data()
        stats["documents"] = doc_count
        logger.info("knowledge_base.vector_store_initialized", documents=doc_count)
    except Exception as e:
        logger.error("knowledge_base.vector_store_init_failed", error=str(e))

    # 3. 规则引擎：确保预置规则已加载
    try:
        # 预置规则在 rules.py 模块加载时已注入
        # 此处确保存在（测试中可能被 clear() 重置过）
        if not rule_engine.list_rules():
            load_builtin_rules(rule_engine)
        stats["rules"] = len(rule_engine.list_rules())
        logger.info("knowledge_base.rules_initialized", rules=stats["rules"])
    except Exception as e:
        logger.error("knowledge_base.rules_init_failed", error=str(e))

    _initialized = True
    logger.info("knowledge_base.initialized", stats=stats)
    return stats


def _init_graph_data() -> None:
    """向知识图谱注入示例企业主数据

    节点：
    - 3 个产品（电阻、电容、芯片）
    - 2 个供应商（华强电子、立创商城）
    - 1 个仓库（深圳主仓）
    - 3 个会计科目（库存商品、应付账款、主营业务收入）

    关系：
    - supplier -[supplies_to]-> product
    - product -[belongs_to]-> warehouse
    - product -[contains]-> account（产品对应的收入科目，简化示例）
    """
    # 产品节点
    p_resistor = knowledge_graph.add_node(
        "product",
        {
            "name": "贴片电阻 0805 10K",
            "sku": "RES-0805-10K",
            "category": "电子元件",
            "unit": "个",
            "unit_price": 0.05,
            "safety_stock": 1000,
        },
    )
    p_capacitor = knowledge_graph.add_node(
        "product",
        {
            "name": "贴片电容 0805 100nF",
            "sku": "CAP-0805-100N",
            "category": "电子元件",
            "unit": "个",
            "unit_price": 0.08,
            "safety_stock": 800,
        },
    )
    p_chip = knowledge_graph.add_node(
        "product",
        {
            "name": "STM32F103C8T6 主控芯片",
            "sku": "IC-STM32F103",
            "category": "集成电路",
            "unit": "个",
            "unit_price": 8.50,
            "safety_stock": 200,
        },
    )

    # 供应商节点
    s_huaqiang = knowledge_graph.add_node(
        "supplier",
        {
            "name": "华强电子有限公司",
            "contact": "张经理",
            "phone": "0755-83000000",
            "credit_score": 85,
            "address": "深圳市福田区华强北路",
        },
    )
    s_lcsc = knowledge_graph.add_node(
        "supplier",
        {
            "name": "立创商城",
            "contact": "李经理",
            "phone": "0755-83280000",
            "credit_score": 92,
            "address": "深圳市南山区科技园",
        },
    )

    # 仓库节点
    wh_main = knowledge_graph.add_node(
        "warehouse",
        {
            "name": "深圳主仓",
            "code": "WH-SZ-001",
            "address": "深圳市宝安区福永街道",
            "capacity": 100000,
        },
    )

    # 会计科目节点
    acc_inventory = knowledge_graph.add_node(
        "account",
        {
            "code": "1405",
            "name": "库存商品",
            "category": "资产类",
            "direction": "借",
        },
    )
    acc_payable = knowledge_graph.add_node(
        "account",
        {
            "code": "2202",
            "name": "应付账款",
            "category": "负债类",
            "direction": "贷",
        },
    )
    acc_revenue = knowledge_graph.add_node(
        "account",
        {
            "code": "6001",
            "name": "主营业务收入",
            "category": "损益类",
            "direction": "贷",
        },
    )

    # 关系：供应商 -> 产品（supplies_to）
    knowledge_graph.add_edge(s_huaqiang, p_resistor, "supplies_to", {"lead_time_days": 7})
    knowledge_graph.add_edge(s_huaqiang, p_capacitor, "supplies_to", {"lead_time_days": 5})
    knowledge_graph.add_edge(s_lcsc, p_chip, "supplies_to", {"lead_time_days": 14})

    # 关系：产品 -> 仓库（belongs_to）
    knowledge_graph.add_edge(p_resistor, wh_main, "belongs_to", {"location": "A区-01架"})
    knowledge_graph.add_edge(p_capacitor, wh_main, "belongs_to", {"location": "A区-02架"})
    knowledge_graph.add_edge(p_chip, wh_main, "belongs_to", {"location": "B区-防静电柜"})

    # 关系：仓库 -> 会计科目（contains，简化示例：仓库对应库存商品科目）
    knowledge_graph.add_edge(wh_main, acc_inventory, "contains", {"relation": "valuation"})

    # 关系：科目 -> 科目（belongs_to，应付账款对应主营业务收入的对手方）
    knowledge_graph.add_edge(acc_payable, acc_revenue, "belongs_to", {"relation": "matching"})


async def _init_vector_store_data() -> int:
    """向向量库注入示例业务文档

    返回注入的文档数量
    """
    documents = [
        Document(
            content=(
                "ERP系统库存操作手册：1. 入库流程 - 仓库管理员收到货物后，需在ERP系统"
                "「库存管理-入库单」模块录入入库单，填写产品SKU、数量、批次号、供应商信息。"
                "系统自动更新库存数量与价值。2. 出库流程 - 销售订单确认后，系统生成出库单，"
                "仓库管理员按出库单拣货、复核、发货，系统扣减库存并生成会计凭证。"
                "3. 库存盘点 - 每月末进行实物盘点，对比系统库存与实物数量，差异需说明并调整。"
            ),
            metadata={
                "source": "erp_manual",
                "category": "inventory",
                "tags": ["operation", "warehouse"],
                "permission_level": "internal",
            },
        ),
        Document(
            content=(
                "会计准则摘要：根据《企业会计准则》，库存商品应按成本进行初始计量，"
                "成本包括采购价格、相关税费、运输费、装卸费、保险费以及其他可归属于存货采购成本的费用。"
                "发出存货的计价方法可采用先进先出法、月末一次加权平均法或个别计价法。"
                "资产负债表日，存货应当按照成本与可变现净值孰低计量，存货成本高于其可变现净值的，"
                "应当计提存货跌价准备，计入当期损益。"
            ),
            metadata={
                "source": "accounting_standard",
                "category": "accounting",
                "tags": ["finance", "standard"],
                "permission_level": "confidential",
            },
        ),
        Document(
            content=(
                "采购流程文档：1. 采购申请 - 业务部门根据库存预警或销售预测提出采购申请，"
                "注明产品、数量、期望到货日期。2. 供应商选择 - 采购部门查询知识图谱中的供应商信息，"
                "对比信用分、历史价格、交货及时率，选择最优供应商。3. 采购订单 - 系统生成采购订单，"
                "经审批后发送给供应商。4. 收货入库 - 供应商发货后，仓库验收入库，生成入库单。"
                "5. 付款 - 财务部门根据合同账期安排付款，生成应付账款凭证。"
            ),
            metadata={
                "source": "procurement_doc",
                "category": "procurement",
                "tags": ["operation", "supplier"],
                "permission_level": "internal",
            },
        ),
        Document(
            content=(
                "供应商管理政策：公司对所有供应商实行分级管理，按信用分划分等级。"
                "A级（90分以上）：战略供应商，可享受优先付款、年度返利等优惠。"
                "B级（75-89分）：合格供应商，正常合作。C级（60-74分）：观察供应商，"
                "需加强质量监控。D级（60分以下）：不合格供应商，暂停新订单，"
                "需提交整改报告并经采购总监审批后方可恢复合作。"
            ),
            metadata={
                "source": "supplier_policy",
                "category": "procurement",
                "tags": ["policy", "supplier"],
                "permission_level": "internal",
            },
        ),
        Document(
            content=(
                "财务风险控制规范：大额资金操作必须遵循以下原则：1. 单笔金额超过10万元的"
                "采购付款、转账、报销等操作，必须经过多Agent共识投票通过后方可执行。"
                "2. 非工作时间（18:00至次日9:00及法定节假日）的大额转账（5万元以上）"
                "原则上不予执行，紧急情况需经财务总监电话确认。3. 供应商付款前需查询"
                "其信用分，低于60分的供应商需先结清历史问题后方可支付新订单。"
            ),
            metadata={
                "source": "risk_control",
                "category": "finance",
                "tags": ["policy", "risk"],
                "permission_level": "confidential",
            },
        ),
    ]

    await vector_store.add_documents(documents)
    return len(documents)


def is_initialized() -> bool:
    """检查知识库是否已初始化"""
    return _initialized


def reset_initialized() -> None:
    """重置初始化状态（主要用于测试）"""
    global _initialized
    _initialized = False
