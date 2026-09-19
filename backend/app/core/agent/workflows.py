"""
跨域业务工作流引擎（路线图 Phase B / B3）
==========================================

把三条跨域业务流程跑成 LangGraph 确定性状态图（规则节点 + interrupt
人审门；LLM 仅在 AgentRuntime 场景图使用，工作流保持规则主干）：

- ``reimbursement_approval``  报销审批流：提交 → 规则预审 → 分级人审门
  （≥5000 元强制人审）→ 核验 → 审批 → 归档
- ``procure_to_pay``          采购到支付：PO 校验 → 支付排程 → 大额人审门
  （≥10 万）→ 付款标记
- ``contract_approval``       合同审批流：字段校验 → 规则审查 → 人审门
  （合同一律人审）→ 批准生效

与 AgentRuntime 的关系：
- 共享 MySQL Checkpointer（thread_id 用 ``wf:`` 前缀隔离，防状态串台）；
- 人审中断产出与场景图相同的 interrupt 载荷 → 复用审批面板与
  ``/agent-ops/approvals/{thread}/decide`` 决定路由（domain 前缀
  ``workflow:`` 时由本模块 resume）；
- 每个节点完成即 Checkpointer 落盘，可跨重启续跑；全程审计哈希链。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import structlog
from typing_extensions import TypedDict

from langgraph.graph import END as _GRAPH_END
from app.core.agent.tools_v2 import ToolContext

logger = structlog.get_logger()

# 分级人审阈值（与审批中心的级别配置对齐）
REIMB_AUTO_APPROVE_LIMIT = 5000.0
PAYMENT_HUMAN_GATE_LIMIT = 100_000.0


class WorkflowState(TypedDict, total=False):
    """工作流通用状态。"""

    input: dict            # 触发参数
    steps: list            # 已完成节点（审计轨迹）
    result: dict           # 输出/单据信息
    error: Optional[str]   # 驳回/失败原因（非空即终止）


def _route_on_error(next_node: str):
    """条件边工厂：state.error 非空时终止（END），否则进入下一节点。"""
    def _route(state: WorkflowState) -> str:
        return _GRAPH_END if state.get("error") else next_node
    return _route


async def _upsert_with_retry(repo, record: dict, key_field: str,
                             attempts: int = 3, delay: float = 0.4) -> None:
    """同步落库带重试：规避与 fire-and-forget 写穿任务的 sqlite 写锁竞争。"""
    import asyncio as _asyncio

    last_err = None
    for _ in range(attempts):
        try:
            if await repo.upsert(record, key_field=key_field):
                return
            last_err = RuntimeError("upsert returned False")
        except Exception as e:  # noqa: BLE001
            last_err = e
        await _asyncio.sleep(delay)
    if last_err:
        logger.warning("workflow.upsert_retry_exhausted",
                       repo=repo.__category__, error=str(last_err))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ctx_for(thread_id: str, user_id: str, domain: str) -> ToolContext:
    return ToolContext(user_id=user_id, username=user_id,
                       agent_key=domain, thread_id=thread_id)


async def _audit(action: str, resource: str, details: dict,
                 risk: str = "low", actor: str = "system") -> None:
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(actor_did=actor, action=action, resource=resource,
                              result="success", details=details, risk_level=risk)
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.audit_failed", action=action, error=str(e))


# ===========================================================================
# 图 1：报销审批流
# ===========================================================================

def _build_reimbursement_approval():
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    async def submit(state: WorkflowState) -> dict:
        from app.core.agent.orchestrator import orchestrator

        agent = orchestrator.get_agent_by_name("报销Agent")
        data = dict(state["input"])
        data.setdefault("has_receipt", True)
        record = await agent.submit_reimbursement(data)
        if "error" in record:
            return {"error": record["error"], "steps": ["submit"]}
        await _audit("workflow.reimb.submitted", f"reimbursement:{record['reimbursement_id']}",
                     {"amount": record["amount"], "employee": record["employee_name"]})
        return {"result": {"reimbursement_id": record["reimbursement_id"],
                           "reimbursement_no": record["reimbursement_no"],
                           "amount": record["amount"]},
                "steps": ["submit"]}

    async def precheck(state: WorkflowState) -> dict:
        inp = state["input"]
        problems = []
        if float(inp.get("amount", 0)) <= 0:
            problems.append("金额必须大于0")
        if not inp.get("description"):
            problems.append("缺少报销事由")
        if not inp.get("has_receipt", False):
            problems.append("未附发票")
        if problems:
            reimb_id = state["result"].get("reimbursement_id", "")
            from app.core.agent.orchestrator import orchestrator

            agent = orchestrator.get_agent_by_name("报销Agent")
            if reimb_id and reimb_id in agent._requests:
                agent._requests[reimb_id]["status"] = "rejected"
                agent._requests[reimb_id]["reject_reason"] = "；".join(problems)
                from app.db.repositories.reimbursement_repository import reimbursement_request_repository

                await _upsert_with_retry(
                    reimbursement_request_repository,
                    agent._requests[reimb_id], "reimbursement_id"
                )
            await _audit("workflow.reimb.precheck_rejected", f"reimbursement:{reimb_id}",
                         {"problems": problems}, risk="medium")
            return {"error": "预审未通过：" + "；".join(problems), "steps": state["steps"] + ["precheck"]}
        return {"steps": state["steps"] + ["precheck"]}

    async def approval_gate(state: WorkflowState) -> dict:
        amount = float(state["input"].get("amount", 0))
        if amount < REIMB_AUTO_APPROVE_LIMIT:
            # 小额：免人审，直接核验+自动审批（决策 D6：write_low 免审）
            return {"steps": state["steps"] + ["approval_gate(auto)"]}

        decision = interrupt({
            "tool": "workflow:reimbursement_approval",
            "risk": "write_high",
            "args": {"reimbursement_id": state["result"].get("reimbursement_id"),
                     "amount": amount},
            "preview": (
                f"报销单 {state['result'].get('reimbursement_no')} 金额 ¥{amount}，"
                f"预审已通过；批准后将完成核验与审批（员工："
                f"{state['input'].get('employee_name', '—')}）"
            ),
        })
        if not isinstance(decision, dict) or not decision.get("approved"):
            reimb_id = state["result"].get("reimbursement_id", "")
            from app.core.agent.orchestrator import orchestrator

            agent = orchestrator.get_agent_by_name("报销Agent")
            if reimb_id and reimb_id in agent._requests:
                agent._requests[reimb_id]["status"] = "rejected"
                agent._requests[reimb_id]["reject_reason"] = "人工审批驳回"
                from app.db.repositories.reimbursement_repository import reimbursement_request_repository

                await _upsert_with_retry(
                    reimbursement_request_repository,
                    agent._requests[reimb_id], "reimbursement_id"
                )
            return {"error": "人工审批驳回该报销单",
                    "steps": state["steps"] + ["approval_gate(rejected)"]}
        return {"steps": state["steps"] + ["approval_gate(approved)"]}

    async def verify_and_approve(state: WorkflowState) -> dict:
        from app.core.agent.orchestrator import orchestrator

        agent = orchestrator.get_agent_by_name("报销Agent")
        reimb_id = state["result"].get("reimbursement_id", "")
        v = await agent.verify_reimbursement(reimb_id)
        if "error" in v:
            return {"error": v["error"], "steps": state["steps"] + ["verify"]}
        a = await agent.approve_reimbursement(reimb_id, approved=True, remark="工作流审批")
        if "error" in a:
            return {"error": a["error"], "steps": state["steps"] + ["approve"]}
        if reimb_id in agent._requests:
            from app.db.repositories.reimbursement_repository import reimbursement_request_repository

            await _upsert_with_retry(
                reimbursement_request_repository,
                agent._requests[reimb_id], "reimbursement_id"
            )
        await _audit("workflow.reimb.approved", f"reimbursement:{reimb_id}",
                     {"amount": state["result"].get("amount")}, risk="medium")
        return {"steps": state["steps"] + ["verify", "approve"],
                "result": {**state["result"], "status": "approved"}}

    builder = StateGraph(WorkflowState)
    builder.add_node("submit", submit)
    builder.add_node("precheck", precheck)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("verify_and_approve", verify_and_approve)
    builder.add_edge(START, "submit")
    builder.add_edge("submit", "precheck")
    builder.add_conditional_edges("precheck", _route_on_error("approval_gate"),
                                  ["approval_gate", END])
    builder.add_conditional_edges("approval_gate", _route_on_error("verify_and_approve"),
                                  ["verify_and_approve", END])
    builder.add_edge("verify_and_approve", END)
    return builder


# ===========================================================================
# 图 2：采购到支付
# ===========================================================================

def _build_procure_to_pay():
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt
    from uuid import uuid4
    from datetime import date, timedelta

    async def validate_po(state: WorkflowState) -> dict:
        inp = state["input"]
        if not inp.get("po_id") or float(inp.get("total_amount", 0)) <= 0:
            return {"error": "PO 校验失败：缺少 po_id 或金额非法", "steps": ["validate_po"]}
        return {"steps": ["validate_po"]}

    async def schedule_payment(state: WorkflowState) -> dict:
        """规则生成支付排程（绕过 LLM 优化，工作流保持确定性）。"""
        inp = state["input"]
        payment = {
            "payment_id": f"PAY-{str(uuid4())[:8]}",
            "po_id": inp["po_id"],
            "supplier_id": inp.get("supplier_id", ""),
            "amount": float(inp["total_amount"]),
            "payment_date": (date.today() + timedelta(days=30)).isoformat(),
            "status": "scheduled",
            "optimization_method": "workflow_rules",
            "reasoning": f"采购到支付工作流生成（PO {inp['po_id']}，net30）",
            "created_at": _now_iso(),
        }
        from app.db.repositories.treasury_contract_repository import payment_schedule_repository

        await payment_schedule_repository.upsert(payment, "payment_id")
        await _audit("workflow.p2p.scheduled", f"payment:{payment['payment_id']}",
                     {"po_id": inp["po_id"], "amount": payment["amount"]}, risk="medium")
        return {"result": {"payment_id": payment["payment_id"],
                           "amount": payment["amount"],
                           "payment_date": payment["payment_date"]},
                "steps": state["steps"] + ["schedule_payment"]}

    async def payment_gate(state: WorkflowState) -> dict:
        amount = float(state["result"].get("amount", 0))
        if amount < PAYMENT_HUMAN_GATE_LIMIT:
            return {"steps": state["steps"] + ["payment_gate(auto)"]}

        decision = interrupt({
            "tool": "workflow:procure_to_pay",
            "risk": "write_high",
            "args": {"payment_id": state["result"].get("payment_id"), "amount": amount},
            "preview": (
                f"大额付款确认：PO {state['input'].get('po_id')} 应付 ¥{amount}，"
                f"排程日 {state['result'].get('payment_date')}；批准后标记为已付"
            ),
        })
        if not isinstance(decision, dict) or not decision.get("approved"):
            return {"error": "大额付款被人工驳回",
                    "steps": state["steps"] + ["payment_gate(rejected)"]}
        return {"steps": state["steps"] + ["payment_gate(approved)"]}

    async def pay(state: WorkflowState) -> dict:
        payment_id = state["result"].get("payment_id", "")
        from app.db.repositories.treasury_contract_repository import payment_schedule_repository
        from app.db.repositories.base import has_db

        if has_db():
            from sqlalchemy import update

            async with __import__("app.db.repositories.base", fromlist=["session_scope"]).session_scope() as session:
                await session.execute(
                    update(payment_schedule_repository.__model__)
                    .where(payment_schedule_repository.__model__.payment_id == payment_id)
                    .values(status="paid")
                )
        from app.core.agent.orchestrator import orchestrator

        agent = orchestrator.get_agent_by_name("资金Agent")
        if agent and payment_id in agent._payment_schedule:
            agent._payment_schedule[payment_id]["status"] = "paid"
        await _audit("workflow.p2p.paid", f"payment:{payment_id}",
                     {"amount": state["result"].get("amount")}, risk="high")
        return {"steps": state["steps"] + ["pay"],
                "result": {**state["result"], "status": "paid"}}

    builder = StateGraph(WorkflowState)
    builder.add_node("validate_po", validate_po)
    builder.add_node("schedule_payment", schedule_payment)
    builder.add_node("payment_gate", payment_gate)
    builder.add_node("pay", pay)
    builder.add_edge(START, "validate_po")
    builder.add_conditional_edges("validate_po", _route_on_error("schedule_payment"),
                                  ["schedule_payment", END])
    builder.add_conditional_edges("schedule_payment", _route_on_error("payment_gate"),
                                  ["payment_gate", END])
    builder.add_conditional_edges("payment_gate", _route_on_error("pay"), ["pay", END])
    builder.add_edge("pay", END)
    return builder


# ===========================================================================
# 图 3：合同审批流
# ===========================================================================

def _build_contract_approval():
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    async def validate(state: WorkflowState) -> dict:
        from app.core.agent.orchestrator import orchestrator

        agent = orchestrator.get_agent_by_name("合同Agent")
        inp = state["input"]
        contract = await agent.create_contract(
            title=inp["title"], type=inp["type"], counterparty=inp["counterparty"],
            amount=float(inp["amount"]), department=inp["department"],
            start_date=inp["start_date"], end_date=inp["end_date"],
            terms=inp.get("terms", ""),
        )
        from app.db.repositories.treasury_contract_repository import contract_repository

        await contract_repository.upsert(contract, "contract_id")
        await _audit("workflow.contract.created", f"contract:{contract['contract_id']}",
                     {"amount": contract["amount"], "counterparty": contract["counterparty"]})
        return {"result": {"contract_id": contract["contract_id"],
                           "amount": contract["amount"],
                           "title": contract["title"]},
                "steps": ["validate"]}

    async def review(state: WorkflowState) -> dict:
        """规则条款审查：金额/期限合理性，产出审查备注。"""
        inp = state["input"]
        notes = []
        amount = float(inp.get("amount", 0))
        if amount > 1_000_000:
            notes.append("金额超100万，建议法务复核")
        if str(inp.get("start_date")) >= str(inp.get("end_date")):
            return {"error": "起止日期非法", "steps": state["steps"] + ["review"]}
        if not inp.get("terms"):
            notes.append("缺少主要条款")
        return {"result": {**state["result"], "review_notes": notes or ["无风险提示"]},
                "steps": state["steps"] + ["review"]}

    async def approval_gate(state: WorkflowState) -> dict:
        decision = interrupt({
            "tool": "workflow:contract_approval",
            "risk": "write_high",
            "args": {"contract_id": state["result"].get("contract_id"),
                     "amount": state["result"].get("amount")},
            "preview": (
                f"合同[{state['result'].get('title')}] ¥{state['result'].get('amount')}，"
                f"审查备注：{'；'.join(state['result'].get('review_notes', []))}。"
                "批准后合同生效（draft → approved）"
            ),
        })
        if not isinstance(decision, dict) or not decision.get("approved"):
            contract_id = state["result"].get("contract_id", "")
            from app.core.agent.orchestrator import orchestrator

            agent = orchestrator.get_agent_by_name("合同Agent")
            if contract_id in agent._contracts:
                agent._contracts[contract_id]["status"] = "rejected"
            from app.db.repositories.treasury_contract_repository import contract_repository

            await contract_repository.update_status(contract_id, "rejected")
            return {"error": "合同被人工驳回", "steps": state["steps"] + ["approval_gate(rejected)"]}
        return {"steps": state["steps"] + ["approval_gate(approved)"]}

    async def approve(state: WorkflowState) -> dict:
        from app.core.agent.orchestrator import orchestrator

        agent = orchestrator.get_agent_by_name("合同Agent")
        contract_id = state["result"].get("contract_id", "")
        contract = await agent.approve_contract(
            contract_id=contract_id, approved_by="workflow",
            comment="；".join(state["result"].get("review_notes", [])),
        )
        if "error" in contract:
            return {"error": contract["error"], "steps": state["steps"] + ["approve"]}
        from app.db.repositories.treasury_contract_repository import contract_repository

        await contract_repository.update_status(
            contract_id, "approved",
            extra={"approved_by": "workflow", "approved_at": contract.get("approved_at")},
        )
        await _audit("workflow.contract.approved", f"contract:{contract_id}",
                     {"amount": state["result"].get("amount")}, risk="high")
        return {"steps": state["steps"] + ["approve"],
                "result": {**state["result"], "status": "approved"}}

    builder = StateGraph(WorkflowState)
    builder.add_node("validate", validate)
    builder.add_node("review", review)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("approve", approve)
    builder.add_edge(START, "validate")
    builder.add_conditional_edges("validate", _route_on_error("review"), ["review", END])
    builder.add_conditional_edges("review", _route_on_error("approval_gate"),
                                  ["approval_gate", END])
    builder.add_conditional_edges("approval_gate", _route_on_error("approve"), ["approve", END])
    builder.add_edge("approve", END)
    return builder


# ===========================================================================
# 注册表 + 启动/恢复
# ===========================================================================

WORKFLOW_CATALOG: dict[str, dict[str, Any]] = {
    "reimbursement_approval": {
        "description": "报销审批流：提交→规则预审→分级人审(≥5000)→核验→审批",
        "input_hint": "{employee_name, employee_id?, department?, category, amount, description, has_receipt?}",
        "builder": _build_reimbursement_approval,
    },
    "procure_to_pay": {
        "description": "采购到支付：PO校验→支付排程(net30)→大额人审(≥10万)→付款",
        "input_hint": "{po_id, total_amount, supplier_id?}",
        "builder": _build_procure_to_pay,
    },
    "contract_approval": {
        "description": "合同审批流：创建→规则审查→人审→批准生效",
        "input_hint": "{title, type, counterparty, amount, department, start_date, end_date, terms?}",
        "builder": _build_contract_approval,
    },
}

graphs_cache: dict[str, Any] = {}


def _get_checkpointer():
    from app.core.agent.checkpoint_mysql import get_mysql_checkpointer

    return get_mysql_checkpointer()


def _get_graph(name: str):
    if name not in WORKFLOW_CATALOG:
        raise KeyError(f"未知工作流: {name}")
    if name not in graphs_cache:
        graphs_cache[name] = WORKFLOW_CATALOG[name]["builder"]().compile(
            checkpointer=_get_checkpointer()
        )
        logger.info("workflow.compiled", workflow=name)
    return graphs_cache[name]


async def start_workflow(name: str, input_data: dict, ctx: ToolContext,
                         thread_id: str = "") -> dict:
    """启动工作流。返回 {"status": done/awaiting_approval/error, reply, interrupt}。"""
    graph = _get_graph(name)
    thread_id = thread_id or f"wf:{name}:{uuid4().hex[:10]}"
    token = _ctx_var.set(ctx)
    try:
        result = await graph.ainvoke(
            {"input": input_data, "steps": [], "result": {}, "error": None},
            {"configurable": {"thread_id": thread_id}},
        )
    finally:
        _ctx_var.reset(token)

    interrupts = result.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        return {"status": "awaiting_approval", "thread_id": thread_id,
                "reply": f"工作流 {name} 需人工审批：{payload.get('preview', '')}",
                "interrupt": payload}

    if result.get("error"):
        return {"status": "error", "thread_id": thread_id,
                "reply": f"工作流 {name} 终止：{result['error']}", "interrupt": None}

    return {"status": "done", "thread_id": thread_id,
            "reply": f"工作流 {name} 完成：{result.get('result', {})}",
            "result": result.get("result", {}), "interrupt": None}


async def resume_workflow(name: str, thread_id: str, decision: dict,
                          ctx: ToolContext) -> dict:
    """人审决定后恢复工作流。"""
    from langgraph.types import Command

    graph = _get_graph(name)
    token = _ctx_var.set(ctx)
    try:
        result = await graph.ainvoke(
            Command(resume=decision),
            {"configurable": {"thread_id": thread_id}},
        )
    finally:
        _ctx_var.reset(token)

    interrupts = result.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        return {"status": "awaiting_approval", "reply": "仍有待审批节点。",
                "interrupt": payload}
    if result.get("error"):
        return {"status": "error", "reply": f"工作流终止：{result['error']}"}
    return {"status": "done",
            "reply": f"工作流 {name} 完成：{result.get('result', {})}",
            "result": result.get("result", {})}


# 调用者上下文（工作流节点内的治理面/审计归属）
from contextvars import ContextVar  # noqa: E402

_ctx_var: ContextVar[Optional[ToolContext]] = ContextVar("wf_tool_ctx", default=None)


async def _graph_pending(name: str, thread_id: str) -> bool:
    """工作流在指定线程上是否有挂起的人审中断（supervisor 重放探测）。"""
    try:
        graph = _get_graph(name)
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return any(getattr(t, "interrupts", None) for t in state.tasks)
    except Exception:  # noqa: BLE001
        return False


def list_workflows() -> list[dict]:
    return [{"name": name, "description": meta["description"],
             "input_hint": meta["input_hint"]}
            for name, meta in WORKFLOW_CATALOG.items()]
