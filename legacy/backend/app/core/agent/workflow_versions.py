"""
工作流定义版本管理（路线图 Phase F / F3.5）
==============================================

DSL 定义的**发布—历史—回滚—灰度**生命周期，配四条治理红线：

- 发布走人审（DSL 影响面大）：经定制层 ``workflow:{name}`` 变更审批通道
  （``customization_service.apply_change`` 判定为放宽类 → 审批行 → 批准后
  :func:`on_publish_decision` 落地）；
- 版本不可变：已发布（stable/canary/superseded）版本内容不可原地改，
  只能另存新版本；草稿可覆盖；
- **粘性灰度**：canary 版本按单据 ID 哈希命中（同单据同桶），
  且线程首次启动即把版本钉在 ``workflow_thread_pins``——同一单据/同一线程
  全程同一版本，杜绝"跑到一半换流程"的状态分裂；
- 发布后自动跑关联域评测集：**资金/薪酬/合同域低分强制阻断**（D-4 修订，
  评测 FAIL 自动回滚到上一 stable），其余域仅告警。

状态机：``draft → (审批) → stable | canary → superseded``。
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

logger = structlog.get_logger()

# 工作流 → 评测集关键字（tests/evals/agent_evals.json 用例 id 前缀）
WF_EVAL_KEYWORD: dict[str, str] = {
    "reimbursement_approval": "reimb",
    "procure_to_pay": "treasury",
    "contract_approval": "contract",
}
# D-4 修订：资金/薪酬/合同域低分强制阻断
BLOCKING_EVAL_DOMAINS = {"treasury", "payroll", "contract"}

# 发布后的评测任务登记（管理端轮询用）：workflow name -> run_id
_publish_eval_runs: dict[str, str] = {}


class WorkflowVersionError(Exception):
    """工作流版本操作被拒绝。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_definition(defn: dict) -> list[str]:
    """DSL 校验（白名单 handler + 阈值键 + 治理红线）。返回错误列表。"""
    from app.core.agent.customization import THRESHOLD_DEFAULTS
    from app.core.agent.workflow_dsl import HANDLERS, validate_dsl

    return validate_dsl(defn, set(THRESHOLD_DEFAULTS.keys()), set(HANDLERS.keys()))


def sticky_bucket(document_key: str) -> int:
    """单据 ID → 稳定桶号（0-99）：同一单据恒定命中同一路径。"""
    digest = hashlib.md5(str(document_key).encode()).hexdigest()
    return int(digest[:8], 16) % 100


# ===========================================================================
# 读写
# ===========================================================================

async def list_versions(name: Optional[str] = None, limit: int = 100) -> list[dict]:
    from sqlalchemy import select

    from app.db.models import workflow_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return []
    async with session_scope() as session:
        stmt = select(workflow_definitions).order_by(
            workflow_definitions.c.id.desc()).limit(limit)
        if name:
            stmt = stmt.where(workflow_definitions.c.name == name)
        rows = (await session.execute(stmt)).all()
    return [{
        "id": r.id, "name": r.name, "version": r.version, "status": r.status,
        "traffic": int(r.traffic or 0), "comment": r.comment,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "published_at": r.published_at.isoformat() if r.published_at else None,
        "node_count": len((r.definition or {}).get("nodes") or []),
    } for r in rows]


async def get_definition(name: str, version: str) -> Optional[dict]:
    from sqlalchemy import select

    from app.db.models import workflow_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return None
    async with session_scope() as session:
        row = (await session.execute(
            select(workflow_definitions).where(
                workflow_definitions.c.name == name,
                workflow_definitions.c.version == version)
        )).first()
    if row is None:
        return None
    return dict(definition=row.definition, status=row.status,
                traffic=int(row.traffic or 0), comment=row.comment,
                created_by=row.created_by, id=row.id)


async def save_draft(name: str, version: str, definition: dict,
                     created_by: str, comment: str = "") -> dict:
    """保存/覆盖草稿（发布前编辑态）。已发布版本不可变。"""
    from app.db.models import workflow_definitions
    from app.db.repositories.base import dialect_upsert_stmt, has_db, session_scope

    if not name or not version:
        raise WorkflowVersionError("name/version 不能为空")
    if not has_db():
        raise WorkflowVersionError("存储不可用")

    errors = validate_definition(definition or {})
    if errors:
        raise WorkflowVersionError("DSL 校验未通过：" + "；".join(errors))

    existing = await get_definition(name, version)
    if existing and existing["status"] in ("stable", "canary", "superseded"):
        raise WorkflowVersionError(f"版本 {version} 已发布，内容不可变（请另存新版本）")

    values = dict(name=name, version=version, status="draft", traffic=0,
                  definition=definition, comment=comment[:512],
                  created_by=created_by)
    async with session_scope() as session:
        await session.execute(dialect_upsert_stmt(
            workflow_definitions, values, conflict_cols=["name", "version"]))
    await _audit("workflow.definition.draft_saved", name,
                 {"version": version, "comment": comment}, created_by)
    logger.info("workflow.draft_saved", name=name, version=version, by=created_by)
    return {"status": "draft", "name": name, "version": version,
            "nodes": len(definition.get("nodes") or [])}


async def request_publish(name: str, version: str, traffic: int,
                          changed_by: str, comment: str = "") -> dict:
    """
    申请发布：DSL 发布影响面大 → 一律走定制层人审（红线 2）。

    Returns 同 ``customization_service.apply_change``：
    ``{"status": "pending_approval", "thread_id": ...}``
    """
    from app.core.agent.customization import customization_service
    from app.db.repositories.base import has_db

    if not has_db():
        raise WorkflowVersionError("存储不可用")
    row = await get_definition(name, version)
    if row is None:
        raise WorkflowVersionError(f"版本不存在: {name}@{version}")
    errors = validate_definition(row["definition"] or {})
    if errors:
        raise WorkflowVersionError("DSL 校验未通过：" + "；".join(errors))

    traffic = max(0, min(100, int(traffic or 0)))
    result = await customization_service.apply_change(
        ctype="workflow", target=name,
        overrides={"action": "publish", "version": version, "traffic": traffic,
                   "comment": comment},
        enabled=True, changed_by=changed_by,
        comment=f"发布工作流 {name}@{version}"
                + (f"（灰度 {traffic}%）" if 0 < traffic < 100 else ""),
    )
    return {**result, "name": name, "version": version, "traffic": traffic}


async def on_publish_decision(name: str, overrides: dict, approved: bool,
                              decided_by: str) -> dict:
    """定制审批决定回调（``customization_service.apply_pending`` 触发）。"""
    if not approved:
        await _audit("workflow.publish.rejected", name,
                     {"version": (overrides or {}).get("version")}, decided_by)
        return {"status": "rejected", "reply": f"发布申请已驳回：{name}"}

    version = (overrides or {}).get("version") or ""
    traffic = int((overrides or {}).get("traffic") or 0)
    result = await publish(name, version, traffic, decided_by, source="approval")
    return result


async def publish(name: str, version: str, traffic: int = 0,
                  published_by: str = "system", source: str = "direct") -> dict:
    """
    落地发布：目标版本 → stable（或 canary），旧版本 → superseded。

    ``traffic`` 在 (0,100) 之间为灰度：canary 按单据哈希粘性命中，
    此时**上一 stable 保留**（否则灰度桶外的单据无主版本可回退，
    S1 粘性灰度退化为全量切换）；仅前一个 canary 被替换。
    全量发布（0/100）才把 stable 与 canary 一并置 superseded。
    """
    from sqlalchemy import select, update

    from app.db.models import workflow_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise WorkflowVersionError("存储不可用")
    row = await get_definition(name, version)
    if row is None:
        raise WorkflowVersionError(f"版本不存在: {name}@{version}")
    errors = validate_definition(row["definition"] or {})
    if errors:
        raise WorkflowVersionError("DSL 校验未通过：" + "；".join(errors))

    traffic = max(0, min(100, int(traffic or 0)))
    target_status = "canary" if 0 < traffic < 100 else "stable"
    demote_statuses = ("canary",) if target_status == "canary" else ("stable", "canary")

    async with session_scope() as session:
        prev_all = (await session.execute(
            select(workflow_definitions.c.version, workflow_definitions.c.status)
            .where(workflow_definitions.c.name == name,
                   workflow_definitions.c.status.in_(("stable", "canary")))
        )).all()
        prev = [(p.version, p.status) for p in prev_all if p.version != version]
        await session.execute(
            update(workflow_definitions)
            .where(workflow_definitions.c.name == name,
                   workflow_definitions.c.status.in_(demote_statuses),
                   workflow_definitions.c.version != version)
            .values(status="superseded"))
        await session.execute(
            update(workflow_definitions)
            .where(workflow_definitions.c.name == name,
                   workflow_definitions.c.version == version)
            .values(status=target_status, traffic=traffic,
                    published_at=_now()))

    await _bump_signal()
    await _audit("workflow.published", name,
                 {"from": [f"{st}:{v}" for v, st in prev],
                  "to": f"{target_status}:{version}", "traffic": traffic,
                  "source": source}, published_by)
    logger.info("workflow.published", name=name, version=version,
                status=target_status, traffic=traffic, by=published_by)

    _start_publish_eval(name, prev, published_by)
    return {"status": "published", "workflow": name, "version": version,
            "mode": target_status, "traffic": traffic,
            "previous": [f"{st}:{v}" for v, st in prev]}


async def rollback(name: str, version: str, rolled_by: str) -> dict:
    """回滚到历史版本（该版本此前已通过审批，属收紧方向 → 即时生效）。"""
    from sqlalchemy import select, update

    from app.db.models import workflow_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise WorkflowVersionError("存储不可用")
    row = await get_definition(name, version)
    if row is None:
        raise WorkflowVersionError(f"版本不存在: {name}@{version}")

    async with session_scope() as session:
        prev = [(p.version, p.status) for p in (await session.execute(
            select(workflow_definitions.c.version, workflow_definitions.c.status)
            .where(workflow_definitions.c.name == name,
                   workflow_definitions.c.status.in_(("stable", "canary")))
        )).all() if p.version != version]
        await session.execute(
            update(workflow_definitions)
            .where(workflow_definitions.c.name == name,
                   workflow_definitions.c.status.in_(("stable", "canary")))
            .values(status="superseded"))
        await session.execute(
            update(workflow_definitions)
            .where(workflow_definitions.c.name == name,
                   workflow_definitions.c.version == version)
            .values(status="stable", traffic=0, published_at=_now()))

    await _bump_signal()
    await _audit("workflow.rolled_back", name,
                 {"from": [f"{st}:{v}" for v, st in prev],
                  "to": f"stable:{version}"}, rolled_by)
    logger.info("workflow.rolled_back", name=name, version=version, by=rolled_by)
    return {"status": "rolled_back", "workflow": name, "version": version,
            "previous": [f"{st}:{v}" for v, st in prev]}


# ===========================================================================
# 运行时解析（粘性版本绑定）
# ===========================================================================

async def resolve_sticky(name: str, document_key: str = "") -> tuple[dict, str]:
    """
    解析本次执行应使用的定义与版本。

    优先级：线程钉住版本 > canary 哈希命中 > stable > 内置模板（出厂默认）。
    """
    from sqlalchemy import select

    from app.db.models import workflow_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return {}, "builtin"
    try:
        async with session_scope() as session:
            rows = (await session.execute(
                select(workflow_definitions)
                .where(workflow_definitions.c.name == name,
                       workflow_definitions.c.status.in_(("stable", "canary")))
                .order_by(workflow_definitions.c.id.desc())
            )).all()
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.definition_load_failed", name=name, error=str(e))
        return {}, "builtin"
    if not rows:
        return {}, "builtin"

    stable = next((r for r in rows if r.status == "stable"), None)
    canary = next((r for r in rows if r.status == "canary"), None)
    chosen = stable
    if canary is not None and int(canary.traffic or 0) > 0 and document_key:
        if sticky_bucket(document_key) < int(canary.traffic):
            chosen = canary
    if chosen is None:
        chosen = canary or stable
    if chosen is None:
        return {}, "builtin"
    return dict(chosen.definition), chosen.version


async def pin_thread(thread_id: str, name: str, version: str,
                     document_key: str = "") -> None:
    """钉住线程版本（同单据全程同版本；重复钉住为幂等覆盖）。"""
    from app.db.models import workflow_thread_pins
    from app.db.repositories.base import dialect_upsert_stmt, has_db, session_scope

    if not thread_id or not version or version == "builtin" or not has_db():
        return
    try:
        async with session_scope() as session:
            await session.execute(dialect_upsert_stmt(
                workflow_thread_pins,
                dict(thread_id=thread_id, name=name, version=version,
                     document_key=(document_key or "")[:128] or None)))
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.pin_failed", thread_id=thread_id, error=str(e))


async def pinned_version(thread_id: str) -> Optional[str]:
    """读取线程已钉住的版本（未钉住返回 None）。"""
    from sqlalchemy import select

    from app.db.models import workflow_thread_pins
    from app.db.repositories.base import has_db, session_scope

    if not thread_id or not has_db():
        return None
    try:
        async with session_scope() as session:
            row = (await session.execute(
                select(workflow_thread_pins.c.version).where(
                    workflow_thread_pins.c.thread_id == thread_id)
            )).first()
        return row.version if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.pin_read_failed", thread_id=thread_id, error=str(e))
        return None


async def get_definition_by_version(name: str, version: str) -> Optional[dict]:
    row = await get_definition(name, version)
    return row["definition"] if row else None


# ===========================================================================
# 发布评测门禁（D-4：三域低分强制阻断）
# ===========================================================================

def _start_publish_eval(name: str, previous: list, published_by: str) -> None:
    """发布后异步跑关联域评测；阻断域 FAIL → 自动回滚上一 stable。"""
    keyword = WF_EVAL_KEYWORD.get(name)
    if not keyword:
        return

    async def _runner():
        from app.core.agent import eval_runner

        # source=gate：门禁触发的评测同样进趋势看板（F5.2），
        # 这样"发布当天被门禁拦下几次"是可数的，不是散在日志里。
        run_id = eval_runner.start(keyword, source="gate",
                                   triggered_by=f"publish:{published_by}")
        _publish_eval_runs[name] = run_id
        info = await eval_runner.wait(run_id)
        blocking = keyword in BLOCKING_EVAL_DOMAINS
        summary = info.get("summary")
        if summary == "FAIL" and blocking:
            # 回退目标：上一 stable；无 stable（灰度替换场景）则上一 canary
            last_good = (next((v for v, st in previous if st == "stable"), None)
                         or next((v for v, st in previous if st == "canary"), None))
            if last_good:
                try:
                    await rollback(name, last_good, f"eval-gate:{published_by}")
                    logger.error("workflow.publish_blocked_rolled_back", name=name,
                                 rolled_back_to=last_good, eval_run=run_id)
                    await _audit("workflow.publish.eval_blocked", name,
                                 {"rolled_back_to": last_good,
                                  "eval_run": run_id}, "system")
                    _publish_eval_runs[name] = run_id
                    return
                except Exception as e:  # noqa: BLE001
                    logger.error("workflow.eval_gate_rollback_failed", error=str(e))
            logger.error("workflow.publish_eval_failed_no_fallback", name=name,
                         summary=summary, run_id=run_id)
        logger.info("workflow.publish_eval_done", name=name, summary=summary,
                    blocking=blocking, run_id=run_id)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    asyncio.ensure_future(_runner())


def publish_eval_status(name: str) -> dict:
    """发布后评测任务状态（管理端查看门禁结果）。"""
    from app.core.agent import eval_runner

    run_id = _publish_eval_runs.get(name)
    if not run_id:
        return {"status": "none"}
    info = eval_runner.status(run_id) or {}
    return {"run_id": run_id, "blocking": WF_EVAL_KEYWORD.get(name)
            in BLOCKING_EVAL_DOMAINS, **info}


# ===========================================================================
# 内部
# ===========================================================================

async def _bump_signal() -> None:
    from app.core.agent.customization import customization_service

    await customization_service.bump_version_signal()


async def _audit(action: str, name: str, details: dict, actor: str) -> None:
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(
            actor_did=actor or "unknown", action=action,
            resource=f"workflow:{name}", result="success", details=details,
            risk_level="high" if action in ("workflow.published",
                                            "workflow.publish.eval_blocked")
            else "medium")
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.version_audit_failed", action=action, error=str(e))


def form_metadata() -> dict:
    """设计器表单元数据（节点类型/条件算子/handler 白名单/阈值白名单）。"""
    from app.core.agent import workflows as wf_mod  # noqa: F401  触发 handler 注册
    from app.core.agent.customization import THRESHOLD_DEFAULTS
    from app.core.agent.workflow_dsl import CONDITION_OPS, HANDLERS, NODE_TYPES

    return {
        "node_types": list(NODE_TYPES),
        "condition_ops": list(CONDITION_OPS),
        "handlers": sorted(HANDLERS.keys()),
        "threshold_keys": sorted(THRESHOLD_DEFAULTS.keys()),
        "workflows": wf_mod.list_workflows(),
        "builtin_dsl": {k: v for k, v in wf_mod.BUILTIN_DSL.items()},
    }
