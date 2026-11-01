"""
Saga协调器测试
验证多步骤事务编排、失败自动补偿、统计信息
"""
import pytest

from app.core.event.saga import Saga, SagaStep, SagaState, SagaCoordinator


# ========== test_successful_saga ==========

async def test_successful_saga():
    """成功执行多步骤Saga"""
    execution_order: list[str] = []

    async def step1_action():
        execution_order.append("step1_action")
        return "result1"

    async def step2_action():
        execution_order.append("step2_action")
        return "result2"

    async def step3_action():
        execution_order.append("step3_action")
        return "result3"

    saga = Saga(name="成功Saga")
    saga.add_step(SagaStep("step1", step1_action))
    saga.add_step(SagaStep("step2", step2_action))
    saga.add_step(SagaStep("step3", step3_action))

    success = await saga.execute()

    assert success is True
    assert saga.state == SagaState.COMPLETED
    assert saga.error is None
    assert execution_order == ["step1_action", "step2_action", "step3_action"]

    # 验证结果存储
    assert saga.results["step1"] == "result1"
    assert saga.results["step2"] == "result2"
    assert saga.results["step3"] == "result3"

    # 验证所有步骤都执行了
    assert all(step.executed for step in saga.steps)


# ========== test_saga_compensation ==========

async def test_saga_compensation():
    """失败后自动补偿"""
    execution_order: list[str] = []

    async def step1_action():
        execution_order.append("step1_action")
        return "result1"

    async def step1_compensation():
        execution_order.append("step1_compensation")

    async def step2_action():
        execution_order.append("step2_action")
        return "result2"

    async def step2_compensation():
        execution_order.append("step2_compensation")

    async def step3_action():
        execution_order.append("step3_action")
        raise RuntimeError("第三步失败")

    async def step3_compensation():
        execution_order.append("step3_compensation")

    saga = Saga(name="补偿Saga")
    saga.add_step(SagaStep("step1", step1_action, step1_compensation))
    saga.add_step(SagaStep("step2", step2_action, step2_compensation))
    saga.add_step(SagaStep("step3", step3_action, step3_compensation))

    success = await saga.execute()

    assert success is False
    assert saga.state == SagaState.COMPENSATED
    assert isinstance(saga.error, RuntimeError)
    assert "第三步失败" in str(saga.error)

    # 验证执行顺序：前两步执行成功，第三步失败，然后逆序补偿
    assert "step1_action" in execution_order
    assert "step2_action" in execution_order
    assert "step3_action" in execution_order
    # 补偿应逆序执行：step2_compensation 在 step1_compensation 之前
    assert execution_order.index("step2_compensation") < execution_order.index("step1_compensation")
    # step3 未执行成功，不需要补偿
    assert "step3_compensation" not in execution_order

    # step1 和 step2 标记为已执行
    assert saga.steps[0].executed is True
    assert saga.steps[1].executed is True
    assert saga.steps[2].executed is False


# ========== test_saga_stats ==========

async def test_saga_stats(fresh_coordinator):
    """统计信息正确"""
    coordinator = fresh_coordinator

    # 成功的 Saga
    async def success_action():
        return "ok"

    saga_success = Saga(name="成功Saga")
    saga_success.add_step(SagaStep("step1", success_action))
    await coordinator.execute_saga(saga_success)

    # 失败的 Saga
    async def fail_action():
        raise ValueError("失败")

    async def compensate_action():
        pass

    saga_fail = Saga(name="失败Saga")
    saga_fail.add_step(SagaStep("step1", success_action, compensate_action))
    saga_fail.add_step(SagaStep("step2", fail_action, compensate_action))
    await coordinator.execute_saga(saga_fail)

    stats = coordinator.get_stats()
    assert stats["completed"] == 2
    assert stats["successful"] == 1
    assert stats["failed"] == 1
    assert stats["active"] == 0
    assert stats["success_rate"] == 0.5


# ========== 额外测试：单步Saga ==========

async def test_single_step_saga():
    """单步Saga执行"""
    async def only_action():
        return "only_result"

    saga = Saga(name="单步Saga")
    saga.add_step(SagaStep("only", only_action))

    success = await saga.execute()
    assert success is True
    assert saga.state == SagaState.COMPLETED
    assert saga.results["only"] == "only_result"


# ========== 额外测试：第一步就失败 ==========

async def test_first_step_failure():
    """第一步失败时无需补偿"""
    compensation_called: list[str] = []

    async def fail_action():
        raise RuntimeError("第一步就失败")

    async def compensation():
        compensation_called.append("compensation_called")

    saga = Saga(name="首步失败Saga")
    saga.add_step(SagaStep("step1", fail_action, compensation))

    success = await saga.execute()

    assert success is False
    assert saga.state == SagaState.COMPENSATED
    # 第一步未执行成功，无需补偿
    assert len(compensation_called) == 0


# ========== 额外测试：补偿函数本身失败 ==========

async def test_compensation_failure_continues():
    """补偿函数失败时继续执行其他补偿"""
    compensation_log: list[str] = []

    async def step1_action():
        return "ok"

    async def step1_compensation():
        compensation_log.append("step1_compensation")
        raise RuntimeError("补偿也失败")

    async def step2_action():
        return "ok"

    async def step2_compensation():
        compensation_log.append("step2_compensation")

    async def step3_action():
        raise RuntimeError("第三步失败")

    saga = Saga(name="补偿失败Saga")
    saga.add_step(SagaStep("step1", step1_action, step1_compensation))
    saga.add_step(SagaStep("step2", step2_action, step2_compensation))
    saga.add_step(SagaStep("step3", step3_action))

    success = await saga.execute()

    assert success is False
    assert saga.state == SagaState.COMPENSATED
    # 即使step1补偿失败，step2补偿仍应执行
    assert "step1_compensation" in compensation_log
    assert "step2_compensation" in compensation_log


# ========== 额外测试：关键步骤标记 ==========

async def test_critical_step_marked():
    """关键步骤标记正确"""
    async def action():
        return "ok"

    critical_step = SagaStep("critical", action, is_critical=True)
    normal_step = SagaStep("normal", action, is_critical=False)

    saga = Saga(name="关键步骤Saga")
    saga.add_step(critical_step)
    saga.add_step(normal_step)

    success = await saga.execute()

    assert success is True
    assert saga.steps[0].is_critical is True
    assert saga.steps[1].is_critical is False


# ========== 额外测试：协调器活跃Saga追踪 ==========

async def test_coordinator_active_sagas(fresh_coordinator):
    """协调器追踪活跃Saga"""
    coordinator = fresh_coordinator

    import asyncio

    async def slow_action():
        await asyncio.sleep(0.2)
        return "done"

    saga = Saga(name="慢Saga")
    saga.add_step(SagaStep("slow", slow_action))

    # 执行（同步等待完成）
    success = await coordinator.execute_saga(saga)

    assert success is True
    # 执行完成后不再有活跃Saga
    active = coordinator.get_active_sagas()
    assert len(active) == 0
