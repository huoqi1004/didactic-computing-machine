"""
合规审计追踪（数据迁移审计）
==============================

P3-3 企业就绪阶段的导出/导入/访问合规审计：
- ``MigrationAuditTrail``：记录导出、导入、数据访问三类操作日志
- 全局单例 ``migration_audit``：内存存储 + 可选 JSONL 文件持久化

日志条目字段：
- timestamp：UTC ISO 8601 时间戳
- actor：操作者
- action：操作类型（export / import / read / write / delete ...）
- object：操作对象
- result：结果（success / failed / denied）
- details：附加明细

文件持久化：append 到 ``data/audit/migration_audit.jsonl``（目录可经
``ERP_MIGRATION_AUDIT_DIR`` 环境变量或构造参数配置），纯内存模式亦可运行。
"""
import json
import os
import structlog
from datetime import datetime, timezone
from uuid import uuid4

logger = structlog.get_logger()

# 审计日志持久化目录（可被环境变量覆盖）
_AUDIT_DIR = os.environ.get("ERP_MIGRATION_AUDIT_DIR", "data/audit")


class MigrationAuditTrail:
    """
    迁移合规审计追踪器

    记录导出/导入/数据访问日志，支持内存查询与 JSONL 文件持久化。
    """

    def __init__(self, persist_dir: str | None = None):
        """
        Args:
            persist_dir: 持久化目录（默认 ``data/audit``，可用环境变量覆盖）；
                传入 ``None`` 且环境变量未设置时使用默认值。
        """
        self._entries: list[dict] = []
        self.persist_dir = persist_dir or _AUDIT_DIR
        os.makedirs(self.persist_dir, exist_ok=True)
        self._jsonl_path = os.path.join(self.persist_dir, "migration_audit.jsonl")

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def _record(self, actor: str, action: str, object_ref: str,
                result: str, details: dict) -> dict:
        """统一记录一条审计日志。"""
        entry = {
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "object": object_ref,
            "result": result,
            "details": details,
        }
        self._entries.append(entry)
        self._persist(entry)
        logger.info("migration.audit", action=action, actor=actor,
                    object=object_ref, result=result)
        return entry

    def log_export(self, user: str, scope: str,
                   records_count: int, mask_applied: bool) -> dict:
        """
        记录一次数据导出操作。

        Args:
            user: 操作者
            scope: 导出范围（如 erp_snapshot / customer / finance）
            records_count: 导出记录数
            mask_applied: 是否应用了脱敏
        """
        return self._record(
            user, "export", f"scope:{scope}", "success",
            {"scope": scope, "records_count": records_count,
             "mask_applied": bool(mask_applied)},
        )

    def log_import(self, user: str, source: str,
                   records_count: int, success_count: int) -> dict:
        """
        记录一次数据导入操作。

        Args:
            user: 操作者
            source: 数据来源（文件路径或描述）
            records_count: 导入总记录数
            success_count: 成功导入条数
        """
        return self._record(
            user, "import", f"source:{source}", "success",
            {"source": source, "records_count": records_count,
             "success_count": success_count},
        )

    def log_access(self, user: str, resource: str, action: str) -> dict:
        """
        记录一次数据访问操作。

        Args:
            user: 操作者
            resource: 被访问资源
            action: 访问动作（read / write / delete / download ...）
        """
        return self._record(user, action, resource, "success", {})

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _persist(self, entry: dict) -> None:
        """append 单条日志到 JSONL 文件（失败仅告警，不影响主流程）。"""
        try:
            with open(self._jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning("migration.audit_persist_failed", error=str(e))

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def query(self, actor: str | None = None, action: str | None = None,
              date_from: str | None = None,
              date_to: str | None = None) -> list[dict]:
        """
        查询审计日志，支持按操作者 / 操作类型 / 时间范围过滤。

        Args:
            actor: 操作者（精确匹配）
            action: 操作类型（export / import / read / write ...）
            date_from: 起始时间（ISO 8601 字符串，含该时刻）
            date_to: 结束时间（ISO 8601 字符串，含该时刻）

        Returns:
            按时间倒序的日志条目列表（不修改内部存储）。
        """
        entries = self._entries
        if actor:
            entries = [e for e in entries if e["actor"] == actor]
        if action:
            entries = [e for e in entries if e["action"] == action]
        if date_from:
            entries = [e for e in entries if e["timestamp"] >= date_from]
        if date_to:
            entries = [e for e in entries if e["timestamp"] <= date_to]
        entries = sorted(entries, key=lambda e: e["timestamp"], reverse=True)
        logger.info("migration.audit_query",
                    actor=actor, action=action, count=len(entries))
        return entries

    def clear(self) -> None:
        """清空内存中的审计日志（测试隔离用）。"""
        self._entries.clear()


# 全局单例（内存存储 + 可选 JSONL 持久化）
migration_audit = MigrationAuditTrail()
