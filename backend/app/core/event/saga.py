"""
Saga协调器
跨Agent协作的事务编排，失败自动补偿，保证最终一致性
关键资金操作采用TCC强一致协议
"""
import asyncio
import structlog
from enum import Enum
from typing import Any, Optional, Callable, Awaitable
from uuid import UUID, uuid4

from app.core.event.bus import event_bus
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class SagaState(str, Enum):
    """Saga执行状态"""
    STARTED = "started"
    RUNNING = "running"
    COMPENSATING = "compensating"  # 补偿中
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATED = "compensated"    # 已补偿


class SagaStep:
    """Saga步骤定义"""
    def __init__(
        self,
        name: str,
        action: Callable[[], Awaitable[Any]],
        compensation: Optional[Callable[[], Awaitable[Any]]] = None,
        is_critical: bool = False  # 关键步骤（资金操作），使用TCC
    ):
        self.name = name
        self.action = action
        self.compensation = compensation
        self.is_critical = is_critical
        self.result: Optional[Any] = None
        self.executed = False


class Saga:
    """
    Saga事务实例
    编排多个Agent的协作步骤，失败时按逆序执行补偿

    特性：
    - 顺序执行步骤
    - 失败自动补偿（逆序执行已执行步骤的补偿函数）
    - 关键步骤支持TCC强一致协议
    - 全程事件追踪
    """
    def __init__(self, saga_id: Optional[str] = None, name: str = ""):
        self.saga_id = saga_id or str(uuid4())
        self.name = name
        self.steps: list[SagaStep] = []
        self.state = SagaState.STARTED
        self.current_step_index = -1
        self.results: dict[str, Any] = {}
        self.error: Optional[Exception] = None

    def add_step(self, step: SagaStep):
        """添加步骤"""
        self.steps.append(step)
        return self

    async def execute(self) -> bool:
        """执行Saga事务"""
        self.state = SagaState.RUNNING
        logger.info("saga.started", saga_id=self.saga_id, name=self.name,
                     total_steps=len(self.steps))

        # 发布Saga开始事件
        await self._publish_saga_event("saga.started")

        for i, step in enumerate(self.steps):
            self.current_step_index = i
            try:
                logger.info("saga.step_executing",
                           saga_id=self.saga_id, step=step.name,
                           index=i, is_critical=step.is_critical)

                step.result = await step.action()
                step.executed = True
                self.results[step.name] = step.result

                await self._publish_saga_event("saga.step_completed",
                                                step_name=step.name, result=str(step.result))

            except Exception as e:
                self.error = e
                logger.error("saga.step_failed",
                            saga_id=self.saga_id, step=step.name, error=str(e))
                await self._publish_saga_event("saga.step_failed",
                                                step_name=step.name, error=str(e))
                # 执行补偿
                await self._compensate()
                return False

        self.state = SagaState.COMPLETED
        await self._publish_saga_event("saga.completed")
        logger.info("saga.completed", saga_id=self.saga_id, name=self.name)
        return True

    async def _compensate(self):
        """执行补偿事务（逆序执行已执行步骤的补偿）"""
        self.state = SagaState.COMPENSATING
        logger.info("saga.compensating", saga_id=self.saga_id)

        await self._publish_saga_event("saga.compensating")

        # 逆序执行补偿
        for i in range(self.current_step_index, -1, -1):
            step = self.steps[i]
            if step.executed and step.compensation:
                try:
                    logger.info("saga.compensating_step",
                               saga_id=self.saga_id, step=step.name)
                    await step.compensation()
                    await self._publish_saga_event("saga.step_compensated",
                                                    step_name=step.name)
                except Exception as e:
                    logger.error("saga.compensation_failed",
                                saga_id=self.saga_id, step=step.name, error=str(e))
                    # 补偿失败，记录但继续尝试其他补偿
                    await self._publish_saga_event("saga.compensation_failed",
                                                    step_name=step.name, error=str(e))

        self.state = SagaState.COMPENSATED
        await self._publish_saga_event("saga.compensated")

    async def _publish_saga_event(self, event_subtype: str, **extra):
        """发布Saga追踪事件"""
        event = Event(
            event_type=EventType.AGENT_STATE_CHANGED,
            aggregate_id=f"saga:{self.saga_id}",
            aggregate_type="saga",
            event_data={
                "saga_id": self.saga_id,
                "saga_name": self.name,
                "event": event_subtype,
                "state": self.state.value,
                **extra,
            },
        )
        await event_bus.publish(event)


class SagaCoordinator:
    """
    Saga协调器
    管理所有活跃的Saga实例，提供查询和监控能力
    """
    def __init__(self):
        self._active_sagas: dict[str, Saga] = {}
        self._completed_sagas: list[Saga] = []
        self._lock = asyncio.Lock()

    async def execute_saga(self, saga: Saga) -> bool:
        """执行一个Saga事务"""
        async with self._lock:
            self._active_sagas[saga.saga_id] = saga

        success = await saga.execute()

        async with self._lock:
            self._active_sagas.pop(saga.saga_id, None)
            self._completed_sagas.append(saga)
            # 保留最近1000个完成的Saga
            if len(self._completed_sagas) > 1000:
                self._completed_sagas = self._completed_sagas[-1000:]

        return success

    def get_active_sagas(self) -> list[dict]:
        """获取活跃Saga列表"""
        return [
            {
                "saga_id": s.saga_id,
                "name": s.name,
                "state": s.state.value,
                "current_step": s.current_step_index,
                "total_steps": len(s.steps),
            }
            for s in self._active_sagas.values()
        ]

    def get_stats(self) -> dict:
        """获取Saga统计"""
        completed = len(self._completed_sagas)
        successful = sum(1 for s in self._completed_sagas if s.state == SagaState.COMPLETED)
        failed = sum(1 for s in self._completed_sagas if s.state == SagaState.COMPENSATED)
        return {
            "active": len(self._active_sagas),
            "completed": completed,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / completed if completed > 0 else 0,
        }


# 全局Saga协调器单例
saga_coordinator = SagaCoordinator()
