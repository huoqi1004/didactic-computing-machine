"""
工作流 Saga 补偿（roadmap-logging-sandbox G5.1/G5.2）
======================================================

工作流（DSL）中途失败的自动逆序补偿：

- **步骤登记**：DSL 任务节点成功后，若 handler 属可补偿类
  （:data:`COMPENSABLE_HANDLERS`——创建类步骤），登记
  （node/handler/tool/args/result）进 ``sagas`` 表（saga_type=workflow_saga，
  saga_id=wf:{thread_id}，留存豁免表，P1-2 固化）；
- **失败补偿**：工作流 error 终止时逆序执行——write_low 步骤自动补偿
  （复用 tools_v2 注册表补偿函数 / 本地补偿），write_high 与状态推进类
  步骤**不自动补偿**（资金/终态属业务决定），标记 needs_manual + error
  级告警待办（红线 2：回滚必审批的语义边界）；
- **重启恢复**（G5.2）：启动时扫描 executing 状态的 workflow_saga 行，
  续跑补偿（sagas 表只持久化数据不持久化闭包，补偿从
  COMPENSABLE_HANDLERS 映射重建——kill -9 后恢复可用）。

补偿闭包不可序列化是刻意设计：持久化的是"如何重建补偿"的数据
（handler→tool 映射 + 正向 args/result），不是闭包本身。
"""
from __future__ import annotations

import json
import structlog
from typing import Any, Optional

from sqlalchemy import text

logger = structlog.get_logger()

SAGA_TYPE = "workflow_saga"
ST_EXECUTING = "executing"
ST_COMPENSATED = "compensated"
ST_NEEDS_MANUAL = "needs_manual"

# 可补偿 handler → 工具注册表名（None = 使用本地补偿 _LOCAL_COMPENSATIONS）
COMPENSABLE_HANDLERS: dict[str, Optional[str]] = {
    "reimb.submit": "reimbursement_submit",   # 删报销单 + 清内存
    "contract.create": "contract_create",     # 删合同草稿 + 清内存
    "p2p.schedule": None,                     # 删付款计划 + 清内存（本地补偿）
}


async def _comp_p2p_schedule(ctx, args: dict, result: Any) -> None:
    """p2p.schedule 本地补偿：删付款计划行 + 清资金 Agent 内存。"""
    from app.agents.treasury.tools import _get_agent
    from app.db.repositories.base import has_db
    from app.db.repositories.treasury_contract_repository import (
        payment_schedule_repository,
    )

    payment_id = (result or {}).get("payment_id", "")
    if not payment_id:
        return
    agent = _get_agent()
    agent._payment_schedule.pop(payment_id, None)
    if has_db():
        await payment_schedule_repository.delete_by_id(payment_id)


_LOCAL_COMPENSATIONS = {
    "p2p.schedule": _comp_p2p_schedule,
}


def _saga_id(thread_id: str) -> str:
    return f"wf:{thread_id}"


def _jsonify(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value or {}


# sagas 表的语句编译用 Table 定义（物理表由 migrations 原生 DDL 创建，
# 此处仅声明列以复用 dialect_upsert_stmt 的方言感知 upsert）
from sqlalchemy import (
    Column as _Column,
    DateTime as _DateTime,
    Integer as _Integer,
    JSON as _SA_JSON,
    MetaData as _MetaData,
    String as _String,
    Table as _Table,
)

_sagas_table = _Table(
    "sagas", _MetaData(),
    _Column("saga_id", _String(150), primary_key=True),
    _Column("saga_type", _String(64)),
    _Column("status", _String(32)),
    _Column("steps", _SA_JSON),
    _Column("current_step", _Integer),
    _Column("created_at", _DateTime),
    _Column("updated_at", _DateTime),
)


async def _persist_saga(engine, saga_id: str, status: str, steps: list[dict]) -> None:
    from app.db.repositories.base import dialect_upsert_stmt

    stmt = dialect_upsert_stmt(
        _sagas_table,
        {"saga_id": saga_id, "saga_type": SAGA_TYPE, "status": status,
         "steps": steps, "current_step": len(steps)},
        conflict_cols=["saga_id"],
    )
    async with engine.begin() as conn:
        await conn.execute(stmt)


async def _saga_load(engine, saga_id: str) -> tuple[str, list[dict]]:
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, steps FROM sagas WHERE saga_id = :s"
        ), {"s": saga_id})).first()
    if row is None:
        return "", []
    return row[0], _loads(row[1])


async def saga_record(thread_id: str, node: str, handler: str, result: Any) -> None:
    """任务节点成功后登记步骤。

    可补偿 handler（COMPENSABLE_HANDLERS）标记 auto=true——失败时逆序自动补偿；
    其余（状态推进/资金终态类）标记 auto=false——失败报告必须点名
    "已发生且未撤销"的步骤（如 p2p.pay 资金已动），转人工业务决定。
    """
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        return
    sid = _saga_id(thread_id)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT steps FROM sagas WHERE saga_id = :s"
            ), {"s": sid})).first()
        steps = _loads(row[0]) if row else []
        steps.append({"node": node, "handler": handler,
                      "tool": COMPENSABLE_HANDLERS.get(handler) or handler,
                      "auto": handler in COMPENSABLE_HANDLERS,
                      "result": result if isinstance(result, dict) else {}})
        await _persist_saga(engine, sid, ST_EXECUTING, steps)
        logger.debug("saga.step_recorded", thread_id=thread_id, node=node,
                     handler=handler, auto=handler in COMPENSABLE_HANDLERS,
                     steps=len(steps))
    except Exception as e:  # noqa: BLE001
        # 登记失败不阻断工作流——失败终止时 needs_manual 兜底
        logger.warning("saga.record_failed", thread_id=thread_id, error=str(e))


async def saga_compensate(thread_id: str, reason: str = "") -> dict:
    """工作流失败终止 → 逆序补偿可补偿步骤；其余标记人工介入。"""
    from app.core import database as db_module
    from app.core.observability.alerts import alert_channel
    from app.core.agent.tools_v2 import ToolContext, tool_registry_v2
    from app.core.security.audit import audit_chain

    engine = db_module.get_engine()
    if engine is None:
        return {"status": "skipped", "reason": "db_unavailable"}
    sid = _saga_id(thread_id)
    status, steps = await _saga_load(engine, sid)
    if not steps:
        return {"status": "no_steps"}

    compensated: list[str] = []
    manual: list[dict] = []
    ctx = ToolContext(user_id="system", username="workflow_saga",
                      agent_key="workflow_saga", thread_id=thread_id)
    for step in reversed(steps):
        handler = step.get("handler", "")
        if not step.get("auto"):
            # 非自动补偿步骤（状态推进/资金终态类）→ 人工业务决定
            manual.append({"node": step.get("node"), "handler": handler,
                           "reason": "状态推进/资金终态类步骤，补偿属业务决定"})
            continue
        tool_name = COMPENSABLE_HANDLERS.get(handler)
        if tool_name is None and handler not in _LOCAL_COMPENSATIONS:
            manual.append({"node": step.get("node"), "handler": handler,
                           "reason": "无补偿实现（映射漂移）"})
            continue
        try:
            if tool_name is None:
                comp = _LOCAL_COMPENSATIONS.get(handler)
                if comp is None:
                    raise RuntimeError(f"handler {handler} 无本地补偿实现")
                await comp(ctx, {}, step.get("result") or {})
            else:
                spec = tool_registry_v2.get(tool_name)
                if spec is None or spec.compensation is None:
                    raise RuntimeError(f"工具 {tool_name} 补偿不可用（注册表漂移）")
                await spec.compensation(ctx, {}, step.get("result") or {})
            compensated.append(handler)
        except Exception as e:  # noqa: BLE001
            manual.append({"node": step.get("node"), "handler": handler,
                           "reason": f"补偿执行失败: {e}"})

    final_status = ST_COMPENSATED if not manual else ST_NEEDS_MANUAL
    await _persist_saga(engine, sid, final_status, steps)
    await audit_chain.log(
        actor_did="system", action="workflow.saga_compensated",
        resource=f"workflow:{thread_id}",
        result="success" if not manual else "partial",
        details={"reason": reason, "compensated": compensated, "manual": manual},
        risk_level="high" if manual else "medium", trace_id="", business_key="",
    )
    if manual:
        try:
            await alert_channel.raise_alert(
                rule="workflow_saga_needs_manual",
                title=f"工作流 {thread_id} 失败补偿需人工介入（{len(manual)} 步）",
                severity="error",
                details={"thread_id": thread_id, "reason": reason,
                         "compensated": compensated, "manual": manual},
                cooldown_key=thread_id, cooldown_s=300.0,
            )
        except Exception:  # noqa: BLE001
            pass
    logger.info("saga.compensated", thread_id=thread_id,
                compensated=compensated, manual=len(manual))
    return {"status": final_status, "compensated": compensated, "manual": manual}


async def saga_recover_pending() -> dict:
    """启动恢复（G5.2）：续跑 executing 状态的工作流补偿（kill -9 语义）。"""
    from app.core import database as db_module

    engine = db_module.get_engine()
    if engine is None:
        return {"recovered": 0}
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT saga_id FROM sagas WHERE saga_type = :t AND status = :s"
            ), {"t": SAGA_TYPE, "s": ST_EXECUTING})).mappings().all()
    except Exception as e:  # noqa: BLE001
        logger.warning("saga.recover_scan_failed", error=str(e))
        return {"recovered": 0, "error": str(e)}
    recovered = []
    for row in rows:
        thread_id = str(row["saga_id"])[len("wf:"):] if str(
            row["saga_id"]).startswith("wf:") else str(row["saga_id"])
        try:
            out = await saga_compensate(thread_id, reason="进程重启恢复（G5.2）")
            recovered.append({"thread_id": thread_id, **out})
        except Exception as e:  # noqa: BLE001
            logger.error("saga.recover_failed", thread_id=thread_id, error=str(e))
    if recovered:
        logger.warning("saga.recovered_pending", count=len(recovered))
    return {"recovered": len(recovered), "items": recovered}
