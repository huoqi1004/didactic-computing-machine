"""
Agent编排器
管理所有Agent的生命周期，协调多Agent协作
支持目标分解、任务分配、多Agent共识机制
"""
import asyncio
import structlog
from typing import Optional, Any
from uuid import uuid4

from app.core.config import settings
from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.bus import event_bus
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class Goal:
    """目标定义"""
    def __init__(self, title: str, description: str, kpi: Optional[dict] = None,
                 priority: int = 5, deadline: Optional[str] = None):
        self.goal_id = str(uuid4())
        self.title = title
        self.description = description
        self.kpi = kpi or {}
        self.priority = priority  # 1-10, 10最高
        self.deadline = deadline
        self.sub_goals: list[Goal] = []
        self.assigned_agents: list[str] = []
        self.status = "pending"  # pending, in_progress, completed, failed

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "kpi": self.kpi,
            "priority": self.priority,
            "deadline": self.deadline,
            "sub_goals": len(self.sub_goals),
            "assigned_agents": self.assigned_agents,
            "status": self.status,
        }


class AgentOrchestrator:
    """
    Agent编排器

    职责：
    - Agent注册与生命周期管理
    - 目标分解与任务分配
    - 多Agent共识投票
    - 跨Agent协作编排
    - 全局监控与调度
    """

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}  # agent_id -> agent
        self._agents_by_name: dict[str, BaseAgent] = {}
        self._goals: dict[str, Goal] = {}
        self._consensus_votes: dict[str, dict] = {}  # decision_id -> votes
        self._lock = asyncio.Lock()

    async def register_agent(self, agent: BaseAgent) -> str:
        """注册Agent"""
        self._agents[agent.agent_id] = agent
        self._agents_by_name[agent.name] = agent
        logger.info("orchestrator.agent_registered",
                    agent=agent.name, agent_id=agent.agent_id)
        return agent.agent_id

    async def start_agent(self, agent_id: str) -> bool:
        """启动Agent"""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        await agent.start()
        return True

    async def stop_agent(self, agent_id: str) -> bool:
        """停止Agent"""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        await agent.stop()
        return True

    async def stop_all(self):
        """停止所有Agent"""
        tasks = [agent.stop() for agent in self._agents.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("orchestrator.all_agents_stopped")

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """获取Agent"""
        return self._agents.get(agent_id)

    def get_agent_by_name(self, name: str) -> Optional[BaseAgent]:
        """按名称获取Agent"""
        return self._agents_by_name.get(name)

    def list_agents(self) -> list[dict]:
        """列出所有Agent状态"""
        return [agent.get_status() for agent in self._agents.values()]

    async def decompose_goal(self, goal: Goal, agent_capabilities: dict[str, list[str]]) -> Goal:
        """
        目标分解 - 将高层目标分解为子目标并分配给合适的Agent

        参数:
        - goal: 要分解的目标
        - agent_capabilities: {agent_name: [capabilities]}
        """
        logger.info("orchestrator.decomposing_goal",
                    goal_id=goal.goal_id, title=goal.title)

        # 简化的目标分解逻辑（生产环境将使用LLM进行智能分解）
        # 根据目标描述关键词匹配Agent能力
        goal_lower = goal.description.lower()

        for agent_name, capabilities in agent_capabilities.items():
            for cap in capabilities:
                if cap.lower() in goal_lower:
                    if agent_name not in goal.assigned_agents:
                        goal.assigned_agents.append(agent_name)

        if goal.assigned_agents:
            goal.status = "in_progress"

        # 发布目标分解事件
        event = Event(
            event_type=EventType.AGENT_STATE_CHANGED,
            aggregate_id=f"goal:{goal.goal_id}",
            aggregate_type="goal",
            event_data={
                "action": "goal_decomposed",
                "goal": goal.to_dict(),
            },
        )
        await event_bus.publish(event)

        logger.info("orchestrator.goal_decomposed",
                    goal_id=goal.goal_id,
                    assigned_agents=goal.assigned_agents)

        return goal

    async def request_consensus(self, decision: AgentDecision,
                                voter_agents: list[str]) -> bool:
        """
        多Agent共识机制
        高风险决策引入多独立Agent投票，未达阈值自动升级人类审批

        参数:
        - decision: 需要共识的决策
        - voter_agents: 参与投票的Agent名称列表
        """
        threshold = settings.agent_consensus_threshold
        votes = {"approve": 0, "reject": 0, "abstain": 0}
        self._consensus_votes[decision.decision_id] = votes

        logger.info("orchestrator.consensus_started",
                    decision_id=decision.decision_id,
                    voters=voter_agents,
                    threshold=threshold)

        # 模拟各Agent投票（生产环境各Agent独立评估）
        for agent_name in voter_agents:
            agent = self.get_agent_by_name(agent_name)
            if agent and agent.state.value == "running":
                # 基于置信度模拟投票
                if decision.confidence > 0.8:
                    votes["approve"] += 1
                elif decision.confidence > 0.5:
                    votes["abstain"] += 1
                else:
                    votes["reject"] += 1

        total = votes["approve"] + votes["reject"] + votes["abstain"]
        approval_rate = votes["approve"] / total if total > 0 else 0

        consensus_reached = approval_rate >= threshold

        # 发布共识结果事件
        event = Event(
            event_type=EventType.AGENT_CONSENSUS_REACHED,
            aggregate_id=f"consensus:{decision.decision_id}",
            aggregate_type="consensus",
            event_data={
                "decision_id": decision.decision_id,
                "votes": votes,
                "approval_rate": approval_rate,
                "threshold": threshold,
                "consensus_reached": consensus_reached,
            },
        )
        await event_bus.publish(event)

        logger.info("orchestrator.consensus_result",
                    decision_id=decision.decision_id,
                    votes=votes,
                    approval_rate=approval_rate,
                    reached=consensus_reached)

        return consensus_reached

    async def broadcast_event(self, event: Event):
        """广播事件给所有Agent"""
        await event_bus.publish(event)

    def get_stats(self) -> dict:
        """获取编排器统计"""
        agents = list(self._agents.values())
        state_dist = {}
        for agent in agents:
            state_dist[agent.state.value] = state_dist.get(agent.state.value, 0) + 1

        return {
            "total_agents": len(agents),
            "state_distribution": state_dist,
            "active_goals": sum(1 for g in self._goals.values() if g.status == "in_progress"),
            "pending_consensus": len(self._consensus_votes),
        }


# 全局编排器单例
orchestrator = AgentOrchestrator()
