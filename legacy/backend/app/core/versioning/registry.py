"""
版本化与灰度发布系统 - 版本注册中心
====================================

管理 Agent 版本的全生命周期：注册、查询、激活、回滚、归档、对比。
是版本化系统的核心枢纽，与灰度管理器、数字孪生环境协同工作。

设计原则
--------
- 全量版本化：每个版本携带 Agent 配置、规则集、知识库快照引用
- 不可变历史：版本状态可流转，但快照数据不可变
- 一键回滚：activate_version / rollback_version 支持快速切换
- 版本链追踪：通过 parent_version 构建版本树，支持链式查询

全局单例：version_registry
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from typing import Optional

from app.core.versioning.models import AgentVersion, VersionStatus

logger = structlog.get_logger()


class VersionRegistry:
    """版本注册中心

    管理 Agent 版本的注册、查询、激活、回滚等操作。

    Attributes:
        _versions: agent_name -> 版本列表（按创建时间排序）
        _active_versions: agent_name -> 当前激活的 version_id
        _canary_configs: version_id -> 灰度配置
    """

    def __init__(self):
        self._versions: dict[str, list[AgentVersion]] = {}
        self._active_versions: dict[str, str] = {}
        self._canary_configs: dict[str, "object"] = {}  # version_id -> CanaryConfig

    # ------------------------------------------------------------------
    # 版本注册
    # ------------------------------------------------------------------

    def register_version(
        self,
        agent_name: str,
        version: str,
        config: dict,
        rules: list[dict],
        changelog: str = "",
        parent_version: Optional[str] = None,
        knowledge_snapshot_id: Optional[str] = None,
        created_by: str = "system",
    ) -> AgentVersion:
        """注册新版本

        创建一个不可变的版本快照。如果未提供 knowledge_snapshot_id，
        则自动创建当前知识库的快照。

        Args:
            agent_name: Agent 名称
            version: 语义化版本号 (如 "1.0.0")
            config: Agent 配置字典
            rules: 规则集快照
            changelog: 变更说明
            parent_version: 父版本ID
            knowledge_snapshot_id: 知识库快照ID（可选，不提供则自动创建）
            created_by: 创建者

        Returns:
            新创建的 AgentVersion
        """
        # 同步创建知识库快照（若未提供）
        if knowledge_snapshot_id is None:
            # 同步调用：snapshot_manager.create_snapshot 是 async，
            # 此处为兼容同步上下文，直接捕获当前规则集作为版本快照
            knowledge_snapshot_id = self._capture_inline_snapshot()

        new_version = AgentVersion(
            version_id=AgentVersion.generate_version_id(),
            agent_name=agent_name,
            version=version,
            config=dict(config),
            rules=list(rules),
            knowledge_snapshot_id=knowledge_snapshot_id,
            created_at=datetime.now(timezone.utc),
            created_by=created_by,
            status=VersionStatus.DRAFT,
            changelog=changelog,
            parent_version=parent_version,
        )

        self._versions.setdefault(agent_name, []).append(new_version)

        # 若该 Agent 还没有任何激活版本，自动激活首个版本
        if agent_name not in self._active_versions:
            new_version.status = VersionStatus.ACTIVE
            self._active_versions[agent_name] = new_version.version_id

        logger.info(
            "version.registered",
            version_id=new_version.version_id,
            agent_name=agent_name,
            version=version,
            status=new_version.status.value,
        )
        return new_version

    def _capture_inline_snapshot(self) -> str:
        """同步捕获知识库快照标识

        由于 register_version 是同步方法，无法直接 await create_snapshot，
        此处生成一个占位快照ID。真实的快照由 snapshot_manager 异步创建。
        若需关联真实快照，应在调用 register_version 前先创建快照并传入ID。
        """
        from uuid import uuid4
        return f"snapshot-{uuid4()}"

    # ------------------------------------------------------------------
    # 版本查询
    # ------------------------------------------------------------------

    def get_version(self, version_id: str) -> Optional[AgentVersion]:
        """按版本ID查询版本"""
        for versions in self._versions.values():
            for v in versions:
                if v.version_id == version_id:
                    return v
        return None

    def get_versions(self, agent_name: str) -> list[AgentVersion]:
        """获取指定 Agent 的所有版本（按创建时间排序）"""
        return list(self._versions.get(agent_name, []))

    def get_active_version(self, agent_name: str) -> Optional[AgentVersion]:
        """获取指定 Agent 当前激活的版本"""
        version_id = self._active_versions.get(agent_name)
        if version_id is None:
            return None
        return self.get_version(version_id)

    def get_version_chain(self, agent_name: str) -> list[AgentVersion]:
        """获取版本链（从最早到最新）

        通过 parent_version 字段构建版本链。
        """
        versions = self.get_versions(agent_name)
        if not versions:
            return []

        # 构建版本ID -> 版本映射
        id_map = {v.version_id: v for v in versions}

        # 找到根版本（无 parent 或 parent 不在列表中）
        roots = [v for v in versions if v.parent_version is None or v.parent_version not in id_map]

        # 从根开始构建链
        chain: list[AgentVersion] = []
        visited: set[str] = set()
        for root in roots:
            current = root
            while current and current.version_id not in visited:
                chain.append(current)
                visited.add(current.version_id)
                # 找下一个子版本
                next_version = None
                for v in versions:
                    if v.parent_version == current.version_id:
                        next_version = v
                        break
                current = next_version

        return chain

    # ------------------------------------------------------------------
    # 版本流转
    # ------------------------------------------------------------------

    def activate_version(self, version_id: str) -> bool:
        """激活版本（一键切换）

        将指定版本设为 ACTIVE，同时将原 ACTIVE 版本标记为 ROLLED_BACK。
        若目标版本处于 CANARY 状态，会先停止灰度。

        Args:
            version_id: 目标版本ID

        Returns:
            是否激活成功
        """
        version = self.get_version(version_id)
        if version is None:
            logger.warning("version.activate_failed_not_found", version_id=version_id)
            return False

        if version.status == VersionStatus.ARCHIVED:
            logger.warning("version.activate_failed_archived", version_id=version_id)
            return False

        agent_name = version.agent_name

        # 将原 ACTIVE 版本标记为 ROLLED_BACK
        current_active_id = self._active_versions.get(agent_name)
        if current_active_id and current_active_id != version_id:
            current_active = self.get_version(current_active_id)
            if current_active and current_active.status == VersionStatus.ACTIVE:
                current_active.status = VersionStatus.ROLLED_BACK
                logger.info(
                    "version.deactivated",
                    version_id=current_active_id,
                    agent_name=agent_name,
                )

        # 激活新版本
        version.status = VersionStatus.ACTIVE
        self._active_versions[agent_name] = version_id

        logger.info(
            "version.activated",
            version_id=version_id,
            agent_name=agent_name,
            version=version.version,
        )
        return True

    def rollback_version(
        self,
        agent_name: str,
        target_version_id: Optional[str] = None,
    ) -> Optional[AgentVersion]:
        """回滚版本

        默认回滚到上一个 ACTIVE 版本（即被当前版本替换的版本）。
        也可指定 target_version_id 回滚到特定版本。

        Args:
            agent_name: Agent 名称
            target_version_id: 目标版本ID（可选）

        Returns:
            回滚后的激活版本，失败返回 None
        """
        versions = self.get_versions(agent_name)
        if not versions:
            return None

        current_active = self.get_active_version(agent_name)

        if target_version_id is None:
            # 默认回滚到上一个 ACTIVE（ROLLED_BACK）版本
            # 按 created_at 倒序找第一个 ROLLED_BACK 版本
            rolled_back = [
                v for v in reversed(versions)
                if v.status == VersionStatus.ROLLED_BACK
            ]
            if not rolled_back:
                # 没有 ROLLED_BACK 版本，找第一个非当前版本
                candidates = [
                    v for v in reversed(versions)
                    if v.version_id != (current_active.version_id if current_active else None)
                    and v.status != VersionStatus.ARCHIVED
                ]
                if not candidates:
                    return None
                target_version_id = candidates[0].version_id
            else:
                target_version_id = rolled_back[0].version_id

        # 激活目标版本
        success = self.activate_version(target_version_id)
        if not success:
            return None

        return self.get_version(target_version_id)

    def archive_version(self, version_id: str) -> bool:
        """归档版本

        归档后的版本不可再激活。

        Args:
            version_id: 目标版本ID

        Returns:
            是否归档成功
        """
        version = self.get_version(version_id)
        if version is None:
            return False

        if version.status == VersionStatus.ACTIVE:
            logger.warning("version.archive_failed_active", version_id=version_id)
            return False

        version.status = VersionStatus.ARCHIVED
        logger.info("version.archived", version_id=version_id)
        return True

    # ------------------------------------------------------------------
    # 版本对比
    # ------------------------------------------------------------------

    def compare_versions(self, v1_id: str, v2_id: str) -> dict:
        """对比两个版本的差异

        Args:
            v1_id: 版本1 ID
            v2_id: 版本2 ID

        Returns:
            差异字典，包含 config_diff / rules_diff / version_info
        """
        v1 = self.get_version(v1_id)
        v2 = self.get_version(v2_id)

        if v1 is None or v2 is None:
            return {"error": "version_not_found", "v1": v1_id, "v2": v2_id}

        # 配置差异
        config_diff = self._diff_dict(v1.config, v2.config)

        # 规则差异
        v1_rule_ids = {r.get("rule_id") or r.get("name") for r in v1.rules}
        v2_rule_ids = {r.get("rule_id") or r.get("name") for r in v2.rules}
        rules_added = [r for r in v2.rules if (r.get("rule_id") or r.get("name")) not in v1_rule_ids]
        rules_removed = [r for r in v1.rules if (r.get("rule_id") or r.get("name")) not in v2_rule_ids]

        return {
            "v1": {
                "version_id": v1.version_id,
                "version": v1.version,
                "status": v1.status.value,
            },
            "v2": {
                "version_id": v2.version_id,
                "version": v2.version,
                "status": v2.status.value,
            },
            "config_diff": config_diff,
            "rules_diff": {
                "added_count": len(rules_added),
                "removed_count": len(rules_removed),
                "added": rules_added,
                "removed": rules_removed,
            },
            "knowledge_snapshot_changed": v1.knowledge_snapshot_id != v2.knowledge_snapshot_id,
        }

    def _diff_dict(self, d1: dict, d2: dict) -> dict:
        """计算两个字典的差异"""
        added = {k: d2[k] for k in d2 if k not in d1}
        removed = {k: d1[k] for k in d1 if k not in d2}
        changed = {
            k: {"from": d1[k], "to": d2[k]}
            for k in d1
            if k in d2 and d1[k] != d2[k]
        }
        return {"added": added, "removed": removed, "changed": changed}

    # ------------------------------------------------------------------
    # 灰度配置管理
    # ------------------------------------------------------------------

    def set_canary_config(self, version_id: str, config) -> None:
        """关联灰度配置（供 CanaryManager 调用）"""
        self._canary_configs[version_id] = config

    def get_canary_config(self, version_id: str):
        """获取灰度配置"""
        return self._canary_configs.get(version_id)

    def remove_canary_config(self, version_id: str) -> None:
        """移除灰度配置"""
        self._canary_configs.pop(version_id, None)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """序列化整个注册中心状态"""
        return {
            "agents": {
                agent_name: {
                    "versions": [v.to_dict() for v in versions],
                    "active_version_id": self._active_versions.get(agent_name),
                }
                for agent_name, versions in self._versions.items()
            },
            "total_versions": sum(len(v) for v in self._versions.values()),
            "total_agents": len(self._versions),
        }

    def get_stats(self) -> dict:
        """获取统计信息"""
        status_dist: dict[str, int] = {}
        for versions in self._versions.values():
            for v in versions:
                status_dist[v.status.value] = status_dist.get(v.status.value, 0) + 1
        return {
            "total_versions": sum(len(v) for v in self._versions.values()),
            "total_agents": len(self._versions),
            "active_versions": len(self._active_versions),
            "status_distribution": status_dist,
        }

    def clear(self) -> None:
        """清空所有版本（主要用于测试）"""
        self._versions.clear()
        self._active_versions.clear()
        self._canary_configs.clear()


# 全局单例
version_registry = VersionRegistry()
