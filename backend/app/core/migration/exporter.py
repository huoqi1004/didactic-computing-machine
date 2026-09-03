"""
数据导出工具（数据迁移模块）
==============================

P3-3 企业就绪阶段的数据导出能力：
- ``DataExporter``：CSV / JSON / Excel（openpyxl）导出，支持字段选择
- ``export_erp_snapshot``：系统完整业务数据快照导出（复用 backup.py
  的采集逻辑：事件存储 / 审计日志 / RBAC / Agent 状态 / 关键配置）
- 导出时自动应用 ``DataMasker`` 脱敏（可通过 ``mask_fields`` 配置）

脱敏语义（mask_fields 参数）：
- ``None``：自动检测敏感字段并脱敏（默认，合规默认开启）
- ``[]``：不脱敏（显式关闭）
- ``dict`` / ``list``：按指定字段/类型脱敏

CSV 使用 ``utf-8-sig`` 编码，兼容 Excel 直接打开中文。
"""
import csv
import json
import os
import structlog
from datetime import datetime, timezone

from app.core.migration.privacy import DataMasker, detect_sensitive_fields

logger = structlog.get_logger()

# 迁移导出目录（可被环境变量覆盖）
_EXPORT_DIR = os.environ.get("ERP_MIGRATION_DIR", "data/migrations")


class DataExporter:
    """
    数据导出器

    支持 CSV / JSON / Excel 三种格式，统一在导出前应用脱敏。
    """

    def __init__(self, masker: DataMasker | None = None,
                 export_dir: str | None = None):
        """
        Args:
            masker: 脱敏器实例（默认新建 DataMasker）
            export_dir: 导出目录（默认 ``data/migrations``，可用环境变量覆盖）
        """
        self.masker = masker or DataMasker()
        self.export_dir = export_dir or _EXPORT_DIR

    # ------------------------------------------------------------------
    # 脱敏预处理
    # ------------------------------------------------------------------

    def _prepare_records(self, records: list[dict],
                         mask_fields) -> tuple[list[dict], bool]:
        """
        根据 mask_fields 配置对记录进行脱敏预处理。

        Returns:
            (处理后的记录, 是否应用了脱敏)
        """
        if mask_fields is None:
            # 默认：自动检测敏感字段并脱敏
            return [
                self.masker.mask_dict(rec, detect_sensitive_fields(rec))
                for rec in records
            ], True
        if not mask_fields:
            # 显式空列表/空字典：不脱敏
            return list(records), False
        return self.masker.mask_list(records, mask_fields), True

    # ------------------------------------------------------------------
    # 各格式导出
    # ------------------------------------------------------------------

    def export_csv(self, records: list[dict], columns: list[str],
                   file_path: str, mask_fields=None) -> dict:
        """
        导出 CSV（utf-8-sig 编码，兼容 Excel 中文）。

        Args:
            records: 记录列表
            columns: 列顺序（仅导出这些字段）
            file_path: 输出文件路径
            mask_fields: 脱敏配置（None=自动 / [] = 不脱敏 / dict|list=指定）

        Returns:
            {"status", "file_path", "records_count", "columns", "mask_applied"}
        """
        prepared, mask_applied = self._prepare_records(records, mask_fields)
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns,
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(prepared)
        logger.info("migration.csv_exported", path=file_path,
                    records=len(prepared), mask=mask_applied)
        return {
            "status": "success",
            "file_path": file_path,
            "records_count": len(prepared),
            "columns": columns,
            "mask_applied": mask_applied,
        }

    def export_json(self, records: list[dict], file_path: str,
                    mask_fields=None) -> dict:
        """
        导出 JSON（ensure_ascii=False，保留中文）。

        Args:
            records: 记录列表
            file_path: 输出文件路径
            mask_fields: 脱敏配置（None=自动 / [] = 不脱敏 / dict|list=指定）

        Returns:
            {"status", "file_path", "records_count", "mask_applied"}
        """
        prepared, mask_applied = self._prepare_records(records, mask_fields)
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(prepared, f, ensure_ascii=False, indent=2, default=str)
        logger.info("migration.json_exported", path=file_path,
                    records=len(prepared), mask=mask_applied)
        return {
            "status": "success",
            "file_path": file_path,
            "records_count": len(prepared),
            "mask_applied": mask_applied,
        }

    def export_excel(self, records: list[dict], columns: list[str],
                     file_path: str, mask_fields=None) -> dict:
        """
        导出 Excel（openpyxl）。

        Args:
            records: 记录列表
            columns: 列顺序
            file_path: 输出文件路径（.xlsx）
            mask_fields: 脱敏配置（None=自动 / [] = 不脱敏 / dict|list=指定）

        Returns:
            {"status", "file_path", "records_count", "columns", "mask_applied"}
        """
        from openpyxl import Workbook

        prepared, mask_applied = self._prepare_records(records, mask_fields)
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "export"
        ws.append(list(columns))
        for rec in prepared:
            ws.append([rec.get(col, "") for col in columns])
        wb.save(file_path)
        logger.info("migration.excel_exported", path=file_path,
                    records=len(prepared), mask=mask_applied)
        return {
            "status": "success",
            "file_path": file_path,
            "records_count": len(prepared),
            "columns": columns,
            "mask_applied": mask_applied,
        }

    # ------------------------------------------------------------------
    # 系统快照导出
    # ------------------------------------------------------------------

    def _mask_nested(self, obj):
        """递归脱敏嵌套结构（对每个 dict 应用敏感字段检测）。"""
        if isinstance(obj, dict):
            sensitive = detect_sensitive_fields(obj)
            base = self.masker.mask_dict(obj, sensitive) if sensitive else obj
            return {k: self._mask_nested(v) for k, v in base.items()}
        if isinstance(obj, list):
            return [self._mask_nested(item) for item in obj]
        return obj

    def export_erp_snapshot(self, mask_fields=None, file_path: str | None = None) -> dict:
        """
        导出系统完整业务数据快照（JSON 文件）。

        采集内容（复用 ``app/core/admin/backup.py::export_snapshot`` 的采集思路）：
        - 事件存储（event_store 内存 streams，最多 1000 条）
        - 审计日志（audit_chain）
        - RBAC 角色配置（rbac_engine）
        - Agent 状态（orchestrator）
        - 关键配置快照（仅非敏感项）

        Args:
            mask_fields: 脱敏配置（None=自动递归脱敏 / [] = 不脱敏 /
                dict|list = 指定字段）
            file_path: 输出文件路径（默认写入导出目录，自动生成文件名）

        Returns:
            {"status", "file_path", "records_count", "mask_applied",
             "sections", "exported_at"}
        """
        snapshot: dict = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
            "sections": {},
        }

        # 1. 事件存储
        try:
            from app.core.event.store import event_store
            events = []
            streams = getattr(event_store, "_streams", {})
            for stream in streams.values():
                events.extend(stream)
            snapshot["sections"]["event_store"] = [
                {
                    "event_id": str(getattr(e, "event_id", "")),
                    "event_type": str(getattr(e, "event_type", "")),
                    "aggregate_id": str(getattr(e, "aggregate_id", "")),
                    "timestamp": str(getattr(e, "timestamp", "")),
                }
                for e in events[-1000:]
            ]
        except Exception as e:  # noqa: BLE001
            snapshot["sections"]["event_store"] = {"error": str(e)}

        # 2. 审计日志
        try:
            from app.core.security.audit import audit_chain
            entries = audit_chain.query() if hasattr(audit_chain, "query") else []
            snapshot["sections"]["audit_logs"] = [
                {
                    "id": e.get("id", e.get("entry_id", "")),
                    "timestamp": e.get("timestamp", ""),
                    "action": e.get("action", ""),
                    "risk_level": e.get("risk_level", ""),
                }
                for e in entries[-1000:]
            ]
        except Exception as e:  # noqa: BLE001
            snapshot["sections"]["audit_logs"] = {"error": str(e)}

        # 3. RBAC 配置
        try:
            from app.core.rbac.engine import rbac_engine
            roles = (rbac_engine.list_roles()
                     if hasattr(rbac_engine, "list_roles") else [])
            snapshot["sections"]["rbac_roles"] = roles
        except Exception as e:  # noqa: BLE001
            snapshot["sections"]["rbac_roles"] = {"error": str(e)}

        # 4. Agent 状态
        try:
            from app.core.agent.orchestrator import orchestrator
            agent_stats = (orchestrator.get_stats()
                           if hasattr(orchestrator, "get_stats") else {})
            snapshot["sections"]["agent_status"] = agent_stats
        except Exception as e:  # noqa: BLE001
            snapshot["sections"]["agent_status"] = {"error": str(e)}

        # 5. 关键配置快照（仅非敏感项）
        try:
            from app.core.config import settings
            snapshot["sections"]["config"] = {
                "app_version": settings.app_version,
                "kafka_enabled": settings.kafka_enabled,
                "debug": settings.debug,
            }
        except Exception as e:  # noqa: BLE001
            snapshot["sections"]["config"] = {"error": str(e)}

        # 统计总记录数（各 section 条目数之和）
        total_count = 0
        for section in snapshot["sections"].values():
            if isinstance(section, list):
                total_count += len(section)

        # 脱敏：快照为嵌套结构，默认递归自动脱敏；mask_fields=[] 时不脱敏；
        # 其他显式字段配置不适用于嵌套结构，统一回退为递归自动脱敏
        if mask_fields is not None and not mask_fields:
            payload_obj = snapshot
            mask_applied = False
        else:
            payload_obj = self._mask_nested(snapshot)
            mask_applied = True

        # 序列化并写入文件
        os.makedirs(os.path.dirname(os.path.abspath(file_path)) if file_path
                    else self.export_dir, exist_ok=True)
        if not file_path:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            file_path = os.path.join(
                self.export_dir, f"erp_migration_snapshot_{timestamp}.json")

        payload = json.dumps(payload_obj, ensure_ascii=False,
                             indent=2, default=str)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(payload)

        logger.info("migration.snapshot_exported", path=file_path,
                    sections=len(snapshot["sections"]),
                    records=total_count, mask=mask_applied)
        return {
            "status": "success",
            "file_path": file_path,
            "records_count": total_count,
            "mask_applied": mask_applied,
            "sections": list(snapshot["sections"].keys()),
            "exported_at": snapshot["exported_at"],
        }
