"""
跨域业务工作流（路线图 Phase F / F3：DSL 驱动 + 版本管理 + 粘性灰度）
======================================================================

三条跨域业务流程以**声明式 DSL** 定义（app/core/agent/workflow_dsl.py），
本模块承载：

- **handler 注册表**：DSL task 节点引用的业务函数（白名单，非任意代码）；
- **内置 DSL 模板**（BUILTIN_DSL）：三条流程的出厂定义；
- **定义驱动运行**：start_workflow 优先加载 workflow_definitions 表中
  stable/canary 版本，无则回退内置模板；
- **版本管理**：发布（校验→published→设 stable/canary）/回滚/粘性灰度路由
  （单据 ID 哈希绑定版本，同单据全程同版本）；
- 人审门经 DSL gate 节点（阈值经定制层 _threshold、审批角色 per-node），
  审批行/决定路由复用 Phase F1 机制。

State 契约与行为与 Phase B 硬编码版**等价**（存量 9 测试即等价证明）。
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

import structlog

logger = structlog.get_logger()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _audit(action: str, resource: str, details: dict,
                 risk: str = "low", actor: str = "system") -> None:
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(actor_did=actor, action=action, resource=resource,
                              result="success", details=details, risk_level=risk)
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.audit_failed", action=action, error=str(e))


def _get_agent_by_name(name: str):
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name(name)
    if agent is None:
        raise RuntimeError(f"{name} 未启动")
    return agent


async def _settle_writes() -> None:
    """等待在途异步写穿任务落库——同记录的同步写必须排在其后，
    否则先调度的旧状态会覆盖后写入的新状态。"""
    from app.db.repositories.base import drain_pending_writes

    await drain_pending_writes()


# 调用者上下文
_ctx_var: ContextVar[Optional[Any]] = ContextVar("wf_tool_ctx", default=None)


# ===========================================================================
# handler 注册表（DSL task 节点白名单引用——安全边界）
# ===========================================================================

from app.core.agent.workflow_dsl import register_handler  # noqa: E402


@register_handler("reimb.submit")
async def _h_reimb_submit(state: dict, params: dict) -> dict:
    agent = _get_agent_by_name("报销Agent")
    data = dict(state.get("input") or {})
    data.setdefault("has_receipt", True)
    record = await agent.submit_reimbursement(data)
    if "error" in record:
        return {"error": record["error"]}
    await _audit("workflow.reimb.submitted",
                 f"reimbursement:{record['reimbursement_id']}",
                 {"amount": record["amount"], "employee": record["employee_name"]})
    return {"result": {"reimbursement_id": record["reimbursement_id"],
                       "reimbursement_no": record["reimbursement_no"],
                       "amount": record["amount"]}}


@register_handler("reimb.precheck")
async def _h_reimb_precheck(state: dict, params: dict) -> dict:
    from app.agents.reimbursement.agent import ReimbursementCategory
    from app.core.agent.domain_params import reimb_require_receipt
    from app.db.repositories.reimbursement_repository import (
        reimbursement_request_repository,
    )

    agent = _get_agent_by_name("报销Agent")
    inp = state.get("input") or {}
    reimb_id = (state.get("result") or {}).get("reimbursement_id", "")
    problems = []
    if float(inp.get("amount", 0) or 0) <= 0:
        problems.append("金额必须大于0")
    if not inp.get("description"):
        problems.append("缺少报销事由")
    # 与 Agent 预审同一判定：定制覆盖优先，未覆盖回退该类别内置默认
    try:
        cat_cfg = agent.CATEGORY_CONFIG[ReimbursementCategory(inp.get("category") or "other")]
        builtin = bool(cat_cfg.get("require_receipt", True))
    except (KeyError, ValueError):
        builtin = True
    if reimb_require_receipt(builtin) and not inp.get("has_receipt", False):
        problems.append("未附发票")
    if problems:
        if reimb_id and reimb_id in agent._requests:
            await _settle_writes()
            agent._requests[reimb_id]["status"] = "rejected"
            agent._requests[reimb_id]["reject_reason"] = "；".join(problems)
            await reimbursement_request_repository.upsert(
                agent._requests[reimb_id], key_field="reimbursement_id")
        await _audit("workflow.reimb.precheck_rejected",
                     f"reimbursement:{reimb_id}", {"problems": problems}, risk="medium")
        return {"error": "预审未通过：" + "；".join(problems)}
    return {}


@register_handler("reimb.verify_approve")
async def _h_reimb_verify_approve(state: dict, params: dict) -> dict:
    from app.db.repositories.reimbursement_repository import (
        reimbursement_request_repository,
    )

    agent = _get_agent_by_name("报销Agent")
    reimb_id = (state.get("result") or {}).get("reimbursement_id", "")
    v = await agent.verify_reimbursement(reimb_id)
    if "error" in v:
        return {"error": v["error"]}
    a = await agent.approve_reimbursement(reimb_id, approved=True, remark="工作流审批")
    if "error" in a:
        return {"error": a["error"]}
    if reimb_id in agent._requests:
        await _settle_writes()
        await reimbursement_request_repository.upsert(
            agent._requests[reimb_id], key_field="reimbursement_id")
    await _audit("workflow.reimb.approved", f"reimbursement:{reimb_id}",
                 {"amount": (state.get("result") or {}).get("amount")}, risk="medium")
    return {"result": {**(state.get("result") or {}), "status": "approved"}}


@register_handler("reimb.reject_mark")
async def _h_reimb_reject_mark(state: dict, params: dict) -> dict:
    from app.db.repositories.reimbursement_repository import (
        reimbursement_request_repository,
    )

    agent = _get_agent_by_name("报销Agent")
    reimb_id = (state.get("result") or {}).get("reimbursement_id", "")
    if reimb_id and reimb_id in agent._requests:
        await _settle_writes()
        agent._requests[reimb_id]["status"] = "rejected"
        agent._requests[reimb_id]["reject_reason"] = "人工审批驳回"
        await reimbursement_request_repository.upsert(
            agent._requests[reimb_id], key_field="reimbursement_id")
    return {"error": params.get("reason", "人工审批驳回该报销单")}


@register_handler("p2p.validate")
async def _h_p2p_validate(state: dict, params: dict) -> dict:
    inp = state.get("input") or {}
    if not inp.get("po_id") or float(inp.get("total_amount", 0) or 0) <= 0:
        return {"error": "PO 校验失败：缺少 po_id 或金额非法"}
    return {}


@register_handler("p2p.schedule")
async def _h_p2p_schedule(state: dict, params: dict) -> dict:
    from app.db.repositories.treasury_contract_repository import (
        payment_schedule_repository,
    )

    inp = state.get("input") or {}
    payment = {
        "payment_id": f"PAY-{str(uuid4())[:8]}",
        "po_id": inp.get("po_id", ""),
        "supplier_id": inp.get("supplier_id", ""),
        "amount": float(inp.get("total_amount", 0)),
        "payment_date": (date.today() + timedelta(days=30)).isoformat(),
        "status": "scheduled",
        "optimization_method": "workflow_rules",
        "reasoning": f"采购到支付工作流生成（PO {inp.get('po_id')}，net30）",
        "created_at": _now_iso(),
    }
    await payment_schedule_repository.upsert(payment, "payment_id")
    await _audit("workflow.p2p.scheduled", f"payment:{payment['payment_id']}",
                 {"po_id": payment["po_id"], "amount": payment["amount"]}, risk="medium")
    return {"result": {"payment_id": payment["payment_id"],
                       "amount": payment["amount"],
                       "payment_date": payment["payment_date"]}}


@register_handler("p2p.pay")
async def _h_p2p_pay(state: dict, params: dict) -> dict:
    from sqlalchemy import update

    from app.db.repositories.base import has_db, session_scope
    from app.db.repositories.treasury_contract_repository import (
        payment_schedule_repository,
    )

    payment_id = (state.get("result") or {}).get("payment_id", "")
    await _settle_writes()
    if has_db():
        async with session_scope() as session:
            await session.execute(
                update(payment_schedule_repository.__model__)
                .where(payment_schedule_repository.__model__.payment_id == payment_id)
                .values(status="paid"))
    agent = _get_agent_by_name("资金Agent")
    if payment_id in agent._payment_schedule:
        agent._payment_schedule[payment_id]["status"] = "paid"
    await _audit("workflow.p2p.paid", f"payment:{payment_id}",
                 {"amount": (state.get("result") or {}).get("amount")}, risk="high")
    return {"result": {**(state.get("result") or {}), "status": "paid"}}


@register_handler("p2p.reject_mark")
async def _h_p2p_reject_mark(state: dict, params: dict) -> dict:
    return {"error": params.get("reason", "大额付款被人工驳回")}


@register_handler("contract.create")
async def _h_contract_create(state: dict, params: dict) -> dict:
    from app.db.repositories.treasury_contract_repository import contract_repository

    agent = _get_agent_by_name("合同Agent")
    inp = state.get("input") or {}
    contract = await agent.create_contract(
        title=inp["title"], type=inp["type"], counterparty=inp["counterparty"],
        amount=float(inp["amount"]), department=inp["department"],
        start_date=inp["start_date"], end_date=inp["end_date"],
        terms=inp.get("terms", ""))
    await _settle_writes()
    await contract_repository.upsert(contract, "contract_id")
    await _audit("workflow.contract.created", f"contract:{contract['contract_id']}",
                 {"amount": contract["amount"], "counterparty": contract["counterparty"]})
    return {"result": {"contract_id": contract["contract_id"],
                       "amount": contract["amount"], "title": contract["title"]}}


@register_handler("contract.review")
async def _h_contract_review(state: dict, params: dict) -> dict:
    inp = state.get("input") or {}
    notes = []
    amount = float(inp.get("amount", 0))
    if amount > 1_000_000:
        notes.append("金额超100万，建议法务复核")
    if str(inp.get("start_date")) >= str(inp.get("end_date")):
        return {"error": "起止日期非法"}
    if not inp.get("terms"):
        notes.append("缺少主要条款")
    return {"result": {**(state.get("result") or {}),
                       "review_notes": notes or ["无风险提示"]}}


@register_handler("contract.approve")
async def _h_contract_approve(state: dict, params: dict) -> dict:
    from app.db.repositories.treasury_contract_repository import contract_repository

    agent = _get_agent_by_name("合同Agent")
    contract_id = (state.get("result") or {}).get("contract_id", "")
    contract = await agent.approve_contract(
        contract_id=contract_id, approved_by="workflow",
        comment="；".join((state.get("result") or {}).get("review_notes", [])))
    if "error" in contract:
        return {"error": contract["error"]}
    await _settle_writes()
    await contract_repository.update_status(
        contract_id, "approved",
        extra={"approved_by": "workflow", "approved_at": contract.get("approved_at")})
    await _audit("workflow.contract.approved", f"contract:{contract_id}",
                 {"amount": (state.get("result") or {}).get("amount")}, risk="high")
    return {"result": {**(state.get("result") or {}), "status": "approved"}}


@register_handler("supdoc.draft")
async def _h_supdoc_draft(state: dict, params: dict) -> dict:
    """监理文档草稿：实时监理数据 + LLM 大纲（LLM 失败降级骨架，不臆造数值）。"""
    inp = state.get("input") or {}
    agent = _get_agent_by_name("项目监理agent")
    if agent is None:
        return {"error": "项目监理Agent 未启动"}
    # 实时监理快照（_supervision 记录含 project_id/name/stage/里程碑/预算）
    realtime = [dict(rec) for rec in getattr(agent, "_supervision", {}).values()]
    try:
        compliance = agent.check_compliance(inp.get("project_id"))
    except Exception:  # noqa: BLE001
        compliance = {"stages": {}, "high_risk": False, "risks": []}
    outline_prompt = (
        "请为以下项目生成监理文档大纲，返回 JSON："
        '{"title": "...", "summary": "...", '
        '"sections": [{"heading": "...", "content": "..."}], "conclusion": "..."}。'
        "按五大监理阶段（立项审批/实施过程/测试验证/上线发布/关闭审计）组织章节。"
    )
    outline = await agent.llm_analyze(
        outline_prompt,
        {"project_id": inp.get("project_id", ""), "doc_type": inp.get("doc_type", "监理大纲"),
         "compliance": compliance, "realtime_count": len(realtime)},
    )
    if "error" in outline:
        # LLM 失败降级：骨架齐备 + 待人工补充（不臆造）
        outline = {
            "title": f"{inp.get('project_id', '项目')}监理大纲",
            "summary": "LLM 暂不可用，以下为标准监理骨架，请人工补充。",
            "sections": [{"heading": h, "content": "待人工补充"}
                         for h in ("监理范围与依据", "监理阶段与检查点",
                                   "风险与应对措施")],
            "conclusion": "",
        }
    outline["doc_amount"] = 1  # gate：amount_from → 恒 ≥ 阈值 0 → 必经人审
    await _audit("workflow.supdoc.drafted", "project_supervisor",
                 {"project_id": inp.get("project_id", "")})
    return {"result": outline}


@register_handler("supdoc.finalize")
async def _h_supdoc_finalize(state: dict, params: dict) -> dict:
    """人审定稿 → docx 生成（复用 conversation 创作装配 + 文档生成器）。"""
    from app.api.routes.conversation import build_creative_doc_data
    from app.core.document import document_generator
    from app.core.observability.trace import get_trace_id

    result = dict(state.get("result") or {})
    agent = _get_agent_by_name("项目监理agent")
    agent_config = {"name": agent.name if agent else "项目监理agent"}
    doc_data = build_creative_doc_data(
        agent_config, "监理文档定稿", result,
        realtime=[],
        trace_id=get_trace_id(), generated_at=datetime.now(timezone.utc).isoformat(),
        user_id=str(state.get("input", {}).get("requested_by", "")),
    )
    filename = f"supervisor_doc_{uuid4().hex[:10]}.docx"
    path = document_generator.generate(
        format="docx", template_type="report", data=doc_data, filename=filename)
    download_url = f"/api/v1/document/download/{filename}"
    await _audit("workflow.supdoc.finalized", f"document:{filename}",
                 {"download_url": download_url, "trace_id": get_trace_id()}, risk="medium")
    return {"result": {**(state.get("result") or {}),
                       "download_url": download_url, "filename": filename,
                       "path": str(path)}}


@register_handler("supdoc.reject")
async def _h_supdoc_reject(state: dict, params: dict) -> dict:
    """人审驳回：草稿作废（审计留痕，状态机闭环）。"""
    await _audit("workflow.supdoc.rejected", "project_supervisor",
                 {"reason": str(params.get("comment", ""))[:200] if isinstance(params, dict) else ""})
    return {"result": {"status": "rejected"}}


@register_handler("contract.reject_mark")
async def _h_contract_reject_mark(state: dict, params: dict) -> dict:
    from app.db.repositories.treasury_contract_repository import contract_repository

    agent = _get_agent_by_name("合同Agent")
    contract_id = (state.get("result") or {}).get("contract_id", "")
    if contract_id and contract_id in agent._contracts:
        agent._contracts[contract_id]["status"] = "rejected"
    await _settle_writes()
    await contract_repository.update_status(contract_id, "rejected")
    return {"error": params.get("reason", "合同被人工驳回")}


# ===========================================================================
# 内置 DSL 模板（出厂定义；运行时以 workflow_definitions 表 active 版本优先）
# ===========================================================================

BUILTIN_DSL: dict[str, dict] = {
    "reimbursement_approval": {
        "name": "reimbursement_approval",
        "entry": "submit",
        "nodes": [
            {"id": "submit", "type": "task", "handler": "reimb.submit",
             "next": "precheck"},
            {"id": "precheck", "type": "task", "handler": "reimb.precheck",
             "next": "gate"},
            {"id": "gate", "type": "gate",
             "threshold": {"key": "reimbursement_auto_approve_limit", "default": 5000},
             "amount_from": "result.amount",
             "preview": "报销单 {reimbursement_no} 金额 ¥{amount}，员工 {employee_name}，预审已通过",
             "next_approved": "verify", "next_rejected": "reject"},
            {"id": "verify", "type": "task", "handler": "reimb.verify_approve",
             "next": "END"},
            {"id": "reject", "type": "task", "handler": "reimb.reject_mark",
             "next": "END"},
        ],
    },
    "procure_to_pay": {
        "name": "procure_to_pay",
        "entry": "validate",
        "nodes": [
            {"id": "validate", "type": "task", "handler": "p2p.validate",
             "next": "schedule"},
            {"id": "schedule", "type": "task", "handler": "p2p.schedule",
             "next": "gate"},
            {"id": "gate", "type": "gate",
             "threshold": {"key": "payment_human_gate_limit", "default": 100000},
             "amount_from": "result.amount",
             "preview": "大额付款确认：PO {po_id} 应付 ¥{amount}，排程日 {payment_date}；批准后标记为已付",
             "next_approved": "pay", "next_rejected": "reject"},
            {"id": "pay", "type": "task", "handler": "p2p.pay", "next": "END"},
            {"id": "reject", "type": "task", "handler": "p2p.reject_mark",
             "next": "END"},
        ],
    },
    "supervisor_doc_workflow": {
        "name": "supervisor_doc_workflow",
        "entry": "draft",
        "nodes": [
            {"id": "draft", "type": "task", "handler": "supdoc.draft",
             "next": "gate"},
            {"id": "gate", "type": "gate",
             "threshold": {"key": "supervisor_doc_auto_approve", "default": 0},
             "amount_from": "result.doc_amount",
             "preview": "监理文档草稿《{title}》待人审定稿（AI 草稿须人工确认）",
             "next_approved": "finalize", "next_rejected": "reject"},
            {"id": "finalize", "type": "task", "handler": "supdoc.finalize",
             "next": "END"},
            {"id": "reject", "type": "task", "handler": "supdoc.reject",
             "next": "END"},
        ],
    },
    "contract_approval": {
        "name": "contract_approval",
        "entry": "create",
        "nodes": [
            {"id": "create", "type": "task", "handler": "contract.create",
             "next": "review"},
            {"id": "review", "type": "task", "handler": "contract.review",
             "next": "gate"},
            {"id": "gate", "type": "gate",
             "amount_from": "result.amount",
             "preview": "合同[{title}] ¥{amount}，审查备注：{review_notes}。批准后合同生效",
             "approve_roles": ["fin_manager"],
             "next_approved": "approve", "next_rejected": "reject"},
            {"id": "approve", "type": "task", "handler": "contract.approve",
             "next": "END"},
            {"id": "reject", "type": "task", "handler": "contract.reject_mark",
             "next": "END"},
        ],
    },
}

# 工作流目录描述（对外）
WORKFLOW_CATALOG: dict[str, dict] = {
    "reimbursement_approval": {
        "description": "报销审批流：提交→规则预审→分级人审(≥5000)→核验→审批",
        "input_hint": "{employee_name, employee_id?, department?, category, amount, description, has_receipt?}",
    },
    "procure_to_pay": {
        "description": "采购到支付：PO校验→支付排程(net30)→大额人审(≥10万)→付款",
        "input_hint": "{po_id, total_amount, supplier_id?}",
    },
    "supervisor_doc_workflow": {
        "description": "监理文档工作流：实时数据起草→AI 草稿人审定稿→docx 生成（D-H5）",
        "input_hint": "{project_id, doc_type?(监理大纲/实施方案), requested_by?}",
    },
    "contract_approval": {
        "description": "合同审批流：创建→规则审查→人审→批准生效",
        "input_hint": "{title, type, counterparty, amount, department, start_date, end_date, terms?}",
    },
}

graphs_cache: dict[str, Any] = {}


def _threshold(key: str, default: float) -> float:
    """读取定制层阈值覆盖（管理端可调；无覆盖用内置默认）。"""
    try:
        from app.core.agent.customization import customization_service

        override = customization_service.get_threshold(key)
        return override if override is not None else default
    except Exception:  # noqa: BLE001
        return default


def _get_checkpointer():
    from app.core.agent.checkpoint_mysql import get_mysql_checkpointer

    return get_mysql_checkpointer()


async def _load_definition_async(name: str, document_key: str = "",
                                 thread_id: str = "") -> tuple[dict, str]:
    """
    解析本次运行的定义与版本（F3.5 粘性）：
    线程已钉住版本 > canary 哈希命中 > stable > 内置模板（出厂默认）。
    """
    from app.core.agent import workflow_versions as wfv

    if thread_id:
        pinned = await wfv.pinned_version(thread_id)
        if pinned:
            defn = await wfv.get_definition_by_version(name, pinned)
            if defn:
                return defn, pinned
    defn, version = await wfv.resolve_sticky(name, document_key or thread_id)
    if defn:
        return defn, version
    return BUILTIN_DSL.get(name) or {}, "builtin"


def _graph_for(name: str, defn: dict):
    """按 (name, 定义哈希) 缓存编译图（定义变更→新图；thread 状态兼容）。"""
    import hashlib
    import json as _json

    from app.core.agent.customization import THRESHOLD_DEFAULTS
    from app.core.agent.workflow_dsl import compile_dsl, validate_dsl

    def_hash = hashlib.md5(_json.dumps(defn, sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()[:8]
    cache_key = f"{name}:{def_hash}"
    if cache_key not in graphs_cache:
        errors = validate_dsl(defn, set(THRESHOLD_DEFAULTS.keys()))
        if errors:
            raise ValueError(f"工作流定义非法: {'；'.join(errors)}")
        graphs_cache[cache_key] = compile_dsl(
            defn, name, _get_checkpointer(), _threshold)
        logger.info("workflow.compiled", workflow=name, def_hash=def_hash)
    return graphs_cache[cache_key]


async def start_workflow(name: str, input_data: dict, ctx,
                         thread_id: str = "",
                         document_key: str = "") -> dict:
    """
    启动工作流（定义驱动 + 粘性版本）。

    ``document_key``：业务单据标识（如 PO 号/报销单号）。缺省以线程 ID 作键——
    同一单据/同一线程在灰度切换后仍沿用首次命中的版本（F3.5）。
    """
    from app.core.agent import workflow_versions as wfv
    from app.core.observability.trace import ensure_trace_id, set_business_key

    thread_id = thread_id or f"wf:{name}:{uuid4().hex[:10]}"
    defn, version = await _load_definition_async(name, document_key, thread_id)
    if not defn:
        raise KeyError(f"未知工作流: {name}")
    await wfv.pin_thread(thread_id, name, version, document_key)
    graph = _graph_for(name, defn)

    token = _ctx_var.set(ctx)
    try:
        # 追踪绑定（G1.3）：复用上游链路键（HTTP 中间件已绑），无则新建；
        # 业务根键取单据号，缺省退化为线程 ID（与粘性灰度同款语义——
        # 同一线程/单据在审计与日志中恒可反查）
        ensure_trace_id()
        set_business_key(document_key or thread_id)
        result = await graph.ainvoke(
            {"input": input_data, "steps": [], "result": {}, "error": None,
             "thread_id": thread_id},
            {"configurable": {"thread_id": thread_id}},
        )
    except Exception as run_err:
        # G5.1：工作流异常终止 → Saga 逆序补偿后原样上抛
        try:
            from app.core.agent.workflow_saga import saga_compensate

            await saga_compensate(thread_id, reason=f"工作流异常终止: {run_err}")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        _ctx_var.reset(token)

    if result.get("error"):
        # G5.1：失败终止 → 自动逆序补偿（write_low）/人工介入标记（其余）
        try:
            from app.core.agent.workflow_saga import saga_compensate

            await saga_compensate(thread_id, reason=str(result.get("error")))
        except Exception:  # noqa: BLE001
            pass
    else:
        # G5.2 修正（UAT-G5-1 现场验收发现）：正常完成必须收口 saga——
        # 否则启动恢复会把已成功的业务操作误补偿
        try:
            from app.core.agent.workflow_saga import saga_complete

            await saga_complete(thread_id)
        except Exception:  # noqa: BLE001
            pass

    interrupts = result.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        return {"status": "awaiting_approval", "thread_id": thread_id,
                "workflow_version": version,
                "reply": f"工作流 {name} 需人工审批：{payload.get('preview', '')}",
                "interrupt": payload}
    if result.get("error"):
        return {"status": "error", "thread_id": thread_id,
                "workflow_version": version,
                "reply": f"工作流 {name} 终止：{result['error']}", "interrupt": None}
    return {"status": "done", "thread_id": thread_id,
            "workflow_version": version,
            "reply": f"工作流 {name} 完成：{result.get('result', {})}",
            "result": result.get("result", {}), "interrupt": None}


async def resume_workflow(name: str, thread_id: str, decision: dict,
                          ctx) -> dict:
    """人审决定后恢复工作流（沿用该线程钉住的版本，从 Checkpointer 断点续跑）。"""
    from langgraph.types import Command
    from app.core.observability.trace import ensure_trace_id, set_business_key

    defn, version = await _load_definition_async(name, "", thread_id)
    graph = _graph_for(name, defn)
    token = _ctx_var.set(ctx)
    try:
        # 恢复链路同样绑定追踪键：恢复属新链路实例（新 trace_id），
        # 业务根键沿用线程 ID——与启动链路同组可互查
        ensure_trace_id()
        set_business_key(thread_id)
        result = await graph.ainvoke(
            Command(resume=decision),
            {"configurable": {"thread_id": thread_id}},
        )
    except Exception as run_err:
        try:
            from app.core.agent.workflow_saga import saga_compensate

            await saga_compensate(thread_id, reason=f"恢复执行异常终止: {run_err}")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        _ctx_var.reset(token)

    if result.get("error"):
        try:
            from app.core.agent.workflow_saga import saga_compensate

            await saga_compensate(thread_id, reason=str(result.get("error")))
        except Exception:  # noqa: BLE001
            pass

    interrupts = result.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        return {"status": "awaiting_approval", "reply": "仍有待审批节点。",
                "workflow_version": version, "interrupt": payload}
    if result.get("error"):
        return {"status": "error", "reply": f"工作流终止：{result['error']}",
                "workflow_version": version}
    try:
        from app.core.agent.workflow_saga import saga_complete

        await saga_complete(thread_id)
    except Exception:  # noqa: BLE001
        pass
    return {"status": "done", "reply": f"工作流 {name} 完成：{result.get('result', {})}",
            "result": result.get("result", {}), "workflow_version": version}


async def _graph_pending(name: str, thread_id: str) -> bool:
    """工作流在指定线程上是否有挂起的人审中断（supervisor 重放探测）。"""
    try:
        defn, _ = await _load_definition_async(name, "", thread_id)
        if not defn:
            return False
        state = await _graph_for(name, defn).aget_state(
            {"configurable": {"thread_id": thread_id}})
        return any(getattr(t, "interrupts", None) for t in state.tasks)
    except Exception:  # noqa: BLE001
        return False


def list_workflows() -> list[dict]:
    return [{"name": name, "description": meta["description"],
             "input_hint": meta["input_hint"]}
            for name, meta in WORKFLOW_CATALOG.items()]
