"""
Agent生命周期管理模块
CIO管理员系统对Agent全生命周期的管理控制能力

特性：
- Agent注册、启动、暂停、恢复、停止
- Agent配置更新（运行时参数）
- Agent版本管理（回滚支持）
- Agent健康检查（心跳检测）
- Agent资源限制（CPU/内存配额）
- Agent依赖关系管理
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

logger = structlog.get_logger()


class AgentLifecycleManager:
    """
    Agent生命周期管理器

    职责：
    - 管理Agent的注册与状态转换（注册→启动→暂停→恢复→停止）
    - 管理Agent配置版本与回滚
    - 执行Agent健康检查（心跳检测）
    - 管理Agent资源配额限制
    - 管理Agent之间的依赖关系
    - 记录完整的生命周期事件历史
    """

    # 资源配额默认值
    DEFAULT_RESOURCE_LIMITS = {
        "cpu_limit": 2.0,        # CPU核心数上限
        "memory_limit_mb": 2048,  # 内存上限（MB）
        "max_concurrent_tasks": 10,  # 最大并发任务数
    }

    def __init__(self):
        # Agent注册信息：{agent_id: agent_info}
        self._agents: dict[str, dict] = {}
        # Agent配置版本历史：{agent_id: [version_records]}
        self._version_history: dict[str, list[dict]] = {}
        # Agent生命周期事件历史：{agent_id: [lifecycle_events]}
        self._lifecycle_history: dict[str, list[dict]] = {}
        # Agent依赖关系：{agent_id: [dependent_agent_ids]}
        self._dependencies: dict[str, list[str]] = {}
        # Agent心跳记录：{agent_id: last_heartbeat}
        self._heartbeats: dict[str, datetime] = {}
        self._seed_data()

    def _seed_data(self) -> None:
        """加载示例数据"""
        now = datetime.now(timezone.utc)
        # 预置3个示例Agent
        seed_agents = [
            {
                "agent_id": "agent-finance-001",
                "name": "财务核算Agent",
                "domain": "finance",
                "config": {
                    "auto_approve_threshold": 10000,
                    "report_schedule": "0 8 * * *",
                    "llm_model": "gpt-4",
                },
                "status": "running",
                "version": "1.2.0",
                "resource_limits": {
                    "cpu_limit": 1.0,
                    "memory_limit_mb": 1024,
                    "max_concurrent_tasks": 5,
                },
            },
            {
                "agent_id": "agent-inventory-001",
                "name": "库存管理Agent",
                "domain": "inventory",
                "config": {
                    "low_stock_threshold": 100,
                    "replenishment_strategy": "auto",
                    "check_interval": 300,
                },
                "status": "running",
                "version": "2.0.1",
                "resource_limits": {
                    "cpu_limit": 2.0,
                    "memory_limit_mb": 2048,
                    "max_concurrent_tasks": 10,
                },
            },
            {
                "agent_id": "agent-procurement-001",
                "name": "采购执行Agent",
                "domain": "procurement",
                "config": {
                    "max_order_amount": 500000,
                    "supplier_pref_list": ["supplier-a", "supplier-b"],
                    "approval_required": True,
                },
                "status": "paused",
                "version": "1.5.3",
                "resource_limits": {
                    "cpu_limit": 1.5,
                    "memory_limit_mb": 1536,
                    "max_concurrent_tasks": 8,
                },
            },
        ]
        for agent in seed_agents:
            agent_id = agent["agent_id"]
            agent["registered_at"] = now.isoformat()
            agent["last_status_change"] = now.isoformat()
            self._agents[agent_id] = agent
            self._version_history[agent_id] = [{
                "version": agent["version"],
                "config": agent["config"],
                "timestamp": now.isoformat(),
                "action": "initial",
            }]
            self._lifecycle_history[agent_id] = [{
                "event_id": str(uuid4()),
                "action": "register",
                "timestamp": now.isoformat(),
                "details": {"name": agent["name"], "domain": agent["domain"]},
            }]
            self._heartbeats[agent_id] = now
        # 预置依赖关系
        self._dependencies["agent-procurement-001"] = ["agent-inventory-001"]
        logger.info("lifecycle.seeded", agent_count=len(self._agents))

    def _record_lifecycle_event(
        self, agent_id: str, action: str, details: Optional[dict] = None
    ) -> None:
        """记录生命周期事件"""
        event = {
            "event_id": str(uuid4()),
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {},
        }
        if agent_id not in self._lifecycle_history:
            self._lifecycle_history[agent_id] = []
        self._lifecycle_history[agent_id].append(event)

    def _update_heartbeat(self, agent_id: str) -> None:
        """更新Agent心跳"""
        self._heartbeats[agent_id] = datetime.now(timezone.utc)

    def register_agent(
        self, agent_id: str, name: str, domain: str, config: dict
    ) -> dict:
        """
        注册新Agent

        参数:
            agent_id: Agent唯一标识
            name: Agent名称
            domain: 业务域（如finance/inventory/procurement）
            config: Agent配置字典

        Returns:
            dict: 注册结果，包含agent信息和注册状态
        """
        if agent_id in self._agents:
            logger.warning("lifecycle.agent_already_registered", agent_id=agent_id)
            return {"error": "agent_already_registered", "agent_id": agent_id}

        now = datetime.now(timezone.utc)
        agent_info = {
            "agent_id": agent_id,
            "name": name,
            "domain": domain,
            "config": config,
            "status": "registered",
            "version": "1.0.0",
            "resource_limits": dict(self.DEFAULT_RESOURCE_LIMITS),
            "registered_at": now.isoformat(),
            "last_status_change": now.isoformat(),
        }
        self._agents[agent_id] = agent_info
        self._version_history[agent_id] = [{
            "version": "1.0.0",
            "config": config,
            "timestamp": now.isoformat(),
            "action": "initial",
        }]
        self._record_lifecycle_event(agent_id, "register", {
            "name": name, "domain": domain,
        })
        self._update_heartbeat(agent_id)

        logger.info("lifecycle.agent_registered",
                    agent_id=agent_id, name=name, domain=domain)
        return {"status": "success", "agent": agent_info}

    def start_agent(self, agent_id: str) -> dict:
        """
        启动Agent

        参数:
            agent_id: Agent唯一标识

        Returns:
            dict: 启动结果
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        if agent["status"] == "running":
            return {"status": "already_running", "agent_id": agent_id}

        # 检查依赖Agent是否运行
        deps = self._dependencies.get(agent_id, [])
        for dep_id in deps:
            dep = self._agents.get(dep_id)
            if dep and dep["status"] != "running":
                logger.warning("lifecycle.dependency_not_running",
                              agent_id=agent_id, dependency=dep_id)
                return {
                    "error": "dependency_not_running",
                    "agent_id": agent_id,
                    "dependency": dep_id,
                }

        agent["status"] = "running"
        agent["last_status_change"] = datetime.now(timezone.utc).isoformat()
        self._record_lifecycle_event(agent_id, "start")
        self._update_heartbeat(agent_id)

        logger.info("lifecycle.agent_started", agent_id=agent_id)
        return {"status": "success", "agent_id": agent_id, "current_status": "running"}

    def pause_agent(self, agent_id: str) -> dict:
        """
        暂停Agent

        参数:
            agent_id: Agent唯一标识

        Returns:
            dict: 暂停结果
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        if agent["status"] != "running":
            return {
                "error": "invalid_state_transition",
                "agent_id": agent_id,
                "current_status": agent["status"],
                "required_status": "running",
            }

        agent["status"] = "paused"
        agent["last_status_change"] = datetime.now(timezone.utc).isoformat()
        self._record_lifecycle_event(agent_id, "pause")

        logger.info("lifecycle.agent_paused", agent_id=agent_id)
        return {"status": "success", "agent_id": agent_id, "current_status": "paused"}

    def resume_agent(self, agent_id: str) -> dict:
        """
        恢复已暂停的Agent

        参数:
            agent_id: Agent唯一标识

        Returns:
            dict: 恢复结果
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        if agent["status"] != "paused":
            return {
                "error": "invalid_state_transition",
                "agent_id": agent_id,
                "current_status": agent["status"],
                "required_status": "paused",
            }

        agent["status"] = "running"
        agent["last_status_change"] = datetime.now(timezone.utc).isoformat()
        self._record_lifecycle_event(agent_id, "resume")
        self._update_heartbeat(agent_id)

        logger.info("lifecycle.agent_resumed", agent_id=agent_id)
        return {"status": "success", "agent_id": agent_id, "current_status": "running"}

    def stop_agent(self, agent_id: str) -> dict:
        """
        停止Agent

        参数:
            agent_id: Agent唯一标识

        Returns:
            dict: 停止结果
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        if agent["status"] == "stopped":
            return {"status": "already_stopped", "agent_id": agent_id}

        # 检查是否有其他Agent依赖此Agent
        dependents = [
            aid for aid, deps in self._dependencies.items()
            if agent_id in deps and self._agents.get(aid, {}).get("status") == "running"
        ]
        if dependents:
            logger.warning("lifecycle.has_running_dependents",
                          agent_id=agent_id, dependents=dependents)
            return {
                "error": "has_running_dependents",
                "agent_id": agent_id,
                "dependents": dependents,
            }

        agent["status"] = "stopped"
        agent["last_status_change"] = datetime.now(timezone.utc).isoformat()
        self._record_lifecycle_event(agent_id, "stop")

        logger.info("lifecycle.agent_stopped", agent_id=agent_id)
        return {"status": "success", "agent_id": agent_id, "current_status": "stopped"}

    def update_config(self, agent_id: str, config: dict) -> dict:
        """
        更新Agent运行时配置

        参数:
            agent_id: Agent唯一标识
            config: 新的配置字典（将合并到现有配置）

        Returns:
            dict: 更新结果，包含新版本号
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        # 保存旧配置用于版本记录
        old_config = dict(agent["config"])
        # 合并新配置
        agent["config"].update(config)

        # 版本递增
        now = datetime.now(timezone.utc)
        parts = agent["version"].split(".")
        if len(parts) == 3:
            parts[2] = str(int(parts[2]) + 1)
            agent["version"] = ".".join(parts)

        # 记录版本历史
        if agent_id not in self._version_history:
            self._version_history[agent_id] = []
        self._version_history[agent_id].append({
            "version": agent["version"],
            "config": dict(agent["config"]),
            "previous_config": old_config,
            "timestamp": now.isoformat(),
            "action": "update",
        })

        self._record_lifecycle_event(agent_id, "config_update", {
            "new_version": agent["version"],
            "changed_keys": list(config.keys()),
        })

        logger.info("lifecycle.config_updated",
                    agent_id=agent_id, new_version=agent["version"])
        return {
            "status": "success",
            "agent_id": agent_id,
            "new_version": agent["version"],
            "config": agent["config"],
        }

    def rollback_agent(self, agent_id: str, version: str) -> dict:
        """
        回滚Agent到指定版本

        参数:
            agent_id: Agent唯一标识
            version: 目标版本号

        Returns:
            dict: 回滚结果
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        versions = self._version_history.get(agent_id, [])
        target = None
        for v in versions:
            if v["version"] == version:
                target = v
                break

        if not target:
            return {"error": "version_not_found",
                    "agent_id": agent_id, "version": version}

        # 保存当前版本
        current_version = agent["version"]
        current_config = dict(agent["config"])

        # 回滚到目标版本
        agent["version"] = version
        agent["config"] = dict(target["config"])
        now = datetime.now(timezone.utc)

        # 记录回滚版本
        self._version_history[agent_id].append({
            "version": version,
            "config": dict(target["config"]),
            "previous_config": current_config,
            "previous_version": current_version,
            "timestamp": now.isoformat(),
            "action": "rollback",
        })

        self._record_lifecycle_event(agent_id, "rollback", {
            "from_version": current_version,
            "to_version": version,
        })

        logger.info("lifecycle.agent_rolled_back",
                    agent_id=agent_id,
                    from_version=current_version,
                    to_version=version)
        return {
            "status": "success",
            "agent_id": agent_id,
            "rolled_back_to": version,
            "config": agent["config"],
        }

    def health_check(self, agent_id: str) -> dict:
        """
        执行Agent健康检查（心跳检测）

        参数:
            agent_id: Agent唯一标识

        Returns:
            dict: 健康检查结果，包含状态、心跳信息和资源使用情况
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        now = datetime.now(timezone.utc)
        last_heartbeat = self._heartbeats.get(agent_id, now)
        heartbeat_age = (now - last_heartbeat).total_seconds()

        # 健康状态判定
        if agent["status"] != "running":
            health_status = "inactive"
        elif heartbeat_age > 120:
            health_status = "unhealthy"
        elif heartbeat_age > 60:
            health_status = "degraded"
        else:
            health_status = "healthy"

        # 如果运行中，更新心跳
        if agent["status"] == "running":
            self._update_heartbeat(agent_id)

        result = {
            "agent_id": agent_id,
            "name": agent["name"],
            "status": agent["status"],
            "health": health_status,
            "last_heartbeat": last_heartbeat.isoformat(),
            "heartbeat_age_seconds": round(heartbeat_age, 1),
            "version": agent["version"],
            "resource_limits": agent["resource_limits"],
            "checked_at": now.isoformat(),
        }

        logger.info("lifecycle.health_check",
                    agent_id=agent_id, health=health_status)
        return result

    def get_lifecycle_history(self, agent_id: str) -> list[dict]:
        """
        获取Agent生命周期事件历史

        参数:
            agent_id: Agent唯一标识

        Returns:
            list[dict]: 生命周期事件列表，按时间正序排列
        """
        history = self._lifecycle_history.get(agent_id, [])
        logger.info("lifecycle.history_queried",
                    agent_id=agent_id, count=len(history))
        return list(history)

    def list_agents(
        self,
        status: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> list[dict]:
        """
        列出所有Agent（可按状态和域过滤）

        参数:
            status: 可选，按状态过滤（registered/running/paused/stopped）
            domain: 可选，按业务域过滤

        Returns:
            list[dict]: Agent信息列表
        """
        agents = list(self._agents.values())
        if status:
            agents = [a for a in agents if a["status"] == status]
        if domain:
            agents = [a for a in agents if a["domain"] == domain]

        logger.info("lifecycle.agents_listed",
                    status=status, domain=domain, count=len(agents))
        return agents

    def set_resource_limits(
        self, agent_id: str, limits: dict
    ) -> dict:
        """
        设置Agent资源限制

        参数:
            agent_id: Agent唯一标识
            limits: 资源限制字典（cpu_limit, memory_limit_mb, max_concurrent_tasks）

        Returns:
            dict: 更新后的资源限制
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}

        agent["resource_limits"].update(limits)
        self._record_lifecycle_event(agent_id, "resource_limits_update", {
            "limits": limits,
        })
        logger.info("lifecycle.resource_limits_set",
                    agent_id=agent_id, limits=limits)
        return {
            "status": "success",
            "agent_id": agent_id,
            "resource_limits": agent["resource_limits"],
        }

    def add_dependency(
        self, agent_id: str, depends_on: str
    ) -> dict:
        """
        添加Agent依赖关系

        参数:
            agent_id: Agent唯一标识
            depends_on: 被依赖的Agent ID

        Returns:
            dict: 操作结果
        """
        if agent_id not in self._agents:
            return {"error": "agent_not_found", "agent_id": agent_id}
        if depends_on not in self._agents:
            return {"error": "dependency_not_found", "depends_on": depends_on}

        if agent_id not in self._dependencies:
            self._dependencies[agent_id] = []
        if depends_on not in self._dependencies[agent_id]:
            self._dependencies[agent_id].append(depends_on)

        self._record_lifecycle_event(agent_id, "dependency_added", {
            "depends_on": depends_on,
        })
        logger.info("lifecycle.dependency_added",
                    agent_id=agent_id, depends_on=depends_on)
        return {
            "status": "success",
            "agent_id": agent_id,
            "depends_on": self._dependencies[agent_id],
        }
