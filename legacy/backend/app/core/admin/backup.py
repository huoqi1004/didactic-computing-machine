"""
备份和恢复模块
CIO管理员系统的数据保护中心，提供全量/增量备份、调度、恢复、验证和灾难恢复能力

特性：
- 全量备份、增量备份
- 备份调度管理
- 备份恢复（指定时间点恢复）
- 备份验证（完整性校验）
- 备份存储管理（本地/远程/云）
- 灾难恢复计划管理
"""
import hashlib
import json
import os
import structlog
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from uuid import uuid4

logger = structlog.get_logger()

# 数据快照导出目录（可被环境变量覆盖）
_SNAPSHOT_DIR = os.environ.get("ERP_BACKUP_DIR", "data/backups")


class BackupManager:
    """
    备份和恢复管理器

    职责：
    - 执行全量备份和增量备份
    - 管理备份调度计划
    - 执行备份恢复（指定备份ID或时间点恢复）
    - 验证备份完整性
    - 管理备份存储位置（本地/远程/云）
    - 管理灾难恢复计划（RPO/RTO目标）
    """

    # 备份存储位置
    STORAGE_LOCATIONS = ["local", "remote", "cloud"]

    def __init__(self):
        # 备份记录：{backup_id: backup_dict}
        self._backups: dict[str, dict] = {}
        # 备份调度计划：list[schedule_dict]
        self._schedules: list[dict] = []
        # 恢复历史：list[restore_dict]
        self._restore_history: list[dict] = []
        # 灾难恢复计划
        self._dr_plan: dict = {}
        # 备份存储配置
        self._storage_config: dict = {}
        self._seed_data()

    def _seed_data(self) -> None:
        """加载示例数据"""
        now = datetime.now(timezone.utc)
        # 备份记录示例
        self._backups = {
            "backup-001": {
                "backup_id": "backup-001",
                "type": "full",
                "description": "每周全量备份",
                "status": "completed",
                "size_mb": 5120,
                "storage": "cloud",
                "storage_path": "s3://erp-backups/full/2026-07-28",
                "checksum": hashlib.sha256(b"backup-001-content").hexdigest(),
                "created_at": (now - timedelta(days=1)).isoformat(),
                "completed_at": (now - timedelta(days=1, seconds=-300)).isoformat(),
                "created_by": "scheduler",
                "verified": True,
                "verified_at": (now - timedelta(days=1, seconds=-600)).isoformat(),
                "contains": ["database", "configurations", "agent_states", "event_store"],
                "retention_days": 30,
                "expires_at": (now + timedelta(days=29)).isoformat(),
            },
            "backup-002": {
                "backup_id": "backup-002",
                "type": "incremental",
                "description": "每日增量备份",
                "status": "completed",
                "size_mb": 256,
                "storage": "cloud",
                "storage_path": "s3://erp-backups/incr/2026-07-28",
                "checksum": hashlib.sha256(b"backup-002-content").hexdigest(),
                "created_at": now.isoformat(),
                "completed_at": now.isoformat(),
                "created_by": "scheduler",
                "verified": False,
                "verified_at": None,
                "contains": ["database", "event_store"],
                "parent_backup_id": "backup-001",
                "retention_days": 7,
                "expires_at": (now + timedelta(days=7)).isoformat(),
            },
            "backup-003": {
                "backup_id": "backup-003",
                "type": "full",
                "description": "每月归档备份",
                "status": "completed",
                "size_mb": 15360,
                "storage": "remote",
                "storage_path": "nfs://backup-server/monthly/2026-07",
                "checksum": hashlib.sha256(b"backup-003-content").hexdigest(),
                "created_at": (now - timedelta(days=7)).isoformat(),
                "completed_at": (now - timedelta(days=7, seconds=-900)).isoformat(),
                "created_by": "admin",
                "verified": True,
                "verified_at": (now - timedelta(days=7, seconds=-1200)).isoformat(),
                "contains": ["database", "configurations", "agent_states",
                            "event_store", "knowledge_base", "audit_logs"],
                "retention_days": 365,
                "expires_at": (now + timedelta(days=358)).isoformat(),
            },
        }
        # 备份调度示例
        self._schedules = [
            {
                "schedule_id": "sched-001",
                "cron_expression": "0 2 * * 0",  # 每周日凌晨2点
                "backup_type": "full",
                "retention_days": 30,
                "storage": "cloud",
                "enabled": True,
                "last_run": (now - timedelta(days=1)).isoformat(),
                "next_run": (now + timedelta(days=6)).isoformat(),
                "description": "每周全量备份",
            },
            {
                "schedule_id": "sched-002",
                "cron_expression": "0 3 * * *",  # 每日凌晨3点
                "backup_type": "incremental",
                "retention_days": 7,
                "storage": "cloud",
                "enabled": True,
                "last_run": now.isoformat(),
                "next_run": (now + timedelta(days=1)).isoformat(),
                "description": "每日增量备份",
            },
            {
                "schedule_id": "sched-003",
                "cron_expression": "0 4 1 * *",  # 每月1日凌晨4点
                "backup_type": "full",
                "retention_days": 365,
                "storage": "remote",
                "enabled": True,
                "last_run": (now - timedelta(days=7)).isoformat(),
                "next_run": (now + timedelta(days=23)).isoformat(),
                "description": "每月归档备份",
            },
        ]
        # 存储配置
        self._storage_config = {
            "local": {
                "path": "/var/backups/erp",
                "max_size_gb": 100,
                "current_usage_gb": 45.2,
            },
            "remote": {
                "type": "nfs",
                "path": "nfs://backup-server/backups",
                "max_size_gb": 500,
                "current_usage_gb": 120.5,
            },
            "cloud": {
                "type": "s3",
                "bucket": "erp-backups",
                "region": "cn-north-1",
                "max_size_gb": 1000,
                "current_usage_gb": 280.8,
                "encryption": True,
                "versioning": True,
            },
        }
        # 灾难恢复计划
        self._dr_plan = {
            "version": "2.0",
            "last_updated": now.isoformat(),
            "updated_by": "admin",
            "rpo_target_minutes": 60,  # 恢复点目标
            "rto_target_minutes": 240,  # 恢复时间目标
            "strategies": [
                {
                    "name": "主备切换",
                    "description": "主数据中心故障时切换至备用数据中心",
                    "priority": 1,
                    "estimated_time_minutes": 30,
                    "responsible_team": "运维团队",
                },
                {
                    "name": "云上恢复",
                    "description": "从云端备份恢复到新环境",
                    "priority": 2,
                    "estimated_time_minutes": 120,
                    "responsible_team": "运维团队",
                },
                {
                    "name": "本地恢复",
                    "description": "从本地备份恢复",
                    "priority": 3,
                    "estimated_time_minutes": 60,
                    "responsible_team": "运维团队",
                },
            ],
            "contact_list": [
                {"role": "DR协调员", "name": "张运维", "phone": "138-0000-0001"},
                {"role": "DBA", "name": "李数据库", "phone": "138-0000-0002"},
                {"role": "网络工程师", "name": "王网络", "phone": "138-0000-0003"},
                {"role": "安全负责人", "name": "赵安全", "phone": "138-0000-0004"},
            ],
            "test_schedule": "0 2 * * 6",  # 每六个月测试一次
            "last_tested": (now - timedelta(days=90)).isoformat(),
            "last_test_result": "passed",
            "critical_systems": [
                "数据库主集群",
                "事件总线",
                "Agent编排器",
                "API网关",
            ],
        }
        logger.info("backup.seeded",
                    backups=len(self._backups),
                    schedules=len(self._schedules))

    def create_backup(
        self, type: str = "full", description: str = ""
    ) -> dict:
        """
        创建备份

        参数:
            type: 备份类型（full/incremental）
            description: 备份描述

        Returns:
            dict: 备份创建结果
        """
        if type not in ("full", "incremental"):
            return {"error": "invalid_type", "type": type,
                    "valid_types": ["full", "incremental"]}

        now = datetime.now(timezone.utc)
        backup_id = f"backup-{str(uuid4())[:8]}"

        # 模拟备份大小
        import random
        if type == "full":
            size_mb = random.randint(4000, 8000)
        else:
            size_mb = random.randint(100, 500)

        # 查找最近的全量备份作为增量备份的父备份
        parent_backup_id = None
        if type == "incremental":
            full_backups = [
                b for b in self._backups.values()
                if b["type"] == "full" and b["status"] == "completed"
            ]
            if full_backups:
                parent_backup_id = max(
                    full_backups, key=lambda b: b["created_at"]
                )["backup_id"]

        contains = ["database", "configurations", "agent_states", "event_store"]
        if type == "full":
            contains.extend(["knowledge_base", "audit_logs"])

        backup = {
            "backup_id": backup_id,
            "type": type,
            "description": description or f"{'全量' if type == 'full' else '增量'}备份",
            "status": "completed",
            "size_mb": size_mb,
            "storage": "cloud",
            "storage_path": f"s3://erp-backups/{'full' if type == 'full' else 'incr'}/{now.strftime('%Y-%m-%d')}/{backup_id}",
            "checksum": hashlib.sha256(f"{backup_id}-{now.isoformat()}".encode()).hexdigest(),
            "created_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "created_by": "admin",
            "verified": False,
            "verified_at": None,
            "contains": contains,
            "retention_days": 30 if type == "full" else 7,
            "expires_at": (now + timedelta(days=30 if type == "full" else 7)).isoformat(),
        }
        if parent_backup_id:
            backup["parent_backup_id"] = parent_backup_id

        self._backups[backup_id] = backup

        logger.info("backup.created",
                    backup_id=backup_id, type=type, size_mb=size_mb)
        return {"status": "success", "backup": backup}

    def export_snapshot(self) -> dict:
        """
        导出系统真实数据快照到本地文件（JSON）。

        采集当前运行态的以下数据：
        - 事件存储（event_store 持久化记录）
        - 审计日志（audit_chain）
        - RBAC 配置（角色/权限/用户）
        - Agent 状态（orchestrator）
        - 配置快照（settings 关键项）

        Returns:
            dict: 导出结果（含文件路径、条目数、SHA-256 校验和）
        """
        snapshot: dict[str, Any] = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
            "sections": {},
        }

        # 1. 事件存储（读取内存 streams，最多 1000 条）
        try:
            from app.core.event.store import event_store
            events: list[Any] = []
            streams = getattr(event_store, "_streams", {})
            for stream in streams.values():
                events.extend(stream)
            snapshot["sections"]["event_store"] = [
                {
                    "event_id": str(getattr(e, "event_id", "")),
                    "event_type": getattr(e, "event_type", ""),
                    "aggregate_id": getattr(e, "aggregate_id", ""),
                    "timestamp": str(getattr(e, "timestamp", "")),
                }
                for e in events[-1000:]
            ]
        except Exception as e:
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
        except Exception as e:
            snapshot["sections"]["audit_logs"] = {"error": str(e)}

        # 3. RBAC 配置
        try:
            from app.core.rbac.engine import rbac_engine
            roles = rbac_engine.list_roles() if hasattr(rbac_engine, "list_roles") else []
            snapshot["sections"]["rbac_roles"] = roles
        except Exception as e:
            snapshot["sections"]["rbac_roles"] = {"error": str(e)}

        # 4. Agent 状态
        try:
            from app.core.agent.orchestrator import orchestrator
            agent_stats = orchestrator.get_stats() if hasattr(orchestrator, "get_stats") else {}
            snapshot["sections"]["agent_status"] = agent_stats
        except Exception as e:
            snapshot["sections"]["agent_status"] = {"error": str(e)}

        # 5. 关键配置快照（仅非敏感项）
        try:
            from app.core.config import settings
            snapshot["sections"]["config"] = {
                "app_version": settings.app_version,
                "kafka_enabled": settings.kafka_enabled,
                "debug": settings.debug,
            }
        except Exception as e:
            snapshot["sections"]["config"] = {"error": str(e)}

        # 序列化并写入文件（二进制写入避免换行符转换导致校验和不一致）
        os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(_SNAPSHOT_DIR, f"erp_snapshot_{timestamp}.json")
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
        payload_bytes = payload.encode("utf-8")
        with open(file_path, "wb") as f:
            f.write(payload_bytes)

        checksum = hashlib.sha256(payload_bytes).hexdigest()
        size_bytes = len(payload_bytes)

        # 登记为一次备份记录
        backup_id = f"backup-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc)
        self._backups[backup_id] = {
            "backup_id": backup_id,
            "type": "snapshot",
            "description": "实时数据快照导出",
            "status": "completed",
            "size_mb": round(size_bytes / 1024 / 1024, 2),
            "storage": "local",
            "storage_path": file_path,
            "checksum": checksum,
            "created_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "created_by": "admin",
            "verified": True,
            "verified_at": now.isoformat(),
            "contains": list(snapshot["sections"].keys()),
            "retention_days": 30,
            "expires_at": (now + timedelta(days=30)).isoformat(),
        }

        logger.info("backup.snapshot_exported",
                    backup_id=backup_id,
                    path=file_path,
                    size_bytes=size_bytes,
                    sections=len(snapshot["sections"]))
        return {
            "status": "success",
            "backup": self._backups[backup_id],
            "checksum": checksum,
            "sections": list(snapshot["sections"].keys()),
            "exported_at": snapshot["exported_at"],
        }

    def get_snapshot_data(self, backup_id: str) -> dict:
        """
        读取已导出的快照数据（用于恢复前预览）。

        Args:
            backup_id: 备份ID

        Returns:
            dict: 快照内容；备份不存在或非快照时返回错误
        """
        backup = self._backups.get(backup_id)
        if not backup:
            return {"error": "backup_not_found", "backup_id": backup_id}
        if backup.get("type") != "snapshot":
            return {"error": "not_a_snapshot_backup", "backup_id": backup_id}

        path = backup.get("storage_path", "")
        if not os.path.exists(path):
            return {"error": "snapshot_file_missing", "path": path}

        try:
            with open(path, "r", encoding="utf-8") as f:
                return {"status": "success", "snapshot": json.load(f)}
        except Exception as e:
            return {"error": "snapshot_read_failed", "detail": str(e)}

    def get_backups(
        self,
        backup_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """
        获取备份列表

        参数:
            backup_type: 可选，按备份类型过滤（full/incremental）
            status: 可选，按状态过滤（completed/failed/in_progress）

        Returns:
            list[dict]: 备份列表
        """
        backups = list(self._backups.values())
        if backup_type:
            backups = [b for b in backups if b["type"] == backup_type]
        if status:
            backups = [b for b in backups if b["status"] == status]
        # 按创建时间倒序
        backups.sort(key=lambda b: b["created_at"], reverse=True)
        logger.info("backup.listed",
                    type=backup_type, status=status, count=len(backups))
        return backups

    def restore_backup(
        self, backup_id: str, target: str = "current"
    ) -> dict:
        """
        从备份恢复数据

        参数:
            backup_id: 备份ID
            target: 恢复目标（current=当前环境/new=新环境）

        Returns:
            dict: 恢复结果
        """
        backup = self._backups.get(backup_id)
        if not backup:
            return {"error": "backup_not_found", "backup_id": backup_id}

        if backup["status"] != "completed":
            return {"error": "backup_not_ready",
                    "backup_id": backup_id,
                    "status": backup["status"]}

        now = datetime.now(timezone.utc)
        # 模拟恢复过程
        import random
        duration = round(random.uniform(5.0, 60.0), 1)
        is_success = random.random() > 0.05  # 95%成功率

        restore_record = {
            "restore_id": str(uuid4()),
            "backup_id": backup_id,
            "target": target,
            "status": "success" if is_success else "failed",
            "started_at": now.isoformat(),
            "completed_at": (now + timedelta(seconds=duration)).isoformat(),
            "duration_seconds": duration,
            "restored_by": "admin",
            "backup_type": backup["type"],
            "restored_items": backup["contains"] if is_success else [],
            "error": None if is_success else "restore_checksum_mismatch",
        }
        self._restore_history.append(restore_record)

        result = {
            "status": "success" if is_success else "failed",
            "restore_id": restore_record["restore_id"],
            "backup_id": backup_id,
            "target": target,
            "duration_seconds": duration,
            "restored_items": restore_record["restored_items"],
            "completed_at": restore_record["completed_at"],
        }
        if not is_success:
            result["error"] = restore_record["error"]

        logger.info("backup.restored",
                    backup_id=backup_id, success=is_success,
                    duration=duration)
        return result

    def verify_backup(self, backup_id: str) -> dict:
        """
        验证备份完整性

        参数:
            backup_id: 备份ID

        Returns:
            dict: 验证结果
        """
        backup = self._backups.get(backup_id)
        if not backup:
            return {"error": "backup_not_found", "backup_id": backup_id}

        now = datetime.now(timezone.utc)
        # 模拟完整性校验
        import random
        is_valid = random.random() > 0.02  # 98%通过率

        backup["verified"] = is_valid
        backup["verified_at"] = now.isoformat()

        # 模拟校验细节
        checks = [
            {"check": "checksum_verification", "passed": is_valid},
            {"check": "file_count_verification", "passed": is_valid},
            {"check": "metadata_consistency", "passed": True},
            {"check": "encryption_verification", "passed": True},
        ]

        result = {
            "status": "valid" if is_valid else "invalid",
            "backup_id": backup_id,
            "verified_at": now.isoformat(),
            "checks": checks,
            "backup_size_mb": backup["size_mb"],
            "all_passed": is_valid,
        }

        logger.info("backup.verified",
                    backup_id=backup_id, valid=is_valid)
        return result

    def delete_backup(self, backup_id: str) -> dict:
        """
        删除备份

        参数:
            backup_id: 备份ID

        Returns:
            dict: 删除结果
        """
        backup = self._backups.get(backup_id)
        if not backup:
            return {"error": "backup_not_found", "backup_id": backup_id}

        # 检查是否有增量备份依赖此全量备份
        if backup["type"] == "full":
            dependent = [
                b["backup_id"] for b in self._backups.values()
                if b.get("parent_backup_id") == backup_id
            ]
            if dependent:
                return {
                    "error": "has_dependent_backups",
                    "backup_id": backup_id,
                    "dependent_backups": dependent,
                }

        deleted = self._backups.pop(backup_id)
        logger.info("backup.deleted", backup_id=backup_id)
        return {
            "status": "success",
            "deleted_backup": {
                "backup_id": backup_id,
                "type": deleted["type"],
                "size_mb": deleted["size_mb"],
            },
        }

    def get_backup_schedule(self) -> list[dict]:
        """
        获取备份调度计划

        Returns:
            list[dict]: 调度计划列表
        """
        logger.info("backup.schedule_listed", count=len(self._schedules))
        return list(self._schedules)

    def set_backup_schedule(
        self,
        cron_expression: str,
        backup_type: str,
        retention_days: int = 30,
        storage: str = "cloud",
        description: str = "",
    ) -> dict:
        """
        设置备份调度计划

        参数:
            cron_expression: cron调度表达式
            backup_type: 备份类型（full/incremental）
            retention_days: 保留天数，默认30天
            storage: 存储位置（local/remote/cloud），默认cloud
            description: 调度描述

        Returns:
            dict: 调度计划信息
        """
        if backup_type not in ("full", "incremental"):
            return {"error": "invalid_backup_type", "backup_type": backup_type}
        if storage not in self.STORAGE_LOCATIONS:
            return {"error": "invalid_storage", "storage": storage,
                    "valid_storages": self.STORAGE_LOCATIONS}

        now = datetime.now(timezone.utc)
        schedule = {
            "schedule_id": f"sched-{str(uuid4())[:8]}",
            "cron_expression": cron_expression,
            "backup_type": backup_type,
            "retention_days": retention_days,
            "storage": storage,
            "enabled": True,
            "last_run": None,
            "next_run": None,
            "description": description,
            "created_at": now.isoformat(),
        }
        self._schedules.append(schedule)

        logger.info("backup.schedule_set",
                    schedule_id=schedule["schedule_id"],
                    cron=cron_expression, type=backup_type)
        return {"status": "success", "schedule": schedule}

    def get_disaster_recovery_plan(self) -> dict:
        """
        获取灾难恢复计划

        Returns:
            dict: 灾难恢复计划，包含RPO/RTO目标、恢复策略、联系人列表等
        """
        logger.info("backup.dr_plan_retrieved")
        return dict(self._dr_plan)

    def update_disaster_recovery_plan(self, plan: dict) -> dict:
        """
        更新灾难恢复计划

        参数:
            plan: 灾难恢复计划字典，可包含部分字段进行更新

        Returns:
            dict: 更新后的灾难恢复计划
        """
        now = datetime.now(timezone.utc)
        self._dr_plan.update(plan)
        self._dr_plan["last_updated"] = now.isoformat()
        self._dr_plan["updated_by"] = "admin"

        logger.info("backup.dr_plan_updated",
                    version=self._dr_plan.get("version"))
        return {"status": "success", "plan": dict(self._dr_plan)}

    def get_storage_usage(self) -> dict:
        """
        获取备份存储使用情况

        Returns:
            dict: 各存储位置的使用量和容量
        """
        usage = {}
        for location, config in self._storage_config.items():
            max_gb = config.get("max_size_gb", 0)
            current_gb = config.get("current_usage_gb", 0)
            usage[location] = {
                **config,
                "usage_percent": round(
                    (current_gb / max_gb * 100) if max_gb > 0 else 0, 1
                ),
            }
        total_max = sum(c.get("max_size_gb", 0) for c in self._storage_config.values())
        total_used = sum(c.get("current_usage_gb", 0) for c in self._storage_config.values())
        usage["total"] = {
            "max_size_gb": total_max,
            "current_usage_gb": round(total_used, 1),
            "usage_percent": round(
                (total_used / total_max * 100) if total_max > 0 else 0, 1
            ),
        }
        return usage

    def get_restore_history(self) -> list[dict]:
        """
        获取恢复操作历史

        Returns:
            list[dict]: 恢复历史记录列表
        """
        history = list(self._restore_history)
        history.sort(key=lambda r: r["started_at"], reverse=True)
        logger.info("backup.restore_history_listed", count=len(history))
        return history
