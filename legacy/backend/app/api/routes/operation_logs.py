"""
操作日志查询 API（roadmap-logging-sandbox G3.1/G3.3）
======================================================

- 双键查询（business_key 业务单据号=第一入口 / trace_id 链路键）+
  人/动作/来源/风险/时间过滤；分页强制 LIMIT 上限（D1 附加条件）；
- 查询与导出行为自身 100% 审计（红线 5：审计者被审计，闭环 P1-3）；
- CSV 导出（operationlog:export，导出行数与过滤条件进审计）；
- 审计链完整性：状态查看 / 手动即时全量校验（P2-1）/ 校验调度状态；
- 告警死信：查看 / 手动补推。

权限（附录 B）：operationlog:read / operationlog:export / audit_verify:run
（log_auditor 角色只读，cio 全量）。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

import orjson
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.middleware.auth import get_current_user, require_permission

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent-ops/operation-logs", tags=["操作日志"])

# 分页 LIMIT 上限（D1 附加条件：防全表拖库）
_MAX_PAGE_LIMIT = 500
_MAX_EXPORT_ROWS = 10000

_FILTERABLE = ("actor", "source", "result", "risk_level")


def _build_filters(
    business_key: str, trace_id: str, actor: str, action: str,
    source: str, result: str, risk_level: str,
    start: Optional[datetime], end: Optional[datetime],
) -> tuple[str, dict]:
    clauses, params = [], {}
    if business_key:
        clauses.append("business_key = :business_key")
        params["business_key"] = business_key
    if trace_id:
        clauses.append("trace_id = :trace_id")
        params["trace_id"] = trace_id
    if actor:
        clauses.append("actor = :actor")
        params["actor"] = actor
    if action:
        clauses.append("action LIKE :action")
        params["action"] = f"{action}%"
    for name in _FILTERABLE:
        value = {"source": source, "result": result, "risk_level": risk_level}.get(name, "")
        if value:
            clauses.append(f"{name} = :{name}")
            params[name] = value
    if start:
        clauses.append("ts >= :start")
        params["start"] = start
    if end:
        clauses.append("ts <= :end")
        params["end"] = end
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _row_to_dict(r) -> dict:
    # r 可为 Row（.mappings() 前）或 RowMapping——两者均可直接 dict()
    m = dict(r)
    for k in ("trace_id", "business_key", "request_id"):
        m[k] = m.get(k) or ""
    if isinstance(m.get("details"), (str, bytes)):
        try:
            m["details"] = orjson.loads(m["details"])
        except Exception:  # noqa: BLE001
            m["details"] = {}
    if isinstance(m.get("ts"), datetime):
        m["ts"] = m["ts"].replace(tzinfo=timezone.utc).isoformat()
    return m


async def _self_audit(user_id: str, action: str, details: dict, risk: str,
                      business_key: str = "") -> None:
    """查询/导出行为自身审计（红线 5）。business_key=被检索的单据号。"""
    try:
        from app.core.observability.operation_log import log_operation

        await log_operation(actor=user_id, action=action, resource="operationlog",
                            result="success", details=details, risk_level=risk,
                            source="http", business_key=business_key,
                            to_audit_chain=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("operation_logs.self_audit_failed", action=action, error=str(e))


@router.get("", summary="操作日志查询（双键+多维过滤，强制 LIMIT 上限）")
async def query_operation_logs(
    business_key: str = Query("", max_length=128, description="业务单据号（第一入口）"),
    trace_id: str = Query("", max_length=64, description="链路键（二等入口）"),
    actor: str = Query("", max_length=128),
    action: str = Query("", max_length=128, description="动作前缀匹配"),
    source: str = Query("", max_length=32),
    result: str = Query("", max_length=32),
    risk_level: str = Query("", max_length=16),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=_MAX_PAGE_LIMIT),
    current_user: dict = Depends(require_permission("operationlog", "read")),
):
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="数据库不可用（操作日志不可查询）")

    where, params = _build_filters(business_key, trace_id, actor, action,
                                   source, result, risk_level, start, end)
    offset = (page - 1) * limit
    sql_from = "FROM operation_logs"
    table_note = "hot"
    if (business_key or trace_id) and where:
        # 双键搜索热表无命中时回落归档表（留存分层后链路仍可还原）
        async with engine.connect() as conn:
            hot_hit = (await conn.execute(
                text(f"SELECT COUNT(*) FROM operation_logs {where}"),
                params)).scalar()
        if not hot_hit:
            sql_from = "FROM operation_logs_archive"
            table_note = "archive"

    async with engine.connect() as conn:
        total = (await conn.execute(text(f"SELECT COUNT(*) {sql_from} {where}"),
                                    params)).scalar()
        rows = (await conn.execute(text(
            f"SELECT id, ts, actor, source, action, resource, result, risk_level, "
            f"trace_id, business_key, request_id, details {sql_from} {where} "
            f"ORDER BY ts DESC, id DESC LIMIT :limit OFFSET :offset"
        ), {**params, "limit": limit, "offset": offset})).mappings().all()

    items = [_row_to_dict(r) for r in rows]
    await _self_audit(current_user.get("user_id", "unknown"), "operationlog.query",
                      {"filters": {k: str(v) for k, v in params.items()},
                       "returned": len(items), "page": page, "table": table_note},
                      risk="low")
    return {"items": items, "total": total, "page": page, "limit": limit,
            "table": table_note}


@router.get("/export", summary="导出 CSV（行为自身审计，含过滤条件与行数）")
async def export_operation_logs(
    business_key: str = Query("", max_length=128),
    trace_id: str = Query("", max_length=64),
    actor: str = Query("", max_length=128),
    action: str = Query("", max_length=128),
    source: str = Query("", max_length=32),
    result: str = Query("", max_length=32),
    risk_level: str = Query("", max_length=16),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    current_user: dict = Depends(require_permission("operationlog", "export")),
):
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="数据库不可用")

    where, params = _build_filters(business_key, trace_id, actor, action,
                                   source, result, risk_level, start, end)
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT id, ts, actor, source, action, resource, result, risk_level, "
            f"trace_id, business_key, request_id, details FROM operation_logs {where} "
            f"ORDER BY ts DESC, id DESC LIMIT :limit"
        ), {**params, "limit": _MAX_EXPORT_ROWS})).mappings().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "ts", "actor", "source", "action", "resource",
                     "result", "risk_level", "trace_id", "business_key",
                     "request_id", "details"])
    for r in rows:
        m = dict(r)
        m["details"] = m.get("details") or ""
        writer.writerow([m.get(k, "") for k in (
            "id", "ts", "actor", "source", "action", "resource", "result",
            "risk_level", "trace_id", "business_key", "request_id", "details")])

    await _self_audit(current_user.get("user_id", "unknown"), "operationlog.export",
                      {"filters": {k: str(v) for k, v in params.items()},
                       "rows": len(rows), "max": _MAX_EXPORT_ROWS},
                      risk="medium", business_key=business_key)
    buf.seek(0)
    filename = f"operation_logs_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ===========================================================================
# 审计链完整性（G3.3）
# ===========================================================================

@router.get("/verify/state", summary="审计链校验状态与最近报告")
async def get_verify_state(
    current_user: dict = Depends(require_permission("operationlog", "read")),
):
    from app.core.security.audit import audit_chain

    report = await audit_chain._get_verify_state(_engine_or_503())
    return {"state": report, "memory_integrity": audit_chain.verify_integrity()}


@router.post("/verify/run", summary="手动触发即时全量校验（P2-1：当场点名篡改）")
async def run_verify(
    current_user: dict = Depends(require_permission("audit_verify", "run")),
):
    from app.core.observability.operation_log import log_operation
    from app.core.security.audit import audit_chain

    report = await audit_chain.verify_chain(full=True)
    # 校验行为自身审计（含触发人与结果摘要）
    await log_operation(
        actor=current_user.get("user_id", "unknown"),
        action="audit_verify.run", resource="audit_logs",
        result="success" if report.get("status") == "ok" else "failure",
        details={k: report.get(k) for k in ("mode", "checked", "status", "broken_at")},
        risk_level="medium", source="http", to_audit_chain=True,
    )
    return report


def _engine_or_503():
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="数据库不可用")
    return engine


# ===========================================================================
# 告警死信（G3.4）
# ===========================================================================

@router.get("/dead-letters", summary="告警死信列表（webhook 投递失败兜底）")
async def list_dead_letters(
    current_user: dict = Depends(require_permission("operationlog", "read")),
):
    engine = _engine_or_503()
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id, alert_id, rule, severity, title, payload, webhook_url, "
            "error, retry_count, resolved, created_at, last_retry_at "
            "FROM alert_dead_letters WHERE resolved = 0 ORDER BY id DESC LIMIT 200"
        ))).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows], "total": len(rows)}


@router.post("/dead-letters/retry", summary="手动补推死信")
async def retry_dead_letters(
    current_user: dict = Depends(require_permission("audit_verify", "run")),
):
    from app.core.observability.alerts import alert_channel

    retried = await alert_channel.retry_dead_letters()
    await _self_audit(current_user.get("user_id", "unknown"),
                      "operationlog.deadletter_retry",
                      {"retried": retried}, risk="low")
    return {"retried": retried}
