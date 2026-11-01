"""
数据迁移与合规隐私模块（P3-3 企业就绪阶段）
==============================================

面向企业就绪阶段的数据迁移能力：
- ``DataMasker``：数据脱敏（手机/身份证/邮箱/姓名/账号/自定义）
- ``detect_sensitive_fields``：敏感字段自动检测
- ``PrivacyPolicy``：合规策略（GDPR / 个人信息保护法 PIPL）
- ``DataExporter``：数据导出（CSV / JSON / Excel / 系统快照）
- ``DataImporter``：数据导入与记录校验
- ``MigrationAuditTrail`` / ``migration_audit``：合规审计追踪
- ``MigrationManager`` / ``migration_manager``：统一迁移入口

用法示例::

    from app.core.migration import migration_manager, DataMasker

    # 统一导出（自动脱敏 + 审计）
    result = migration_manager.export(scope="erp_snapshot", format="json")

    # 统一导入（文件 + 校验 + 审计）
    report = migration_manager.import_data(
        file_path="data/migrations/customers.csv", target="crm")

    # 合规报告
    report = migration_manager.compliance_report()

    # 直接使用脱敏器
    masked = DataMasker().mask("13812345678", "phone")   # 138****5678
"""
from app.core.migration.privacy import (
    DataMasker,
    PrivacyPolicy,
    detect_sensitive_fields,
)
from app.core.migration.exporter import DataExporter
from app.core.migration.importer import DataImporter
from app.core.migration.audit_trail import (
    MigrationAuditTrail,
    migration_audit,
)
from app.core.migration.manager import (
    MigrationManager,
    migration_manager,
)

__all__ = [
    # 脱敏与隐私
    "DataMasker",
    "PrivacyPolicy",
    "detect_sensitive_fields",
    # 导出
    "DataExporter",
    # 导入
    "DataImporter",
    # 审计追踪
    "MigrationAuditTrail",
    "migration_audit",
    # 迁移管理器
    "MigrationManager",
    "migration_manager",
]
