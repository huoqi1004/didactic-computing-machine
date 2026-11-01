"""
数据导入工具（数据迁移模块）
==============================

P3-3 企业就绪阶段的数据导入能力：
- ``DataImporter``：CSV / JSON 导入（CSV 支持字段映射）
- ``validate_records``：记录校验（必填字段 / 类型 / 枚举值）

导入报告格式：
{
    "status": "success",
    "total": int,             # 总记录数
    "success_count": int,     # 成功条数
    "failed_count": int,      # 失败条数
    "errors": [{"record_index", "field", "error"}, ...],
    "records": [...],         # 导入的原始记录
    "validation": {...},      # 可选：schema 校验详情
}
"""
import csv
import json
import os
import structlog

logger = structlog.get_logger()

# 支持的类型别名 → 校验器
_TYPE_CHECKERS = {
    "int": lambda v: isinstance(v, bool) or isinstance(v, int)
        or (isinstance(v, str) and v.strip().lstrip("+-").isdigit()),
    "float": lambda v: isinstance(v, (int, float))
        or (isinstance(v, str) and v.strip().replace(".", "", 1)
            .replace("-", "", 1).replace("+", "", 1).isdigit()),
    "number": lambda v: isinstance(v, (int, float))
        or (isinstance(v, str) and v.strip().replace(".", "", 1)
            .replace("-", "", 1).replace("+", "", 1).isdigit()),
    "bool": lambda v: isinstance(v, bool)
        or (isinstance(v, str) and v.strip().lower() in ("true", "false", "1", "0")),
    "str": lambda v: isinstance(v, str),
}


class DataImporter:
    """
    数据导入器

    支持 CSV / JSON 文件导入与记录校验，全部离线运行。
    """

    # ------------------------------------------------------------------
    # 文件导入
    # ------------------------------------------------------------------

    def import_csv(self, file_path: str, mapping: dict | None = None) -> dict:
        """
        导入 CSV 文件（utf-8-sig / utf-8 兼容读取）。

        Args:
            file_path: CSV 文件路径
            mapping: 字段映射 {源列名: 目标字段名}；为 None 时原样导入

        Returns:
            导入报告（含 records）
        """
        if not os.path.exists(file_path):
            return self._error_report(f"file_not_found: {file_path}")
        try:
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                raw_rows = list(reader)
        except Exception as e:  # noqa: BLE001
            logger.error("migration.csv_read_failed", path=file_path, error=str(e))
            return self._error_report(f"csv_read_failed: {e}")

        records = []
        for row in raw_rows:
            if mapping:
                records.append({dst: row.get(src) for src, dst in mapping.items()})
            else:
                records.append(dict(row))

        logger.info("migration.csv_imported", path=file_path,
                    total=len(records), mapping=bool(mapping))
        return self._success_report(records)

    def import_json(self, file_path: str) -> dict:
        """
        导入 JSON 文件。

        支持两种结构：
        - 顶层为记录列表：``[{...}, {...}]``
        - 顶层为对象：``{"records": [{...}]}``

        Args:
            file_path: JSON 文件路径

        Returns:
            导入报告（含 records）
        """
        if not os.path.exists(file_path):
            return self._error_report(f"file_not_found: {file_path}")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.error("migration.json_read_failed", path=file_path, error=str(e))
            return self._error_report(f"json_read_failed: {e}")

        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = data.get("records", [data])
        else:
            return self._error_report("unsupported_json_structure")

        logger.info("migration.json_imported", path=file_path,
                    total=len(records))
        return self._success_report(records)

    def import_data(self, file_path: str | None = None,
                    records: list[dict] | None = None,
                    mapping: dict | None = None,
                    schema: dict | None = None) -> dict:
        """
        统一导入入口：支持文件（CSV/JSON）或直接传入记录。

        Args:
            file_path: 文件路径（.csv / .json / .jsonl）
            records: 直接传入的记录列表（优先于文件）
            mapping: CSV 字段映射
            schema: 校验规则（可选，见 validate_records）

        Returns:
            导入报告
        """
        if records is None:
            if not file_path:
                return self._error_report("file_path_or_records_required")
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".csv":
                report = self.import_csv(file_path, mapping)
            elif ext in (".json", ".jsonl"):
                report = self.import_json(file_path)
            else:
                return self._error_report(
                    f"unsupported_file_format: {ext or '(none)'}")
        else:
            report = self._success_report(list(records))

        if schema and report.get("status") == "success" and report["records"]:
            validation = self.validate_records(report["records"], schema)
            report["success_count"] = validation["success_count"]
            report["failed_count"] = validation["failed_count"]
            report["errors"] = validation["errors"]
            report["validation"] = validation

        report.setdefault("errors", [])
        return report

    # ------------------------------------------------------------------
    # 记录校验
    # ------------------------------------------------------------------

    def validate_records(self, records: list[dict], schema: dict) -> dict:
        """
        校验记录列表。

        Args:
            records: 待校验记录
            schema: 形如
                {
                    "field_name": {
                        "required": bool,
                        "type": "str|int|float|number|bool",
                        "enum": [允许值...],
                    },
                    ...
                }

        Returns:
            {
                "status", "total", "success_count", "failed_count",
                "errors": [{"record_index", "field", "error"}],
                "valid_records": [...],
            }
        """
        errors = []
        valid_records = []
        for idx, rec in enumerate(records):
            rec_errors = []
            for field, rules in (schema or {}).items():
                value = rec.get(field)
                # 必填校验
                if rules.get("required") and (value is None or value == ""):
                    rec_errors.append(f"{field}: required field is missing")
                    continue
                if value is None or value == "":
                    continue
                # 类型校验
                expected = rules.get("type")
                if expected:
                    checker = _TYPE_CHECKERS.get(expected)
                    if checker is None:
                        rec_errors.append(f"{field}: unsupported type '{expected}'")
                    elif not checker(value):
                        rec_errors.append(
                            f"{field}: expected {expected}, got {type(value).__name__}")
                # 枚举校验
                enum = rules.get("enum")
                if enum is not None and value not in enum:
                    rec_errors.append(f"{field}: value not in enum {enum}")

            if rec_errors:
                errors.append({
                    "record_index": idx,
                    "field": rec_errors[0].split(":")[0].strip(),
                    "error": "; ".join(rec_errors),
                })
            else:
                valid_records.append(rec)

        logger.info("migration.records_validated",
                    total=len(records), valid=len(valid_records),
                    failed=len(records) - len(valid_records))
        return {
            "status": "success",
            "total": len(records),
            "success_count": len(valid_records),
            "failed_count": len(records) - len(valid_records),
            "errors": errors,
            "valid_records": valid_records,
        }

    # ------------------------------------------------------------------
    # 报告构造
    # ------------------------------------------------------------------

    @staticmethod
    def _success_report(records: list[dict]) -> dict:
        return {
            "status": "success",
            "total": len(records),
            "success_count": len(records),
            "failed_count": 0,
            "errors": [],
            "records": records,
        }

    @staticmethod
    def _error_report(message: str) -> dict:
        return {
            "status": "error",
            "total": 0,
            "success_count": 0,
            "failed_count": 0,
            "errors": [{"record_index": None, "field": "",
                        "error": message}],
            "records": [],
        }
