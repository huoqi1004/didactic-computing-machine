"""
Supervisor 多 Agent 协同（路线图 Phase C / C1-C4）
====================================================

跨域指令的监督者图：

    START → classify（意图分类：LLM 优先，规则兜底）
          → delegate（按域清单逐个委派子域场景图——**全部经治理面**）
          → synthesize（综合各子域结果答复）
          → END

设计要点：
- **无后门原则**（C 阶段红线）：supervisor 不直连任何子 Agent 内部方法，
  委派一律走 AgentRuntime 场景图（其工具调用经 AgentGovernor 治理面），
  子域 write_high 人审中断向 supervisor **透传**（sub thread 挂起，
  supervisor 同步 interrupt，审批决定后先恢复子图、再恢复综合）；
- **意图分类**：LLM 输出 JSON 域清单（多选），解析失败/超预算降级规则
  关键词路由（确定性，可测）；
- **失败传播可控**：子域异常不中断其他子域，结果里标注失败原因，
  综合节点如实向用户报告（不无限重试）；
- **跨重启**：supervisor 图与子图均挂 MySQL Checkpointer，审批等待中
  重启可续（thread 命名：父 sup:...，子 sup:{parent}:{domain}）。
"""
from __future__ import annotations

from typing import Annotated, Any, Optional

import structlog
from uuid import uuid4
from typing_extensions import TypedDict

from contextvars import ContextVar

from app.core.agent.tools_v2 import ToolContext, tool_registry_v2

# supervisor 图与子域委派的调用者上下文（治理面/审计归属）
_ctx_var: ContextVar[Optional[ToolContext]] = ContextVar("sup_tool_ctx", default=None)

logger = structlog.get_logger()

# 域路由关键词表（规则兜底；顺序无关，命中即归入该域）
ROUTE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "reimbursement": ("报销", "发票报销", "餐补", "差旅费"),
    "treasury": ("资金", "调拨", "余额", "支付", "付款", "现金"),
    "payroll": ("薪酬", "工资", "薪资", "发薪", "个税"),
    "hr": ("员工", "考勤", "入职", "人事", "组织"),
    "crm": ("客户", "商机", "联系人"),
    "sales": ("订单", "销售", "下单", "销售额"),
    "contract": ("合同", "签约", "条款"),
}

# 工作流委派项（名称以 workflow: 开头时由 delegate 节点启动工作流）
WORKFLOW_ROUTES: dict[str, tuple[str, ...]] = {
    "workflow:procure_to_pay": ("采购到支付", "采购付款", "po付款", "安排付款"),
}

SUPERVISOR_DOMAIN = "supervisor"


def classify_by_rules(message: str) -> list[str]:
    """规则关键词分类（确定性兜底）。"""
    routes: list[str] = []
    lowered = message.lower()
    for domain, keywords in ROUTE_KEYWORDS.items():
        if any(k in message or k in lowered for k in keywords):
            routes.append(domain)
    for wf_name, keywords in WORKFLOW_ROUTES.items():
        if any(k in message or k in lowered for k in keywords):
            routes.append(wf_name)
    return routes


async def classify_with_llm(message: str) -> Optional[list[str]]:
    """LLM 意图分类（返回域清单；失败返回 None 走规则兜底）。"""
    try:
        from langchain_openai import ChatOpenAI

        from app.core.config import settings

        if not settings.llm_enabled:
            return None
        available = sorted({s.agent_domain for s in tool_registry_v2.list_specs()})
        llm = ChatOpenAI(
            model=settings.llm_model, base_url=settings.llm_base_url,
            api_key=settings.llm_api_key, temperature=0,
            timeout=15, max_retries=0,
        )
        resp = await llm.ainvoke(
            f"可用业务域：{available}\n"
            f"另有工作流：{sorted(WORKFLOW_ROUTES.keys())}\n"
            f"用户指令：{message}\n\n"
            '判断该指令涉及哪些域（可多选）。只返回 JSON：{{"domains": ["...", "..."]}}。'
            "不确定的域不要包含。"
        )
        import json as _json

        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        data = _json.loads(text[text.find("{"): text.rfind("}") + 1])
        domains = [d for d in data.get("domains", [])
                   if d in available or d in WORKFLOW_ROUTES]
        return domains or None
    except Exception as e:  # noqa: BLE001
        logger.warning("supervisor.llm_classify_failed", error=str(e))
        return None


class SupervisorState(TypedDict, total=False):
    """监督者图状态。"""

    input: dict                    # {message, history?}
    route_plan: list               # 委派域清单
    sub_results: list              # 各子域结果 [{domain, status, reply, thread_id}]
    reply: str                     # 综合答复
    error: Optional[str]


def build_supervisor_graph():
    """编译监督者图（Checkpointer 由调用方注入 compile）。"""
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    async def classify(state: SupervisorState) -> dict:
        message = state["input"].get("message", "")
        domains = await classify_with_llm(message)
        if domains is None:
            domains = classify_by_rules(message)
        if not domains:
            return {"route_plan": [], "reply": (
                "未识别出该指令涉及的业务域。目前可协同的域："
                + "、".join(sorted(ROUTE_KEYWORDS.keys()))
                + "。请换一种问法或指定业务域。"
            )}
        logger.info("supervisor.routed", domains=domains)
        return {"route_plan": domains}

    async def delegate(state: SupervisorState) -> dict:
        """逐域委派子域场景图（治理面执行），write_high 中断透传。

        重放安全：supervisor interrupt 恢复后本节点整体重放，
        子图已有挂起中断时跳过重复 run（探测子 thread pending
        interrupt），interrupt() 幂等取回决定后直接 resume 子图。
        """
        from langgraph.errors import GraphInterrupt
        from langgraph.types import interrupt as _interrupt

        from app.core.agent.runtime import agent_runtime
        from app.core.agent.workflows import _graph_pending, start_workflow

        message = state["input"].get("message", "")
        parent_thread = state["input"].get("thread_id", "")
        ctx: ToolContext = state["input"]["ctx"]
        sub_results: list[dict] = []

        for route in state["route_plan"]:
            sub_thread = f"sup:{parent_thread}:{route}"
            sub_ctx = ToolContext(
                user_id=ctx.user_id, username=ctx.username,
                agent_key=route, thread_id=sub_thread, channel_id=ctx.channel_id,
                extra=dict(ctx.extra),
            )
            try:
                payload = {
                    "tool": f"supervisor:{route}",
                    "risk": "write_high",
                    "sub_thread_id": sub_thread,
                    "sub_domain": route,
                }

                # ---- 重放探测：子图已挂起 → 直接取决定并 resume ----
                if route.startswith("workflow:"):
                    replay_pending = await _graph_pending(
                        route.split(":", 1)[1], sub_thread)
                else:
                    replay_pending = await agent_runtime.sub_pending(route, sub_thread)

                if replay_pending:
                    decision = _interrupt(payload)
                    if not isinstance(decision, dict) or not decision.get("approved"):
                        sub_results.append({
                            "domain": route, "status": "rejected",
                            "reply": f"[已驳回] 子任务（{route}）人工审批未通过，未执行。",
                            "thread_id": sub_thread,
                        })
                        continue
                    sub = await resume_sub(route, sub_thread, decision, sub_ctx)
                    sub_results.append({
                        "domain": route, "status": sub["status"],
                        "reply": sub.get("reply", ""), "thread_id": sub_thread,
                    })
                    continue

                # ---- 首次执行 ----
                if route.startswith("workflow:"):
                    wf_name = route.split(":", 1)[1]
                    sub = await start_workflow(wf_name, {"message": message}, sub_ctx)
                else:
                    sub = await agent_runtime.run(route, message, sub_ctx)

                sub_results.append({
                    "domain": route, "status": sub["status"],
                    "reply": sub.get("reply", ""), "thread_id": sub_thread,
                })

                if sub["status"] == "awaiting_approval":
                    payload["preview"] = sub.get("interrupt", {}).get("preview", "")
                    payload["args"] = sub.get("interrupt", {}).get("args")
                    decision = _interrupt(payload)
                    if not isinstance(decision, dict) or not decision.get("approved"):
                        sub_results[-1].update(
                            status="rejected",
                            reply=f"[已驳回] 子任务（{route}）人工审批未通过，未执行。",
                        )
                        continue
                    sub = await resume_sub(route, sub_thread, decision, sub_ctx)
                    sub_results[-1].update(status=sub["status"], reply=sub.get("reply", ""))

            except GraphInterrupt:
                raise  # 子域人审透传必须穿透（暂停 supervisor 图）
            except Exception as e:  # noqa: BLE001
                # 失败传播可控：记录失败，继续其他子域
                logger.warning("supervisor.delegate_failed", route=route, error=str(e))
                sub_results.append({
                    "domain": route, "status": "failed",
                    "reply": f"[执行失败] {type(e).__name__}: {e}",
                    "thread_id": sub_thread,
                })

        return {"sub_results": sub_results}

    async def synthesize(state: SupervisorState) -> dict:
        """综合各子域结果（LLM 可用时智能汇总，否则结构化拼接）。"""
        subs = state.get("sub_results", [])
        if not subs:
            return {"reply": state.get("reply", "没有可执行的子任务。")}

        failed = [s for s in subs if s["status"] in ("failed", "rejected")]
        done = [s for s in subs if s["status"] == "done"]

        sections = []
        for s in subs:
            mark = {"done": "✅", "awaiting_approval": "⏳",
                    "failed": "❌", "rejected": "🚫"}.get(s["status"], "·")
            sections.append(f"{mark} {s['domain']}：{s['reply']}")
        reply = "\n\n".join(sections)
        if failed:
            reply += f"\n\n（注意：{len(failed)} 个子任务未成功完成，详见上方标注。）"
        return {"reply": reply}

    builder = StateGraph(SupervisorState)
    builder.add_node("classify", classify)
    builder.add_node("delegate", delegate)
    builder.add_node("synthesize", synthesize)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify",
        lambda s: "delegate" if s.get("route_plan") else END,
        ["delegate", END],
    )
    builder.add_edge("delegate", "synthesize")
    builder.add_edge("synthesize", END)
    return builder


# ===========================================================================
# 启动/恢复入口 + 总调度 Agent（Phase C 接线）
# ===========================================================================

_sup_graph_cache: Optional[Any] = None


def _compiled_supervisor():
    global _sup_graph_cache
    if _sup_graph_cache is None:
        from app.core.agent.checkpoint_mysql import get_mysql_checkpointer

        _sup_graph_cache = build_supervisor_graph().compile(
            checkpointer=get_mysql_checkpointer()
        )
        logger.info("supervisor.graph_compiled")
    return _sup_graph_cache


async def resume_sub(route: str, sub_thread: str,
                     decision: dict, sub_ctx: ToolContext) -> dict:
    """恢复子任务（域场景图或工作流）——supervisor delegate 专用。"""
    if route.startswith("workflow:"):
        from app.core.agent.workflows import resume_workflow

        return await resume_workflow(route.split(":", 1)[1], sub_thread,
                                     decision, sub_ctx)
    from app.core.agent.runtime import agent_runtime

    return await agent_runtime.resume(route, sub_thread, decision, sub_ctx)


async def start_supervisor(message: str, ctx: ToolContext,
                           thread_id: str = "") -> dict:
    """启动监督者图。返回 {"status", "reply", "interrupt"?}。

    thread 缺省优先级：显式参数 > ctx.thread_id > 自动生成。
    （start/resume 必须落在同一 thread——此前参数与 ctx 混用导致
    resume 找不到挂起状态、图从头重跑。）
    """
    graph = _compiled_supervisor()
    thread_id = thread_id or ctx.thread_id or f"sup:{ctx.user_id}:{uuid4().hex[:8]}"
    token = _ctx_var.set(ctx)
    try:
        result = await graph.ainvoke(
            {"input": {"message": message, "thread_id": thread_id, "ctx": ctx},
             "route_plan": [], "sub_results": [], "reply": "", "error": None},
            {"configurable": {"thread_id": thread_id}},
        )
    finally:
        _ctx_var.reset(token)

    interrupts = result.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        preview_text = str(payload.get("preview", ""))
        return {
            "status": "awaiting_approval",
            "reply": ("子任务涉及高危操作，已提交人工审批：" + chr(10)
                      + preview_text + chr(10)
                      + "批准后 supervisor 将恢复该子任务并汇总结果。"),
            "interrupt": payload,
        }

    return {"status": "done", "reply": result.get("reply", "（无回复）")}


async def resume_supervisor(thread_id: str, decision: dict, ctx: ToolContext) -> dict:
    """审批决定后恢复监督者图（delegate 节点先恢复子图、再综合）。"""
    from langgraph.types import Command

    graph = _compiled_supervisor()
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
        return {"status": "awaiting_approval", "reply": "仍有待审批子任务。",
                "interrupt": payload}
    return {"status": "done", "reply": result.get("reply", "（无回复）")}


class SupervisorAgent:
    """
    总调度 Agent（多域协同入口，Phase C）。

    不承载业务逻辑——意图分类与委派全部在 Supervisor 图内完成，
    委派一律经子域场景图（治理面）。注册进 orchestrator 与 messaging
    目录后，用户可在"总调度"频道直接下跨域指令。
    """

    name = "总调度Agent"
    domain = SUPERVISOR_DOMAIN
    capabilities = ["multi_domain_routing", "cross_domain_summary", "general"]

    @property
    def description(self) -> str:
        return "跨域指令路由与汇总：意图分类后委派各业务域助手协同完成"

    async def llm_chat(self, messages: list[dict], **kwargs) -> str:
        """与 BaseAgent.llm_chat 同形（messaging agent-command 入口）。"""
        from app.core.config import settings as _s
        from app.db.repositories.base import has_db as _has_db

        if not _s.llm_enabled or not _has_db():
            return ""
        from app.core.agent.orchestration_service import run_scenario_for_agent

        try:
            return await run_scenario_for_agent(self, messages, kwargs) or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("supervisor.llm_chat_failed", error=str(e))
            return ""


# 模块级单例（不注册 orchestrator：非 BaseAgent，经 messaging getter 暴露）
_supervisor_agent: Optional[SupervisorAgent] = None


def get_supervisor_agent() -> SupervisorAgent:
    global _supervisor_agent
    if _supervisor_agent is None:
        _supervisor_agent = SupervisorAgent()
    return _supervisor_agent
