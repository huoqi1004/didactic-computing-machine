"""
告警通道（roadmap-logging-sandbox G3.4，闭环 P1-4）
====================================================

告警三路投递（任一失败不影响其余，告警不可静默丢失）：

1. **事件总线**：AUDIT_ALERT_RAISED 事件（消息中心/看板消费）；
2. **webhook 外发**：settings.alert_webhook_url（飞书/钉钉/企微机器人等
   通用入口）；失败写 ``alert_dead_letters`` 死信表，下次告警或手动重试
   时补推（告警发送失败本身有显式兜底记录）；
3. **结构化日志**：warning/error 级（Loki/采集器可消费）。

内置规则触发器（供治理面/审计链/日志底座调用）：

- :func:`note_permission_denied`：高频越权（滑动窗计数，超阈值告警）；
- :func:`note_off_hours_write`：异常时段（22:00~06:00）写操作；
- :func:`note_audit_db_failure`：审计落库失败（红线 3 联动）；
- :func:`note_oplog_flush_failure`：操作日志刷盘失败；
- :func:`note_chain_broken`：审计哈希链断链（G3.3 校验结果联动）。
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import orjson
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# 高频越权阈值：同一用户 5 分钟内 ≥10 次权限拒绝
_DENIED_WINDOW_S = 300.0
_DENIED_THRESHOLD = 10
# 异常时段（本地小时）：22:00 ~ 06:00
_OFF_HOURS = tuple(h for h in range(22, 24)) + tuple(h for h in range(0, 7))


class AlertChannel:
    """告警通道（进程内单例）：限频状态 + webhook 投递 + 死信兜底。"""

    def __init__(self) -> None:
        self._denied_buckets: dict[str, deque] = defaultdict(deque)
        # 限频冷却：rule+key -> 上次告警 monotonic（同因 10 分钟内不重复轰炸）
        self._cooldowns: dict[str, float] = {}
        self.stats = {"raised": 0, "webhook_ok": 0, "webhook_failed": 0, "dead_lettered": 0}

    def _cooled(self, key: str, cooldown_s: float = 600.0) -> bool:
        """冷却判定：True=已冷却可告警（并占坑）。"""
        now = time.monotonic()
        last = self._cooldowns.get(key)
        if last is not None and now - last < cooldown_s:
            return False
        self._cooldowns[key] = now
        return True

    # ------------------------------------------------------------------
    async def raise_alert(
        self, rule: str, title: str, severity: str = "warning",
        details: Optional[dict] = None, cooldown_key: str = "",
        cooldown_s: float = 600.0,
    ) -> Optional[str]:
        """发起一条告警（事件总线 + webhook + 日志三路）。返回 alert_id。"""
        if cooldown_key and not self._cooled(f"{rule}:{cooldown_key}", cooldown_s):
            return None
        alert_id = uuid.uuid4().hex[:16]
        payload = {
            "alert_id": alert_id, "rule": rule, "title": title,
            "severity": severity, "details": details or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.stats["raised"] += 1
        log_fn = logger.error if severity in ("error", "critical") else logger.warning
        log_fn("alert.raised", **payload)

        # 1. 事件总线（消息中心/看板）
        try:
            from app.core.event.bus import event_bus
            from app.core.event.models import Event, EventType

            await event_bus.publish(Event(
                event_type=EventType.AUDIT_ALERT_RAISED,
                aggregate_id=f"alert:{alert_id}",
                aggregate_type="alert",
                event_data=payload,
                agent_id="system",
                security_level="confidential",
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("alert.bus_publish_failed", alert_id=alert_id, error=str(e))

        # 2. webhook 外发（失败 → 死信）
        webhook_url = (settings.alert_webhook_url or "").strip()
        if webhook_url:
            await self._post_webhook(alert_id, webhook_url, payload)
        return alert_id

    async def _post_webhook(self, alert_id: str, webhook_url: str, payload: dict) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(webhook_url, content=orjson.dumps(payload).decode(),
                                         headers={"Content-Type": "application/json"})
            if resp.status_code < 300:
                self.stats["webhook_ok"] += 1
                return
            error = f"webhook http {resp.status_code}"
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
        self.stats["webhook_failed"] += 1
        await self._dead_letter(alert_id, webhook_url, payload, error)

    async def _dead_letter(self, alert_id: str, webhook_url: str,
                           payload: dict, error: str) -> None:
        """webhook 失败写死信表（显式兜底，绝不静默吞掉）。"""
        try:
            from app.core import database as db_module
            from sqlalchemy import text

            engine = db_module.get_engine()
            if engine is None:
                logger.error("alert.dead_letter_db_unavailable",
                             alert_id=alert_id, error=error)
                return
            # sqlite CAST AS JSON 会把文本清成 0（NUMERIC 亲和性），直接存文本
            is_sqlite = engine.dialect.name == "sqlite"
            payload_ph = ":payload" if is_sqlite else "CAST(:payload AS JSON)"
            async with engine.begin() as conn:
                await conn.execute(text(
                    f"INSERT INTO alert_dead_letters "
                    "(alert_id, rule, severity, title, payload, webhook_url, error) "
                    f"VALUES (:alert_id, :rule, :severity, :title, "
                    f"{payload_ph}, :webhook_url, :error)"
                ), {
                    "alert_id": alert_id, "rule": payload.get("rule", ""),
                    "severity": payload.get("severity", "warning"),
                    "title": payload.get("title", ""),
                    "payload": orjson.dumps(payload).decode(),
                    "webhook_url": webhook_url, "error": error[:500],
                })
            self.stats["dead_lettered"] += 1
        except Exception as e:  # noqa: BLE001
            logger.error("alert.dead_letter_failed",
                         alert_id=alert_id, error=error, dead_letter_error=str(e))

    async def retry_dead_letters(self, limit: int = 20) -> int:
        """补推死信（启动时/周期调用/手动触发）；成功即标记 resolved。"""
        webhook_url = (settings.alert_webhook_url or "").strip()
        if not webhook_url:
            return 0
        from app.core import database as db_module
        from sqlalchemy import text

        engine = db_module.get_engine()
        if engine is None:
            return 0
        retried = 0
        try:
            async with engine.begin() as conn:
                rows = (await conn.execute(text(
                    "SELECT id, alert_id, rule, severity, title, payload "
                    "FROM alert_dead_letters WHERE resolved = 0 "
                    "AND retry_count < 5 ORDER BY id LIMIT :limit"
                ), {"limit": limit})).mappings().all()
            for row in rows:
                ok = False
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.post(
                            webhook_url, content=row["payload"],
                            headers={"Content-Type": "application/json"})
                    ok = resp.status_code < 300
                except Exception:  # noqa: BLE001
                    ok = False
                async with engine.begin() as conn:
                    if ok:
                        await conn.execute(text(
                            "UPDATE alert_dead_letters SET resolved = 1, "
                            "last_retry_at = CURRENT_TIMESTAMP WHERE id = :id"
                        ), {"id": row["id"]})
                    else:
                        await conn.execute(text(
                            "UPDATE alert_dead_letters SET retry_count = retry_count + 1, "
                            "last_retry_at = CURRENT_TIMESTAMP WHERE id = :id"
                        ), {"id": row["id"]})
                retried += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("alert.dead_letter_retry_failed", error=str(e))
        return retried


# 进程内单例
alert_channel = AlertChannel()


# ===========================================================================
# 内置规则触发器
# ===========================================================================

def note_permission_denied(user_id: str, resource: str, action: str) -> None:
    """高频越权（同步调用，滑动窗计数；超阈值异步告警由调用方事件循环驱动）。"""
    now = time.monotonic()
    bucket = alert_channel._denied_buckets[user_id]
    while bucket and bucket[0] < now - _DENIED_WINDOW_S:
        bucket.popleft()
    bucket.append(now)
    if len(bucket) == _DENIED_THRESHOLD:  # 恰好触顶告警一次（冷却去重）
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(alert_channel.raise_alert(
            rule="high_freq_permission_denied",
            title=f"用户 {user_id} 短时间内多次越权尝试",
            severity="warning",
            details={"user": user_id, "denied_count": len(bucket),
                     "window_s": _DENIED_WINDOW_S,
                     "latest": f"{resource}:{action}"},
            cooldown_key=user_id,
        ))


async def note_off_hours_write(user_id: str, tool: str) -> None:
    """异常时段写操作（22:00~06:00 本地时间；同用户+工具 1 小时冷却）。"""
    if datetime.now().hour not in _OFF_HOURS:
        return
    await alert_channel.raise_alert(
        rule="off_hours_write",
        title=f"异常时段写操作：{user_id} 执行 {tool}",
        severity="warning",
        details={"user": user_id, "tool": tool,
                 "local_hour": datetime.now().hour},
        cooldown_key=f"{user_id}:{tool}", cooldown_s=3600.0,
    )


async def note_audit_db_failure(entry_id: str, error: str) -> None:
    await alert_channel.raise_alert(
        rule="audit_db_failure", title="审计落库失败（降级内存模式）",
        severity="error", details={"entry_id": entry_id, "error": error[:300]},
        cooldown_key="audit_db", cooldown_s=300.0,
    )


async def note_oplog_flush_failure(error: str, buffered: int) -> None:
    await alert_channel.raise_alert(
        rule="oplog_flush_failure", title="操作日志刷盘失败（内存重试中）",
        severity="warning", details={"error": error[:300], "buffered": buffered},
        cooldown_key="oplog_flush", cooldown_s=300.0,
    )


async def note_chain_broken(report: dict) -> None:
    await alert_channel.raise_alert(
        rule="audit_chain_broken", title="审计哈希链完整性校验失败",
        severity="critical", details=report,
        cooldown_key="chain", cooldown_s=3600.0,
    )
