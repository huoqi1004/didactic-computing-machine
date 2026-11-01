"""
操作日志统一通道（roadmap-logging-sandbox G2.1/G2.2）
======================================================

**一切操作记录的唯一入口**——禁止业务代码散落自记（红线 5：审计者被审计）。

分层模型：

- **审计哈希链**（audit_chain）：防篡改完整性层，写操作/安全事件必进；
- **操作日志表**（operation_logs，本模块落库）：可查询层，G3.1 查询
  API/UI 服务于此；热表 30 天 → 归档表 180 天（retention.py 搬运）。

写路径：

1. ``log_operation()``：显式调用（登录/登出/鉴权失败/RBAC 变更/知识库/
   文件/导出等非 Agent 操作面）；
2. **审计事件订阅**：订阅 ``AUDIT_LOG_CREATED``，治理面/工作流/Agent 的
   全部审计条目自动流入操作日志表（Agent 写操作无需改造即可查）。

落库为**异步批量**（G2.2）：缓冲队列 + 定时批量 INSERT；失败重试不丢——
DB 不可用时留在内存（红线 3：禁止静默丢弃），恢复后自动续写。
（例外：G4 的 operation_ledger 账本登记走同步 await，见路线图 §3.2.1。）

LLM 交互日志策略（G2.4，D6 裁决）：默认只记摘要（token/延迟/消息数）；
提示词与响应全文按域开关（走定制层 domain_param——放宽类变更自动转
人审，即"双人确认"），开启超 7 天自动关闭并审计。
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import orjson
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# 操作来源（source 列）
SOURCE_HTTP = "http"
SOURCE_AGENT = "agent"
SOURCE_MCP = "mcp"
SOURCE_WORKFLOW = "workflow"
SOURCE_SYSTEM = "system"

_SUBSCRIBER_ID = "operation_log.feed"


class OperationLogBuffer:
    """操作日志缓冲 + 异步批量落库（单进程实例）。"""

    def __init__(self) -> None:
        self._buf: deque[dict] = deque()
        self._flusher_task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        # 观测计数（G3.4 告警取用）：落库行数 / 失败次数 / 缓冲水位峰值
        self.stats = {"flushed": 0, "failed": 0, "peak_buffer": 0}

    # ------------------------------------------------------------------
    def enqueue(self, row: dict) -> None:
        """入缓冲；超上限立即尝试强刷（红线 3：不静默丢弃）。"""
        self._buf.append(row)
        if len(self._buf) > self.stats["peak_buffer"]:
            self.stats["peak_buffer"] = len(self._buf)
        if len(self._buf) >= settings.operation_log_buffer_max:
            logger.warning(
                "operation_log.buffer_overflow_flush",
                buffer=len(self._buf),
            )
            # 强刷在后台跑（当前可能处于无事件循环的同步上下文）
            self._kick_flush()

    def _kick_flush(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.flush_once())

    # ------------------------------------------------------------------
    async def flush_once(self) -> int:
        """把缓冲批量写入 operation_logs。返回落库行数；失败不丢（重新入队）。"""
        if not self._buf:
            return 0
        from app.db.repositories.base import has_db
        from sqlalchemy import text

        if not has_db():
            return 0

        batch = []
        for _ in range(min(len(self._buf), settings.operation_log_batch_size)):
            batch.append(self._buf.popleft())

        try:
            from app.core import database as db_module

            engine = db_module.get_engine()
            # sqlite 的 CAST AS JSON 会把 JSON 文本清成 0（NUMERIC 亲和性）
            is_sqlite = engine.dialect.name == "sqlite"
            insert_sql = _INSERT_SQL.replace(
                "CAST(:details AS JSON)", ":details") if is_sqlite else _INSERT_SQL
            async with engine.begin() as conn:
                await conn.execute(text(insert_sql), [ _normalize_row(r) for r in batch ])
            self.stats["flushed"] += len(batch)
            return len(batch)
        except Exception as e:  # noqa: BLE001
            self.stats["failed"] += len(batch)
            # 失败回灌队首（保序重试）——DB 恢复后自动续写
            for r in reversed(batch):
                self._buf.appendleft(r)
            logger.warning(
                "operation_log.flush_failed",
                error=str(e),
                batch=len(batch),
                buffered=len(self._buf),
                degraded="retry_in_memory",
            )
            # G3.4 告警联动：刷盘失败显式告警（红线 3）
            try:
                from app.core.observability.alerts import note_oplog_flush_failure
                await note_oplog_flush_failure(str(e), len(self._buf))
            except Exception:  # noqa: BLE001
                pass
            return 0

    # ------------------------------------------------------------------
    async def _flusher_loop(self) -> None:
        """定时刷盘循环（间隔/批量来自 settings）。"""
        interval = max(0.5, settings.operation_log_flush_interval_s)
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
                break  # stop 信号
            except asyncio.TimeoutError:
                pass
            try:
                await self.flush_once()
            except Exception as e:  # noqa: BLE001
                logger.warning("operation_log.flusher_error", error=str(e))

    async def start(self) -> None:
        """启动刷盘任务（幂等）+ 挂载审计事件订阅。"""
        if self._flusher_task is None or self._flusher_task.done():
            self._stopping.clear()
            self._flusher_task = asyncio.ensure_future(self._flusher_loop())
            logger.info("operation_log.flusher_started",
                        interval_s=settings.operation_log_flush_interval_s,
                        batch_size=settings.operation_log_batch_size)
        _subscribe_audit_feed()

    async def stop(self) -> None:
        """停止刷盘任务并最终清空缓冲（容器优雅退出不丢日志）。"""
        self._stopping.set()
        if self._flusher_task is not None:
            try:
                await asyncio.wait_for(self._flusher_task, timeout=5)
            except Exception:  # noqa: BLE001
                pass
            self._flusher_task = None
        remaining = len(self._buf)
        if remaining:
            await self.flush_once()
            logger.info("operation_log.final_flush",
                        remaining=remaining, flushed_remaining=len(self._buf) == 0)

    def pending(self) -> int:
        return len(self._buf)


_INSERT_SQL = """
INSERT INTO operation_logs
    (ts, actor, source, action, resource, result, risk_level,
     trace_id, business_key, request_id, details)
VALUES
    (:ts, :actor, :source, :action, :resource, :result, :risk_level,
     :trace_id, :business_key, :request_id, CAST(:details AS JSON))
"""


def _normalize_row(row: dict) -> dict:
    out = dict(row)
    out["details"] = orjson.dumps(out.get("details") or {}).decode()
    for k in ("trace_id", "business_key", "request_id"):
        out[k] = out.get(k) or None
    return out


# 进程内单例
operation_log_buffer = OperationLogBuffer()


# ===========================================================================
# 统一采集通道（G2.1）
# ===========================================================================

async def log_operation(
    actor: str,
    action: str,
    resource: str = "",
    result: str = "success",
    details: Optional[dict] = None,
    risk_level: str = "low",
    source: str = SOURCE_SYSTEM,
    request_id: str = "",
    trace_id: str = "",
    business_key: str = "",
    to_audit_chain: bool = False,
) -> None:
    """记录一次操作（统一通道）。

    - 必进操作日志表（异步批量，不阻塞业务）；
    - ``to_audit_chain=True`` 时同步写审计哈希链（安全敏感操作：
      登录/鉴权失败/RBAC 变更/导出——审计与查询双层留痕）；
    - 追踪键缺省自动取当前上下文（G1 中间件/治理面已绑定）。
    """
    from app.core.observability.trace import get_business_key, get_trace_id

    trace_id = trace_id or get_trace_id()
    business_key = business_key or get_business_key()

    operation_log_buffer.enqueue({
        "ts": datetime.now(timezone.utc),
        "actor": actor or "unknown",
        "source": source,
        "action": action,
        "resource": resource or "",
        "result": result,
        "risk_level": risk_level,
        "trace_id": trace_id,
        "business_key": business_key,
        "request_id": request_id,
        "details": details or {},
    })

    if to_audit_chain:
        try:
            from app.core.security.audit import audit_chain

            await audit_chain.log(
                actor_did=actor or "unknown",
                action=action,
                resource=resource,
                result=result,
                details=details or {},
                risk_level=risk_level,
                trace_id=trace_id,
                business_key=business_key,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("operation_log.audit_chain_failed",
                           action=action, error=str(e))


def _subscribe_audit_feed() -> None:
    """订阅审计事件：治理面/工作流/Agent 全部审计条目自动流入操作日志。

    幂等：重复调用不重复订阅。订阅处理器只做入队（快），投递失败
    不影响审计主链路。
    """
    from app.core.event.bus import event_bus
    from app.core.event.models import EventType, EventSubscription

    if any(s.subscriber_id == _SUBSCRIBER_ID for s in event_bus._subscriptions):
        return

    async def _on_audit_event(event) -> None:
        try:
            data = event.event_data or {}
            operation_log_buffer.enqueue({
                "ts": datetime.now(timezone.utc),
                "actor": data.get("actor_did") or "unknown",
                "source": SOURCE_AGENT if str(data.get("action", "")).startswith("tool.") else SOURCE_SYSTEM,
                "action": data.get("action") or "",
                "resource": data.get("resource") or "",
                "result": data.get("result") or "",
                "risk_level": data.get("risk_level") or "low",
                "trace_id": data.get("trace_id") or "",
                "business_key": data.get("business_key") or "",
                "request_id": "",
                "details": {"entry_id": data.get("entry_id"), "via": "audit_chain"},
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("operation_log.audit_feed_error", error=str(e))

    event_bus.subscribe(EventSubscription(
        subscriber_id=_SUBSCRIBER_ID,
        event_types=[EventType.AUDIT_LOG_CREATED],
        handler=_on_audit_event,
    ))
    logger.info("operation_log.audit_feed_subscribed")


# ===========================================================================
# LLM 交互日志策略（G2.4，D6 裁决）
# ===========================================================================

# 开关存定制层 agent:observability 域参数——"agent"类型变更按分类器
# 兜底规则一律视为放宽 → 自动转人审，批准人即第二确认人（"双人确认"
# 复用既有审批流）；关闭同样走审批（分类器保守兜底，见 customization.py
# _classify_change 的"其他类型"分支）。
_OBS_DOMAIN = "observability"
_LLM_FULLTEXT_KEY = "llm_fulltext_logging"
_LLM_FULLTEXT_MAX_DAYS = 7


def llm_fulltext_state() -> dict:
    """当前全文开关状态：{"enabled": bool, "domains": [...], "expires_at": ...}。

    缓存读取（进程启动加载 + 5 秒收敛节流），失败安全：读不到按
    "默认摘要"处理（更保守）。超 expires_at 自动失效。
    """
    result = {"enabled": False, "domains": [], "expires_at": None, "expired": False}
    try:
        from app.core.agent.customization import customization_service

        raw = customization_service.get_domain_param(_OBS_DOMAIN, _LLM_FULLTEXT_KEY)
        if not raw:
            return result
        expires_at = raw.get("expires_at")
        if expires_at:
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= expires_at:
                result["expired"] = True
                return result
            result["expires_at"] = expires_at.isoformat()
        result["domains"] = list(raw.get("domains") or [])
        result["enabled"] = bool(result["domains"])
        return result
    except Exception as e:  # noqa: BLE001
        logger.debug("operation_log.llm_fulltext_state_unavailable", error=str(e))
        return result


def llm_fulltext_enabled(domain: str) -> bool:
    return domain in llm_fulltext_state().get("domains", [])


async def request_llm_fulltext(
    domains: list[str], changed_by: str, comment: str = "",
) -> dict:
    """申请开启/调整全文开关（走既有放宽审批流 = 双人确认）。

    Returns:
        {"status": "applied"|"pending_approval"|..., "reason": ...}
        —— pending_approval 时由第二名管理员经既有审批 decide 通道批准生效。
    """
    from app.core.agent.customization import customization_service

    if not domains:
        raise ValueError("domains 不能为空（全文开关必须显式指定域）")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=_LLM_FULLTEXT_MAX_DAYS)
    ).isoformat()
    old = customization_service.get_domain_param(_OBS_DOMAIN, _LLM_FULLTEXT_KEY) or {}
    params = {**(old.get("params") or {}), _LLM_FULLTEXT_KEY: {
        "domains": sorted(set(domains)), "expires_at": expires_at,
    }}
    return await customization_service.apply_change(
        ctype="agent", target=_OBS_DOMAIN, overrides={"params": params},
        changed_by=changed_by,
        comment=comment or f"开启 LLM 全文日志（域 {domains}，≤{_LLM_FULLTEXT_MAX_DAYS} 天自动过期）",
        idempotency_key=f"llm-fulltext-{changed_by}",
    )


async def log_llm_interaction(
    domain: str, ctx, messages_in: int, tokens: int, latency_ms: float,
    reply_text: str = "", user_message: str = "",
    extra: Optional[dict] = None,
) -> None:
    """LLM 交互日志：默认摘要；按域开关开启时附全文（带过期元数据）。

    全文行带 ``fulltext=true`` 与 ``expires_at`` 标记——G2.3 归档/清理
    按 expires_at 提前淘汰，保证"7 天自动关闭"对已落库全文同样生效。
    """
    actor = getattr(ctx, "user_id", "") or "unknown"
    details: dict[str, Any] = {
        "domain": domain,
        "thread_id": getattr(ctx, "thread_id", ""),
        "agent_key": getattr(ctx, "agent_key", ""),
        "messages_in": messages_in,
        "tokens": tokens,
        "latency_ms": round(latency_ms, 2),
    }
    if extra:
        details.update(extra)
    if llm_fulltext_enabled(domain):
        state = llm_fulltext_state()
        details["fulltext"] = True
        details["expires_at"] = state.get("expires_at")
        # 截断防膨胀（单条上限 16K 字符）
        details["user_message"] = (user_message or "")[:16000]
        details["reply_text"] = (reply_text or "")[:16000]
    await log_operation(
        actor=actor,
        action="llm.interaction",
        resource=f"llm:{domain}",
        result="success",
        details=details,
        risk_level="low",
        source=SOURCE_AGENT,
    )
