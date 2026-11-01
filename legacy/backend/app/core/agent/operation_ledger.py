"""
操作回滚账本（roadmap-logging-sandbox G4）
==========================================

红线 1/2/6 的运营载体：

- **登记**（:func:`ledger_append`）：写工具经治理面执行成功后**同步 await**
  落一行（§3.2.1 论证：治理面不持有业务事务，同事务方案不可行；同步
  INSERT 把幽灵窗口缩至毫秒级，再由 :func:`ledger_reconcile` 定时对账兜底）；
- **状态机 CAS**（:func:`ledger_transition`）：executed → compensating →
  compensated / compensate_failed；推进为条件 UPDATE——并发/重复回滚在
  门禁处直接拒绝（红线 2，闭环 P0-3 的运营层防线）；
- **对账**（:func:`ledger_reconcile`）：审计链 ``tool.*.executed``（medium/
  high 风险）↔ 账本 audit_entry_id 比对，缺口自动补登记（backfilled=1）
  + P0 级告警（红线 1 增补条款）；
- **回滚**（:func:`request_rollback` / :func:`execute_rollback`）：
  发起 → 前置检查（红线 6：下游引用默认拒绝）→ 治理面审批（write_high，
  D4 裁决一律人审）→ 执行补偿 → 全量审计。审批复用既有
  agent_approval_requests 行（agent_key="rollback"），决定经
  resume_after_decision 的 rollback 分支路由到 :func:`execute_rollback`。
"""
from __future__ import annotations

import json
import structlog
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text

logger = structlog.get_logger()

# 状态机合法值
ST_EXECUTED = "executed"
ST_COMPENSATING = "compensating"
ST_COMPENSATED = "compensated"
ST_FAILED = "compensate_failed"

_MAX_RESULT_CHARS = 4000


def _jsonable(value: Any) -> Any:
    """结果转可 JSON 存储的子集（默认 str 兜底 + 截断）。"""
    try:
        text_dump = json.dumps(value, ensure_ascii=False, default=str)
        if len(text_dump) > _MAX_RESULT_CHARS:
            return {"_truncated": text_dump[:_MAX_RESULT_CHARS]}
        return value
    except (TypeError, ValueError):
        return {"_repr": str(value)[:_MAX_RESULT_CHARS]}


# ===========================================================================
# 登记与查询（G4.1/G4.2）
# ===========================================================================

async def ledger_append(
    audit_entry_id: str,
    tool: str,
    actor: str,
    forward_args: dict,
    forward_result: Any,
    trace_id: str = "",
    business_key: str = "",
    domain: str = "",
    compensation_ref: str = "",
) -> str:
    """同步落一行账本（executed）。调用方：governor.execute 成功路径。"""
    from app.db.repositories.base import has_db
    from app.core import database as db_module

    if not has_db():
        return ""
    engine = db_module.get_engine()
    ledger_id = uuid4().hex[:16]
    is_sqlite = engine.dialect.name == "sqlite"
    args_ph = ":forward_args" if is_sqlite else "CAST(:forward_args AS JSON)"
    result_ph = ":forward_result" if is_sqlite else "CAST(:forward_result AS JSON)"

    async with engine.begin() as conn:
        await conn.execute(text(
            f"INSERT INTO operation_ledger "
            "(ledger_id, business_key, trace_id, actor, tool, domain, "
            "forward_args, forward_result, compensation_ref, audit_entry_id, "
            "status, backfilled, created_at, updated_at) "
            f"VALUES (:ledger_id, :business_key, :trace_id, :actor, :tool, :domain, "
            f"{args_ph}, {result_ph}, :compensation_ref, :audit_entry_id, "
            "'executed', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {
            "ledger_id": ledger_id,
            "business_key": business_key or "",
            "trace_id": trace_id or "",
            "actor": actor or "unknown",
            "tool": tool,
            "domain": domain or "",
            "forward_args": json.dumps(forward_args or {}, ensure_ascii=False, default=str),
            "forward_result": json.dumps(_jsonable(forward_result), ensure_ascii=False, default=str),
            "compensation_ref": compensation_ref or tool,
            "audit_entry_id": audit_entry_id or None,
        })
    logger.debug("ledger.appended", ledger_id=ledger_id, tool=tool,
                 business_key=business_key, audit_entry_id=audit_entry_id)
    return ledger_id


async def ledger_get(ledger_id: str) -> Optional[dict]:
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        return None
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT ledger_id, business_key, trace_id, actor, tool, domain, "
            "forward_args, forward_result, compensation_ref, audit_entry_id, "
            "status, backfilled, precheck_result, compensation_error, "
            "rolled_by, rolled_at, created_at FROM operation_ledger "
            "WHERE ledger_id = :l"
        ), {"l": ledger_id})).mappings().first()
    return dict(row) if row else None


async def ledger_query(
    business_key: str = "", trace_id: str = "", tool: str = "",
    status: str = "", actor: str = "", limit: int = 100,
) -> list[dict]:
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        return []
    clauses, params = [], {"limit": min(max(limit, 1), 500)}
    if business_key:
        clauses.append("business_key = :business_key")
        params["business_key"] = business_key
    if trace_id:
        clauses.append("trace_id = :trace_id")
        params["trace_id"] = trace_id
    if tool:
        clauses.append("tool = :tool")
        params["tool"] = tool
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if actor:
        clauses.append("actor = :actor")
        params["actor"] = actor
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT ledger_id, business_key, trace_id, actor, tool, domain, "
            "status, backfilled, audit_entry_id, compensation_error, "
            "rolled_by, rolled_at, created_at "
            f"FROM operation_ledger {where} ORDER BY created_at DESC, id DESC LIMIT :limit"
        ), params)).mappings().all()
    return [dict(r) for r in rows]


async def ledger_transition(
    ledger_id: str, to_status: str, from_statuses: tuple[str, ...],
    rolled_by: str = "",
) -> bool:
    """状态机 CAS 推进（红线 2 门禁）：条件 UPDATE，未命中即 False。"""
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        return False
    placeholders = ", ".join(f":f{i}" for i in range(len(from_statuses)))
    params: dict = {"l": ledger_id, "to": to_status,
                    "rolled_by": rolled_by or None}
    for i, s in enumerate(from_statuses):
        params[f"f{i}"] = s
    async with engine.begin() as conn:
        result = await conn.execute(text(
            f"UPDATE operation_ledger SET status = :to, "
            + ("rolled_by = :rolled_by, "
               "rolled_at = CURRENT_TIMESTAMP, " if rolled_by else "")
            + "updated_at = CURRENT_TIMESTAMP "
            f"WHERE ledger_id = :l AND status IN ({placeholders})"
        ), params)
    ok = (result.rowcount or 0) == 1
    if ok:
        logger.info("ledger.transition", ledger_id=ledger_id,
                    to=to_status, rolled_by=rolled_by)
    return ok


# ===========================================================================
# 对账（G4.2，红线 1 增补：审计有而账本无 = 幽灵操作缺口）
# ===========================================================================

async def ledger_reconcile(lookback_hours: int = 24) -> dict:
    """审计链写操作 ↔ 账本比对；缺口补登记 + P0 级告警。"""
    from app.core import database as db_module
    from app.core.observability.alerts import alert_channel

    engine = db_module.get_engine()
    if engine is None:
        return {"audited": 0, "backfilled": 0, "db_available": False}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    out = {"audited": 0, "backfilled": 0, "db_available": True}

    # bootstrap-only 库（部分测试/极简部署）可能没有 audit_logs 原生表——优雅跳过
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM audit_logs LIMIT 1"))
    except Exception:
        out["skipped"] = "audit_logs_missing"
        return out

    async with engine.begin() as conn:
        audited = (await conn.execute(text(
            "SELECT entry_id, actor_did, action, resource, details, "
            "trace_id, business_key FROM audit_logs "
            "WHERE action LIKE 'tool.%.executed' AND risk_level IN ('medium','high') "
            "AND timestamp >= :c ORDER BY timestamp"
        ), {"c": cutoff})).mappings().all()
        out["audited"] = len(audited)
        for a in audited:
            known = (await conn.execute(text(
                "SELECT COUNT(*) FROM operation_ledger WHERE audit_entry_id = :e"
            ), {"e": a["entry_id"]})).scalar()
            if known:
                continue
            # 缺口：从审计条目反推正向信息补登记（backfilled=1）
            tool = str(a["action"])[len("tool."):-len(".executed")]
            try:
                details = json.loads(a["details"]) if isinstance(a["details"], str) else (a["details"] or {})
            except (ValueError, TypeError):
                details = {}
            await conn.execute(text(
                "INSERT INTO operation_ledger "
                "(ledger_id, business_key, trace_id, actor, tool, domain, "
                "forward_args, forward_result, compensation_ref, audit_entry_id, "
                "status, backfilled, created_at, updated_at) "
                "VALUES (:ledger_id, :business_key, :trace_id, :actor, :tool, :domain, "
                ":forward_args, :forward_result, :compensation_ref, :audit_entry_id, "
                "'executed', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {
                "ledger_id": uuid4().hex[:16],
                "business_key": a.get("business_key") or "",
                "trace_id": a.get("trace_id") or "",
                "actor": a.get("actor_did") or "unknown",
                "tool": tool,
                "domain": "",
                "forward_args": json.dumps(details.get("args") or {}, ensure_ascii=False, default=str),
                "forward_result": json.dumps({}, ensure_ascii=False),
                "compensation_ref": tool,  # 由 drill/运行期校验注册表存在性
                "audit_entry_id": a["entry_id"],
            })
            out["backfilled"] += 1
            logger.error("ledger.ghost_operation_backfilled",
                         audit_entry_id=a["entry_id"], tool=tool)

    if out["backfilled"]:
        try:
            await alert_channel.raise_alert(
                rule="ledger_ghost_operation",
                title=f"对账发现 {out['backfilled']} 笔幽灵操作（审计有而账本无），已补登记",
                severity="error",
                details=out, cooldown_key="ledger_ghost", cooldown_s=300.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("ledger.ghost_alert_failed", error=str(e))
    if out["backfilled"] or out["audited"]:
        logger.info("ledger.reconciled", **out)
    return out


# ===========================================================================
# 回滚（G4.3/G4.4，红线 2/6）
# ===========================================================================

def _reject_reason(ledger: dict) -> str:
    return f"当前状态 {ledger.get('status')} 不可发起回滚"


async def request_rollback(ledger_id: str, requested_by: str, reason: str = "") -> dict:
    """发起回滚：前置检查 → 落审批行（write_high，一律人审）。

    - 状态非 executed/compensate_failed → 拒绝；
    - 前置检查 blocked → **默认拒绝**并返回下游引用清单（红线 6）；
    - 通过 → 落 agent_approval_requests 行（thread_id=rollback:{ledger_id}），
      决定经 resume_after_decision 的 rollback 分支执行。
    """
    from app.core.observability.operation_log import log_operation
    from app.core.security.audit import audit_chain
    from app.core.agent.tools_v2 import tool_registry_v2

    ledger = await ledger_get(ledger_id)
    if ledger is None:
        return {"status": "not_found"}
    if ledger["status"] not in (ST_EXECUTED, ST_FAILED):
        return {"status": "rejected", "reason": _reject_reason(ledger)}

    # 前置检查（红线 6）：登记了 rollback_precheck 的工具执行下游依赖检查
    precheck_out: dict = {"blocked": False, "dependencies": [], "message": ""}
    spec = tool_registry_v2.get(ledger["tool"])
    if spec is None:
        return {"status": "rejected",
                "reason": f"工具 {ledger['tool']} 已不在注册表（补偿无从执行）"}
    if spec.rollback_precheck is not None:
        try:
            from app.core.agent.tools_v2 import ToolContext

            ctx = ToolContext(user_id=requested_by, agent_key=f"rollback:{ledger['tool']}")
            precheck_out = await spec.rollback_precheck(
                ctx, _load_json(ledger.get("forward_args")) or {},
                _load_json(ledger.get("forward_result")))
        except Exception as e:  # noqa: BLE001
            precheck_out = {"blocked": True, "dependencies": [],
                            "message": f"前置检查执行失败（保守拒绝）：{e}"}
    if precheck_out.get("blocked"):
        await audit_chain.log(
            actor_did=requested_by, action="rollback.precheck_blocked",
            resource=f"operation_ledger:{ledger_id}", result="denied",
            details={"tool": ledger["tool"], **precheck_out}, risk_level="medium",
            trace_id=ledger.get("trace_id") or "", business_key=ledger.get("business_key") or "",
        )
        return {"status": "rejected_precheck", **precheck_out}

    # 落审批行（复用既有三级审批链；回滚执行本身仍需人审批准，D4 裁决）
    from app.db.repositories.base import dialect_upsert_stmt, has_db, session_scope

    if not has_db():
        return {"status": "error", "reason": "数据库不可用"}
    thread_id = f"rollback:{ledger_id}"
    preview = (
        f"回滚操作：{ledger['tool']}（单据 {ledger.get('business_key') or ledger_id}）\n"
        f"正向参数：{json.dumps(_load_json(ledger.get('forward_args')) or {}, ensure_ascii=False)[:400]}\n"
        f"发起理由：{reason or '（未填写）'}\n"
        f"前置检查：通过（{precheck_out.get('message') or '无下游引用'}）"
    )
    async with session_scope() as session:
        from app.db.models import agent_approval_requests

        values = dict(
            thread_id=thread_id,
            channel_id=None,
            agent_key="rollback",
            domain="rollback",
            tool=f"rollback:{ledger['tool']}",
            args_json={"ledger_id": ledger_id, "reason": reason,
                       "business_key": ledger.get("business_key") or ""},
            preview=preview,
            risk="write_high",
            status="pending",
            requested_by=requested_by,
            approve_roles=None,
        )
        await session.execute(dialect_upsert_stmt(agent_approval_requests, values))

    # 前置检查结果登记进账本行
    from app.core import database as db_module

    engine = db_module.get_engine()
    async with engine.begin() as conn:
        await conn.execute(text(
            "UPDATE operation_ledger SET precheck_result = :p, updated_at = CURRENT_TIMESTAMP "
            "WHERE ledger_id = :l"
        ), {"p": json.dumps(precheck_out, ensure_ascii=False, default=str), "l": ledger_id})

    await audit_chain.log(
        actor_did=requested_by, action="rollback.requested",
        resource=f"operation_ledger:{ledger_id}", result="success",
        details={"tool": ledger["tool"], "reason": reason,
                 "precheck": precheck_out, "thread_id": thread_id},
        risk_level="high", trace_id=ledger.get("trace_id") or "",
        business_key=ledger.get("business_key") or "",
    )
    await log_operation(actor=requested_by, action="operation_rollback.request",
                        resource=f"ledger:{ledger_id}", result="success",
                        details={"tool": ledger["tool"], "reason": reason},
                        risk_level="high", source="http", to_audit_chain=False)
    return {"status": "pending_approval", "thread_id": thread_id, "preview": preview}


def _load_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value


async def execute_rollback(ledger_id: str, decided_by: str,
                           approved: bool, comment: str = "") -> dict:
    """执行（或驳回）回滚——由审批决定路由调用。

    - 驳回 → 全量审计，账本不动；
    - 批准 → CAS（executed/compensate_failed → compensating）→ 调补偿 →
      CAS（→ compensated）或失败处置（→ compensate_failed + 告警待办）；
    - 补偿失败后可重新发起（重放经 CAS 门禁 + 协议幂等双重保护）。
    """
    from app.core.observability.operation_log import log_operation
    from app.core.security.audit import audit_chain
    from app.core.agent.tools_v2 import ToolContext, tool_registry_v2

    ledger = await ledger_get(ledger_id)
    if ledger is None:
        return {"status": "not_found"}
    trace_id = ledger.get("trace_id") or ""
    business_key = ledger.get("business_key") or ""
    tool = ledger["tool"]

    if not approved:
        await audit_chain.log(
            actor_did=decided_by, action="rollback.rejected",
            resource=f"operation_ledger:{ledger_id}", result="denied",
            details={"tool": tool, "comment": comment}, risk_level="medium",
            trace_id=trace_id, business_key=business_key,
        )
        await log_operation(actor=decided_by, action="operation_rollback.reject",
                            resource=f"ledger:{ledger_id}", result="denied",
                            details={"tool": tool, "comment": comment},
                            risk_level="medium", source="http", to_audit_chain=False)
        return {"status": "rejected", "reply": f"回滚已驳回：{comment or '未填写理由'}"}

    # CAS 门禁（红线 2）：executed/compensate_failed → compensating
    ok = await ledger_transition(ledger_id, ST_COMPENSATING,
                                 (ST_EXECUTED, ST_FAILED), rolled_by=decided_by)
    if not ok:
        current = await ledger_get(ledger_id)
        return {"status": "rejected",
                "reason": f"状态已变更（当前 {current.get('status') if current else '?'}），"
                          "可能已回滚或存在并发回滚"}

    spec = tool_registry_v2.get(tool)
    comp_error = ""
    try:
        if spec is None or spec.compensation is None:
            raise RuntimeError(f"工具 {tool} 的补偿函数不可用（注册表漂移）")
        ctx = ToolContext(user_id=decided_by, username=decided_by,
                          agent_key=f"rollback:{tool}",
                          thread_id=f"rollback:{ledger_id}",
                          trace_id=trace_id, business_key=business_key)
        await spec.compensation(ctx, _load_json(ledger.get("forward_args")) or {},
                                _load_json(ledger.get("forward_result")))
    except Exception as e:  # noqa: BLE001
        comp_error = f"{type(e).__name__}: {e}"
        await ledger_transition(ledger_id, ST_FAILED, (ST_COMPENSATING,))
        await conn_mark_error(ledger_id, comp_error)
        await audit_chain.log(
            actor_did=decided_by, action="rollback.failed",
            resource=f"operation_ledger:{ledger_id}", result="failure",
            details={"tool": tool, "error": comp_error[:400]}, risk_level="high",
            trace_id=trace_id, business_key=business_key,
        )
        # 告警即待办（G4.4：管理员经消息中心/告警介入，重新发起走重放路径）
        try:
            from app.core.observability.alerts import alert_channel

            await alert_channel.raise_alert(
                rule="rollback_compensation_failed",
                title=f"回滚补偿失败：{tool}（账本 {ledger_id}）",
                severity="error",
                details={"ledger_id": ledger_id, "tool": tool,
                         "business_key": business_key, "error": comp_error[:300],
                         "action": "请管理员排查后重新发起回滚"},
                cooldown_key=ledger_id, cooldown_s=60.0,
            )
        except Exception:  # noqa: BLE001
            pass
        return {"status": "compensate_failed", "error": comp_error,
                "reply": f"回滚执行失败：{comp_error}（已通知管理员，可排查后重新发起）"}

    # 成功：compensating → compensated（CAS 收口，状态机闭环）
    if not await ledger_transition(ledger_id, ST_COMPENSATED, (ST_COMPENSATING,)):
        logger.error("rollback.compensated_transition_failed", ledger_id=ledger_id)

    await audit_chain.log(
        actor_did=decided_by, action="rollback.executed",
        resource=f"operation_ledger:{ledger_id}", result="success",
        details={"tool": tool, "comment": comment, "business_key": business_key},
        risk_level="high", trace_id=trace_id, business_key=business_key,
    )
    await log_operation(actor=decided_by, action="operation_rollback.execute",
                        resource=f"ledger:{ledger_id}", result="success",
                        details={"tool": tool, "comment": comment},
                        risk_level="high", source="http", to_audit_chain=False)
    logger.info("rollback.executed", ledger_id=ledger_id, tool=tool,
                decided_by=decided_by, business_key=business_key)
    return {"status": "compensated",
            "reply": f"回滚完成：{tool}（单据 {business_key or ledger_id}）已恢复"}


async def conn_mark_error(ledger_id: str, error: str) -> None:
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        return
    async with engine.begin() as conn:
        await conn.execute(text(
            "UPDATE operation_ledger SET compensation_error = :e, "
            "updated_at = CURRENT_TIMESTAMP WHERE ledger_id = :l"
        ), {"e": error[:500], "l": ledger_id})
