"""
P3-3 数据迁移工具与合规隐私模块测试
====================================

覆盖：
- DataMasker 各类型脱敏（手机/身份证/邮箱/姓名/账号/自定义）及边界输入
- mask_dict / mask_list / detect_sensitive_fields
- PrivacyPolicy 合规校验与按策略脱敏
- DataExporter CSV/JSON/Excel 导出与自动脱敏、系统快照导出
- DataImporter 导入与记录校验（错误记录处理）
- MigrationAuditTrail 记录与查询（含 JSONL 持久化）
- MigrationManager 导出/导入/历史/合规报告
- 数据迁移 API 端点

全部离线运行；文件操作均使用 pytest 临时目录。
"""
import csv
import json
import os

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.migration import (
    DataMasker,
    DataExporter,
    DataImporter,
    PrivacyPolicy,
    MigrationAuditTrail,
    MigrationManager,
    detect_sensitive_fields,
    migration_audit,
)
from app.api.routes.admin_system import router as admin_router

# 测试用客户记录
CUSTOMERS = [
    {
        "name": "张三",
        "phone": "13812345678",
        "email": "zhangsan@example.com",
        "id_card": "110101199003071234",
        "account": "6222000011111234",
        "address": "北京市朝阳区",
    },
    {
        "name": "李四",
        "phone": "13987654321",
        "email": "lisi@example.com",
        "id_card": "110101198805051234",
        "account": "6222888899995678",
        "address": "上海市浦东新区",
    },
]
COLUMNS = ["name", "phone", "email", "id_card", "account", "address"]


# ========== DataMasker 各类型脱敏 ==========

class TestDataMasker:

    def test_mask_phone(self):
        masker = DataMasker()
        assert masker.mask("13812345678", "phone") == "138****5678"
        # 带分隔符格式
        assert masker.mask("138-1234-5678", "phone") == "138****5678"
        assert masker.mask("+86 138 1234 5678", "phone") == "138****5678"

    def test_mask_id_card(self):
        masker = DataMasker()
        assert masker.mask("110101199003071234", "id_card") == "1101**********1234"

    def test_mask_email(self):
        masker = DataMasker()
        assert masker.mask("zhangsan@example.com", "email") == "z***@example.com"
        # 单字符用户名
        assert masker.mask("a@x.com", "email") == "*@x.com"

    def test_mask_name(self):
        masker = DataMasker()
        assert masker.mask("张三", "name") == "张*"
        assert masker.mask("张小明", "name") == "张*明"
        assert masker.mask("欧阳娜娜", "name") == "欧**娜"

    def test_mask_account(self):
        masker = DataMasker()
        assert masker.mask("6222000011111234", "account") == "6222 **** 1234"
        # 带空格卡号
        assert masker.mask("6222 0000 1111 1234", "account") == "6222 **** 1234"

    def test_mask_custom(self):
        masker = DataMasker()
        assert masker.mask("13812345678", "custom",
                           head_keep=3, tail_keep=4) == "138****5678"
        assert masker.mask("abc123", "custom",
                           head_keep=2, tail_keep=1) == "ab***3"

    def test_mask_edge_cases(self):
        masker = DataMasker()
        # None / 空字符串
        assert masker.mask(None, "phone") is None
        assert masker.mask("", "phone") == ""
        assert masker.mask("   ", "phone") == ""
        # 短字符串
        assert masker.mask("123", "phone") == "***"
        assert masker.mask("138", "phone") == "***"
        assert masker.mask("1385", "phone") == "1***"
        # 数字输入
        assert masker.mask(13812345678, "phone") == "138****5678"
        # 短身份证
        assert masker.mask("1101", "id_card") == "****"
        # 不支持的类型
        with pytest.raises(ValueError):
            masker.mask("abc", "unknown")

    def test_mask_dict_and_list(self):
        masker = DataMasker()
        # dict 形式：显式指定脱敏类型
        masked = masker.mask_dict(CUSTOMERS[0], {"phone": "phone", "name": "name"})
        assert masked["phone"] == "138****5678"
        assert masked["name"] == "张*"
        assert masked["email"] == "zhangsan@example.com"  # 未指定字段不变
        assert CUSTOMERS[0]["phone"] == "13812345678"  # 原字典不被修改

        # list 形式：按字段名推断类型
        masked = masker.mask_dict(CUSTOMERS[0], ["phone", "email", "id_card"])
        assert masked["phone"] == "138****5678"
        assert masked["email"] == "z***@example.com"
        assert masked["id_card"] == "1101**********1234"

        # mask_list：批量
        records = masker.mask_list(CUSTOMERS, {"phone": "phone"})
        assert len(records) == 2
        assert records[0]["phone"] == "138****5678"
        assert records[1]["phone"] == "139****4321"

    def test_detect_sensitive_fields(self):
        detected = detect_sensitive_fields(CUSTOMERS[0])
        assert set(detected) == {"name", "phone", "email", "id_card", "account", "address"}

        # 非敏感记录
        assert detect_sensitive_fields(
            {"product_code": "A001", "price": 100, "stock": 5}) == []

        # 模式匹配：mobile / contact_number / cert_no
        assert detect_sensitive_fields(
            {"mobile": "1", "contact_number": "2", "cert_no": "3",
             "note": "ok"}) == ["mobile", "contact_number", "cert_no"]


# ========== PrivacyPolicy 合规策略 ==========

class TestPrivacyPolicy:

    def test_check_exports_detects_unmasked(self):
        policy = PrivacyPolicy(policy="pipl")
        # 只脱敏了 phone，其余敏感字段未脱敏 → 不合规
        result = policy.check_exports({
            "records": [CUSTOMERS[0]],
            "masked_fields": ["phone"],
        })
        assert result["compliant"] is False
        fields = {issue["field"] for issue in result["issues"]}
        assert "name" in fields and "email" in fields
        assert "phone" not in fields

    def test_check_exports_compliant_when_all_masked(self):
        policy = PrivacyPolicy(policy="gdpr")
        masker = DataMasker()
        masked = masker.mask_dict(CUSTOMERS[0], ["phone", "email", "id_card",
                                                 "account", "name", "address"])
        result = policy.check_exports({
            "records": [masked],
            "masked_fields": list(masked.keys()),
        })
        assert result["compliant"] is True
        assert result["issues"] == []
        assert result["checked_records"] == 1

    def test_redact_for_export(self):
        policy = PrivacyPolicy(policy="pipl")
        redacted = policy.redact_for_export(CUSTOMERS, policy="pipl")
        assert len(redacted) == 2
        assert redacted[0]["phone"] == "138****5678"
        assert redacted[0]["name"] == "张*"
        assert redacted[0]["address"] != CUSTOMERS[0]["address"]

        # 显式规则 dict
        redacted = policy.redact_for_export(
            CUSTOMERS, policy={"phone": "phone", "email": "email"})
        assert redacted[0]["phone"] == "138****5678"
        assert redacted[0]["email"] == "z***@example.com"
        assert redacted[0]["name"] == "张三"  # 未在规则内


# ========== DataExporter 导出 ==========

class TestDataExporter:

    def test_export_csv_with_auto_mask(self, tmp_path):
        path = str(tmp_path / "customers.csv")
        exporter = DataExporter()
        result = exporter.export_csv(CUSTOMERS, COLUMNS, path)
        assert result["status"] == "success"
        assert result["records_count"] == 2
        assert result["mask_applied"] is True

        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["phone"] == "138****5678"
        assert rows[0]["name"] == "张*"
        assert rows[1]["phone"] == "139****4321"

    def test_export_csv_without_mask(self, tmp_path):
        path = str(tmp_path / "customers_raw.csv")
        exporter = DataExporter()
        result = exporter.export_csv(CUSTOMERS, COLUMNS, path, mask_fields=[])
        assert result["mask_applied"] is False
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["phone"] == "13812345678"

    def test_export_csv_with_explicit_mask_fields(self, tmp_path):
        path = str(tmp_path / "customers_partial.csv")
        exporter = DataExporter()
        exporter.export_csv(CUSTOMERS, COLUMNS, path,
                            mask_fields=["phone", "email"])
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["phone"] == "138****5678"
        assert rows[0]["email"] == "z***@example.com"
        assert rows[0]["name"] == "张三"  # 未指定字段保持原值

    def test_export_json_with_auto_mask(self, tmp_path):
        path = str(tmp_path / "customers.json")
        exporter = DataExporter()
        result = exporter.export_json(CUSTOMERS, path)
        assert result["mask_applied"] is True
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data[0]["phone"] == "138****5678"
        assert data[0]["id_card"] == "1101**********1234"
        assert data[0]["account"] == "6222 **** 1234"

    def test_export_excel_with_auto_mask(self, tmp_path):
        from openpyxl import load_workbook
        path = str(tmp_path / "customers.xlsx")
        exporter = DataExporter()
        result = exporter.export_excel(CUSTOMERS, COLUMNS, path)
        assert result["mask_applied"] is True

        wb = load_workbook(path)
        ws = wb.active
        rows = list(ws.values)
        assert rows[0] == tuple(COLUMNS)
        assert rows[1][0] == "张*"               # name 列
        assert rows[1][1] == "138****5678"       # phone 列
        assert rows[1][4] == "6222 **** 1234"    # account 列

    def test_export_erp_snapshot(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        exporter = DataExporter()
        result = exporter.export_erp_snapshot(file_path=path)
        assert result["status"] == "success"
        assert os.path.exists(path)
        expected = {"event_store", "audit_logs", "rbac_roles",
                    "agent_status", "config"}
        assert set(result["sections"]) == expected
        with open(path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        assert snapshot["version"] == "1.0"
        assert "sections" in snapshot

    def test_export_erp_snapshot_no_mask(self, tmp_path):
        path = str(tmp_path / "snapshot_raw.json")
        exporter = DataExporter()
        result = exporter.export_erp_snapshot(mask_fields=[], file_path=path)
        assert result["mask_applied"] is False
        assert os.path.exists(path)


# ========== DataImporter 导入与校验 ==========

class TestDataImporter:

    def _write_csv(self, tmp_path, rows: list[dict], name="import.csv"):
        path = str(tmp_path / name)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_import_csv(self, tmp_path):
        path = self._write_csv(tmp_path, CUSTOMERS)
        importer = DataImporter()
        report = importer.import_csv(path)
        assert report["status"] == "success"
        assert report["total"] == 2
        assert report["success_count"] == 2
        assert report["records"][0]["phone"] == "13812345678"

    def test_import_csv_with_mapping(self, tmp_path):
        path = self._write_csv(
            tmp_path, [{"客户姓名": "张三", "手机": "13812345678"}])
        importer = DataImporter()
        report = importer.import_csv(path, mapping={"客户姓名": "name", "手机": "phone"})
        assert report["status"] == "success"
        assert report["records"][0] == {"name": "张三", "phone": "13812345678"}

    def test_import_json(self, tmp_path):
        path = str(tmp_path / "import.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"records": CUSTOMERS}, f, ensure_ascii=False)
        importer = DataImporter()
        report = importer.import_json(path)
        assert report["total"] == 2

        # 顶层列表形式
        path2 = str(tmp_path / "import_list.json")
        with open(path2, "w", encoding="utf-8") as f:
            json.dump(CUSTOMERS, f, ensure_ascii=False)
        report = importer.import_json(path2)
        assert report["total"] == 2

    def test_import_missing_file(self, tmp_path):
        importer = DataImporter()
        report = importer.import_csv(str(tmp_path / "not_exist.csv"))
        assert report["status"] == "error"
        assert report["failed_count"] == 0

    def test_validate_records(self):
        importer = DataImporter()
        records = [
            {"name": "张三", "age": 30, "status": "active"},
            {"name": "", "age": "not-a-number", "status": "banned"},  # 必填缺失+类型错误
            {"name": "李四", "age": 25, "status": "active"},
            {"name": "王五", "age": 40, "status": "unknown"},  # 枚举错误
        ]
        schema = {
            "name": {"required": True, "type": "str"},
            "age": {"required": True, "type": "int"},
            "status": {"enum": ["active", "banned"]},
        }
        result = importer.validate_records(records, schema)
        assert result["total"] == 4
        assert result["success_count"] == 2
        assert result["failed_count"] == 2
        assert len(result["errors"]) == 2
        # 错误记录携带索引与字段信息
        error_fields = {e["record_index"]: e["field"] for e in result["errors"]}
        assert error_fields[1] == "name"
        assert error_fields[3] == "status"
        # 有效记录可继续使用
        assert result["valid_records"][0]["name"] == "张三"

    def test_import_data_with_schema(self):
        importer = DataImporter()
        records = [
            {"code": "A001", "amount": 100},
            {"code": "", "amount": 200},       # 必填缺失
            {"code": "A003", "amount": "abc"}, # 类型错误
        ]
        schema = {
            "code": {"required": True, "type": "str"},
            "amount": {"required": True, "type": "number"},
        }
        report = importer.import_data(records=records, schema=schema)
        assert report["total"] == 3
        assert report["success_count"] == 1
        assert report["failed_count"] == 2
        assert len(report["errors"]) == 2
        assert "validation" in report


# ========== MigrationAuditTrail 审计追踪 ==========

class TestMigrationAuditTrail:

    def test_log_and_query(self, tmp_path):
        trail = MigrationAuditTrail(persist_dir=str(tmp_path / "audit"))
        trail.log_export("cio", "erp_snapshot", 120, True)
        trail.log_export("cio", "customers", 50, False)
        trail.log_import("admin", "customers.csv", 100, 95)
        trail.log_access("admin", "customers", "read")

        # 全量查询（倒序）
        entries = trail.query()
        assert len(entries) == 4
        assert entries[0]["timestamp"] >= entries[-1]["timestamp"]

        # 按操作者过滤
        cio_logs = trail.query(actor="cio")
        assert len(cio_logs) == 2

        # 按操作类型过滤
        exports = trail.query(action="export")
        assert len(exports) == 2
        assert all(e["action"] == "export" for e in exports)

        # 按时间范围过滤
        now = datetime.now(timezone.utc)
        recent = trail.query(
            date_from=(now - timedelta(minutes=5)).isoformat(),
            date_to=now.isoformat(),
        )
        assert len(recent) == 4

    def test_log_fields(self, tmp_path):
        trail = MigrationAuditTrail(persist_dir=str(tmp_path / "audit2"))
        entry = trail.log_export("cio", "erp_snapshot", 10, True)
        assert entry["actor"] == "cio"
        assert entry["action"] == "export"
        assert entry["object"] == "scope:erp_snapshot"
        assert entry["result"] == "success"
        assert entry["details"]["mask_applied"] is True
        assert entry["details"]["records_count"] == 10
        # 时间戳可解析
        datetime.fromisoformat(entry["timestamp"])

    def test_jsonl_persistence(self, tmp_path):
        persist_dir = str(tmp_path / "audit3")
        trail = MigrationAuditTrail(persist_dir=persist_dir)
        trail.log_export("cio", "scope_a", 1, True)
        trail.log_import("admin", "file.csv", 3, 2)

        jsonl_path = os.path.join(persist_dir, "migration_audit.jsonl")
        assert os.path.exists(jsonl_path)
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["action"] == "export"

    def test_global_singleton(self, tmp_path):
        migration_audit.clear()
        assert migration_audit.query() == []


# ========== MigrationManager 迁移管理器 ==========

class TestMigrationManager:

    def _make_manager(self, tmp_path):
        audit = MigrationAuditTrail(persist_dir=str(tmp_path / "audit"))
        manager = MigrationManager(
            audit=audit, export_dir=str(tmp_path / "exports"))
        return manager

    def test_export_snapshot(self, tmp_path):
        manager = self._make_manager(tmp_path)
        result = manager.export(scope="erp_snapshot", format="json")
        assert result["status"] == "success"
        assert result["scope"] == "erp_snapshot"
        assert result["format"] == "json"
        assert result["mask_applied"] is True
        assert os.path.exists(result["file_path"])
        # 审计日志已记录
        history = manager.get_export_history()
        assert len(history) == 1
        assert history[0]["details"]["scope"] == "erp_snapshot"

    def test_export_custom_csv(self, tmp_path):
        manager = self._make_manager(tmp_path)
        result = manager.export(scope="customers", format="csv",
                                records=CUSTOMERS, columns=COLUMNS,
                                user="cio")
        assert result["status"] == "success"
        assert result["format"] == "csv"
        assert result["mask_applied"] is True
        assert result["records_count"] == 2
        with open(result["file_path"], "r", encoding="utf-8-sig",
                  newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["phone"] == "138****5678"

    def test_export_excel_and_no_mask(self, tmp_path):
        manager = self._make_manager(tmp_path)
        # excel 导出
        result = manager.export(scope="customers", format="excel",
                                records=CUSTOMERS, columns=COLUMNS)
        assert result["format"] == "excel"
        assert result["file_path"].endswith(".xlsx")
        # 关闭脱敏
        result = manager.export(scope="customers", format="json",
                                records=CUSTOMERS, mask_fields=[])
        assert result["mask_applied"] is False

    def test_export_validation(self, tmp_path):
        manager = self._make_manager(tmp_path)
        # 未知格式
        result = manager.export(scope="customers", format="pdf",
                                records=CUSTOMERS)
        assert "error" in result
        # 自定义 scope 缺少 records
        result = manager.export(scope="customers", format="json")
        assert "error" in result

    def test_import_data(self, tmp_path):
        manager = self._make_manager(tmp_path)
        records = [
            {"name": "张三", "age": 30},
            {"name": "", "age": 25},
        ]
        schema = {"name": {"required": True, "type": "str"}}
        report = manager.import_data(records=records, target="crm",
                                     schema=schema, user="admin")
        assert report["target"] == "crm"
        assert report["total"] == 2
        assert report["success_count"] == 1
        assert report["failed_count"] == 1
        # 审计日志
        history = manager.get_import_history()
        assert len(history) == 1
        assert history[0]["details"]["success_count"] == 1

    def test_import_from_csv_file(self, tmp_path):
        manager = self._make_manager(tmp_path)
        csv_path = str(tmp_path / "in.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["客户姓名", "手机"])
            writer.writeheader()
            writer.writerow({"客户姓名": "张三", "手机": "13812345678"})
        report = manager.import_data(
            file_path=csv_path, target="crm",
            mapping={"客户姓名": "name", "手机": "phone"})
        assert report["status"] == "success"
        assert report["records"][0]["name"] == "张三"
        assert report["audit_log"]["action"] == "import"

    def test_compliance_report(self, tmp_path):
        manager = self._make_manager(tmp_path)
        # 导出2次（一次脱敏一次不脱敏）+ 导入1次 + 访问2次
        manager.export(scope="erp_snapshot", format="json")
        manager.export(scope="customers", format="json",
                       records=CUSTOMERS, mask_fields=[])
        manager.import_data(records=[{"a": 1}], target="t1")
        manager.audit.log_access("admin", "customers", "read")
        manager.audit.log_access("admin", "finance", "write")

        report = manager.compliance_report()
        assert report["export_count"] == 2
        assert report["import_count"] == 1
        assert report["masked_export_count"] == 1
        assert report["mask_applied_rate"] == 0.5
        assert report["access_count"] == 5  # 2导出+1导入+2访问
        assert report["total_records_exported"] > 0
        assert report["recent_exports"][0]["action"] == "export"
        # read/write 在同一微秒内记录，时间戳可能相同 → 断言其一
        assert report["last_activity"]["action"] in ("read", "write")


# ========== 数据迁移 API 端点 ==========

class TestMigrationAPI:

    @pytest_asyncio.fixture
    async def admin_client(self):
        app = FastAPI()
        app.include_router(admin_router)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            migration_audit.clear()
            yield client
            migration_audit.clear()
            # API 端点使用全局单例，会写入 backend/data/audit 与 data/migrations；
            # teardown 时清理这些测试残留文件，保持项目目录干净
            import shutil
            backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            for sub in ("audit", "migrations"):
                shutil.rmtree(os.path.join(backend_dir, "data", sub),
                              ignore_errors=True)

    async def test_get_export(self, admin_client):
        resp = await admin_client.get(
            "/api/v1/admin/data/export?scope=erp_snapshot&format=json&mask=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["scope"] == "erp_snapshot"
        assert body["mask_applied"] is True
        assert body["file_path"]

    async def test_get_export_invalid_format(self, admin_client):
        resp = await admin_client.get(
            "/api/v1/admin/data/export?scope=customers&format=pdf&mask=true")
        assert resp.status_code == 400

    async def test_post_import(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/data/import",
            json={
                "records": [{"name": "张三", "phone": "13812345678"}],
                "target": "crm",
                "schema": {"name": {"required": True, "type": "str"}},
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["success_count"] == 1

    async def test_post_import_missing_body(self, admin_client):
        resp = await admin_client.post("/api/v1/admin/data/import", json={})
        assert resp.status_code == 400

    async def test_histories_and_report(self, admin_client):
        # 制造导出与导入记录
        await admin_client.get(
            "/api/v1/admin/data/export?scope=erp_snapshot&format=json")
        await admin_client.post(
            "/api/v1/admin/data/import", json={"records": [{"x": 1}]})

        resp = await admin_client.get("/api/v1/admin/data/export-history")
        assert resp.status_code == 200
        assert len(resp.json()["exports"]) == 1

        resp = await admin_client.get("/api/v1/admin/data/import-history")
        assert resp.status_code == 200
        assert len(resp.json()["imports"]) == 1

        resp = await admin_client.get("/api/v1/admin/data/compliance-report")
        assert resp.status_code == 200
        report = resp.json()
        assert report["export_count"] == 1
        assert report["import_count"] == 1
        assert report["mask_applied_rate"] == 1.0
