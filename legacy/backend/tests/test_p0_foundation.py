"""
P0 根基稳固测试
验证事件溯源重放（P0-1）与 Saga 跨 Agent 事务验证（P0-2）
"""
import asyncio
import pytest

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.config import AgentState
from app.core.event.bus import event_bus
from app.core.event.store import event_store
from app.core.event.models import Event, EventType
from app.core.event.saga import Saga, SagaStep, SagaState, SagaCoordinator
from app.core.security.did import did_registry


# ========== 测试用 Agent 子类 ==========

class ReplayTestAgent(BaseAgent):
    """用于测试事件溯源的 Agent 子类"""

    def __init__(self, **kwargs):
        super().__init__(
            name="测试Agent",
            domain="test",
            capabilities=["test_capability"],
            security_clearance="internal",
            **kwargs,
        )
        self.custom_state: dict = {}
        self.events_applied: list[str] = []

    @property
    def description(self) -> str:
        return "测试Agent"

    async def initialize(self):
        pass

    async def process_event(self, event: Event):
        pass

    async def make_decision(self, context: dict) -> AgentDecision:
        return AgentDecision(
            decision_type="test",
            reasoning="test reasoning",
            action="test action",
            confidence=0.9,
        )

    async def apply_event(self, event: Event):
        """覆盖：恢复自定义状态"""
        await super().apply_event(event)
        self.events_applied.append(event.event_type.value)
        if event.event_type == EventType.AGENT_DECISION_MADE:
            decision_type = event.event_data.get("decision_type", "unknown")
            self.custom_state[decision_type] = event.event_data.get("action", "")

    def _get_snapshot_state(self) -> dict:
        state = super()._get_snapshot_state()
        state["custom_state"] = self.custom_state
        return state

    def _restore_from_snapshot(self, state: dict):
        super()._restore_from_snapshot(state)
        self.custom_state = state.get("custom_state", {})


class StatefulAgent(BaseAgent):
    """带自定义业务状态的 Agent，用于验证重放恢复"""

    def __init__(self, name="状态Agent", domain="finance", **kwargs):
        super().__init__(
            name=name,
            domain=domain,
            capabilities=["stateful"],
            **kwargs,
        )
        self.balance: float = 0.0
        self.transactions: list[dict] = []

    @property
    def description(self) -> str:
        return "有状态Agent"

    async def initialize(self):
        pass

    async def process_event(self, event: Event):
        pass

    async def make_decision(self, context: dict) -> AgentDecision:
        return AgentDecision("noop", "noop", "noop", 1.0)

    async def apply_event(self, event: Event):
        await super().apply_event(event)
        if event.event_type == EventType.AGENT_DECISION_MADE:
            amount = event.event_data.get("amount", 0)
            tx_type = event.event_data.get("tx_type", "unknown")
            self.balance += amount
            self.transactions.append({"type": tx_type, "amount": amount})

    def _get_snapshot_state(self) -> dict:
        state = super()._get_snapshot_state()
        state["balance"] = self.balance
        state["transactions"] = list(self.transactions)
        return state

    def _restore_from_snapshot(self, state: dict):
        super()._restore_from_snapshot(state)
        self.balance = state.get("balance", 0.0)
        self.transactions = list(state.get("transactions", []))

    async def record_transaction(self, tx_type: str, amount: float):
        """记录一笔交易：更新本地状态 + 发布事件（事件溯源模式）"""
        signed = amount if tx_type == "deposit" else -amount
        # 命令方法直接更新状态
        self.balance += signed
        self.transactions.append({"type": tx_type, "amount": signed})
        # 发布事件持久化
        await self._publish_event(
            EventType.AGENT_DECISION_MADE,
            {
                "decision_type": tx_type,
                "reasoning": f"交易 {tx_type}",
                "action": f"金额 {signed}",
                "confidence": 1.0,
                "tx_type": tx_type,
                "amount": signed,
            },
        )


# ========== P0-1: 事件溯源重放测试 ==========

async def test_deterministic_agent_id():
    """Agent ID 基于 name+domain 确定性生成，重启后不变"""
    agent1 = ReplayTestAgent()
    agent2 = ReplayTestAgent()

    assert agent1.agent_id == agent2.agent_id, \
        "相同 name+domain 的 Agent 应有相同的 agent_id"

    agent3 = StatefulAgent(name="状态Agent", domain="finance")
    agent4 = StatefulAgent(name="状态Agent", domain="finance")
    assert agent3.agent_id == agent4.agent_id

    agent5 = StatefulAgent(name="不同Agent", domain="finance")
    assert agent3.agent_id != agent5.agent_id


async def test_event_store_persistence_on_publish():
    """Agent 发布事件后自动持久化到事件存储"""
    agent = ReplayTestAgent()
    aggregate_id = f"agent:{agent.agent_id}"

    agent.identity = did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities, security_clearance="internal",
    )
    await agent._publish_event(
        EventType.AGENT_DECISION_MADE,
        {"decision_type": "test_decision", "action": "test_action", "confidence": 0.9},
    )

    events = await event_store.get_events(aggregate_id)
    assert len(events) == 1
    assert events[0].event_type == EventType.AGENT_DECISION_MADE
    assert events[0].version == 1


async def test_event_replay_restores_state():
    """Agent 重启后通过事件重放恢复状态"""
    agent1 = StatefulAgent(name="重放测试", domain="finance")
    agent1.identity = did_registry.register_agent(
        name=agent1.name, domain=agent1.domain,
        capabilities=agent1.capabilities, security_clearance="internal",
    )

    await agent1.record_transaction("deposit", 100.0)
    await agent1.record_transaction("withdraw", 30.0)
    await agent1.record_transaction("deposit", 50.0)

    aggregate_id = f"agent:{agent1.agent_id}"
    events = await event_store.get_events(aggregate_id)
    assert len(events) == 3
    assert agent1.balance == 120.0  # 100 - 30 + 50
    assert len(agent1.transactions) == 3

    # 新实例：同 name+domain → 相同 agent_id
    agent2 = StatefulAgent(name="重放测试", domain="finance")
    assert agent2.agent_id == agent1.agent_id

    await agent2.replay_from_event_store()

    assert agent2.balance == 120.0, \
        f"重放后 balance 应为 120.0，实际 {agent2.balance}"
    assert len(agent2.transactions) == 3
    assert agent2.transactions[0] == {"type": "deposit", "amount": 100.0}
    assert agent2.transactions[1] == {"type": "withdraw", "amount": -30.0}
    assert agent2.transactions[2] == {"type": "deposit", "amount": 50.0}
    assert agent2._replayed is True
    assert agent2._state_version == 3


async def test_snapshot_load_and_incremental_replay():
    """快照加载 + 增量事件重放"""
    agent1 = StatefulAgent(name="快照测试", domain="finance")
    agent1.identity = did_registry.register_agent(
        name=agent1.name, domain=agent1.domain,
        capabilities=agent1.capabilities, security_clearance="internal",
    )

    await agent1.record_transaction("deposit", 100.0)
    await agent1.record_transaction("deposit", 200.0)
    await agent1.record_transaction("withdraw", 50.0)

    assert agent1.balance == 250.0  # 100 + 200 - 50
    await agent1.save_state_snapshot()

    await agent1.record_transaction("deposit", 30.0)
    await agent1.record_transaction("withdraw", 10.0)

    assert agent1.balance == 270.0  # 250 + 30 - 10

    # 新实例：先加载快照（balance=250），再增量重放 2 个事件（+30, -10）
    agent2 = StatefulAgent(name="快照测试", domain="finance")
    await agent2.replay_from_event_store()

    assert agent2.balance == 270.0, \
        f"快照+增量重放后 balance 应为 270.0，实际 {agent2.balance}"
    assert len(agent2.transactions) == 5
    assert agent2._state_version == 5


async def test_no_history_first_run():
    """首次运行（无历史事件）不报错"""
    agent = StatefulAgent(name="首次运行", domain="finance")
    await agent.replay_from_event_store()

    assert agent.balance == 0.0
    assert len(agent.transactions) == 0
    assert agent._replayed is False


async def test_state_version_increments():
    """_publish_event 递增版本号且写入事件"""
    agent = ReplayTestAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities, security_clearance="internal",
    )

    assert agent._state_version == 0

    await agent._publish_event(EventType.AGENT_REGISTERED, {"test": 1})
    assert agent._state_version == 1

    await agent._publish_event(EventType.AGENT_STATE_CHANGED, {"state": "running"})
    assert agent._state_version == 2

    await agent._publish_event(EventType.AGENT_DECISION_MADE, {"decision": "test"})
    assert agent._state_version == 3

    events = await event_store.get_events(f"agent:{agent.agent_id}")
    versions = [e.version for e in events]
    assert versions == [1, 2, 3]


async def test_get_status_includes_event_sourcing():
    """get_status 包含事件溯源信息"""
    agent = ReplayTestAgent()
    status = agent.get_status()

    assert "event_sourcing" in status
    assert status["event_sourcing"]["state_version"] == 0
    assert status["event_sourcing"]["replayed"] is False
    assert status["event_sourcing"]["events_since_snapshot"] == 0


# ========== P0-2: Saga 跨 Agent 事务验证测试 ==========

async def test_cross_agent_saga_with_compensation():
    """跨 Agent Saga 事务：成功执行 + 补偿回滚"""
    agent_states = {
        "inventory": {"stock": 100, "reserved": 0},
        "finance": {"balance": 5000, "held": 0},
        "logistics": {"shipments": []},
    }

    async def reserve_inventory():
        agent_states["inventory"]["stock"] -= 10
        agent_states["inventory"]["reserved"] += 10
        return "reserved_10"

    async def release_inventory():
        agent_states["inventory"]["stock"] += 10
        agent_states["inventory"]["reserved"] -= 10

    async def hold_payment():
        agent_states["finance"]["balance"] -= 500
        agent_states["finance"]["held"] += 500
        return "held_500"

    async def release_payment():
        agent_states["finance"]["balance"] += 500
        agent_states["finance"]["held"] -= 500

    async def create_shipment():
        agent_states["logistics"]["shipments"].append({"id": "S001", "status": "created"})
        return "shipment_S001"

    async def cancel_shipment():
        for s in agent_states["logistics"]["shipments"]:
            if s["id"] == "S001":
                s["status"] = "cancelled"

    # --- 场景1：全部成功 ---
    saga_ok = Saga(name="跨Agent订单流程")
    saga_ok.add_step(SagaStep("reserve_inventory", reserve_inventory, release_inventory))
    saga_ok.add_step(SagaStep("hold_payment", hold_payment, release_payment))
    saga_ok.add_step(SagaStep("create_shipment", create_shipment, cancel_shipment))

    success = await saga_ok.execute()
    assert success is True
    assert saga_ok.state == SagaState.COMPLETED

    assert agent_states["inventory"]["stock"] == 90
    assert agent_states["inventory"]["reserved"] == 10
    assert agent_states["finance"]["balance"] == 4500
    assert agent_states["finance"]["held"] == 500
    assert len(agent_states["logistics"]["shipments"]) == 1
    assert agent_states["logistics"]["shipments"][0]["status"] == "created"

    # --- 重置状态，场景2：第三步失败，补偿回滚 ---
    agent_states = {
        "inventory": {"stock": 100, "reserved": 0},
        "finance": {"balance": 5000, "held": 0},
        "logistics": {"shipments": []},
    }

    async def fail_shipment():
        raise RuntimeError("物流系统不可用")

    saga_fail = Saga(name="跨Agent订单流程-失败")
    saga_fail.add_step(SagaStep("reserve_inventory", reserve_inventory, release_inventory))
    saga_fail.add_step(SagaStep("hold_payment", hold_payment, release_payment))
    saga_fail.add_step(SagaStep("create_shipment", fail_shipment, cancel_shipment))

    success = await saga_fail.execute()
    assert success is False
    assert saga_fail.state == SagaState.COMPENSATED

    # 验证补偿后状态回滚到初始值
    assert agent_states["inventory"]["stock"] == 100, \
        f"补偿后库存应恢复到 100，实际 {agent_states['inventory']['stock']}"
    assert agent_states["inventory"]["reserved"] == 0
    assert agent_states["finance"]["balance"] == 5000, \
        f"补偿后余额应恢复到 5000，实际 {agent_states['finance']['balance']}"
    assert agent_states["finance"]["held"] == 0


async def test_saga_compensation_reverse_order():
    """Saga 补偿严格按逆序执行"""
    execution_log: list[str] = []

    async def step1_action():
        execution_log.append("step1_do")
        return "r1"

    async def step1_comp():
        execution_log.append("step1_undo")

    async def step2_action():
        execution_log.append("step2_do")
        return "r2"

    async def step2_comp():
        execution_log.append("step2_undo")

    async def step3_action():
        execution_log.append("step3_do")
        return "r3"

    async def step3_comp():
        execution_log.append("step3_undo")

    async def step4_action():
        execution_log.append("step4_do")
        raise RuntimeError("step4 failed")

    async def step4_comp():
        execution_log.append("step4_undo")

    saga = Saga(name="逆序补偿验证")
    saga.add_step(SagaStep("s1", step1_action, step1_comp))
    saga.add_step(SagaStep("s2", step2_action, step2_comp))
    saga.add_step(SagaStep("s3", step3_action, step3_comp))
    saga.add_step(SagaStep("s4", step4_action, step4_comp))

    await saga.execute()

    # 执行顺序：s1_do, s2_do, s3_do, s4_do(fail), s3_undo, s2_undo, s1_undo
    assert execution_log == [
        "step1_do", "step2_do", "step3_do", "step4_do",
        "step3_undo", "step2_undo", "step1_undo",
    ]


async def test_saga_persist_degrades_without_db():
    """Saga 持久化在无数据库时优雅降级"""
    saga = Saga(name="持久化降级测试")

    async def noop():
        return "ok"

    saga.add_step(SagaStep("step1", noop, None))

    # persist() 在数据库不可用时应仅记录警告，不抛异常
    await saga.persist()


async def test_saga_recover_returns_none_without_db():
    """Saga 恢复在无数据库时返回 None"""
    result = await Saga.recover("nonexistent-saga-id")
    assert result is None


async def test_saga_coordinator_recovery_without_db():
    """Saga 协调器恢复在无数据库时返回空列表"""
    coordinator = SagaCoordinator()
    result = await coordinator.recover_incomplete_sagas()
    assert result == []


async def test_saga_step_to_dict():
    """SagaStep.to_dict 正确序列化"""
    async def action():
        return "result"

    async def compensation():
        pass

    step = SagaStep("test_step", action, compensation, is_critical=True)
    step.result = "test_result"
    step.executed = True

    d = step.to_dict()
    assert d["name"] == "test_step"
    assert d["is_critical"] is True
    assert d["executed"] is True
    assert d["result"] == "test_result"


async def test_saga_results_preserved():
    """Saga 执行结果在完成后保留"""
    async def action1():
        return {"data": "value1"}

    async def action2():
        return {"data": "value2"}

    saga = Saga(name="结果保留测试")
    saga.add_step(SagaStep("step1", action1))
    saga.add_step(SagaStep("step2", action2))

    await saga.execute()

    assert saga.results["step1"] == {"data": "value1"}
    assert saga.results["step2"] == {"data": "value2"}


async def test_saga_empty_steps():
    """空步骤 Saga 直接完成"""
    saga = Saga(name="空Saga")
    success = await saga.execute()

    assert success is True
    assert saga.state == SagaState.COMPLETED
    assert saga.current_step_index == -1


async def test_saga_coordinator_tracks_completion(fresh_coordinator):
    """协调器正确追踪完成状态"""
    coordinator = fresh_coordinator

    async def quick_action():
        return "done"

    for i in range(3):
        saga = Saga(name=f"Saga-{i}")
        saga.add_step(SagaStep("step", quick_action))
        await coordinator.execute_saga(saga)

    stats = coordinator.get_stats()
    assert stats["completed"] == 3
    assert stats["successful"] == 3
    assert stats["failed"] == 0
    assert stats["active"] == 0
    assert stats["success_rate"] == 1.0
