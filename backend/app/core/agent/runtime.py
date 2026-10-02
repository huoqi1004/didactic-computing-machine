"""
AgentRuntime：LangGraph 单 Agent 场景图模板（路线图 Phase A / A5）
====================================================================

把"理解意图 → 调用工具 → 综合答复"跑成 LangGraph 状态图：

    START → agent(LLM+bind_tools) →(有 tool_calls?)→ tools → agent → … → END
                                    └(无)────────────────────────→ END

- **工具执行一律经治理面**（AgentGovernor）：本模块把 ToolSpecV2 包装为
  LangChain StructuredTool，包装器内调用 governor.execute；
- **write_high 人审门**：包装器收到 approval 载荷时调用 LangGraph
  ``interrupt()``——图在 MySQL Checkpointer 落盘暂停，审批路由（A8）
  收到决定后以 ``Command(resume=...)`` 恢复，工具以 approved=True 重放；
- **线程上下文**：调用者身份经 contextvar 注入工具（不污染工具签名）；
- **降级**：LLM 未启用/不可用时由调用方（BaseAgent.llm_chat）回退旧路径。

图按 agent_domain 缓存编译（同一域的多个 Agent 共享图结构，
线程状态经 thread_id 隔离）。
"""
from __future__ import annotations

import contextvars
from datetime import timezone
from typing import Annotated, Any, Optional

import structlog
from typing_extensions import TypedDict

from app.core.agent.tools_v2 import ToolContext, tool_registry_v2

logger = structlog.get_logger()

# 调用者上下文（graph.ainvoke 前设置，工具包装器内读取）
_current_tool_ctx: contextvars.ContextVar[Optional[ToolContext]] = contextvars.ContextVar(
    "erp_tool_ctx", default=None
)


def get_current_tool_ctx() -> ToolContext:
    ctx = _current_tool_ctx.get()
    if ctx is None:
        raise RuntimeError("工具上下文缺失（必须在 AgentRuntime.run 场景内调用）")
    return ctx


try:
    from langgraph.graph.message import add_messages as _add_messages
except ImportError:  # 兼容旧版本
    from langgraph.graph import add_messages as _add_messages


class ScenarioState(TypedDict):
    """单 Agent 场景图状态：消息列表是唯一通道（append-only reducer）。

    注意：reducer 必须用 ``add_messages`` 函数——旧版字符串魔法
    ``"__add_messages__"`` 在 langgraph 1.x 不生效，会退化为 LastValue
    （每个节点覆盖整个列表，历史丢失，LLM 收到孤立 ToolMessage 报 400）。
    """

    messages: Annotated[list, _add_messages]


def _sanitize_history(messages: list) -> list:
    """
    发送 LLM 前清洗历史：剔除开头的孤立 ToolMessage（崩溃/中断恢复残留）。

    DeepSeek 等严格兼容端点要求 ToolMessage 必须紧跟 assistant(tool_calls)；
    历史若因异常运行留下残缺序列，整轮对话会 400。防御式修复。
    """
    cleaned = list(messages)
    i = 0
    while i < len(cleaned) and type(cleaned[i]).__name__ == "ToolMessage":
        i += 1
    if i > 0:
        cleaned = cleaned[i:]
        logger.warning("runtime.sanitized_orphan_tool_messages", dropped=i)
    return cleaned


class AgentRuntime:
    """按域编译并缓存 ReAct 场景图。"""

    def __init__(self) -> None:
        self._graphs: dict[str, Any] = {}
        self._checkpointer: Any = None

    # ------------------------------------------------------------------
    def _get_checkpointer(self):
        if self._checkpointer is None:
            from app.core.agent.checkpoint_mysql import get_mysql_checkpointer

            self._checkpointer = get_mysql_checkpointer()
        return self._checkpointer

    def _get_llm(self):
        from app.core.config import settings

        if not settings.llm_enabled:
            raise RuntimeError("LLM 未启用（ERP_LLM_ENABLED=false）")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            temperature=0.3,
            timeout=settings.llm_timeout,
            max_retries=1,  # 编排路径不做长重试（成本护栏），失败走降级
        )

    def _wrap_tool(self, spec):
        """ToolSpecV2 → LangChain StructuredTool（仅供 bind_tools 的 schema 绑定）。

        真正的执行在 :meth:`_invoke_tool_by_call`（治理面 + 人审 interrupt），
        不经过本包装器——避免两处重复人审逻辑。
        """
        from langchain_core.tools import StructuredTool

        async def _not_executed(**kwargs) -> str:
            raise RuntimeError("工具必须经 AgentRuntime 场景图执行")

        return StructuredTool(
            name=spec.name,
            description=spec.description,
            args_schema=spec.args_model,
            coroutine=_not_executed,
        )

    def _build_graph(self, domain: str, skill: Optional[dict] = None):
        from langgraph.graph import END, START, StateGraph
        from langchain_core.messages import ToolMessage

        skill = skill or {}
        specs = tool_registry_v2.list_specs(domain=domain)
        # per-Agent 工具白名单（agent 级定制）：仅 LLM 可见子集变化；
        # 治理面仍逐调用校验（白名单是收窄，不放宽权限）
        cfg = self._agent_cfg(domain)
        whitelist = cfg.get("tool_whitelist") or None
        if whitelist:
            allowed = set(whitelist)
            specs = [s for s in specs if s.name in allowed]
        # Skill 工具白名单（F4.4）：同样是收窄——技能只能引用域内已注册工具
        skill_tools = set(skill.get("tools") or [])
        if skill_tools:
            specs = [s for s in specs if s.name in skill_tools]
        if not specs:
            raise RuntimeError(f"域 {domain} 无已注册工具（或白名单过滤后为空）")
        llm = self._get_llm().bind_tools([self._wrap_tool(s) for s in specs])
        # per-Agent 温度覆盖（构造后设置；_get_llm 无域上下文）
        if "temperature" in cfg:
            llm.temperature = float(cfg["temperature"])

        async def node_agent(state: ScenarioState) -> dict:
            history = _sanitize_history(state["messages"])
            # Agent 级人设定制 + 技能指令：合成一条 System 消息前置
            persona = self._agent_cfg(domain).get("prompt_override")
            system = "\n\n".join(x for x in (persona, skill.get("instructions")) if x)
            if system:
                from langchain_core.messages import SystemMessage

                has_system = any(getattr(m, "type", "") == "system" for m in history)
                if not has_system:
                    history = [SystemMessage(content=str(system))] + history
            response = await llm.ainvoke(history)
            return {"messages": [response]}

        async def node_tools(state: ScenarioState) -> dict:
            last = state["messages"][-1]
            results: list[ToolMessage] = []
            from langgraph.errors import GraphInterrupt

            for tc in (last.tool_calls or []):
                try:
                    observation = await self._invoke_tool_by_call(tc)
                except GraphInterrupt:
                    raise  # 人审中断必须穿透（暂停图、落盘 Checkpointer）
                except Exception as e:  # noqa: BLE001
                    observation = f"[工具异常] {type(e).__name__}: {e}"
                results.append(
                    ToolMessage(
                        content=str(observation),
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
            return {"messages": results}

        builder = StateGraph(ScenarioState)
        builder.add_node("agent", node_agent)
        builder.add_node("tools", node_tools)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges(
            "agent",
            lambda state: "tools" if (state["messages"][-1].tool_calls) else END,
            ["tools", END],
        )
        builder.add_edge("tools", "agent")
        return builder.compile(checkpointer=self._get_checkpointer())

    async def _invoke_tool_by_call(self, tool_call: dict) -> str:
        """按 LLM 的 tool_call 执行单个工具（经治理面包装器，保留人审语义）。"""
        from app.core.agent.governor import agent_governor
        from langgraph.types import interrupt

        name = tool_call["name"]
        spec = tool_registry_v2.get(name)
        if spec is None:
            return f"[执行失败] 未知工具: {name}"
        ctx = get_current_tool_ctx()
        result = await agent_governor.execute(name, tool_call.get("args") or {}, ctx)
        if result.approval is not None:
            decision = interrupt(result.approval)
            if not isinstance(decision, dict) or not decision.get("approved"):
                logger.info(
                    "runtime.approval_rejected",
                    tool=name, thread=ctx.thread_id,
                    comment=(decision or {}).get("comment", ""),
                )
                return f"[已驳回] 人工审批未通过该操作（{name}），未执行。"
            result = await agent_governor.execute(
                name, tool_call.get("args") or {}, ctx, approved=True,
                approval_comment=str(decision.get("comment", "")),
            )
        return result.for_llm()

    def get_graph(self, domain: str, skill: str = ""):
        """获取（或编译并缓存）指定域的场景图（缓存键含配置哈希）。

        agent 级定制（人设/温度/工具白名单）变更 → 配置哈希变化 →
        下次调用重新编译新图（旧线程 checkpoint 的 channel 结构不变，兼容）。
        ``skill``（F4.4，来自 ``ctx.extra["skill"]``）同样进缓存键：
        技能发布/停用后哈希变化，下一轮自动用新图，无需重启。
        """
        cfg = dict(self._agent_cfg(domain))
        skill_cfg = self._skill_cfg(skill)
        if skill_cfg:
            cfg["__skill__"] = skill_cfg
        cache_key = f"{domain}:{self._cfg_hash(cfg)}:{skill or '-'}"
        if cache_key not in self._graphs:
            self._graphs[cache_key] = self._build_graph(domain, skill_cfg)
            logger.info("runtime.graph_compiled", domain=domain, skill=skill,
                        cfg=self._cfg_hash(cfg),
                        tools=len(tool_registry_v2.list_specs(domain=domain)))
        return self._graphs[cache_key]

    def _skill_for(self, ctx: ToolContext, domain: str) -> str:
        """``ctx.extra["skill"]`` → 本轮生效的技能名（不可用则为空 = 按纯域执行）。

        停用/未登记/归属域不一致的技能一律不套用：技能白名单只会收窄工具集，
        套错域会把工具过滤成空集而直接报错，那不是降级而是故障。
        """
        name = str((getattr(ctx, "extra", None) or {}).get("skill") or "")
        if not name:
            return ""
        cfg = self._skill_cfg(name)
        if not cfg or (cfg.get("domain") and cfg["domain"] != domain):
            logger.info("runtime.skill_not_applied", skill=name, domain=domain)
            return ""
        return name

    # ------------------------------------------------------------------
    async def run(
        self,
        domain: str,
        user_message: str,
        ctx: ToolContext,
        history: Optional[list] = None,
    ) -> dict:
        """
        执行一轮场景指令。

        Returns:
            {"status": "done"|"awaiting_approval",
             "reply": str, "interrupt": dict|None}
        """
        from langchain_core.messages import AIMessage, HumanMessage

        if not self._check_budget(domain):
            logger.warning("runtime.token_budget_exceeded", domain=domain)
            raise RuntimeError(f"域 {domain} 当日 LLM token 预算已耗尽，编排降级")

        graph = self.get_graph(domain, self._skill_for(ctx, domain))
        token = _current_tool_ctx.set(ctx)
        try:
            messages = list(history or []) + [HumanMessage(content=user_message)]
            result = await graph.ainvoke(
                {"messages": messages},
                {"configurable": {"thread_id": ctx.thread_id}},
            )
        finally:
            _current_tool_ctx.reset(token)

        tokens = self._sum_tokens(result.get("messages", []))
        self._record_tokens(domain, tokens)
        await self._audit_tokens(domain, ctx, tokens)
        await self._log_llm_op(domain, ctx, user_message, result.get("messages", []), tokens)

        interrupts = result.get("__interrupt__") or []
        if interrupts:
            payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
            return {
                "status": "awaiting_approval",
                "reply": (
                    "该操作风险较高，已提交人工审批。\n"
                    f"操作：{payload.get('tool')}\n效果预览：{payload.get('preview')}\n"
                    "请在审批中心或本频道审批卡片中处理，批准后自动继续执行。"
                ),
                "interrupt": payload,
            }

        reply = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                reply = msg.content if isinstance(msg.content, str) else str(msg.content)
                break
        return {"status": "done", "reply": reply or "（无回复）", "interrupt": None}

    async def resume(self, domain: str, thread_id: str, decision: dict,
                     ctx: ToolContext) -> dict:
        """人审决定后恢复中断的图执行（决策 D1/D6 的落地闭环）。"""
        from langchain_core.messages import AIMessage
        from langgraph.types import Command

        graph = self.get_graph(domain, self._skill_for(ctx, domain))
        token = _current_tool_ctx.set(ctx)
        try:
            result = await graph.ainvoke(
                Command(resume=decision),
                {"configurable": {"thread_id": thread_id}},
            )
        finally:
            _current_tool_ctx.reset(token)

        tokens = self._sum_tokens(result.get("messages", []))
        self._record_tokens(domain, tokens)
        await self._audit_tokens(domain, ctx, tokens)
        await self._log_llm_op(domain, ctx, "", result.get("messages", []), tokens,
                               resumed=True)

        interrupts = result.get("__interrupt__") or []
        if interrupts:
            payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
            return {"status": "awaiting_approval", "reply": "仍有待审批操作。",
                    "interrupt": payload}
        reply = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                reply = msg.content if isinstance(msg.content, str) else str(msg.content)
                break
        return {"status": "done", "reply": reply or "（无回复）", "interrupt": None}

    @staticmethod
    async def _audit_tokens(domain: str, ctx, tokens: int) -> None:
        """token 消耗入审计哈希链（路线图 B 阶段：单指令成本可追溯）。"""
        if tokens <= 0:
            return
        try:
            from app.core.security.audit import audit_chain

            await audit_chain.log(
                actor_did=ctx.user_id or "unknown",
                action="llm.tokens",
                resource=f"agent:{domain}",
                result="success",
                details={"tokens": tokens, "thread_id": ctx.thread_id},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime.token_audit_failed", error=str(e))

    @staticmethod
    async def _log_llm_op(domain: str, ctx, user_message: str, messages: list,
                          tokens: int, resumed: bool = False) -> None:
        """LLM 交互日志（G2.4，D6 裁决）：默认摘要，全文按域开关（≤7 天过期）。"""
        try:
            from app.core.observability.operation_log import log_llm_interaction

            reply = ""
            for m in reversed(messages):
                content = getattr(m, "content", None)
                if content and type(m).__name__ == "AIMessage":
                    reply = str(content)
                    break
            await log_llm_interaction(
                domain=domain, ctx=ctx,
                messages_in=len(messages), tokens=tokens, latency_ms=0.0,
                reply_text=reply, user_message=user_message,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime.llm_op_log_failed", error=str(e))

    async def sub_pending(self, domain: str, thread_id: str) -> bool:
        """该域场景图在指定线程上是否有挂起的人审中断（supervisor 重放探测）。"""
        try:
            graph = self.get_graph(domain)
            state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            return any(getattr(t, "interrupts", None) for t in state.tasks)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _agent_cfg(domain: str) -> dict:
        """读取 agent 级定制覆盖（人设/温度/预算/工具子集；无定制=空）。"""
        try:
            from app.core.agent.customization import customization_service

            entry = customization_service.get_effective_override("agent", domain)
            return (entry or {}).get("overrides") or {}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _skill_cfg(skill_name: str) -> dict:
        """读取启用中的 Skill（F4.4 运行期消费点；停用/未知技能返回空 = 按纯域跑）。"""
        if not skill_name:
            return {}
        try:
            from app.core.agent.skills import skill_registry

            skill = skill_registry.get(skill_name)   # get() 已过滤停用项
            if skill is None:
                logger.info("runtime.skill_inactive", skill=skill_name)
                return {}
            return {"name": skill.name, "domain": skill.domain,
                    "tools": list(skill.tools), "instructions": skill.instructions,
                    "guardrails": dict(skill.guardrails)}
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime.skill_load_failed", skill=skill_name,
                           error=str(e))
            return {}

    @staticmethod
    def _cfg_hash(cfg: dict) -> str:
        import hashlib
        import json as _json

        if not cfg:
            return "default"
        raw = _json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]

    def has_domain(self, domain: str) -> bool:
        return bool(tool_registry_v2.list_specs(domain=domain))

    # ------------------------------------------------------------------
    # LLM 成本计量与预算护栏（路线图 Phase B）
    # ------------------------------------------------------------------

    _token_usage_today: dict[str, int] = {}
    _usage_date: str = ""

    @classmethod
    def _check_budget(cls, domain: str) -> bool:
        """该域当日 token 预算检查（per-agent 定制覆盖优先，超限降级规则路径）。"""
        from datetime import datetime as _dt

        from app.core.config import settings as _settings

        limit = cls._agent_cfg(domain).get("daily_token_budget")
        limit = float(limit) if limit else _settings.agent_daily_token_budget
        today = _dt.now(timezone.utc).strftime("%Y-%m-%d")
        if cls._usage_date != today:
            cls._usage_date = today
            cls._token_usage_today.clear()
        used = cls._token_usage_today.get(domain, 0)
        return used < limit

    @classmethod
    def _record_tokens(cls, domain: str, tokens: int) -> None:
        from datetime import datetime as _dt

        today = _dt.now(timezone.utc).strftime("%Y-%m-%d")
        if cls._usage_date != today:
            cls._usage_date = today
            cls._token_usage_today.clear()
        cls._token_usage_today[domain] = cls._token_usage_today.get(domain, 0) + tokens

    @staticmethod
    def _sum_tokens(messages: list) -> int:
        """聚合 AIMessage 的 usage_metadata（输入+输出 token）。"""
        total = 0
        for msg in messages:
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                total += int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        return total

    def has_domain(self, domain: str) -> bool:
        return bool(tool_registry_v2.list_specs(domain=domain))


# 进程内单例
agent_runtime = AgentRuntime()
