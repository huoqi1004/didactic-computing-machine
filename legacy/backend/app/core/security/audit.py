"""
审计链
所有Agent操作记录附带加密校验和，存入不可篡改的审计链
支持哈希链验证和区块链存证

持久化架构：
- 内存层（List）：作为高速缓存，保证查询低延迟，同时是测试/开发的零依赖后端
- 数据库层（MySQL audit_logs 表）：作为持久化存储，保证审计日志不丢失
- 区块链层（Hyperledger Fabric，可选）：作为防篡改存证层，提供司法级证据保全
- 自动降级：数据库不可用时透明降级到纯内存模式；Fabric 不可用时降级到哈希链

区块链集成：
- ``init_blockchain()`` 初始化区块链客户端（懒加载，按 ``settings.blockchain_enabled`` 选择实现）
- ``log()`` 写入审计记录时同步提交到区块链（失败不影响主流程，仅记录警告）
- ``verify_on_blockchain()`` 在区块链上验证单条记录的完整性
- ``query_from_blockchain()`` 从区块链查询审计记录（支持时间范围与 actor 过滤）
- 保留现有 SHA-256 哈希链作为本地缓存和降级后端
"""
import asyncio
import hashlib
import structlog
from datetime import datetime, timezone
from typing import Optional, Any
from uuid import uuid4

from app.core.event.bus import event_bus
from app.core.event.models import Event, EventType
from app.core.config import settings

logger = structlog.get_logger()

_CHAIN_RESTORE_TIMEOUT_S = 10.0


class AuditEntry:
    """审计日志条目"""
    def __init__(self, actor_did: str, action: str, resource: str,
                 result: str, details: dict, risk_level: str = "low",
                 trace_id: str = "", business_key: str = ""):
        self.entry_id = str(uuid4())
        self.actor_did = actor_did          # 操作者DID
        self.action = action                # 操作类型
        self.resource = resource            # 操作资源
        self.result = result                # 操作结果 (success/failed/denied)
        self.details = details              # 详细信息
        self.risk_level = risk_level        # 风险等级
        # 追踪键（G1.1 三层模型）：不参与 compute_hash——哈希覆盖既有字段，
        # 补列不破坏哈希链连续性；防篡改由链本体保证
        self.trace_id = trace_id            # 链路实例键（32 hex）
        self.business_key = business_key    # 业务根键（单据号）
        self.timestamp = datetime.now(timezone.utc)
        self.prev_hash: Optional[str] = None
        self.hash: Optional[str] = None

    def compute_hash(self, prev_hash: Optional[str] = None) -> str:
        """计算审计条目哈希"""
        import orjson
        content = f"{self.entry_id}{self.actor_did}{self.action}{self.resource}"
        content += f"{self.result}{self.timestamp.isoformat()}{self.risk_level}"
        content += orjson.dumps(
            self.details, option=orjson.OPT_SORT_KEYS
        ).decode()
        if prev_hash:
            content += prev_hash
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "actor_did": self.actor_did,
            "action": self.action,
            "resource": self.resource,
            "result": self.result,
            "details": self.details,
            "risk_level": self.risk_level,
            "trace_id": getattr(self, "trace_id", "") or None,
            "business_key": getattr(self, "business_key", "") or None,
            "timestamp": self.timestamp.isoformat(),
            "prev_hash": self.prev_hash[:16] + "..." if self.prev_hash else None,
            "hash": self.hash[:16] + "..." if self.hash else None,
        }


class AuditChain:
    """
    审计链 - 不可篡改的操作审计日志

    架构：
    - 内存层（``_entries``）：高速读缓存 + 零依赖降级后端
    - 数据库层（MySQL ``audit_logs`` 表）：持久化存储
    - ``init_db()`` 初始化数据库表结构，设置 ``_db_available`` 标志
    - 写操作（``log``）双写内存 + 数据库
    - 读操作（``query``）从内存缓存读取（内存始终与数据库保持同步）
    - 数据库不可用时，自动降级到纯内存模式

    特性：
    - 哈希链防篡改：每个条目包含前一个条目的哈希
    - 加密校验和：所有操作可验证完整性
    - 区块链存证：生产环境对接Hyperledger Fabric
    - 实时告警：高风险操作即时通知
    """

    def __init__(self):
        # 内存审计条目列表（缓存层）
        self._entries: list[AuditEntry] = []
        # 内存条目上限（防止长期运行内存无限膨胀；超出后淘汰最旧条目。
        # 完整历史在 MySQL audit_logs，哈希链续接不受淘汰影响）
        self._max_memory_entries: int = 10000
        # 上一条目的哈希（哈希链指针）
        self._last_hash: Optional[str] = None
        self._lock_count = 0

        # 数据库可用性标志（init_db() 成功后置为 True）
        self._db_available: bool = False
        self._db_initialized: bool = False

        # 区块链客户端（懒加载，init_blockchain() 后赋值）
        # None 表示尚未初始化或显式禁用
        self._blockchain_client = None
        self._blockchain_initialized: bool = False
        # 本地缓存：entry_id -> blockchain tx_id（用于 verify_on_blockchain 反查）
        self._blockchain_tx_index: dict[str, str] = {}

    # ===================================================================
    # 数据库初始化
    # ===================================================================

    async def init_db(self) -> bool:
        """
        初始化数据库表结构（audit_logs 表）。

        幂等：重复调用不会重复建表。
        成功后设置 ``_db_available = True``，后续 ``log()`` 将双写数据库。
        失败时记录警告，``_db_available`` 保持 ``False``，自动降级到内存模式。

        返回 ``True`` 表示初始化成功，``False`` 表示数据库不可用。
        """
        if self._db_initialized:
            return self._db_available

        try:
            from app.core import database as db_module
            from app.core.migrations import run_migrations

            # 确保数据库引擎已初始化
            engine = await db_module.init_db()
            if engine is None:
                logger.warning("audit_chain.init_db_failed_engine_none")
                self._db_initialized = True
                self._db_available = False
                return False

            # 执行迁移（建表，包含 audit_logs 表）
            success = await run_migrations(engine)
            self._db_initialized = True
            self._db_available = success

            if success:
                logger.info("audit_chain.db_initialized", backend="mysql")
            else:
                logger.warning("audit_chain.db_init_failed_degraded_to_memory")

            return success

        except Exception as e:
            logger.warning("audit_chain.init_db_error", error=str(e))
            self._db_initialized = True
            self._db_available = False
            return False

    # ===================================================================
    # 区块链客户端初始化
    # ===================================================================

    async def init_blockchain(self) -> bool:
        """
        初始化区块链审计客户端（懒加载单例）。

        根据 ``settings.blockchain_enabled`` 选择实现：
        - ``True``  → 尝试初始化 :class:`FabricAuditClient`
        - ``False`` → 直接使用 :class:`HashChainFallbackClient`（默认）

        Fabric 不可用时（SDK 缺失 / 网络连接失败）自动降级到哈希链。
        成功后将 ``_blockchain_client`` 赋值为 ``BlockchainAuditClient`` 实例。

        返回 ``True`` 表示客户端可用（无论 Fabric 还是降级实现）。
        """
        if self._blockchain_initialized:
            return self._blockchain_client is not None

        self._blockchain_initialized = True
        try:
            from app.core.blockchain import get_blockchain_client
            self._blockchain_client = await get_blockchain_client()
            impl = self._blockchain_client.implementation_name
            logger.info(
                "audit_chain.blockchain_initialized",
                implementation=impl,
                blockchain_enabled=settings.blockchain_enabled,
            )
            return True
        except Exception as e:
            logger.warning(
                "audit_chain.blockchain_init_failed",
                error=str(e),
                degraded="hash_chain_only",
            )
            self._blockchain_client = None
            return False

    # ===================================================================
    # 哈希链恢复（D-05 缺陷 1：重启断链）
    # ===================================================================

    async def restore_chain_from_db(self) -> bool:
        """
        重启后从 MySQL audit_logs 恢复哈希链尾指针，使新条目接续历史链。

        在 ``init_db()`` 成功后调用。库为空（全新部署）时不恢复，
        从零起链是正确行为。

        返回 ``True`` 表示链尾已恢复。
        """
        if not self._db_available:
            logger.debug("audit.chain_restore_skipped", reason="db_unavailable")
            return False

        try:
            from app.core import database as db_module
            from sqlalchemy import text

            engine = db_module.get_engine()
            if engine is None:
                return False

            async with engine.connect() as conn:
                # 链尾查询位于启动路径上：audit_logs 无 timestamp 单列索引时
                # 实测 149 万行全表 filesort 耗时 49.9s/66.2s，足以把 lifespan 拖死。
                # 超时不阻断启动——按既有降级语义从零起链并告警（断链可见）。
                result = await asyncio.wait_for(
                    conn.execute(
                        text(
                            "SELECT entry_hash FROM audit_logs "
                            "ORDER BY timestamp DESC LIMIT 1"
                        )
                    ),
                    timeout=_CHAIN_RESTORE_TIMEOUT_S,
                )
                row = result.fetchone()

            if row and row[0]:
                self._last_hash = row[0]
                logger.info(
                    "audit.chain_restored",
                    last_hash=row[0][:16] + "...",
                    message="审计哈希链从数据库续接，重启不再断链",
                )
                return True

            logger.debug("audit.chain_restore_empty_db")
            return False
        except Exception as e:
            logger.warning(
                "audit.chain_restore_failed",
                error=str(e),
                degraded="fresh_chain",
            )
            return False

    # ===================================================================
    # 审计日志记录（写操作）
    # ===================================================================

    async def log(self, actor_did: str, action: str, resource: str,
                  result: str, details: dict, risk_level: str = "low",
                  trace_id: str = "", business_key: str = "") -> str:
        """
        记录审计日志。

        写入策略：
        1. 内存层：始终写入（保证缓存一致性）
        2. 数据库层：``_db_available`` 为 True 时写入 MySQL ``audit_logs`` 表
        3. 数据库写入失败时记录警告，不影响内存写入（降级模式）

        追踪键（G1.1）：未显式传 ``trace_id``/``business_key`` 时自动取当前
        上下文（observability.trace 的 contextvars）——所有经治理面/HTTP
        中间件的调用无需改造即获得链路关联。

        返回审计条目ID。
        """
        if not trace_id or not business_key:
            from app.core.observability.trace import get_business_key, get_trace_id

            trace_id = trace_id or get_trace_id()
            business_key = business_key or get_business_key()

        entry = AuditEntry(
            actor_did=actor_did,
            action=action,
            resource=resource,
            result=result,
            details=details,
            risk_level=risk_level,
            trace_id=trace_id,
            business_key=business_key,
        )

        # 计算哈希链
        entry.prev_hash = self._last_hash
        entry.hash = entry.compute_hash(self._last_hash)
        self._last_hash = entry.hash
        self._entries.append(entry)

        # 内存条目上限裁剪（B-03 关联：长期运行不膨胀）
        if len(self._entries) > self._max_memory_entries:
            self._entries = self._entries[-self._max_memory_entries:]

        # 尝试写入数据库
        if self._db_available:
            await self._append_to_db(entry)

        # 高风险操作告警
        if risk_level in ("high", "critical"):
            logger.warning("audit.high_risk_operation",
                          actor=actor_did, action=action,
                          resource=resource, risk_level=risk_level)
            await self._publish_alert(entry)

        # 发布审计事件
        await self._publish_audit_event(entry)

        # 提交到区块链（如果客户端可用）
        # 失败时仅记录警告，不影响主流程（哈希链本地缓存仍然有效）
        if self._blockchain_client is not None:
            await self._submit_to_blockchain(entry)

        logger.info("audit.logged",
                    entry_id=entry.entry_id,
                    action=action, resource=resource,
                    result=result, risk_level=risk_level,
                    db_persisted=self._db_available)

        return entry.entry_id

    async def _submit_to_blockchain(self, entry: AuditEntry) -> None:
        """将审计条目提交到区块链（内部方法，失败不抛异常）"""
        try:
            entry_payload = {
                "entry_id": entry.entry_id,
                "actor_did": entry.actor_did,
                "action": entry.action,
                "resource": entry.resource,
                "result": entry.result,
                "details": entry.details,
                "risk_level": entry.risk_level,
                "timestamp": entry.timestamp.isoformat(),
                "prev_hash": entry.prev_hash,
                "entry_hash": entry.hash,
            }
            tx_id = await self._blockchain_client.submit_audit_entry(entry_payload)
            # 缓存 entry_id -> tx_id 映射，便于后续 verify_on_blockchain 反查
            self._blockchain_tx_index[entry.entry_id] = tx_id
            logger.debug(
                "audit.blockchain_submitted",
                entry_id=entry.entry_id,
                tx_id=tx_id,
            )
        except Exception as e:
            # 区块链提交失败不影响审计日志主流程
            # 本地哈希链与数据库（如可用）已保证审计记录的持久化
            logger.warning(
                "audit.blockchain_submit_failed",
                entry_id=entry.entry_id,
                error=str(e),
                error_type=type(e).__name__,
                degraded="local_hash_chain_only",
            )

    async def _append_to_db(self, entry: AuditEntry) -> None:
        """将审计条目写入 MySQL audit_logs 表（内部方法）"""
        try:
            from app.core import database as db_module
            from sqlalchemy import text
            import orjson

            engine = db_module.get_engine()
            if engine is None:
                return

            # MySQL 用 CAST AS JSON；sqlite 的 CAST 到未知类型会按 NUMERIC
            # 亲和性把 JSON 文本清成 0（哈希链校验/详情读回全坏），直接存文本
            is_sqlite = engine.dialect.name == "sqlite"
            details_ph = ":details" if is_sqlite else "CAST(:details AS JSON)"

            async with engine.begin() as conn:
                await conn.execute(
                    text(f"""
                        INSERT INTO audit_logs
                            (entry_id, actor_did, action, resource, result,
                             details, risk_level, prev_hash, entry_hash,
                             trace_id, business_key, timestamp)
                        VALUES
                            (:entry_id, :actor_did, :action, :resource, :result,
                             {details_ph}, :risk_level, :prev_hash, :entry_hash,
                             :trace_id, :business_key, :timestamp)
                    """),
                    {
                        "entry_id": entry.entry_id,
                        "actor_did": entry.actor_did,
                        "action": entry.action,
                        "resource": entry.resource,
                        "result": entry.result,
                        "details": orjson.dumps(entry.details).decode(),
                        "risk_level": entry.risk_level,
                        "prev_hash": entry.prev_hash,
                        "entry_hash": entry.hash,
                        "trace_id": getattr(entry, "trace_id", "") or None,
                        "business_key": getattr(entry, "business_key", "") or None,
                        "timestamp": entry.timestamp,
                    }
                )

            logger.debug("audit.db_appended",
                         entry_id=entry.entry_id,
                         action=entry.action)

        except Exception as e:
            logger.warning("audit.db_append_failed",
                           error=str(e),
                           entry_id=entry.entry_id,
                           degraded="memory_only")
            # G3.4 告警联动：审计落库失败必须显式告警（红线 3）
            try:
                from app.core.observability.alerts import note_audit_db_failure
                await note_audit_db_failure(entry.entry_id, str(e))
            except Exception:  # noqa: BLE001
                pass

    async def _publish_audit_event(self, entry: AuditEntry):
        """发布审计事件到事件总线"""
        event = Event(
            event_type=EventType.AUDIT_LOG_CREATED,
            aggregate_id=f"audit:{entry.entry_id}",
            aggregate_type="audit",
            event_data=entry.to_dict(),
            agent_id=entry.actor_did,
            security_level="confidential",
        )
        await event_bus.publish(event)

    async def _publish_alert(self, entry: AuditEntry):
        """发布安全告警"""
        event = Event(
            event_type=EventType.SECURITY_ALERT,
            aggregate_id=f"alert:{entry.entry_id}",
            aggregate_type="security_alert",
            event_data={
                "alert_type": "high_risk_operation",
                "actor": entry.actor_did,
                "action": entry.action,
                "resource": entry.resource,
                "risk_level": entry.risk_level,
                "timestamp": entry.timestamp.isoformat(),
            },
            security_level="confidential",
        )
        await event_bus.publish(event)

    # ===================================================================
    # 完整性验证
    # ===================================================================

    def verify_integrity(self) -> bool:
        """
        验证审计链完整性。

        始终从内存缓存读取条目进行验证，
        这样可以检测到对内存中条目的任何篡改。
        """
        prev_hash = None
        for entry in self._entries:
            expected_hash = entry.compute_hash(prev_hash)
            if entry.hash != expected_hash:
                logger.error("audit.integrity_broken",
                            entry_id=entry.entry_id,
                            expected=expected_hash[:16],
                            actual=entry.hash[:16] if entry.hash else None)
                return False
            if entry.prev_hash != prev_hash:
                logger.error("audit.chain_broken", entry_id=entry.entry_id)
                return False
            prev_hash = entry.hash
        return True

    # ===================================================================
    # DB 级完整性校验（G3.3：增量/全量 + 点位状态 + 断链告警）
    # ===================================================================

    @staticmethod
    def _recompute_entry_hash(row: dict, running_prev: Optional[str]) -> str:
        """按 AuditEntry.compute_hash 同一公式从库行重算哈希。

        timestamp 还原：MySQL 读回 naive UTC datetime → 补 +00:00 与写入时的
        aware isoformat 对齐（哈希内容含 timestamp.isoformat()，不还原会
        全量误报断链）。
        """
        import hashlib
        import orjson as _orjson

        ts = row.get("timestamp")
        if hasattr(ts, "tzinfo") and not isinstance(ts, str):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_text = ts.isoformat()
        else:
            ts_text = str(ts)
            try:
                from datetime import datetime as _dt

                parsed = _dt.fromisoformat(ts_text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                ts_text = parsed.isoformat()
            except ValueError:
                pass  # 非标准格式按原样参与计算（写入端同样如此的概率低）

        details = row.get("details")
        if isinstance(details, (str, bytes)):
            details = _orjson.loads(details)
        content = (f"{row['entry_id']}{row['actor_did']}{row['action']}{row['resource']}"
                   f"{row['result']}{ts_text}{row['risk_level']}"
                   + _orjson.dumps(details or {}, option=_orjson.OPT_SORT_KEYS).decode())
        if running_prev:
            content += running_prev
        return hashlib.sha256(content.encode()).hexdigest()

    async def _get_verify_state(self, engine) -> dict:
        from sqlalchemy import text

        try:
            async with engine.connect() as conn:
                row = (await conn.execute(text(
                    "SELECT last_incremental_at, last_full_at, last_report "
                    "FROM audit_verify_state WHERE id = 1"
                ))).first()
            if row is None:
                return {}
            return {"last_incremental_at": row[0], "last_full_at": row[1],
                    "last_report": row[2]}
        except Exception:  # noqa: BLE001
            return {}

    async def _save_verify_state(self, engine, mode: str, report: dict) -> None:
        import orjson
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        is_sqlite = engine.dialect.name == "sqlite"
        report_ph = ":report" if is_sqlite else "CAST(:report AS JSON)"
        try:
            async with engine.begin() as conn:
                exists = (await conn.execute(text(
                    "SELECT COUNT(*) FROM audit_verify_state WHERE id = 1"
                ))).scalar()
                col = "last_full_at" if mode == "full" else "last_incremental_at"
                if exists:
                    await conn.execute(text(
                        f"UPDATE audit_verify_state SET {col} = :now, "
                        f"last_report = {report_ph}, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = 1"
                    ), {"now": now, "report": orjson.dumps(report).decode()})
                else:
                    await conn.execute(text(
                        "INSERT INTO audit_verify_state (id, last_incremental_at, "
                        f"last_full_at, last_report) VALUES (1, :inc, :full, {report_ph})"
                    ), {"inc": now if mode == "incremental" else None,
                       "full": now if mode == "full" else None,
                       "report": orjson.dumps(report).decode()})
        except Exception as e:  # noqa: BLE001
            logger.warning("audit.verify_state_save_failed", error=str(e))

    async def verify_chain(self, full: bool = False) -> dict:
        """
        DB 级哈希链完整性校验（G3.3）。

        - ``full=False`` 增量：自上次增量点位起校验（锚定窗口前一行，
          链接与内容逐行重算）；
        - ``full=True`` 全量：从链首逐行校验（每周错峰 / 手动即时触发）。

        Returns:
            {"mode", "checked", "status": "ok"|"broken"|"skipped",
             "broken_at"?, "expected"?, "actual"?, "error"?}
        """
        from app.core import database as db_module
        from sqlalchemy import text

        engine = db_module.get_engine()
        if engine is None:
            return {"mode": "full" if full else "incremental", "checked": 0,
                    "status": "skipped", "reason": "db_unavailable"}

        report: dict = {"mode": "full" if full else "incremental",
                        "checked": 0, "status": "ok"}
        try:
            state = await self._get_verify_state(engine)
            cutoff = None if full else state.get("last_incremental_at")
            async with engine.connect() as conn:
                running_prev: Optional[str] = None
                params: dict = {}
                where = ""
                if cutoff and not full:
                    anchor = (await conn.execute(text(
                        "SELECT entry_hash FROM audit_logs "
                        "WHERE timestamp < :c ORDER BY timestamp DESC, entry_id LIMIT 1"
                    ), {"c": cutoff})).first()
                    running_prev = anchor[0] if anchor else None
                    where = "WHERE timestamp >= :c"
                    params = {"c": cutoff}
                rows = (await conn.execute(text(
                    "SELECT entry_id, actor_did, action, resource, result, details, "
                    f"risk_level, prev_hash, entry_hash, timestamp FROM audit_logs {where} "
                    "ORDER BY timestamp, entry_id"
                ), params)).mappings().all()

                for r in rows:
                    expected = self._recompute_entry_hash(dict(r), running_prev)
                    report["checked"] += 1
                    if expected != r["entry_hash"] or (r["prev_hash"] or "") != (running_prev or ""):
                        report.update(status="broken", broken_at=r["entry_id"],
                                      expected=expected, actual=r["entry_hash"] or "")
                        break
                    running_prev = r["entry_hash"]
        except Exception as e:  # noqa: BLE001
            report.update(status="skipped", error=str(e))

        # 断链即时告警（G3.4）+ 状态落表
        if report["status"] == "broken":
            logger.error("audit.chain_verify_broken", **report)
            try:
                from app.core.observability.alerts import note_chain_broken
                await note_chain_broken(report)
            except Exception:  # noqa: BLE001
                pass
        await self._save_verify_state(engine, report["mode"], report)
        return report

    # ===================================================================
    # 审计日志查询（读操作）
    # ===================================================================

    def query(self, actor_did: Optional[str] = None, action: Optional[str] = None,
              resource: Optional[str] = None, risk_level: Optional[str] = None,
              limit: int = 100) -> list[dict]:
        """
        查询审计日志。

        读取策略：
        - 从内存缓存读取（内存始终与数据库保持同步）
        - ``log()`` 方法双写内存 + 数据库，保证缓存一致性
        - 返回结果按时间逆序（最新优先），支持多维度过滤

        注意：此方法为同步方法，保持与现有调用方兼容。
        如需直接从数据库异步查询，请使用 ``query_from_db()``。
        """
        results = []
        for entry in reversed(self._entries):
            if actor_did and entry.actor_did != actor_did:
                continue
            if action and entry.action != action:
                continue
            if resource and entry.resource != resource:
                continue
            if risk_level and entry.risk_level != risk_level:
                continue
            results.append(entry.to_dict())
            if len(results) >= limit:
                break
        return results

    async def query_from_db(
        self,
        actor_did: Optional[str] = None,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        risk_level: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        从 MySQL audit_logs 表异步查询审计日志。

        支持多维度过滤，返回结果按时间逆序（最新优先）。
        若数据库不可用或查询失败，降级到内存查询（``query()``）。
        """
        if not self._db_available:
            return self.query(
                actor_did=actor_did, action=action,
                resource=resource, risk_level=risk_level, limit=limit
            )

        try:
            from app.core import database as db_module
            from sqlalchemy import text
            import orjson

            engine = db_module.get_engine()
            if engine is None:
                return self.query(
                    actor_did=actor_did, action=action,
                    resource=resource, risk_level=risk_level, limit=limit
                )

            # 动态构建查询条件
            conditions = []
            params: dict[str, Any] = {"limit": limit}

            if actor_did:
                conditions.append("actor_did = :actor_did")
                params["actor_did"] = actor_did
            if action:
                conditions.append("action = :action")
                params["action"] = action
            if resource:
                conditions.append("resource = :resource")
                params["resource"] = resource
            if risk_level:
                conditions.append("risk_level = :risk_level")
                params["risk_level"] = risk_level

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            query_sql = f"""
                SELECT entry_id, actor_did, action, resource, result,
                       details, risk_level, prev_hash, entry_hash, timestamp
                FROM audit_logs
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT :limit
            """

            async with engine.connect() as conn:
                result = await conn.execute(text(query_sql), params)
                rows = result.fetchall()

            results = []
            for row in rows:
                details = row[5] if isinstance(row[5], dict) else orjson.loads(row[5])
                results.append({
                    "entry_id": row[0],
                    "actor_did": row[1],
                    "action": row[2],
                    "resource": row[3],
                    "result": row[4],
                    "details": details,
                    "risk_level": row[6],
                    "timestamp": row[9].isoformat() if row[9] else None,
                    "prev_hash": row[7][:16] + "..." if row[7] else None,
                    "hash": row[8][:16] + "..." if row[8] else None,
                })

            logger.debug("audit.db_queried", count=len(results))
            return results

        except Exception as e:
            logger.warning("audit.db_query_failed",
                           error=str(e),
                           degraded="memory")
            return self.query(
                actor_did=actor_did, action=action,
                resource=resource, risk_level=risk_level, limit=limit
            )

    # ===================================================================
    # 区块链验证与查询
    # ===================================================================

    async def verify_on_blockchain(self, entry_id: str) -> bool:
        """
        在区块链上验证单条审计记录的完整性。

        流程：
        1. 从本地缓存 ``_blockchain_tx_index`` 查找 entry_id 对应的 tx_id
        2. 从区块链客户端查询该 tx_id 对应的链上记录
        3. 调用客户端的 ``verify_entry_integrity`` 校验记录未被篡改

        返回 ``True`` 表示记录在链上存在且完整；``False`` 表示未找到或校验失败。
        若区块链客户端不可用，返回 ``False`` 并记录警告。
        """
        if self._blockchain_client is None:
            logger.warning(
                "audit.blockchain_verify_unavailable",
                entry_id=entry_id,
                reason="blockchain_client_not_initialized",
            )
            return False

        try:
            # 优先用本地缓存的 tx_id 查询
            tx_id = self._blockchain_tx_index.get(entry_id)
            query_key = tx_id or entry_id
            entry_on_chain = await self._blockchain_client.query_audit_entry(query_key)
            if not entry_on_chain:
                logger.info(
                    "audit.blockchain_entry_not_found",
                    entry_id=entry_id,
                    tx_id=tx_id,
                )
                return False

            # 调用客户端校验完整性
            return await self._blockchain_client.verify_entry_integrity(entry_on_chain)
        except Exception as e:
            logger.warning(
                "audit.blockchain_verify_failed",
                entry_id=entry_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    async def query_from_blockchain(
        self,
        filters: Optional[dict] = None,
    ) -> list[dict]:
        """
        从区块链查询审计记录。

        支持的过滤参数（``filters`` dict）：
        - ``start_time`` / ``end_time``：时间范围（ISO8601 字符串或 datetime）
        - ``actor_did``：按操作者过滤（客户端内存过滤）
        - ``action``：按操作类型过滤
        - ``risk_level``：按风险等级过滤
        - ``limit``：返回条数上限（默认 100）

        若区块链客户端不可用，返回空列表。
        """
        if self._blockchain_client is None:
            logger.warning(
                "audit.blockchain_query_unavailable",
                reason="blockchain_client_not_initialized",
            )
            return []

        filters = filters or {}
        limit = int(filters.get("limit", 100))
        start_time = filters.get("start_time")
        end_time = filters.get("end_time")

        try:
            # 若指定了时间范围，走范围查询；否则返回全部（受 limit 限制）
            if start_time is not None or end_time is not None:
                # 未指定的边界用空字符串占位（降级客户端会跳过该侧过滤）
                results = await self._blockchain_client.query_audit_range(
                    start_time if start_time is not None else "",
                    end_time if end_time is not None else "",
                )
            else:
                # 无时间范围：从降级客户端内存层读取全部
                # Fabric 客户端需要通过 GetChainStats + 索引查询，
                # 此处统一走 query_audit_range("", "") 以保持接口一致
                results = await self._blockchain_client.query_audit_range("", "")

            # 客户端侧二次过滤（actor_did / action / risk_level）
            actor = filters.get("actor_did")
            action = filters.get("action")
            risk_level = filters.get("risk_level")
            filtered: list[dict] = []
            for entry in results:
                if actor and entry.get("actor_did") != actor:
                    continue
                if action and entry.get("action") != action:
                    continue
                if risk_level and entry.get("risk_level") != risk_level:
                    continue
                filtered.append(entry)
                if len(filtered) >= limit:
                    break
            return filtered
        except Exception as e:
            logger.warning(
                "audit.blockchain_query_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            return []

    # ===================================================================
    # 统计信息
    # ===================================================================

    def get_stats(self) -> dict:
        """获取审计统计"""
        risk_dist = {}
        for entry in self._entries:
            risk_dist[entry.risk_level] = risk_dist.get(entry.risk_level, 0) + 1

        # 区块链可用性
        blockchain_available = self._blockchain_client is not None
        chain_height = 0
        blockchain_impl = None
        if blockchain_available:
            blockchain_impl = self._blockchain_client.implementation_name
            # chain_height 是异步方法，get_stats 是同步方法，
            # 此处返回缓存信息（实际高度通过 API 端点异步获取）
            # 但若客户端为 HashChainFallbackClient，可同步读取其内存条目数
            try:
                from app.core.blockchain import HashChainFallbackClient
                if isinstance(self._blockchain_client, HashChainFallbackClient):
                    chain_height = len(self._blockchain_client._entries_by_tx)
            except Exception:  # noqa: BLE001
                pass

        return {
            "total_entries": len(self._entries),
            "integrity_valid": self.verify_integrity(),
            "risk_distribution": risk_dist,
            "db_available": self._db_available,
            "db_initialized": self._db_initialized,
            "blockchain_available": blockchain_available,
            "blockchain_initialized": self._blockchain_initialized,
            "blockchain_implementation": blockchain_impl,
            "chain_height": chain_height,
            "blockchain_tx_count": len(self._blockchain_tx_index),
        }


# 全局审计链单例
audit_chain = AuditChain()


async def audit_verify_loop() -> None:
    """
    审计链完整性校验调度（G3.3）：小时自检，到期触发。

    - 增量：距上次增量 ≥ ``audit_verify_incremental_hours``（默认 24h）；
    - 全量：距上次全量 ≥ ``audit_verify_full_days``（默认 7 天）；
    - 顺带补推告警死信（G3.4，webhook 恢复后自动送达）。

    模块级函数——必须位于 AuditChain 类定义之外（曾误插类中导致
    query/query_from_db/get_stats 沦为嵌套函数、类方法整体丢失）。
    """
    import asyncio

    from app.core.config import settings

    await asyncio.sleep(60)  # 启动缓冲：等 DB/审计链就绪
    while True:
        try:
            from app.core import database as db_module

            engine = db_module.get_engine()
            if engine is not None:
                state = await audit_chain._get_verify_state(engine)
                now = datetime.now(timezone.utc)

                async def _due(key: str, hours: int) -> bool:
                    last = state.get(key)
                    if not last:
                        return True
                    if isinstance(last, str):
                        last = datetime.fromisoformat(last)
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    return (now - last).total_seconds() >= hours * 3600

                if await _due("last_full_at", settings.audit_verify_full_days * 24):
                    await audit_chain.verify_chain(full=True)
                elif await _due("last_incremental_at",
                                settings.audit_verify_incremental_hours):
                    await audit_chain.verify_chain(full=False)

                # G4.2 账本对账（红线 1）：审计写操作 ↔ 账本，缺口补登记+告警
                try:
                    from app.core.agent.operation_ledger import ledger_reconcile
                    await ledger_reconcile()
                except Exception:  # noqa: BLE001
                    pass

                try:
                    from app.core.observability.alerts import alert_channel
                    await alert_channel.retry_dead_letters()
                except Exception:  # noqa: BLE001
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("audit.verify_loop_error", error=str(e))
        await asyncio.sleep(3600)
