"""
事件/审计数据保留策略（评估报告 B-03 / 路线图任务 2.8）
========================================================

events 表在高频 Agent 运行下以约 8.6 万行/天的速度膨胀且只增不删；
audit_logs 要求保留更长周期（审计合规）。

策略：
- ``events``     默认保留 **7 天**（ERP_EVENT_RETENTION_DAYS）
- ``snapshots``  默认保留 **30 天**（快照体积大、价值随版本衰减）
- ``audit_logs`` 默认保留 **90 天**（ERP_AUDIT_RETENTION_DAYS，长于事件）
- 启动时执行一次清理，此后每 ``ERP_RETENTION_CLEANUP_INTERVAL_HOURS``
  （默认 6 小时）周期执行，由 lifespan 后台任务驱动
- 删除按 ``timestamp`` 列分批执行（每批上限，避免长事务锁表）

目标表为 ``app/core/migrations.py`` 创建的原生表（events/snapshots/audit_logs
），而非 models.py 中尚未投用的 ORM 定义（event_records/audit_entries）。
"""
from __future__ import annotations

import asyncio
import structlog
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.config import settings

logger = structlog.get_logger()

# 单批删除上限：避免单条 DELETE 长时间锁表
_BATCH_SIZE = 5000


async def purge_expired_records(engine: Optional[Any] = None) -> dict:
    """
    按保留策略清理过期的事件、快照与审计记录。

    Returns:
        {"events_deleted": int, "snapshots_deleted": int,
         "audit_logs_deleted": int, "db_available": bool}
    """
    from app.core import database as db_module
    from sqlalchemy import text

    if engine is None:
        engine = db_module.get_engine() or await db_module.init_db()
    if engine is None:
        logger.debug("retention.purge_skipped", reason="db_unavailable")
        return {"events_deleted": 0, "snapshots_deleted": 0,
                "audit_logs_deleted": 0, "db_available": False}

    now = datetime.now(timezone.utc)
    result = {
        "events_deleted": 0,
        "snapshots_deleted": 0,
        "audit_logs_deleted": 0,
        "db_available": True,
    }

    # (表名, 保留天数) —— 审计保留期必须长于事件
    targets = [
        ("events", settings.event_retention_days),
        ("snapshots", settings.snapshot_retention_days),
        ("audit_logs", settings.audit_retention_days),
    ]

    for table, retention_days in targets:
        cutoff = now - timedelta(days=retention_days)
        try:
            # MySQL 支持 DELETE ... LIMIT 分批；sqlite 等方言一次删完
            supports_limit = engine.dialect.name == "mysql"
            total = 0
            while True:
                async with engine.begin() as conn:
                    if supports_limit:
                        delete_result = await conn.execute(
                            text(
                                f"DELETE FROM {table} "
                                f"WHERE timestamp < :cutoff LIMIT :batch"
                            ),
                            {"cutoff": cutoff, "batch": _BATCH_SIZE},
                        )
                    else:
                        delete_result = await conn.execute(
                            text(f"DELETE FROM {table} WHERE timestamp < :cutoff"),
                            {"cutoff": cutoff},
                        )
                    deleted = delete_result.rowcount or 0
                total += deleted
                if not supports_limit or deleted < _BATCH_SIZE:
                    break
            result[f"{table}_deleted"] = total
            if total > 0:
                logger.info(
                    "retention.purged",
                    table=table,
                    retention_days=retention_days,
                    deleted=total,
                    cutoff=cutoff.isoformat(),
                )
        except Exception as e:
            # 单表清理失败不影响其余表
            logger.warning(
                "retention.purge_failed",
                table=table,
                error=str(e),
                error_type=type(e).__name__,
            )

    return result


async def retention_loop() -> None:
    """
    保留策略周期任务（由 lifespan 作为后台任务启动）。

    启动即执行一次清理，随后按配置的间隔周期执行；
    任务被取消（应用关闭）时静默退出。
    """
    interval_seconds = settings.retention_cleanup_interval_hours * 3600
    while True:
        try:
            await purge_expired_records()
        except Exception as e:
            logger.warning(
                "retention.loop_iteration_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
        await asyncio.sleep(interval_seconds)
