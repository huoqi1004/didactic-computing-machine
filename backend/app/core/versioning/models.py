"""
版本化与灰度发布系统 - 版本模型
================================

定义 Agent 版本化系统的核心数据模型：
- AgentVersion: Agent 版本快照（模型/规则/知识库全量版本化）
- VersionStatus: 版本生命周期状态枚举
- CanaryConfig: 灰度发布配置

设计原则
--------
- 全量版本化：每个版本包含 Agent 配置、规则集快照、知识库快照引用
- 不可变快照：版本一旦注册即不可修改，保证可回溯性
- 语义化版本：遵循 SemVer 规范 (MAJOR.MINOR.PATCH)
- 状态机驱动：DRAFT → TESTING → CANARY → ACTIVE → (ROLLED_BACK/ARCHIVED)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4


class VersionStatus(str, Enum):
    """版本生命周期状态

    状态流转：
        DRAFT → TESTING → CANARY → ACTIVE → ARCHIVED
                                       ↓
                                  ROLLED_BACK
    """
    DRAFT = "draft"            # 草稿：版本已创建但未进入测试
    TESTING = "testing"        # 数字孪生环境测试中
    CANARY = "canary"          # 灰度发布中
    ACTIVE = "active"          # 正式生效（生产环境）
    ROLLED_BACK = "rolled_back"  # 已回滚（曾被 ACTIVE 后被替换）
    ARCHIVED = "archived"      # 已归档（不再可用）


@dataclass
class AgentVersion:
    """Agent 版本快照

    封装某一时刻 Agent 的完整配置：模型参数、规则集、知识库引用。
    版本一旦注册即不可变，保证回滚的精确性与可追溯性。

    Attributes:
        version_id: 版本唯一标识（UUID）
        agent_name: 所属 Agent 名称
        version: 语义化版本号，如 "1.2.0"
        config: Agent 配置（capabilities, security_clearance, model_params等）
        rules: 规则集快照（list[dict]）
        knowledge_snapshot_id: 关联的知识库快照ID
        created_at: 创建时间
        created_by: 创建者标识
        status: 当前版本状态
        changelog: 变更说明
        parent_version: 父版本ID（用于追踪版本链）
    """

    version_id: str
    agent_name: str
    version: str
    config: dict
    rules: list[dict]
    knowledge_snapshot_id: str
    created_at: datetime
    created_by: str
    status: VersionStatus = VersionStatus.DRAFT
    changelog: str = ""
    parent_version: Optional[str] = None

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "version_id": self.version_id,
            "agent_name": self.agent_name,
            "version": self.version,
            "config": self.config,
            "rules": self.rules,
            "knowledge_snapshot_id": self.knowledge_snapshot_id,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "status": self.status.value,
            "changelog": self.changelog,
            "parent_version": self.parent_version,
        }

    @staticmethod
    def generate_version_id() -> str:
        """生成新的版本ID"""
        return str(uuid4())


@dataclass
class CanaryConfig:
    """灰度发布配置

    定义灰度发布的流量切分、评估指标与决策阈值。

    Attributes:
        version_id: 灰度目标版本ID
        traffic_percentage: 灰度流量百分比 (0-100)
        evaluation_metrics: 评估指标（SLO），如 {"p99_latency_ms": 2000}
        success_threshold: 成功率阈值（达到即视为通过）
        error_rate_threshold: 错误率阈值（超过即视为失败）
        duration_seconds: 灰度持续时间（秒）
        started_at: 灰度启动时间
        status: 灰度状态 (running, passed, failed, aborted)
    """

    version_id: str
    traffic_percentage: int = 10
    evaluation_metrics: dict = field(default_factory=dict)
    success_threshold: float = 0.95
    error_rate_threshold: float = 0.05
    duration_seconds: int = 3600
    started_at: Optional[datetime] = None
    status: str = "running"

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "version_id": self.version_id,
            "traffic_percentage": self.traffic_percentage,
            "evaluation_metrics": self.evaluation_metrics,
            "success_threshold": self.success_threshold,
            "error_rate_threshold": self.error_rate_threshold,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "status": self.status,
        }

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """检查灰度是否已超过持续时间"""
        if self.started_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        elapsed = (now - self.started_at).total_seconds()
        return elapsed >= self.duration_seconds
