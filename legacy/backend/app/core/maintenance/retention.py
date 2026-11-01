"""
事件/审计数据保留策略（评估报告 B-03 / 路线图任务 2.8）
========================================================

events 表在高频 Agent 运行下以约 8.6 万行/天的速度膨胀且只增不删；
audit_logs 要求保留更长周期（审计合规）。

策略：
- ``events``     默认保留 **7 天**（ERP_EVENT_RETENTION_DAYS）
- ``snapshots``  默认保留 **30 天**（快照体积大、价值随版本衰减）
- ``audit_logs`` 默认保留 **365 天**（G2.3，D3 裁决：90 → 365；审计合规）
- ``sagas``      **留存豁免**（G2.3 固化 P1-2）：Saga 持久化状态承载未完成
  事务的补偿恢复能力（saga.recover），清理会杀掉长挂起 Saga，永不清理
- ``operation_logs``  **分层留存**（G2.3）：热表 30 天（在线查询）→
  ``operation_logs_archive`` 归档表 180 天后清除（先搬运后删除，分批）
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


async def _archive_operation_logs(engine: Any, cutoff: datetime) -> int:
    """把热表中早于 cutoff 的行搬运到归档表（确定性 id 水位分段）。

    每段：以 ``ORDER BY id LIMIT batch`` 求段内最大 id（seg_max），同事务内
    以**完全相同谓词**（``ts < cutoff AND id <= seg_max``）先 INSERT 归档
    再 DELETE——两语句作用行集恒等，不会出现"删了没归档"或重复归档。
    （MySQL 的 IN 子查询不支持 LIMIT，故用水位分段而非 IN 删除。）
    """
    from sqlalchemy import text

    total = 0
    while True:
        async with engine.begin() as conn:
            seg_max = (
                await conn.execute(text(
                    "SELECT MAX(id) FROM ("
                    "  SELECT id FROM operation_logs WHERE ts < :cutoff ORDER BY id LIMIT :batch"
                    ") t"
                ), {"cutoff": cutoff, "batch": _BATCH_SIZE})
            ).scalar()
            if seg_max is None:
                break
            inserted = await conn.execute(text(
                "INSERT INTO operation_logs_archive "
                "SELECT * FROM operation_logs WHERE ts < :cutoff AND id <= :m"
            ), {"cutoff": cutoff, "m": int(seg_max)})
            await conn.execute(text(
                "DELETE FROM operation_logs WHERE ts < :cutoff AND id <= :m"
            ), {"cutoff": cutoff, "m": int(seg_max)})
            total += inserted.rowcount or 0
    return total


async def _purge_archive(engine: Any, cutoff: datetime) -> int:
    """清除归档表中超过归档保留期的行（分批）。"""
    from sqlalchemy import text

    supports_limit = engine.dialect.name == "mysql"
    total = 0
    while True:
        async with engine.begin() as conn:
            if supports_limit:
                deleted = await conn.execute(text(
                    "DELETE FROM operation_logs_archive WHERE ts < :cutoff LIMIT :batch"
                ), {"cutoff": cutoff, "batch": _BATCH_SIZE})
            else:
                deleted = await conn.execute(text(
                    "DELETE FROM operation_logs_archive WHERE ts < :cutoff"
                ), {"cutoff": cutoff})
            n = deleted.rowcount or 0
        total += n
        if not supports_limit or n < _BATCH_SIZE:
            break
    return total


async def archive_operation_logs(engine: Optional[Any] = None) -> dict:
    """
    G2.3 留存分层：热表（30 天）→ 归档表（180 天后清除）。

    独立入口（便于运维手动触发与测试），retention_loop 周期调用。
    """
    from app.core import database as db_module

    if engine is None:
        engine = db_module.get_engine() or await db_module.init_db()
    if engine is None:
        return {"archived": 0, "purged": 0, "db_available": False}

    now = datetime.now(timezone.utc)
    out = {"archived": 0, "purged": 0, "db_available": True}
    try:
        out["archived"] = await _archive_operation_logs(
            engine, now - timedelta(days=settings.operation_log_hot_days))
        out["purged"] = await _purge_archive(
            engine, now - timedelta(days=settings.operation_log_archive_days))
        if out["archived"] or out["purged"]:
            logger.info("retention.operation_logs_tiered", **out)
    except Exception as e:  # noqa: BLE001
        logger.warning("retention.operation_logs_tiered_failed", error=str(e))
    return out


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
            # G2.3：操作日志分层（热表→归档→清除）
            await archive_operation_logs()
        except Exception as e:
            logger.warning(
                "retention.loop_iteration_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
        await asyncio.sleep(interval_seconds)
