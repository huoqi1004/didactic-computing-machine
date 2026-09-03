# -*- coding: utf-8 -*-
"""
资产Agent测试
验证固定资产登记、直线法折旧、批量折旧、盘点管理、处置与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.asset.agent import AssetAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> AssetAgent:
    """创建并初始化资产Agent（不启动主循环，仅注册身份和订阅）"""
    agent = AssetAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_purchase_approved_event(
    po_id: str = "PO-AST-001",
    total_amount: float = 50000.0,
    product_name: str = "测试设备",
) -> Event:
    """创建采购订单审批事件"""
    return Event(
        event_type=EventType.PURCHASE_ORDER_APPROVED,
        aggregate_id=po_id,
        aggregate_type="purchase_order",
        event_data={
            "po_id": po_id,
            "total_amount": total_amount,
            "product_name": product_name,
            "status": "approved",
        },
    )


def _make_asset_disposed_event(
    asset_id: str = "asset-001",
    disposal_method: str = "scrap",
    disposal_value: float = 0.0,
) -> Event:
    """创建资产处置事件"""
    return Event(
        event_type=EventType.ASSET_DISPOSED,
        aggregate_id=asset_id,
        aggregate_type="asset",
        event_data={
            "asset_id": asset_id,
            "disposal_method": disposal_method,
            "disposal_value": disposal_value,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "资产Agent"
    assert agent.domain == "asset"
    assert agent.security_clearance == "internal"
    assert "asset_registration" in agent.capabilities
    assert "depreciation_calculation" in agent.capabilities
    assert "asset_inventory" in agent.capabilities
    assert "disposal_management" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None
    assert agent.HUMAN_APPROVAL_ON_DISPOSAL is True
    assert agent.HUMAN_APPROVAL_THRESHOLD == 100_000.0


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载：5个固定资产"""
    agent = await _setup_agent()

    assets = agent.get_assets()
    assert len(assets) == 5

    # 验证关键资产存在
    asset_ids = [a["asset_id"] for a in assets]
    assert "asset-001" in asset_ids
    assert "asset-002" in asset_ids
    assert "asset-003" in asset_ids
    assert "asset-004" in asset_ids
    assert "asset-005" in asset_ids

    # 验证所有种子资产均为在用状态
    in_use = agent.get_assets(status="in_use")
    assert len(in_use) == 5

    # 验证资产有折旧和净值字段
    asset = agent.get_asset("asset-001")
    assert asset["original_value"] == 85000.0
    assert asset["accumulated_depreciation"] > 0
    assert asset["net_value"] > 0
    assert asset["salvage_value"] > 0


# ========== test_register_asset ==========

async def test_register_asset():
    """固定资产登记"""
    agent = await _setup_agent()

    asset = agent.register_asset(
        name="测试服务器",
        category="it_equipment",
        original_value=30000.0,
        purchase_date="2026-08-01",
        useful_life=60,
        salvage_rate=0.05,
        location="机房B",
        department="信息技术部",
    )
    assert asset["asset_id"].startswith("AST-")
    assert asset["name"] == "测试服务器"
    assert asset["status"] == "in_use"
    assert asset["original_value"] == 30000.0
    assert asset["salvage_value"] == 1500.0
    assert asset["net_value"] == 30000.0
    assert asset["accumulated_depreciation"] == 0.0

    # 验证已加入资产列表
    found = agent.get_asset(asset["asset_id"])
    assert found is not None
    assert found["name"] == "测试服务器"


# ========== test_depreciation_calculation ==========

async def test_depreciation_calculation():
    """直线法折旧计算"""
    agent = await _setup_agent()

    # asset-002: 原值12000, 残值600, useful_life=36
    # 月折旧 = (12000 - 600) / 36 = 316.67
    asset_before = agent.get_asset("asset-002")
    acc_before = asset_before["accumulated_depreciation"]
    net_before = asset_before["net_value"]
    record = await agent.calculate_depreciation("asset-002", "2026-07")

    assert "error" not in record
    assert record["method"] == "straight_line"
    assert record["asset_id"] == "asset-002"
    assert record["monthly_depreciation"] > 0

    # 验证资产累计折旧增加（get_asset返回内部引用，需先快照原值）
    asset_after = agent.get_asset("asset-002")
    assert asset_after["accumulated_depreciation"] > acc_before
    assert asset_after["net_value"] < net_before

    # 验证折旧记录已保存
    records = agent.get_depreciation_records(asset_id="asset-002")
    assert len(records) >= 1
    assert records[0]["period"] == "2026-07"


# ========== test_batch_depreciation ==========

async def test_batch_depreciation():
    """批量计提折旧"""
    agent = await _setup_agent()

    records = await agent.batch_depreciation("2026-07")

    # 5个在用资产都应计提折旧
    assert len(records) == 5
    for record in records:
        assert record["method"] == "straight_line"
        assert record["monthly_depreciation"] > 0

    # 验证折旧记录数
    all_records = agent.get_depreciation_records(period="2026-07")
    assert len(all_records) == 5


# ========== test_purchase_approved_auto_register ==========

async def test_purchase_approved_auto_register():
    """采购审批通过自动登记固定资产"""
    agent = await _setup_agent()

    event = _make_purchase_approved_event(
        po_id="PO-AST-001",
        total_amount=50000.0,
        product_name="采购服务器",
    )
    await agent.process_event(event)

    # 应自动登记为固定资产
    procurement_assets = agent.get_assets(category="procurement")
    assert len(procurement_assets) == 1
    assert procurement_assets[0]["name"] == "采购服务器"
    assert procurement_assets[0]["original_value"] == 50000.0
    assert procurement_assets[0]["source_po_id"] == "PO-AST-001"

    # 重复事件不应重复登记
    await agent.process_event(event)
    procurement_assets2 = agent.get_assets(category="procurement")
    assert len(procurement_assets2) == 1

    # 金额低于5000不应登记
    small_event = _make_purchase_approved_event(
        po_id="PO-AST-002",
        total_amount=3000.0,
        product_name="小额采购",
    )
    await agent.process_event(small_event)
    procurement_assets3 = agent.get_assets(category="procurement")
    assert len(procurement_assets3) == 1


# ========== test_dispose_asset ==========

async def test_dispose_asset():
    """资产处置"""
    agent = await _setup_agent()

    result = await agent.dispose_asset(
        asset_id="asset-005",
        disposal_method="scrap",
        disposal_value=100.0,
        reason="设备老化报废",
    )
    assert "error" not in result
    assert result["status"] == "disposed"
    assert result["disposal_method"] == "scrap"
    assert result["disposal_value"] == 100.0
    assert "disposal_gain_loss" in result

    # 验证资产状态已更新
    asset = agent.get_asset("asset-005")
    assert asset["status"] == "disposed"

    # 重复处置应返回错误
    result2 = await agent.dispose_asset(
        asset_id="asset-005",
        disposal_method="sale",
        disposal_value=0.0,
        reason="重复处置",
    )
    assert "error" in result2


# ========== test_make_decision_disposal ==========

async def test_make_decision_disposal():
    """资产处置决策需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "disposal",
        "asset_id": "asset-001",
        "disposal_method": "sale",
    })
    assert decision.decision_type == "asset_disposal"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.9
    assert "asset-001" in decision.context["asset_id"]


# ========== test_make_decision_large_registration ==========

async def test_make_decision_large_registration():
    """大额资产登记需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "registration",
        "original_value": 200000.0,
        "name": "大型生产设备",
    })
    assert decision.decision_type == "asset_registration_approval"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.9

    # 小额资产自动登记
    decision2 = await agent.make_decision({
        "action": "registration",
        "original_value": 50000.0,
        "name": "普通设备",
    })
    assert decision2.decision_type == "asset_registration_auto"
    assert decision2.requires_human_approval is False
    assert decision2.confidence == 0.95


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的资产ID返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "disposal",
        "asset_id": "nonexistent-asset",
    })
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_inventory_management ==========

async def test_inventory_management():
    """盘点计划创建与结果记录"""
    agent = await _setup_agent()

    # 创建盘点计划
    inventory = agent.create_inventory(
        name="2026年度盘点",
        scope="全公司",
        date="2026-08-07",
    )
    assert inventory["status"] == "in_progress"
    assert inventory["discrepancy_count"] == 0
    assert inventory["inventory_id"].startswith("INV-")

    # 记录账实相符的盘点结果
    result1 = agent.record_inventory_result(
        inventory_id=inventory["inventory_id"],
        asset_id="asset-001",
        actual_status="in_use",
        discrepancy="无",
    )
    assert result1["has_discrepancy"] is False
    assert result1["book_status"] == "in_use"

    # 记录有差异的盘点结果
    result2 = agent.record_inventory_result(
        inventory_id=inventory["inventory_id"],
        asset_id="asset-002",
        actual_status="damaged",
        discrepancy="设备外壳损坏",
    )
    assert result2["has_discrepancy"] is True

    # 验证盘点详情
    inv_detail = agent.get_inventory(inventory["inventory_id"])
    assert inv_detail["discrepancy_count"] == 1
    assert len(inv_detail["results"]) == 2

    # 验证资产状态被同步更新为实际状态
    asset = agent.get_asset("asset-002")
    assert asset["status"] == "damaged"


# ========== test_get_assets_filter ==========

async def test_get_assets_filter():
    """资产列表按类别与状态过滤"""
    agent = await _setup_agent()

    # 按类别过滤
    office = agent.get_assets(category="office_equipment")
    assert len(office) == 2  # asset-002, asset-005
    assert all(a["category"] == "office_equipment" for a in office)

    it_equipment = agent.get_assets(category="it_equipment")
    assert len(it_equipment) == 1  # asset-001

    vehicles = agent.get_assets(category="vehicle")
    assert len(vehicles) == 1  # asset-003

    # 按状态过滤
    in_use = agent.get_assets(status="in_use")
    assert len(in_use) == 5

    disposed = agent.get_assets(status="disposed")
    assert len(disposed) == 0


# ========== test_on_tick ==========

async def test_on_tick():
    """on_tick周期性任务：检查资产净值异常"""
    agent = await _setup_agent()

    # 正常执行不应抛出异常
    await agent.on_tick()

    # 构造净值低于残值的在用资产
    asset = agent.get_asset("asset-005")
    original_net = asset["net_value"]
    asset["net_value"] = 100.0  # 低于残值175.0

    # 应正常执行（仅记录警告日志）
    await agent.on_tick()

    # 恢复净值
    asset["net_value"] = original_net


# ========== test_get_stats ==========

async def test_get_stats():
    """资产统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["total_assets"] == 5
    assert stats["in_use"] == 5
    assert stats["disposed"] == 0
    assert stats["total_original_value"] > 0
    assert stats["total_accumulated_depreciation"] > 0
    assert stats["total_net_value"] > 0
    assert stats["depreciation_records"] == 0
    assert stats["inventories"] == 0
    assert "by_category" in stats
    assert "by_department" in stats

    # 按类别验证
    assert "it_equipment" in stats["by_category"]
    assert "office_equipment" in stats["by_category"]
    assert "vehicle" in stats["by_category"]
    assert "machinery" in stats["by_category"]
