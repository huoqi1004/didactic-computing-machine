"""
Agent 编排服务（路线图 Phase A / A8）
======================================

连接 BaseAgent.llm_chat（指令入口）、AgentRuntime（图执行）与
审批持久化（agent_approval_requests）：人审中断时落审批行，
恢复路由据此定位 thread 并以 Command(resume) 续跑。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.core.agent.tools_v2 import ToolContext, tool_registry_v2

logger = structlog.get_logger()


def _get_agent_by_domain(domain: str):
    from app.core.agent.orchestrator import orchestrator

    for agent in orchestrator.list_agents():
        if getattr(agent, "domain", "") == domain:
            return agent
    return None


async def run_scenario_for_agent(agent, messages: list[dict], kwargs: dict) -> Optional[str]:
    """
    BaseAgent.llm_chat 的编排入口。

    - 本域无注册工具 → 返回 None（回退旧单发路径）；
    - 编排成功 → 返回最终回复文本（含"待审批"提示文案）；
    - write_high 中断 → 落 agent_approval_requests 行（状态 pending）。

    kwargs 识别：user_id / username / channel_id / thread_id。
    """
    from app.core.agent.runtime import agent_runtime
    from app.db.repositories.base import has_db

    # supervisor 是特判域（无治理面工具，走分类+委派），跳过工具域检查
    if agent.domain != "supervisor":
        if not has_db() or not agent_runtime.has_domain(agent.domain):
            return None
    elif not has_db():
        return None

    # 预算护栏：该域当日 token 超限时抛出（llm_chat 捕获后回退规则路径）
    if not agent_runtime._check_budget(agent.domain):
        from app.core.agent.runtime import AgentRuntime as _AR

        from app.core.config import settings as _s

        raise RuntimeError(
            f"域 {agent.domain} 当日 LLM token 预算已耗尽（上限 {_s.agent_daily_token_budget}），编排降级"
        )

    # 提取最后一条用户消息作为指令
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = str(msg.get("content", ""))
            break
    if not user_message:
        return None

    user_id = str(kwargs.get("user_id") or "unknown")
    username = str(kwargs.get("username") or user_id)
    channel_id = str(kwargs.get("channel_id") or "")
    thread_id = str(
        kwargs.get("thread_id") or f"agent:{agent.domain}:{user_id}"
    )

    ctx = ToolContext(
        user_id=user_id, username=username,
        agent_key=agent.domain, thread_id=thread_id, channel_id=channel_id,
    )

    # Supervisor 跨域协同（Phase C）：意图分类→委派子域→综合
    if agent.domain == "supervisor":
        from app.core.agent.supervisor import start_supervisor

        result = await start_supervisor(user_message, ctx, thread_id=thread_id)
        if result["status"] == "awaiting_approval":
            await _create_approval_record(result.get("interrupt") or {}, ctx)
        return str(result.get("reply") or "")

    if result["status"] == "awaiting_approval":
        await _create_approval_record(result.get("interrupt") or {}, ctx)

    return str(result.get("reply") or "")


async def resume_after_decision(
    thread_id: str, approved: bool, comment: str, decided_by: str,
) -> Optional[dict]:
    """
    审批决定后恢复图执行（审批路由调用）。

    Returns:
        {"reply": str, "status": str, "channel_id": str} | None（无此审批行）
    """
    from app.core.agent.runtime import agent_runtime
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return None

    async with session_scope() as session:
        from app.db.models import agent_approval_requests
        from sqlalchemy import select

        row = (
            await session.execute(
                select(agent_approval_requests).where(
                    agent_approval_requests.c.thread_id == thread_id
                )
            )
        ).first()
        if row is None:
            return None
        domain = row.domain
        channel_id = row.channel_id or ""
        agent_key = row.agent_key
        # 执行权限沿用发起人语境（审批人仅需 agent:approve 权限做决定，
        # 工具 RBAC 校验的是指令发起人的权限——agent 借用用户权限原则）
        requested_by = row.requested_by or decided_by

    ctx = ToolContext(
        user_id=requested_by, username=requested_by,
        agent_key=agent_key, thread_id=thread_id, channel_id=channel_id,
    )

    # 定制变更审批恢复（Phase F1：批准生效/驳回）
    if domain == "customization":
        from app.core.agent.customization import customization_service

        # row.tool 形如 "tool:sales_create_order" / "gate:reimbursement_..."
        ctype, _, ctarget = (row.tool or "").partition(":")
        cust_result = await customization_service.apply_pending(
            ctype, ctarget, approved, decided_by=decided_by, comment=comment,
        )
        status = "executed" if cust_result.get("status") == "executed" else (
            "rejected" if not approved else "failed")
        async with session_scope() as session:
            from app.db.models import agent_approval_requests
            from sqlalchemy import update

            await session.execute(
                update(agent_approval_requests)
                .where(agent_approval_requests.c.thread_id == thread_id)
                .values(
                    status=status,
                    decided_by=decided_by,
                    decided_at=datetime.now(timezone.utc),
                    result_reply=str(cust_result.get("reply", ""))[:4000],
                )
            )
        await _audit_decision(decided_by, thread_id, approved, comment, agent_key)
        return {"reply": str(cust_result.get("reply", "")),
                "status": cust_result.get("status", "done"),
                "channel_id": channel_id}

    # Supervisor 协同人审恢复（Phase C：先恢复子图、再综合）
    if domain == "supervisor":
        from app.core.agent.supervisor import resume_supervisor

        sup_result = await resume_supervisor(
            thread_id, {"approved": approved, "comment": comment}, ctx,
        )
        status = {"done": "executed"}.get(sup_result["status"], sup_result["status"])
        if not approved:
            status = "rejected"
        async with session_scope() as session:
            from app.db.models import agent_approval_requests
            from sqlalchemy import update

            await session.execute(
                update(agent_approval_requests)
                .where(agent_approval_requests.c.thread_id == thread_id)
                .values(
                    status=status,
                    decided_by=decided_by,
                    decided_at=datetime.now(timezone.utc),
                    result_reply=str(sup_result.get("reply", ""))[:4000],
                )
            )
        await _audit_decision(decided_by, thread_id, approved, comment, agent_key)
        return {"reply": str(sup_result.get("reply", "")),
                "status": sup_result["status"], "channel_id": channel_id}

    # 跨域工作流的人审恢复（domain 形如 "workflow:reimbursement_approval"）
    if domain.startswith("workflow:"):
        from app.core.agent.workflows import resume_workflow

        wf_result = await resume_workflow(
            domain.split(":", 1)[1], thread_id,
            {"approved": approved, "comment": comment},
            ctx,
        )
        status = {"done": "executed", "error": "rejected"}.get(
            wf_result["status"], wf_result["status"]
        )
        if not approved:
            status = "rejected"
        async with session_scope() as session:
            from app.db.models import agent_approval_requests
            from sqlalchemy import update

            await session.execute(
                update(agent_approval_requests)
                .where(agent_approval_requests.c.thread_id == thread_id)
                .values(
                    status=status,
                    decided_by=decided_by,
                    decided_at=datetime.now(timezone.utc),
                    result_reply=str(wf_result.get("reply", ""))[:4000],
                )
            )
        await _audit_decision(decided_by, thread_id, approved, comment, agent_key)
        return {"reply": str(wf_result.get("reply", "")),
                "status": wf_result["status"], "channel_id": channel_id}

    result = await agent_runtime.resume(
        domain, thread_id,
        {"approved": approved, "comment": comment},
        ctx,
    )

    # 更新审批行状态与最终回复
    status = "executed" if result["status"] == "done" else result["status"]
    if not approved:
        status = "rejected"
    async with session_scope() as session:
        from app.db.models import agent_approval_requests
        from sqlalchemy import update

        await session.execute(
            update(agent_approval_requests)
            .where(agent_approval_requests.c.thread_id == thread_id)
            .values(
                status=status,
                decided_by=decided_by,
                decided_at=datetime.now(timezone.utc),
                result_reply=str(result.get("reply", ""))[:4000],
            )
        )

    await _audit_decision(decided_by, thread_id, approved, comment, agent_key)

    logger.info(
        "orchestration.resumed",
        thread_id=thread_id, approved=approved, status=status,
    )
    return {
        "reply": str(result.get("reply", "")),
        "status": result["status"],
        "channel_id": channel_id,
    }


async def _create_approval_record(payload: dict, ctx: ToolContext) -> None:
    """interrupt 载荷 → agent_approval_requests 行（状态 pending）。"""
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return
    # 审批人路由：gate 节点 per-node 角色（F3.4）优先 → 路由矩阵（F2.5）→
    # 均未给出则 None（落入存量三级审批链，向后兼容）
    approve_roles = None
    try:
        node_roles = payload.get("approve_roles")
        if isinstance(node_roles, (list, tuple)) and node_roles:
            approve_roles = [str(r) for r in node_roles]
        else:
            doc_type = str(payload.get("document_type")
                           or str(payload.get("tool", "")).split(":")[-1])
            amount = (payload.get("args") or {}).get("amount")
            from app.core.agent.customization import customization_service

            approve_roles = customization_service.resolve_approve_roles(
                doc_type, float(amount) if amount is not None else None,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("customization.route_resolve_failed", error=str(e))

    try:
        from app.db.repositories.base import dialect_upsert_stmt

        async with session_scope() as session:
            from app.db.models import agent_approval_requests

            values = dict(
                thread_id=ctx.thread_id,
                channel_id=ctx.channel_id or None,
                agent_key=ctx.agent_key,
                domain=ctx.agent_key,
                tool=str(payload.get("tool", "")),
                args_json=payload.get("args") or {},
                preview=str(payload.get("preview", "")),
                risk=str(payload.get("risk", "write_high")),
                status="pending",
                requested_by=ctx.user_id,
                approve_roles=approve_roles,
            )
            await session.execute(
                dialect_upsert_stmt(agent_approval_requests, values)
            )
        logger.info(
            "orchestration.approval_created",
            thread_id=ctx.thread_id, tool=payload.get("tool"),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("orchestration.approval_record_failed", error=str(e))


async def _audit_decision(decided_by: str, thread_id: str, approved: bool,
                          comment: str, agent_key: str) -> None:
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(
            actor_did=decided_by,
            action="approval.decided",
            resource=f"agent_approval:{thread_id}",
            result="success",
            details={"approved": approved, "comment": comment, "agent": agent_key},
            risk_level="high" if approved else "medium",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("orchestration.decision_audit_failed", error=str(e))


async def list_approvals(status: str = "pending", limit: int = 50,
                         approve_role: Optional[str] = None) -> list[dict]:
    """审批列表（审批路由/前端卡片数据源）。

    approve_role：按路由矩阵的审批角色过滤（命中角色或未路由单据均可见）。
    """
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return []
    async with session_scope() as session:
        from app.db.models import agent_approval_requests
        from sqlalchemy import select

        stmt = (
            select(agent_approval_requests)
            .order_by(agent_approval_requests.c.created_at.desc())
            .limit(limit)
        )
        if status and status != "all":
            stmt = stmt.where(agent_approval_requests.c.status == status)
        rows = (await session.execute(stmt)).all()

    result = []
    for r in rows:
        if approve_role:
            roles = r.approve_roles or []
            if roles and approve_role not in roles:
                continue  # 路由至其他角色的任务对本角色不可见
        created = r.created_at
        result.append({
            "thread_id": r.thread_id,
            "channel_id": r.channel_id,
            "agent_key": r.agent_key,
            "domain": r.domain,
            "tool": r.tool,
            "args": r.args_json,
            "preview": r.preview,
            "risk": r.risk,
            "status": r.status,
            "requested_by": r.requested_by,
            "decided_by": r.decided_by,
            "decided_at": decided_at.isoformat() if (decided_at := r.decided_at) else None,
            "result_reply": r.result_reply,
            "created_at": created.isoformat() if created else None,
            "approve_roles": r.approve_roles,
        })
    return result
