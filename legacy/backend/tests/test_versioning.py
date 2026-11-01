"""
版本化与灰度发布系统测试
========================

覆盖：
- 版本注册和查询
- 版本激活和回滚
- 灰度发布流程（启动→评估→全量/中止）
- 灰度路由一致性
- 数字孪生创建和仿真
- 知识库快照创建和恢复
- API端点

全局单例由 conftest.py 的 reset_global_singletons 夹具在每个测试前自动重置。
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes import router
from app.core.versioning import (
    AgentVersion,
    CanaryConfig,
    VersionStatus,
    VersionRegistry,
    CanaryManager,
    DigitalTwin,
    SnapshotManager,
    version_registry,
    canary_manager,
    digital_twin,
    snapshot_manager,
)
from app.core.config import settings


# ========== 辅助函数 ==========

def _make_config(version: str = "1.0.0") -> dict:
    """生成测试用 Agent 配置"""
    return {
        "capabilities": ["inventory_monitoring", "voucher_generation"],
        "security_clearance": "confidential",
        "model_params": {"temperature": 0.3, "confidence": 0.9},
    }


def _make_rules(count: int = 2) -> list[dict]:
    """生成测试用规则集"""
    return [
        {
            "rule_id": f"rule-{i}",
            "name": f"测试规则{i}",
            "condition": f"amount > {1000 * (i + 1)}",
            "action": "alert",
            "priority": 80 + i,
            "enabled": True,
        }
        for i in range(count)
    ]


def _register_sample_version(agent_name: str = "测试Agent", version: str = "1.0.0"):
    """注册一个示例版本"""
    return version_registry.register_version(
        agent_name=agent_name,
        version=version,
        config=_make_config(),
        rules=_make_rules(),
        changelog="初始版本",
    )


# ========== 版本模型测试 ==========


def test_version_status_enum():
    """测试版本状态枚举值"""
    assert VersionStatus.DRAFT == "draft"
    assert VersionStatus.TESTING == "testing"
    assert VersionStatus.CANARY == "canary"
    assert VersionStatus.ACTIVE == "active"
    assert VersionStatus.ROLLED_BACK == "rolled_back"
    assert VersionStatus.ARCHIVED == "archived"


def test_agent_version_to_dict():
    """测试 AgentVersion 序列化"""
    from datetime import datetime, timezone
    version = AgentVersion(
        version_id="v-001",
        agent_name="测试Agent",
        version="1.0.0",
        config={"capabilities": ["read"]},
        rules=[{"rule_id": "r1"}],
        knowledge_snapshot_id="snap-001",
        created_at=datetime.now(timezone.utc),
        created_by="tester",
        status=VersionStatus.ACTIVE,
        changelog="测试",
    )
    d = version.to_dict()
    assert d["version_id"] == "v-001"
    assert d["agent_name"] == "测试Agent"
    assert d["version"] == "1.0.0"
    assert d["status"] == "active"
    assert d["config"]["capabilities"] == ["read"]
    assert d["knowledge_snapshot_id"] == "snap-001"


def test_canary_config_defaults():
    """测试 CanaryConfig 默认值"""
    config = CanaryConfig(version_id="v-001")
    assert config.traffic_percentage == 10
    assert config.success_threshold == 0.95
    assert config.error_rate_threshold == 0.05
    assert config.duration_seconds == 3600
    assert config.status == "running"


# ========== 版本注册和查询测试 ==========


def test_register_version_returns_active_for_first():
    """注册首个版本时自动激活"""
    version = _register_sample_version()
    assert version.status == VersionStatus.ACTIVE
    assert version.version_id is not None
    assert version.agent_name == "测试Agent"


def test_get_version_by_id():
    """通过版本ID查询版本"""
    version = _register_sample_version()
    found = version_registry.get_version(version.version_id)
    assert found is not None
    assert found.version_id == version.version_id


def test_get_version_not_found():
    """查询不存在的版本返回None"""
    assert version_registry.get_version("nonexistent") is None


def test_get_versions_by_agent():
    """获取Agent所有版本列表"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="1.1.0",
        config=_make_config(),
        rules=_make_rules(),
        parent_version=v1.version_id,
        changelog="新增功能",
    )
    versions = version_registry.get_versions("测试Agent")
    assert len(versions) == 2
    assert versions[0].version == "1.0.0"
    assert versions[1].version == "1.1.0"


def test_get_active_version():
    """获取当前激活版本"""
    v1 = _register_sample_version(version="1.0.0")
    active = version_registry.get_active_version("测试Agent")
    assert active is not None
    assert active.version_id == v1.version_id


def test_get_active_version_no_agent():
    """未注册的Agent没有激活版本"""
    assert version_registry.get_active_version("不存在Agent") is None


# ========== 版本激活和回滚测试 ==========


def test_activate_version_switches_active():
    """激活新版本会切换当前激活版本"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
        changelog="大版本升级",
    )
    # v1 默认激活，v2 为 DRAFT
    assert v1.status == VersionStatus.ACTIVE
    assert v2.status == VersionStatus.DRAFT

    # 激活 v2
    success = version_registry.activate_version(v2.version_id)
    assert success is True
    assert v2.status == VersionStatus.ACTIVE
    assert v1.status == VersionStatus.ROLLED_BACK

    # 当前激活的是 v2
    active = version_registry.get_active_version("测试Agent")
    assert active.version_id == v2.version_id


def test_activate_nonexistent_version_fails():
    """激活不存在的版本失败"""
    assert version_registry.activate_version("nonexistent") is False


def test_rollback_to_previous_version():
    """回滚到上一个版本"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
        changelog="升级",
    )
    version_registry.activate_version(v2.version_id)
    assert version_registry.get_active_version("测试Agent").version_id == v2.version_id

    # 回滚
    rolled = version_registry.rollback_version("测试Agent")
    assert rolled is not None
    assert rolled.version_id == v1.version_id
    assert rolled.status == VersionStatus.ACTIVE
    assert v2.status == VersionStatus.ROLLED_BACK


def test_rollback_to_specific_version():
    """回滚到指定版本"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
        parent_version=v1.version_id,
    )
    v3 = version_registry.register_version(
        agent_name="测试Agent",
        version="3.0.0",
        config=_make_config(),
        rules=_make_rules(),
        parent_version=v2.version_id,
    )
    version_registry.activate_version(v3.version_id)

    # 回滚到 v1
    rolled = version_registry.rollback_version("测试Agent", target_version_id=v1.version_id)
    assert rolled.version_id == v1.version_id
    assert rolled.status == VersionStatus.ACTIVE


def test_archive_version():
    """归档版本"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    # 激活 v2，使 v1 变为 ROLLED_BACK（非激活状态）
    version_registry.activate_version(v2.version_id)
    assert v1.status == VersionStatus.ROLLED_BACK

    # 归档 v1（非激活状态）
    assert version_registry.archive_version(v1.version_id) is True
    assert v1.status == VersionStatus.ARCHIVED
    # 归档版本不能激活
    assert version_registry.activate_version(v1.version_id) is False


def test_archive_active_version_fails():
    """归档激活中的版本失败"""
    v1 = _register_sample_version(version="1.0.0")
    assert version_registry.archive_version(v1.version_id) is False


def test_compare_versions():
    """对比两个版本差异"""
    v1 = version_registry.register_version(
        agent_name="测试Agent",
        version="1.0.0",
        config={"capabilities": ["read"], "threshold": 100},
        rules=[{"rule_id": "r1", "name": "规则1"}],
    )
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config={"capabilities": ["read", "write"], "threshold": 200},
        rules=[{"rule_id": "r1", "name": "规则1"}, {"rule_id": "r2", "name": "规则2"}],
    )
    diff = version_registry.compare_versions(v1.version_id, v2.version_id)
    assert "config_diff" in diff
    assert "rules_diff" in diff
    assert diff["rules_diff"]["added_count"] == 1
    assert diff["config_diff"]["changed"]["threshold"]["from"] == 100
    assert diff["config_diff"]["changed"]["threshold"]["to"] == 200


def test_version_chain():
    """测试版本链追踪"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
        parent_version=v1.version_id,
    )
    v3 = version_registry.register_version(
        agent_name="测试Agent",
        version="3.0.0",
        config=_make_config(),
        rules=_make_rules(),
        parent_version=v2.version_id,
    )
    chain = version_registry.get_version_chain("测试Agent")
    assert len(chain) == 3
    assert chain[0].version_id == v1.version_id
    assert chain[1].version_id == v2.version_id
    assert chain[2].version_id == v3.version_id


def test_registry_to_dict_and_stats():
    """测试注册中心序列化和统计"""
    _register_sample_version(version="1.0.0")
    _register_sample_version(version="2.0.0")
    d = version_registry.to_dict()
    assert d["total_versions"] == 2
    assert d["total_agents"] == 1

    stats = version_registry.get_stats()
    assert stats["total_versions"] == 2
    assert "status_distribution" in stats


# ========== 灰度发布测试 ==========


@pytest.mark.asyncio
async def test_canary_start_and_status():
    """测试灰度启动与状态查询"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(
        version_id=v2.version_id,
        traffic_percentage=20,
        success_threshold=0.9,
        error_rate_threshold=0.1,
        duration_seconds=60,
    )
    result = await canary_manager.start_canary(v2.version_id, config)
    assert result.status == "running"
    assert v2.status == VersionStatus.CANARY

    status = canary_manager.get_canary_status(v2.version_id)
    assert status["active"] is True
    assert status["config"]["traffic_percentage"] == 20


@pytest.mark.asyncio
async def test_canary_start_nonexistent_raises():
    """启动不存在的版本灰度应抛出异常"""
    config = CanaryConfig(version_id="nonexistent")
    with pytest.raises(ValueError):
        await canary_manager.start_canary("nonexistent", config)


@pytest.mark.asyncio
async def test_canary_start_duplicate_raises():
    """重复启动灰度应抛出异常"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(version_id=v2.version_id, duration_seconds=60)
    await canary_manager.start_canary(v2.version_id, config)
    with pytest.raises(ValueError):
        await canary_manager.start_canary(v2.version_id, config)


@pytest.mark.asyncio
async def test_canary_route_consistency():
    """测试灰度路由一致性：同一请求始终路由到同一版本"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(
        version_id=v2.version_id,
        traffic_percentage=50,
        duration_seconds=60,
    )
    await canary_manager.start_canary(v2.version_id, config)

    # 同一 request_id 多次路由结果一致
    request_id = "req-consistency-test-001"
    ctx = {"request_id": request_id}
    route1 = canary_manager.route_request("测试Agent", ctx)
    route2 = canary_manager.route_request("测试Agent", ctx)
    route3 = canary_manager.route_request("测试Agent", ctx)
    assert route1 == route2 == route3
    assert route1 in (v1.version_id, v2.version_id)


@pytest.mark.asyncio
async def test_canary_route_traffic_distribution():
    """测试灰度流量分配比例"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(
        version_id=v2.version_id,
        traffic_percentage=30,
        duration_seconds=60,
    )
    await canary_manager.start_canary(v2.version_id, config)

    # 路由1000个请求，统计分布
    v2_count = 0
    total = 1000
    for i in range(total):
        ctx = {"request_id": f"req-{i}"}
        routed = canary_manager.route_request("测试Agent", ctx)
        if routed == v2.version_id:
            v2_count += 1

    # 30% 灰度流量，允许 ±10% 误差
    ratio = v2_count / total
    assert 0.20 < ratio < 0.40, f"灰度流量比例 {ratio:.2%} 不在预期范围"


@pytest.mark.asyncio
async def test_canary_route_no_canary_returns_active():
    """无灰度时路由到激活版本"""
    v1 = _register_sample_version(version="1.0.0")
    routed = canary_manager.route_request("测试Agent", {"request_id": "r1"})
    assert routed == v1.version_id


@pytest.mark.asyncio
async def test_canary_record_result_and_metrics():
    """测试指标记录"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(version_id=v2.version_id, duration_seconds=60)
    await canary_manager.start_canary(v2.version_id, config)

    # 记录20条结果：18成功2失败
    for i in range(18):
        canary_manager.record_result(v2.version_id, success=True, latency_ms=50.0)
    for i in range(2):
        canary_manager.record_result(v2.version_id, success=False, latency_ms=100.0)

    metrics = canary_manager.get_metrics(v2.version_id)
    assert metrics["requests"] == 20
    assert metrics["successes"] == 18
    assert metrics["errors"] == 2
    assert metrics["latency_sum"] == 18 * 50.0 + 2 * 100.0


@pytest.mark.asyncio
async def test_canary_evaluate_pass():
    """测试灰度评估通过（成功率高、错误率低）"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(
        version_id=v2.version_id,
        traffic_percentage=50,
        success_threshold=0.9,
        error_rate_threshold=0.1,
        duration_seconds=0,  # 立即满足持续时间
    )
    await canary_manager.start_canary(v2.version_id, config)

    # 记录20条成功结果
    for i in range(20):
        canary_manager.record_result(v2.version_id, success=True, latency_ms=50.0)

    result = await canary_manager.evaluate_canary(v2.version_id)
    assert result["decision"] == "pass"
    assert result["metrics"]["success_rate"] == 1.0


@pytest.mark.asyncio
async def test_canary_evaluate_fail():
    """测试灰度评估失败（错误率过高）"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(
        version_id=v2.version_id,
        traffic_percentage=50,
        success_threshold=0.95,
        error_rate_threshold=0.05,
        duration_seconds=0,
    )
    await canary_manager.start_canary(v2.version_id, config)

    # 记录20条结果：15成功5失败（错误率25%）
    for i in range(15):
        canary_manager.record_result(v2.version_id, success=True, latency_ms=50.0)
    for i in range(5):
        canary_manager.record_result(v2.version_id, success=False, latency_ms=100.0)

    result = await canary_manager.evaluate_canary(v2.version_id)
    assert result["decision"] == "fail"
    assert result["metrics"]["error_rate"] == 0.25


@pytest.mark.asyncio
async def test_canary_evaluate_continue_insufficient_samples():
    """测试样本不足时决策为continue"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(version_id=v2.version_id, duration_seconds=0)
    await canary_manager.start_canary(v2.version_id, config)

    # 只记录5条（< 10）
    for i in range(5):
        canary_manager.record_result(v2.version_id, success=True, latency_ms=50.0)

    result = await canary_manager.evaluate_canary(v2.version_id)
    assert result["decision"] == "continue"


@pytest.mark.asyncio
async def test_canary_promote():
    """测试灰度全量切换"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(version_id=v2.version_id, duration_seconds=60)
    await canary_manager.start_canary(v2.version_id, config)
    assert v2.status == VersionStatus.CANARY

    success = await canary_manager.promote_canary(v2.version_id)
    assert success is True
    assert v2.status == VersionStatus.ACTIVE
    assert v1.status == VersionStatus.ROLLED_BACK

    # 灰度已停止
    status = canary_manager.get_canary_status(v2.version_id)
    assert status["active"] is False


@pytest.mark.asyncio
async def test_canary_abort():
    """测试中止灰度"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    config = CanaryConfig(version_id=v2.version_id, duration_seconds=60)
    await canary_manager.start_canary(v2.version_id, config)

    success = await canary_manager.abort_canary(v2.version_id, reason="test_abort")
    assert success is True
    # 中止后 v1 仍为激活版本
    active = version_registry.get_active_version("测试Agent")
    assert active.version_id == v1.version_id
    # v2 回到 DRAFT
    assert v2.status == VersionStatus.DRAFT


# ========== 数字孪生测试 ==========


@pytest.mark.asyncio
async def test_digital_twin_create():
    """测试创建数字孪生实例"""
    v1 = _register_sample_version(version="1.0.0")
    twin_id = await digital_twin.create_twin("测试Agent", v1.version_id)
    assert twin_id is not None

    status = digital_twin.get_twin_status(twin_id)
    assert status["status"] == "created"
    assert status["agent_name"] == "测试Agent"
    assert status["version_id"] == v1.version_id


@pytest.mark.asyncio
async def test_digital_twin_create_nonexistent_version():
    """创建孪生实例时版本不存在应抛出异常"""
    with pytest.raises(ValueError):
        await digital_twin.create_twin("测试Agent", "nonexistent")


@pytest.mark.asyncio
async def test_digital_twin_inject_event():
    """测试注入测试事件"""
    v1 = _register_sample_version(version="1.0.0")
    twin_id = await digital_twin.create_twin("测试Agent", v1.version_id)

    event = {
        "event_type": "INVENTORY_ADJUSTED",
        "aggregate_id": "test-001",
        "event_data": {
            "product_id": "test-001",
            "product_name": "测试产品",
            "quantity": 30,
            "safety_stock": 50,
            "reorder_point": 100,
            "unit_cost": 10.0,
        },
    }
    await digital_twin.inject_event(twin_id, event)

    status = digital_twin.get_twin_status(twin_id)
    assert status["events_injected"] == 1
    assert status["results_count"] == 1


@pytest.mark.asyncio
async def test_digital_twin_run_simulation():
    """测试运行仿真测试"""
    v1 = _register_sample_version(version="1.0.0")
    twin_id = await digital_twin.create_twin("测试Agent", v1.version_id)

    # 使用简化的自定义场景
    scenarios = [
        {
            "scenario_id": "test_scenario",
            "name": "测试场景",
            "events": [
                {
                    "event_type": "INVENTORY_ADJUSTED",
                    "aggregate_id": "sim-001",
                    "event_data": {
                        "product_id": "sim-001",
                        "product_name": "仿真产品",
                        "quantity": 30,
                        "safety_stock": 50,
                        "reorder_point": 100,
                        "unit_cost": 10.0,
                    },
                },
            ],
            "expected": {"replenishment_proposals": ">=1"},
        },
    ]
    report = await digital_twin.run_simulation(twin_id, scenarios)
    assert report["status"] == "completed"
    assert report["summary"]["total_scenarios"] == 1
    assert report["summary"]["total_events"] == 1
    assert len(report["scenarios"]) == 1


@pytest.mark.asyncio
async def test_digital_twin_run_default_scenarios():
    """测试运行默认仿真场景"""
    v1 = _register_sample_version(version="1.0.0")
    twin_id = await digital_twin.create_twin("测试Agent", v1.version_id)

    # 使用默认场景
    report = await digital_twin.run_simulation(twin_id, None)
    assert report["status"] == "completed"
    assert report["summary"]["total_scenarios"] >= 3
    assert report["summary"]["total_events"] > 0


@pytest.mark.asyncio
async def test_digital_twin_compare_with_production():
    """测试与生产环境对比"""
    v1 = _register_sample_version(version="1.0.0")
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config={**_make_config(), "model_params": {"confidence": 0.95}},
        rules=_make_rules(3),
    )
    twin_id = await digital_twin.create_twin("测试Agent", v2.version_id)

    # 注入一些事件
    await digital_twin.inject_event(twin_id, {
        "event_type": "INVENTORY_ADJUSTED",
        "aggregate_id": "cmp-001",
        "event_data": {
            "product_id": "cmp-001",
            "quantity": 30,
            "safety_stock": 50,
            "reorder_point": 100,
            "unit_cost": 10.0,
        },
    })

    report = await digital_twin.compare_with_production(twin_id)
    assert "twin_version" in report
    assert "production_version" in report
    assert report["production_version"]["version_id"] == v1.version_id
    assert report["twin_version"]["version_id"] == v2.version_id
    assert "recommendation" in report


@pytest.mark.asyncio
async def test_digital_twin_destroy():
    """测试销毁孪生实例"""
    v1 = _register_sample_version(version="1.0.0")
    twin_id = await digital_twin.create_twin("测试Agent", v1.version_id)

    await digital_twin.destroy_twin(twin_id)
    status = digital_twin.get_twin_status(twin_id)
    assert status["status"] == "not_found"


@pytest.mark.asyncio
async def test_digital_twin_sets_testing_status():
    """创建孪生实例后版本状态变为TESTING"""
    v1 = _register_sample_version(version="1.0.0")
    assert v1.status == VersionStatus.ACTIVE

    # 注册一个 DRAFT 版本用于测试
    v2 = version_registry.register_version(
        agent_name="测试Agent",
        version="2.0.0",
        config=_make_config(),
        rules=_make_rules(),
    )
    # v2 是 DRAFT（因为 v1 已激活）
    assert v2.status == VersionStatus.DRAFT

    twin_id = await digital_twin.create_twin("测试Agent", v2.version_id)
    assert v2.status == VersionStatus.TESTING

    # 销毁后回到 DRAFT
    await digital_twin.destroy_twin(twin_id)
    assert v2.status == VersionStatus.DRAFT


# ========== 知识库快照测试 ==========


@pytest.mark.asyncio
async def test_snapshot_create():
    """测试创建知识库快照"""
    snapshot = await snapshot_manager.create_snapshot()
    assert snapshot.snapshot_id is not None
    assert "nodes" in snapshot.graph_data
    assert isinstance(snapshot.documents, list)
    assert isinstance(snapshot.rules, list)


@pytest.mark.asyncio
async def test_snapshot_list():
    """测试列出快照"""
    await snapshot_manager.create_snapshot()
    await snapshot_manager.create_snapshot()
    snapshots = snapshot_manager.list_snapshots()
    assert len(snapshots) == 2
    assert "snapshot_id" in snapshots[0]
    assert "stats" in snapshots[0]


@pytest.mark.asyncio
async def test_snapshot_get():
    """测试获取快照详情"""
    snapshot = await snapshot_manager.create_snapshot()
    found = snapshot_manager.get_snapshot(snapshot.snapshot_id)
    assert found is not None
    assert found.snapshot_id == snapshot.snapshot_id


@pytest.mark.asyncio
async def test_snapshot_get_not_found():
    """获取不存在的快照返回None"""
    assert snapshot_manager.get_snapshot("nonexistent") is None


@pytest.mark.asyncio
async def test_snapshot_restore():
    """测试恢复知识库快照"""
    # 创建初始快照（基于初始化的规则引擎）
    snapshot1 = await snapshot_manager.create_snapshot()
    initial_rules_count = len(snapshot1.rules)

    # 恢复快照
    success = await snapshot_manager.restore_snapshot(snapshot1.snapshot_id)
    assert success is True

    # 恢复后规则引擎应有规则
    from app.core.knowledge.rules import rule_engine
    restored_rules = rule_engine.list_rules()
    assert len(restored_rules) >= 1


@pytest.mark.asyncio
async def test_snapshot_restore_not_found():
    """恢复不存在的快照失败"""
    success = await snapshot_manager.restore_snapshot("nonexistent")
    assert success is False


@pytest.mark.asyncio
async def test_snapshot_to_dict():
    """测试快照序列化"""
    snapshot = await snapshot_manager.create_snapshot()
    d = snapshot.to_dict()
    assert "snapshot_id" in d
    assert "created_at" in d
    assert "graph_data" in d
    assert "documents" in d
    assert "rules" in d
    assert "stats" in d


# ========== API 端点测试 ==========


@pytest_asyncio.fixture
async def api_client():
    """创建API测试客户端（基于ASGITransport，不触发lifespan）"""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_api_list_versions_empty(api_client):
    """API: 列出无版本的Agent"""
    response = await api_client.get("/api/v1/versioning/agents/新Agent/versions")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["versions"] == []


@pytest.mark.asyncio
async def test_api_register_version(api_client):
    """API: 注册新版本"""
    response = await api_client.post(
        "/api/v1/versioning/agents/库存守护Agent/versions",
        json={
            "version": "1.0.0",
            "config": {"capabilities": ["inventory_monitoring"]},
            "rules": [{"rule_id": "r1", "name": "规则1"}],
            "changelog": "初始版本",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0.0"
    assert data["agent_name"] == "库存守护Agent"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_api_list_versions_after_register(api_client):
    """API: 注册后列出版本"""
    await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    response = await api_client.get("/api/v1/versioning/agents/测试Agent/versions")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["versions"][0]["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_api_activate_version(api_client):
    """API: 激活版本"""
    # 注册两个版本
    resp1 = await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    v1_id = resp1.json()["version_id"]
    resp2 = await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "2.0.0", "config": {}, "rules": []},
    )
    v2_id = resp2.json()["version_id"]

    # 激活 v2
    response = await api_client.post(f"/api/v1/versioning/versions/{v2_id}/activate")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "activated"

    # 验证激活版本
    list_resp = await api_client.get("/api/v1/versioning/agents/测试Agent/versions")
    assert list_resp.json()["active_version_id"] == v2_id


@pytest.mark.asyncio
async def test_api_activate_nonexistent_version(api_client):
    """API: 激活不存在的版本返回404"""
    response = await api_client.post("/api/v1/versioning/versions/nonexistent/activate")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_rollback_version(api_client):
    """API: 回滚版本"""
    await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    resp2 = await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "2.0.0", "config": {}, "rules": []},
    )
    v2_id = resp2.json()["version_id"]
    await api_client.post(f"/api/v1/versioning/versions/{v2_id}/activate")

    # 回滚
    response = await api_client.post("/api/v1/versioning/agents/测试Agent/rollback")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_api_canary_flow(api_client):
    """API: 灰度发布完整流程"""
    # 注册两个版本
    await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    resp2 = await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "2.0.0", "config": {"new_feature": True}, "rules": []},
    )
    v2_id = resp2.json()["version_id"]

    # 启动灰度
    start_resp = await api_client.post(
        f"/api/v1/versioning/versions/{v2_id}/canary",
        json={"traffic_percentage": 20, "duration_seconds": 60},
    )
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "running"

    # 查询灰度状态
    status_resp = await api_client.get(f"/api/v1/versioning/versions/{v2_id}/canary/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["active"] is True

    # 全量切换
    promote_resp = await api_client.post(f"/api/v1/versioning/versions/{v2_id}/canary/promote")
    assert promote_resp.status_code == 200
    assert promote_resp.json()["status"] == "promoted"


@pytest.mark.asyncio
async def test_api_canary_abort(api_client):
    """API: 中止灰度"""
    await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    resp2 = await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "2.0.0", "config": {}, "rules": []},
    )
    v2_id = resp2.json()["version_id"]
    await api_client.post(
        f"/api/v1/versioning/versions/{v2_id}/canary",
        json={"traffic_percentage": 20, "duration_seconds": 60},
    )

    abort_resp = await api_client.post(f"/api/v1/versioning/versions/{v2_id}/canary/abort")
    assert abort_resp.status_code == 200
    assert abort_resp.json()["status"] == "aborted"


@pytest.mark.asyncio
async def test_api_digital_twin_create_and_simulate(api_client):
    """API: 创建数字孪生并运行仿真"""
    # 注册版本
    resp = await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    version_id = resp.json()["version_id"]

    # 创建数字孪生
    twin_resp = await api_client.post(
        "/api/v1/versioning/digital-twin",
        json={"agent_name": "测试Agent", "version_id": version_id},
    )
    assert twin_resp.status_code == 200
    twin_id = twin_resp.json()["twin_id"]

    # 运行仿真
    sim_resp = await api_client.post(
        f"/api/v1/versioning/digital-twin/{twin_id}/simulate",
        json={},
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    assert sim_data["status"] == "completed"
    assert sim_data["summary"]["total_scenarios"] >= 1


@pytest.mark.asyncio
async def test_api_digital_twin_status(api_client):
    """API: 获取数字孪生状态"""
    resp = await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    version_id = resp.json()["version_id"]
    twin_resp = await api_client.post(
        "/api/v1/versioning/digital-twin",
        json={"agent_name": "测试Agent", "version_id": version_id},
    )
    twin_id = twin_resp.json()["twin_id"]

    status_resp = await api_client.get(f"/api/v1/versioning/digital-twin/{twin_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "created"


@pytest.mark.asyncio
async def test_api_snapshots(api_client):
    """API: 创建和列出知识库快照"""
    # 列出快照（初始为空）
    list_resp = await api_client.get("/api/v1/versioning/snapshots")
    assert list_resp.status_code == 200
    assert list_resp.json()["snapshots"] == []

    # 创建快照
    create_resp = await api_client.post("/api/v1/versioning/snapshots")
    assert create_resp.status_code == 200
    snapshot_id = create_resp.json()["snapshot_id"]

    # 再次列出
    list_resp2 = await api_client.get("/api/v1/versioning/snapshots")
    assert len(list_resp2.json()["snapshots"]) == 1

    # 获取快照详情
    get_resp = await api_client.get(f"/api/v1/versioning/snapshots/{snapshot_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["snapshot_id"] == snapshot_id


@pytest.mark.asyncio
async def test_api_snapshot_restore(api_client):
    """API: 恢复知识库快照"""
    create_resp = await api_client.post("/api/v1/versioning/snapshots")
    snapshot_id = create_resp.json()["snapshot_id"]

    restore_resp = await api_client.post(f"/api/v1/versioning/snapshots/{snapshot_id}/restore")
    assert restore_resp.status_code == 200
    assert restore_resp.json()["status"] == "restored"


@pytest.mark.asyncio
async def test_api_versioning_stats(api_client):
    """API: 版本化系统统计"""
    await api_client.post(
        "/api/v1/versioning/agents/测试Agent/versions",
        json={"version": "1.0.0", "config": {}, "rules": []},
    )
    response = await api_client.get("/api/v1/versioning/stats")
    assert response.status_code == 200
    data = response.json()
    assert "version_registry" in data
    assert data["version_registry"]["total_versions"] >= 1


# ========== 配置测试 ==========


def test_versioning_config_defaults():
    """测试版本化配置默认值"""
    assert settings.versioning_enabled is True
    assert settings.canary_default_traffic == 10
    assert settings.canary_success_threshold == 0.95
    assert settings.canary_error_rate_threshold == 0.05
    assert settings.canary_duration_seconds == 3600
    assert settings.digital_twin_enabled is True
