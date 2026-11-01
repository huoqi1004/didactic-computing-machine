"""
迁移管理器（数据迁移与合规）
==============================

P3-3 企业就绪阶段的统一数据迁移入口，组合：
- ``DataMasker`` / ``PrivacyPolicy``：脱敏与合规
- ``DataExporter``：导出（CSV / JSON / Excel / 系统快照）
- ``DataImporter``：导入与校验
- ``MigrationAuditTrail``：导出/导入/访问审计

用法示例::

    from app.core.migration import migration_manager

    # 统一导出（自动脱敏 + 审计）
    result = migration_manager.export(scope="erp_snapshot", format="json")

    # 统一导入（文件 + 校验 + 审计）
    report = migration_manager.import_data(
        file_path="data/migrations/customers.csv", target="crm",
        mapping={"客户姓名": "name", "手机": "phone"})

    # 合规报告
    report = migration_manager.compliance_report()
"""
import os
import structlog
from datetime import datetime, timezone

from app.core.migration.privacy import DataMasker, PrivacyPolicy
from app.core.migration.exporter import DataExporter
from app.core.migration.importer import DataImporter
from app.core.migration.audit_trail import MigrationAuditTrail, migration_audit

logger = structlog.get_logger()

# 支持导出的格式
EXPORT_FORMATS = ("csv", "json", "excel", "xlsx")


class MigrationManager:
    """
    迁移管理器：统一导出 / 导入 / 历史 / 合规报告入口。
    """

    def __init__(self, masker: DataMasker | None = None,
                 exporter: DataExporter | None = None,
                 importer: DataImporter | None = None,
                 audit: MigrationAuditTrail | None = None,
                 export_dir: str | None = None):
        """
        Args:
            masker: 脱敏器（默认新建 DataMasker）
            exporter: 导出器（默认新建，export_dir 优先）
            importer: 导入器（默认新建 DataImporter）
            audit: 审计追踪（默认全局单例 migration_audit）
            export_dir: 导出目录（默认 ``data/migrations``）
        """
        self.masker = masker or DataMasker()
        self.policy = PrivacyPolicy()
        self.exporter = exporter or DataExporter(export_dir=export_dir)
        self.importer = importer or DataImporter()
        self.audit = audit or migration_audit
        self.export_dir = export_dir or self.exporter.export_dir

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def export(self, scope: str = "erp_snapshot", format: str = "json",
               mask_fields=None, records: list[dict] | None = None,
               columns: list[str] | None = None,
               user: str = "admin") -> dict:
        """
        统一导出入口。

        Args:
            scope: 导出范围。``erp_snapshot`` 导出系统完整快照；
                其他范围需配合 ``records`` 参数导出指定记录
            format: 导出格式 csv / json / excel（snapshot 固定 json）
            mask_fields: 脱敏配置（None=自动 / [] = 不脱敏 / dict|list=指定）
            records: 自定义导出记录（scope 非 erp_snapshot 时必填）
            columns: CSV/Excel 列顺序（默认取记录键）
            user: 操作者（写入审计日志）

        Returns:
            {"status", "scope", "format", "file_path", "records_count",
             "mask_applied", "exported_at", "audit_log"}
        """
        fmt = format.lower()
        if fmt == "xlsx":
            fmt = "excel"
        if fmt not in EXPORT_FORMATS:
            return {"error": "unsupported_format", "format": format,
                    "valid_formats": ["csv", "json", "excel"]}

        # 系统快照导出
        if scope == "erp_snapshot":
            result = self.exporter.export_erp_snapshot(mask_fields=mask_fields)
            if result.get("status") != "success":
                return result
            file_path = result["file_path"]
            records_count = result["records_count"]
            mask_applied = result["mask_applied"]
            fmt = "json"
        else:
            # 自定义数据导出
            if not records:
                return {"error": "records_required_for_scope", "scope": scope}
            os.makedirs(self.export_dir, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            ext = "xlsx" if fmt == "excel" else fmt
            file_path = os.path.join(
                self.export_dir, f"{scope}_{timestamp}.{ext}")
            if fmt == "csv":
                cols = columns or list(records[0].keys())
                result = self.exporter.export_csv(
                    records, cols, file_path, mask_fields)
            elif fmt == "excel":
                cols = columns or list(records[0].keys())
                result = self.exporter.export_excel(
                    records, cols, file_path, mask_fields)
            else:
                result = self.exporter.export_json(
                    records, file_path, mask_fields)
            records_count = result["records_count"]
            mask_applied = result["mask_applied"]

        audit_entry = self.audit.log_export(
            user, scope, records_count, mask_applied)
        logger.info("migration.export", scope=scope, format=fmt,
                    path=file_path, records=records_count,
                    mask=mask_applied, user=user)
        return {
            "status": "success",
            "scope": scope,
            "format": fmt,
            "file_path": file_path,
            "records_count": records_count,
            "mask_applied": mask_applied,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "audit_log": audit_entry,
        }

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------

    def import_data(self, file_path: str | None = None,
                    target: str = "default",
                    mapping: dict | None = None,
                    records: list[dict] | None = None,
                    schema: dict | None = None,
                    user: str = "admin") -> dict:
        """
        统一导入入口。

        Args:
            file_path: 导入文件路径（.csv / .json / .jsonl）
            target: 导入目标（如 crm / finance / hr）
            mapping: CSV 字段映射 {源列: 目标字段}
            records: 直接传入记录列表（优先于文件）
            schema: 校验规则（见 DataImporter.validate_records）
            user: 操作者（写入审计日志）

        Returns:
            导入报告（含 audit_log 与 target）
        """
        report = self.importer.import_data(
            file_path=file_path, records=records,
            mapping=mapping, schema=schema)

        source = file_path or "inline"
        if report.get("status") == "success":
            audit_entry = self.audit.log_import(
                user, source, report["total"], report["success_count"])
            report["audit_log"] = audit_entry
        else:
            audit_entry = self.audit.log_import(
                user, source, report["total"], 0)
            report["audit_log"] = audit_entry

        report["target"] = target
        logger.info("migration.import", target=target, source=source,
                    total=report["total"],
                    success=report["success_count"], user=user)
        return report

    # ------------------------------------------------------------------
    # 历史查询
    # ------------------------------------------------------------------

    def get_export_history(self, limit: int = 50) -> list[dict]:
        """查询导出历史（按时间倒序）。"""
        return self.audit.query(action="export")[:limit]

    def get_import_history(self, limit: int = 50) -> list[dict]:
        """查询导入历史（按时间倒序）。"""
        return self.audit.query(action="import")[:limit]

    # ------------------------------------------------------------------
    # 合规报告
    # ------------------------------------------------------------------

    def compliance_report(self) -> dict:
        """
        生成合规报告：统计导出/导入次数、脱敏应用率、最近活动。

        Returns:
            {
                "generated_at", "export_count", "import_count",
                "mask_applied_rate", "masked_export_count",
                "total_records_exported", "total_records_imported",
                "access_count", "last_activity", "recent_exports",
                "recent_imports",
            }
        """
        exports = self.audit.query(action="export")
        imports = self.audit.query(action="import")
        all_entries = self.audit.query()

        export_count = len(exports)
        masked_exports = sum(
            1 for e in exports
            if e.get("details", {}).get("mask_applied"))
        mask_applied_rate = (
            round(masked_exports / export_count, 4) if export_count else 0.0)

        total_records_exported = sum(
            e.get("details", {}).get("records_count", 0) for e in exports)
        total_records_imported = sum(
            e.get("details", {}).get("records_count", 0) for e in imports)
        imported_success = sum(
            e.get("details", {}).get("success_count", 0) for e in imports)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "policy": self.policy.policy,
            "export_count": export_count,
            "import_count": len(imports),
            "masked_export_count": masked_exports,
            "mask_applied_rate": mask_applied_rate,
            "total_records_exported": total_records_exported,
            "total_records_imported": total_records_imported,
            "imported_success_count": imported_success,
            "access_count": len(all_entries),
            "last_activity": all_entries[0] if all_entries else None,
            "recent_exports": exports[:5],
            "recent_imports": imports[:5],
        }
        logger.info("migration.compliance_report",
                    exports=export_count, imports=len(imports),
                    mask_rate=mask_applied_rate)
        return report


# 全局单例（使用全局审计单例 migration_audit）
migration_manager = MigrationManager()
