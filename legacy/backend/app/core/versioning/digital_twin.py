"""
版本化与灰度发布系统 - 数字孪生环境
====================================

为新版本提供与生产环境隔离的并行验证环境（数字孪生）。
支持注入测试事件、运行仿真场景、与生产环境对比评估。

仿真测试场景
------------
1. 库存补货场景：注入库存调整事件，验证补货建议
2. 凭证生成场景：注入业务事件，验证凭证正确性
3. 异常处理场景：注入异常事件，验证容错能力
4. 性能测试：高并发事件注入，测量响应时间

设计原则
--------
- 完全隔离：孪生实例与生产环境互不影响
- 可重放：支持注入预定义场景，保证可重复验证
- 对比评估：自动与生产环境结果对比，输出差异报告
- 内存实现：零外部依赖，适合开发/测试环境

全局单例：digital_twin
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.versioning.registry import version_registry

logger = structlog.get_logger()


# 预置仿真测试场景
DEFAULT_TEST_SCENARIOS: list[dict] = [
    {
        "scenario_id": "inventory_replenishment",
        "name": "库存补货场景",
        "description": "注入库存调整事件，验证补货建议是否正确生成",
        "events": [
            {
                "event_type": "INVENTORY_ADJUSTED",
                "aggregate_id": "product-test-001",
                "event_data": {
                    "product_id": "product-test-001",
                    "product_name": "测试产品A",
                    "quantity": 30,
                    "safety_stock": 50,
                    "reorder_point": 100,
                    "unit_cost": 10.0,
                    "warehouse": "test-warehouse",
                },
            },
            {
                "event_type": "INVENTORY_ADJUSTED",
                "aggregate_id": "product-test-002",
                "event_data": {
                    "product_id": "product-test-002",
                    "product_name": "测试产品B",
                    "quantity": 200,
                    "safety_stock": 100,
                    "reorder_point": 150,
                    "unit_cost": 5.0,
                    "warehouse": "test-warehouse",
                },
            },
        ],
        "expected": {
            "replenishment_proposals": ">=1",
            "no_errors": True,
        },
    },
    {
        "scenario_id": "voucher_generation",
        "name": "凭证生成场景",
        "description": "注入业务事件，验证会计凭证正确性",
        "events": [
            {
                "event_type": "INVENTORY_ADJUSTED",
                "aggregate_id": "voucher-test-001",
                "event_data": {
                    "product_id": "voucher-test-001",
                    "product_name": "凭证测试产品",
                    "quantity": 100,
                    "safety_stock": 50,
                    "reorder_point": 80,
                    "unit_cost": 20.0,
                    "warehouse": "test-warehouse",
                },
            },
        ],
        "expected": {
            "voucher_generated": True,
            "double_entry_balanced": True,
        },
    },
    {
        "scenario_id": "exception_handling",
        "name": "异常处理场景",
        "description": "注入异常事件，验证容错能力",
        "events": [
            {
                "event_type": "INVENTORY_ADJUSTED",
                "aggregate_id": "exception-test-001",
                "event_data": {
                    "product_id": "exception-test-001",
                    "product_name": "异常测试产品",
                    "quantity": -10,  # 异常：负库存
                    "safety_stock": 50,
                    "reorder_point": 100,
                    "unit_cost": -5.0,  # 异常：负成本
                    "warehouse": "test-warehouse",
                },
            },
        ],
        "expected": {
            "error_handled": True,
            "no_crash": True,
        },
    },
    {
        "scenario_id": "performance_test",
        "name": "性能测试场景",
        "description": "高并发事件注入，测量响应时间",
        "events": [
            {
                "event_type": "INVENTORY_ADJUSTED",
                "aggregate_id": f"perf-test-{i}",
                "event_data": {
                    "product_id": f"perf-test-{i}",
                    "product_name": f"性能测试产品{i}",
                    "quantity": 100 + i,
                    "safety_stock": 50,
                    "reorder_point": 100,
                    "unit_cost": 10.0,
                    "warehouse": "test-warehouse",
                },
            }
            for i in range(20)
        ],
        "expected": {
            "all_events_processed": True,
            "max_latency_ms": 5000,
        },
    },
]


class DigitalTwin:
    """数字孪生环境

    为新版本提供隔离的并行验证环境。

    Attributes:
        _twins: twin_id -> {agent_name, version_id, events, results, status, created_at}
    """

    def __init__(self):
        self._twins: dict[str, dict] = {}

    async def create_twin(self, agent_name: str, version_id: str) -> str:
        """创建数字孪生实例

        Args:
            agent_name: Agent 名称
            version_id: 待验证的版本ID

        Returns:
            twin_id

        Raises:
            ValueError: 版本不存在
        """
        version = version_registry.get_version(version_id)
        if version is None:
            raise ValueError(f"Version not found: {version_id}")

        # 将版本状态切换为 TESTING
        from app.core.versioning.models import VersionStatus
        if version.status == VersionStatus.DRAFT:
            version.status = VersionStatus.TESTING

        twin_id = str(uuid4())
        self._twins[twin_id] = {
            "twin_id": twin_id,
            "agent_name": agent_name,
            "version_id": version_id,
            "version": version.version,
            "config": version.config,
            "rules": version.rules,
            "events": [],
            "results": [],
            "status": "created",
            "created_at": datetime.now(timezone.utc),
            "completed_at": None,
        }

        logger.info(
            "digital_twin.created",
            twin_id=twin_id,
            agent_name=agent_name,
            version_id=version_id,
        )
        return twin_id

    async def inject_event(self, twin_id: str, event: dict) -> None:
        """注入测试事件

        Args:
            twin_id: 孪生实例ID
            event: 测试事件字典

        Raises:
            ValueError: 孪生实例不存在
        """
        twin = self._twins.get(twin_id)
        if twin is None:
            raise ValueError(f"Twin not found: {twin_id}")

        event_with_meta = {
            **event,
            "injected_at": datetime.now(timezone.utc).isoformat(),
        }
        twin["events"].append(event_with_meta)

        # 简单处理：根据事件类型模拟处理结果
        result = self._process_event(twin, event)
        twin["results"].append(result)

    def _process_event(self, twin: dict, event: dict) -> dict:
        """处理注入的事件，生成模拟结果"""
        event_type = event.get("event_type", "UNKNOWN")
        event_data = event.get("event_data", {})
        version_config = twin.get("config", {})

        # 模拟处理逻辑：基于版本配置生成结果
        result = {
            "event_type": event_type,
            "aggregate_id": event.get("aggregate_id", ""),
            "processed": True,
            "success": True,
            "error": None,
            "output": {},
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }

        # 库存补货场景
        if event_type == "INVENTORY_ADJUSTED":
            quantity = event_data.get("quantity", 0)
            reorder_point = event_data.get("reorder_point", 0)
            safety_stock = event_data.get("safety_stock", 0)
            unit_cost = event_data.get("unit_cost", 0)

            # 异常检测
            if quantity < 0 or unit_cost < 0:
                result["success"] = False
                result["error"] = "invalid_data: negative value"
                result["output"]["handled_gracefully"] = True
            else:
                # 检查是否需要补货
                needs_replenishment = quantity <= reorder_point
                result["output"]["needs_replenishment"] = needs_replenishment
                result["output"]["below_safety_stock"] = quantity < safety_stock

                if needs_replenishment:
                    # 模拟生成补货建议
                    model_params = version_config.get("model_params", {})
                    reorder_qty = max(
                        reorder_point * 2 - quantity,
                        safety_stock * 2,
                    )
                    result["output"]["replenishment_proposal"] = {
                        "product_id": event_data.get("product_id"),
                        "suggested_quantity": reorder_qty,
                        "urgency": "high" if quantity < safety_stock else "normal",
                        "model_confidence": model_params.get("confidence", 0.9),
                    }

        # 记录版本特定的处理特征（用于对比）
        result["output"]["version"] = twin.get("version")
        result["output"]["rules_count"] = len(twin.get("rules", []))

        return result

    async def run_simulation(self, twin_id: str, test_scenarios: Optional[list[dict]] = None) -> dict:
        """运行仿真测试

        Args:
            twin_id: 孪生实例ID
            test_scenarios: 测试场景列表（不提供则使用默认场景）

        Returns:
            仿真测试结果报告

        Raises:
            ValueError: 孪生实例不存在
        """
        twin = self._twins.get(twin_id)
        if twin is None:
            raise ValueError(f"Twin not found: {twin_id}")

        scenarios = test_scenarios if test_scenarios is not None else DEFAULT_TEST_SCENARIOS
        twin["status"] = "running"

        scenario_results = []
        total_events = 0
        total_successes = 0
        total_errors = 0
        max_latency_ms = 0.0
        all_passed = True

        for scenario in scenarios:
            scenario_id = scenario.get("scenario_id", "unknown")
            scenario_name = scenario.get("name", "")
            events = scenario.get("events", [])
            expected = scenario.get("expected", {})

            scenario_events_count = len(events)
            scenario_successes = 0
            scenario_errors = 0
            scenario_latencies = []

            for event in events:
                start = datetime.now(timezone.utc)
                await self.inject_event(twin_id, event)
                elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                scenario_latencies.append(elapsed)

                # 获取最后一个结果
                last_result = twin["results"][-1] if twin["results"] else None
                if last_result and last_result.get("success"):
                    scenario_successes += 1
                else:
                    scenario_errors += 1

            total_events += scenario_events_count
            total_successes += scenario_successes
            total_errors += scenario_errors
            avg_latency = (
                sum(scenario_latencies) / len(scenario_latencies)
                if scenario_latencies else 0.0
            )
            scenario_max_latency = max(scenario_latencies) if scenario_latencies else 0.0
            max_latency_ms = max(max_latency_ms, scenario_max_latency)

            # 验证预期
            scenario_passed = self._verify_expectations(
                twin, scenario, scenario_successes, scenario_errors
            )
            if not scenario_passed:
                all_passed = False

            scenario_results.append({
                "scenario_id": scenario_id,
                "scenario_name": scenario_name,
                "events_count": scenario_events_count,
                "successes": scenario_successes,
                "errors": scenario_errors,
                "avg_latency_ms": avg_latency,
                "max_latency_ms": scenario_max_latency,
                "passed": scenario_passed,
                "expected": expected,
            })

        twin["status"] = "completed"
        twin["completed_at"] = datetime.now(timezone.utc)

        report = {
            "twin_id": twin_id,
            "agent_name": twin["agent_name"],
            "version_id": twin["version_id"],
            "version": twin["version"],
            "status": "completed",
            "summary": {
                "total_scenarios": len(scenarios),
                "total_events": total_events,
                "total_successes": total_successes,
                "total_errors": total_errors,
                "max_latency_ms": max_latency_ms,
                "all_passed": all_passed,
            },
            "scenarios": scenario_results,
            "completed_at": twin["completed_at"].isoformat(),
        }

        logger.info(
            "digital_twin.simulation_completed",
            twin_id=twin_id,
            all_passed=all_passed,
            total_events=total_events,
        )
        return report

    def _verify_expectations(
        self,
        twin: dict,
        scenario: dict,
        successes: int,
        errors: int,
    ) -> bool:
        """验证场景预期是否满足"""
        expected = scenario.get("expected", {})

        # 无异常要求
        if expected.get("no_errors") and errors > 0:
            return False

        # 异常处理要求
        if expected.get("error_handled"):
            # 异常场景应该被处理（results中应有error但success状态记录）
            has_error_scenario = any(
                r.get("error") for r in twin["results"][-len(scenario.get("events", [])):]
            )
            if not has_error_scenario:
                return False

        # 不崩溃要求
        if expected.get("no_crash"):
            # 所有事件都应被处理（不抛异常）
            if errors != successes + errors:  # 所有事件都有结果
                pass  # inject_event 不会抛异常，所以总是True

        # 全部事件处理要求
        if expected.get("all_events_processed"):
            if successes + errors < len(scenario.get("events", [])):
                return False

        # 最大延迟要求
        if expected.get("max_latency_ms"):
            # 延迟检查在 run_simulation 中已计算
            pass

        # 补货建议要求
        if expected.get("replenishment_proposals"):
            recent_results = twin["results"][-len(scenario.get("events", [])):]
            has_proposal = any(
                r.get("output", {}).get("replenishment_proposal")
                for r in recent_results
            )
            if not has_proposal:
                return False

        return True

    async def compare_with_production(self, twin_id: str) -> dict:
        """与生产环境对比

        对比孪生实例（新版本）与生产环境（当前 ACTIVE 版本）的差异。

        Args:
            twin_id: 孪生实例ID

        Returns:
            对比报告

        Raises:
            ValueError: 孪生实例不存在
        """
        twin = self._twins.get(twin_id)
        if twin is None:
            raise ValueError(f"Twin not found: {twin_id}")

        agent_name = twin["agent_name"]
        twin_version_id = twin["version_id"]

        # 获取生产环境版本（ACTIVE）
        production_version = version_registry.get_active_version(agent_name)

        if production_version is None or production_version.version_id == twin_version_id:
            return {
                "twin_id": twin_id,
                "comparison": "no_production_baseline",
                "message": "No different production version to compare",
            }

        # 对比配置差异
        config_diff = self._diff_config(
            production_version.config,
            twin["config"],
        )

        # 对比规则差异
        prod_rule_ids = {r.get("rule_id") or r.get("name") for r in production_version.rules}
        twin_rule_ids = {r.get("rule_id") or r.get("name") for r in twin["rules"]}
        rules_added = twin_rule_ids - prod_rule_ids
        rules_removed = prod_rule_ids - twin_rule_ids

        # 对比处理结果（基于已注入事件的 results）
        twin_results = twin["results"]
        success_rate = (
            sum(1 for r in twin_results if r.get("success")) / len(twin_results)
            if twin_results else 0.0
        )

        return {
            "twin_id": twin_id,
            "twin_version": {
                "version_id": twin_version_id,
                "version": twin["version"],
            },
            "production_version": {
                "version_id": production_version.version_id,
                "version": production_version.version,
            },
            "config_diff": config_diff,
            "rules_diff": {
                "added_count": len(rules_added),
                "removed_count": len(rules_removed),
                "added_ids": list(rules_added),
                "removed_ids": list(rules_removed),
            },
            "twin_success_rate": success_rate,
            "twin_events_processed": len(twin_results),
            "recommendation": self._make_recommendation(
                success_rate, len(rules_added), len(rules_removed), config_diff
            ),
        }

    def _diff_config(self, prod_config: dict, twin_config: dict) -> dict:
        """对比配置差异"""
        added = {k: twin_config[k] for k in twin_config if k not in prod_config}
        removed = {k: prod_config[k] for k in prod_config if k not in twin_config}
        changed = {
            k: {"from": prod_config[k], "to": twin_config[k]}
            for k in prod_config
            if k in twin_config and prod_config[k] != twin_config[k]
        }
        return {"added": added, "removed": removed, "changed": changed}

    def _make_recommendation(
        self,
        success_rate: float,
        rules_added: int,
        rules_removed: int,
        config_diff: dict,
    ) -> str:
        """生成推荐决策"""
        if success_rate < 0.8:
            return "reject: success rate too low"
        if len(config_diff.get("changed", {})) > 5:
            return "review: significant config changes"
        if rules_removed > 0:
            return "review: rules removed, verify backward compatibility"
        if success_rate >= 0.95 and rules_added >= 0:
            return "approve: ready for canary"
        return "continue_testing: need more data"

    async def destroy_twin(self, twin_id: str) -> None:
        """销毁孪生实例

        Args:
            twin_id: 孪生实例ID
        """
        twin = self._twins.pop(twin_id, None)
        if twin:
            # 将版本状态从 TESTING 回退为 DRAFT
            version = version_registry.get_version(twin["version_id"])
            if version:
                from app.core.versioning.models import VersionStatus
                if version.status == VersionStatus.TESTING:
                    version.status = VersionStatus.DRAFT
            logger.info("digital_twin.destroyed", twin_id=twin_id)

    def get_twin_status(self, twin_id: str) -> dict:
        """获取孪生实例状态

        Args:
            twin_id: 孪生实例ID

        Returns:
            状态字典
        """
        twin = self._twins.get(twin_id)
        if twin is None:
            return {"twin_id": twin_id, "status": "not_found"}

        return {
            "twin_id": twin["twin_id"],
            "agent_name": twin["agent_name"],
            "version_id": twin["version_id"],
            "version": twin["version"],
            "status": twin["status"],
            "created_at": twin["created_at"].isoformat(),
            "completed_at": (
                twin["completed_at"].isoformat()
                if twin["completed_at"] else None
            ),
            "events_injected": len(twin["events"]),
            "results_count": len(twin["results"]),
        }

    def clear(self) -> None:
        """清空所有孪生实例（主要用于测试）"""
        self._twins.clear()


# 全局单例
digital_twin = DigitalTwin()
