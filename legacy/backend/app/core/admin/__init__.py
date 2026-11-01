"""
CIO管理员系统模块
提供企业数字化大脑的7大管理模块核心逻辑

核心导出：
    - SystemMonitor:          系统监控模块（资源、API、Agent、事件总线、数据库）
    - AgentLifecycleManager:  Agent生命周期管理模块（注册/启停/版本/健康检查）
    - ConfigManager:          配置管理模块（CRUD/版本控制/导入导出/热更新）
    - SecurityController:     安全控制模块（策略/审计/事件响应/IP/API密钥）
    - DataGovernanceManager:  数据治理模块（分类分级/血缘/质量/保留/脱敏/合规）
    - IntegrationPlatform:    集成平台模块（外部连接/Webhook/同步任务/监控）
    - BackupManager:          备份和恢复模块（全量/增量/调度/恢复/验证/DR）

用法示例::

    from app.core.admin import system_monitor, lifecycle_manager

    # 获取实时监控指标
    metrics = system_monitor.get_realtime_metrics()

    # 列出所有Agent
    agents = lifecycle_manager.list_agents()
"""
from app.core.admin.monitor import SystemMonitor
from app.core.admin.lifecycle import AgentLifecycleManager
from app.core.admin.config import ConfigManager
from app.core.admin.security import SecurityController
from app.core.admin.governance import DataGovernanceManager
from app.core.admin.integration import IntegrationPlatform
from app.core.admin.backup import BackupManager

# 全局单例（已加载种子数据）
system_monitor = SystemMonitor()
lifecycle_manager = AgentLifecycleManager()
config_manager = ConfigManager()
security_controller = SecurityController()
data_governance_manager = DataGovernanceManager()
integration_platform = IntegrationPlatform()
backup_manager = BackupManager()

__all__ = [
    # 管理器类
    "SystemMonitor",
    "AgentLifecycleManager",
    "ConfigManager",
    "SecurityController",
    "DataGovernanceManager",
    "IntegrationPlatform",
    "BackupManager",
    # 全局单例
    "system_monitor",
    "lifecycle_manager",
    "config_manager",
    "security_controller",
    "data_governance_manager",
    "integration_platform",
    "backup_manager",
]
