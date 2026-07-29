"""
版本化与灰度发布系统 (Versioning & Canary Release)
====================================================

设计方案 4.2 - Agent 模型、规则、知识库全量版本化，支持一键回滚。
新版本在数字孪生环境并行验证，灰度切流。

子模块
------
1. models.py - 版本数据模型
   - AgentVersion: Agent 版本快照（配置 + 规则 + 知识库引用）
   - VersionStatus: 版本生命周期状态枚举
   - CanaryConfig: 灰度发布配置

2. registry.py - 版本注册中心
   - VersionRegistry: 版本注册、查询、激活、回滚、归档、对比
   - 全局单例: version_registry

3. canary.py - 灰度发布管理器
   - CanaryManager: 灰度启动、路由、评估、全量/中止
   - 全局单例: canary_manager

4. digital_twin.py - 数字孪生环境
   - DigitalTwin: 孪生实例创建、事件注入、仿真测试、生产对比
   - 全局单例: digital_twin

5. snapshot.py - 知识库快照
   - KnowledgeSnapshot: 知识库快照（图谱 + 文档 + 规则）
   - SnapshotManager: 快照创建、查询、恢复
   - 全局单例: snapshot_manager

设计原则
--------
- 全量版本化：每个版本携带 Agent 配置、规则集、知识库快照引用
- 不可变快照：版本一旦注册即不可修改，保证可回溯性
- 一键回滚：activate_version / rollback_version 支持快速切换
- 数字孪生：新版本在隔离环境并行验证，不影响生产
- 灰度切流：一致性哈希路由 + 实时指标评估 + 自动决策
- 内存实现：零外部依赖，接口兼容未来切换到持久化后端
"""
from app.core.versioning.models import (
    AgentVersion,
    CanaryConfig,
    VersionStatus,
)
from app.core.versioning.registry import (
    VersionRegistry,
    version_registry,
)
from app.core.versioning.canary import (
    CanaryManager,
    canary_manager,
)
from app.core.versioning.digital_twin import (
    DigitalTwin,
    digital_twin,
)
from app.core.versioning.snapshot import (
    KnowledgeSnapshot,
    SnapshotManager,
    snapshot_manager,
)

__all__ = [
    # 版本模型
    "AgentVersion",
    "VersionStatus",
    "CanaryConfig",
    # 版本注册中心
    "VersionRegistry",
    "version_registry",
    # 灰度发布管理器
    "CanaryManager",
    "canary_manager",
    # 数字孪生环境
    "DigitalTwin",
    "digital_twin",
    # 知识库快照
    "KnowledgeSnapshot",
    "SnapshotManager",
    "snapshot_manager",
]
