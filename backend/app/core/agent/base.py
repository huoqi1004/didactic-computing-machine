"""
Agent基类与生命周期管理
所有业务Agent继承此类，提供完整的状态机、事件感知和安全治理能力

特性：
- 完整生命周期状态机（创建→初始化→运行→暂停/降级/熔断→终止）
- 事件驱动感知（订阅事件总线，响应业务变化）
- 安全治理集成（DID身份、意图授权、审计记录、护栏过滤）
- 健康探针与熔断器（SLO监控、自动降级）
- 人在回路（人类可随时暂停、接管、终止）
- 工具调用沙箱（安全执行注册工具）
- LLM认知引擎集成（llm_chat / llm_analyze，自动降级到规则模式）
"""
import asyncio
import json
import structlog
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional, Callable
from uuid import uuid4

from app.core.config import settings, AgentState
from app.core.event.bus import event_bus
from app.core.event.models import Event, EventType, EventSubscription
from app.core.security.did import did_registry, AgentIdentity
from app.core.security.authorization import authz_engine, AuthorizationRequest, Permission
from app.core.security.audit import audit_chain
from app.core.security.guardrails import guardrails, GuardrailAction
from app.core.config import SecurityLevel

logger = structlog.get_logger()


class AgentDecision:
    """Agent决策记录"""
    def __init__(self, decision_type: str, reasoning: str, action: str,
                 confidence: float, requires_human_approval: bool = False,
                 context: Optional[dict] = None):
        self.decision_id = str(uuid4())
        self.decision_type = decision_type
        self.reasoning = reasoning
        self.action = action
        self.confidence = confidence          # 置信度 0-1
        self.requires_human_approval = requires_human_approval
        self.context = context or {}
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "decision_type": self.decision_type,
            "reasoning": self.reasoning,
            "action": self.action,
            "confidence": self.confidence,
            "requires_human_approval": self.requires_human_approval,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
        }


class CircuitBreaker:
    """熔断器"""
    def __init__(self, threshold: int = 5, reset_timeout: int = 60):
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "closed"  # closed, open, half_open

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)
        if self.failure_count >= self.threshold:
            self.state = "open"
            logger.warning("circuit_breaker.opened",
                          threshold=self.threshold,
                          failures=self.failure_count)

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if self.last_failure_time:
                elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
                if elapsed > self.reset_timeout:
                    self.state = "half_open"
                    return True
            return False
        return True  # half_open


class BaseAgent(ABC):
    """
    Agent基类 - 所有业务Agent的根基类

    生命周期：CREATED → INITIALIZING → RUNNING → (PAUSED/DEGRADED/FUSED) → TERMINATED
    """

    def __init__(self, name: str, domain: str, capabilities: list[str],
                 security_clearance: str = "internal"):
        self.agent_id = str(uuid4())
        self.name = name
        self.domain = domain
        self.capabilities = capabilities
        self.security_clearance = security_clearance

        # 身份与状态
        self.identity: Optional[AgentIdentity] = None
        self.state = AgentState.CREATED

        # 运行时组件
        self._subscriptions: list[EventSubscription] = []
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # 健康与可靠性
        self.circuit_breaker = CircuitBreaker(
            threshold=settings.agent_circuit_breaker_threshold
        )
        self._health_status = "healthy"
        self._last_health_check = datetime.now(timezone.utc)
        self._error_count = 0
        self._success_count = 0

        # 人在回路
        self._human_override = False
        self._pending_approvals: dict[str, asyncio.Future] = {}

        # 决策历史
        self._decision_history: list[AgentDecision] = []

        # LLM认知引擎客户端（懒加载，首次调用llm_chat/llm_analyze时初始化）
        # 为None表示尚未初始化；LLM未启用时保持None，避免不必要的对象创建
        self._llm_client = None

        logger.info("agent.created", agent_id=self.agent_id, name=name, domain=domain)

    @property
    @abstractmethod
    def description(self) -> str:
        """Agent描述"""
        pass

    @abstractmethod
    async def initialize(self):
        """初始化Agent（子类实现：加载配置、注册订阅等）"""
        pass

    @abstractmethod
    async def process_event(self, event: Event):
        """处理接收到的事件（子类实现具体业务逻辑）"""
        pass

    @abstractmethod
    async def make_decision(self, context: dict) -> AgentDecision:
        """做出决策（子类实现具体决策逻辑）"""
        pass

    async def start(self):
        """启动Agent"""
        if self.state in (AgentState.RUNNING, AgentState.INITIALIZING):
            logger.warning("agent.already_running", agent=self.name)
            return

        self.state = AgentState.INITIALIZING
        logger.info("agent.starting", agent=self.name, agent_id=self.agent_id)

        # 注册DID身份
        self.identity = did_registry.register_agent(
            name=self.name,
            domain=self.domain,
            capabilities=self.capabilities,
            security_clearance=self.security_clearance,
        )

        # 审计记录
        await audit_chain.log(
            actor_did=self.identity.did,
            action="agent.start",
            resource=f"agent:{self.agent_id}",
            result="success",
            details={"name": self.name, "domain": self.domain},
            risk_level="low",
        )

        # 发布Agent注册事件
        await self._publish_event(
            EventType.AGENT_REGISTERED,
            {"agent_id": self.agent_id, "name": self.name, "domain": self.domain,
             "capabilities": self.capabilities, "did": self.identity.did}
        )

        # 子类初始化
        try:
            await self.initialize()
            self.state = AgentState.RUNNING
            self._stop_event.clear()

            # 启动主循环
            self._task = asyncio.create_task(self._run_loop())

            logger.info("agent.started", agent=self.name, agent_id=self.agent_id,
                       did=self.identity.did)

        except Exception as e:
            self.state = AgentState.ERROR
            self.circuit_breaker.record_failure()
            logger.error("agent.start_failed", agent=self.name, error=str(e))
            raise

    async def stop(self):
        """停止Agent"""
        logger.info("agent.stopping", agent=self.name)
        self._stop_event.set()
        self.state = AgentState.TERMINATED

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # 取消事件订阅
        for sub in self._subscriptions:
            event_bus.unsubscribe(sub.subscriber_id)

        # 吊销身份
        if self.identity:
            did_registry.revoke_identity(self.identity.did, reason="agent_stopped")
            await audit_chain.log(
                actor_did=self.identity.did,
                action="agent.stop",
                resource=f"agent:{self.agent_id}",
                result="success",
                details={"name": self.name},
            )

        logger.info("agent.stopped", agent=self.name)

    async def pause(self, reason: str = "manual"):
        """暂停Agent"""
        self.state = AgentState.PAUSED
        logger.info("agent.paused", agent=self.name, reason=reason)
        await self._publish_event(
            EventType.AGENT_STATE_CHANGED,
            {"agent_id": self.agent_id, "state": "paused", "reason": reason}
        )

    async def resume(self):
        """恢复Agent"""
        self.state = AgentState.RUNNING
        logger.info("agent.resumed", agent=self.name)
        await self._publish_event(
            EventType.AGENT_STATE_CHANGED,
            {"agent_id": self.agent_id, "state": "running"}
        )

    async def _run_loop(self):
        """Agent主循环"""
        while not self._stop_event.is_set():
            try:
                if self.state == AgentState.PAUSED:
                    await asyncio.sleep(1)
                    continue

                if self._human_override:
                    # 人在回路：等待人类接管结束
                    await asyncio.sleep(0.5)
                    continue

                if not self.circuit_breaker.can_execute():
                    self.state = AgentState.FUSED
                    logger.warning("agent.fused", agent=self.name)
                    await asyncio.sleep(5)
                    continue

                # 执行健康检查
                await self._health_check()

                # 执行周期性任务（子类可覆盖）
                await self.on_tick()

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                self.circuit_breaker.record_failure()
                logger.error("agent.run_loop_error", agent=self.name, error=str(e))
                if self.circuit_breaker.state == "open":
                    self.state = AgentState.FUSED
                await asyncio.sleep(1)

    async def on_tick(self):
        """周期性任务（子类可覆盖）"""
        pass

    async def _health_check(self):
        """健康检查"""
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_health_check).total_seconds()
        if elapsed < settings.agent_health_check_interval:
            return

        self._last_health_check = now

        # 检查熔断器状态
        if self.circuit_breaker.state == "open":
            self._health_status = "unhealthy"
            self.state = AgentState.FUSED
        elif self.circuit_breaker.state == "half_open":
            self._health_status = "degraded"
            self.state = AgentState.DEGRADED
        else:
            self._health_status = "healthy"
            if self.state == AgentState.FUSED or self.state == AgentState.DEGRADED:
                self.state = AgentState.RUNNING

    def subscribe_events(self, event_types: list[EventType], handler: Callable,
                         filter_expr: Optional[dict] = None):
        """订阅事件"""
        sub = EventSubscription(
            subscriber_id=f"{self.name}:{self.agent_id}",
            event_types=event_types,
            handler=handler,
            filter_expr=filter_expr,
        )
        self._subscriptions.append(sub)
        event_bus.subscribe(sub)

    async def _publish_event(self, event_type: EventType, data: dict):
        """发布事件"""
        event = Event(
            event_type=event_type,
            aggregate_id=f"agent:{self.agent_id}",
            aggregate_type="agent",
            event_data=data,
            agent_id=self.identity.did if self.identity else None,
        )
        await event_bus.publish(event)

    async def request_authorization(self, intent: str, resource: str,
                                    data_level: SecurityLevel,
                                    permissions: list[Permission],
                                    context: Optional[dict] = None) -> bool:
        """请求意图级授权"""
        if not self.identity:
            return False

        request = AuthorizationRequest(
            agent_did=self.identity.did,
            intent=intent,
            target_resource=resource,
            data_security_level=data_level,
            context=context or {},
            requested_permissions=permissions,
        )

        result = await authz_engine.authorize(request)

        await audit_chain.log(
            actor_did=self.identity.did,
            action=f"authorize.{intent}",
            resource=resource,
            result="success" if result.granted else "denied",
            details=result.to_dict(),
            risk_level=result.risk_level.value,
        )

        return result.granted

    async def record_decision(self, decision: AgentDecision):
        """记录Agent决策"""
        self._decision_history.append(decision)

        # 安全护栏检查输出
        action, sanitized, threats = guardrails.check_output(
            decision.action, self.identity.did if self.identity else ""
        )
        if action == GuardrailAction.BLOCK:
            logger.warning("agent.decision_blocked",
                          agent=self.name,
                          threats=threats)
            return

        # 发布决策事件
        await self._publish_event(
            EventType.AGENT_DECISION_MADE,
            decision.to_dict(),
        )

        # 审计记录
        if self.identity:
            await audit_chain.log(
                actor_did=self.identity.did,
                action=f"decision.{decision.decision_type}",
                resource=self.name,
                result="success",
                details=decision.to_dict(),
                risk_level="high" if decision.confidence < 0.7 else "medium",
            )

        self._success_count += 1
        self.circuit_breaker.record_success()

    async def request_human_approval(self, decision: AgentDecision,
                                     timeout: int = 3600) -> bool:
        """请求人类审批（人在回路）"""
        decision.requires_human_approval = True
        future = asyncio.get_event_loop().create_future()
        self._pending_approvals[decision.decision_id] = future

        logger.info("agent.human_approval_requested",
                    agent=self.name,
                    decision_id=decision.decision_id,
                    action=decision.action)

        await self._publish_event(
            EventType.AGENT_DECISION_MADE,
            {**decision.to_dict(), "status": "pending_approval"},
        )

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("agent.approval_timeout",
                          agent=self.name,
                          decision_id=decision.decision_id)
            return False
        finally:
            self._pending_approvals.pop(decision.decision_id, None)

    def resolve_approval(self, decision_id: str, approved: bool):
        """解决人类审批（由人类监督员调用）"""
        future = self._pending_approvals.get(decision_id)
        if future and not future.done():
            future.set_result(approved)
            logger.info("agent.approval_resolved",
                       agent=self.name,
                       decision_id=decision_id,
                       approved=approved)

    async def human_takeover(self):
        """人类接管（人在回路安全阀）"""
        self._human_override = True
        self.state = AgentState.PAUSED
        logger.warning("agent.human_takeover", agent=self.name)
        await self._publish_event(
            EventType.AGENT_STATE_CHANGED,
            {"agent_id": self.agent_id, "state": "human_takeover"}
        )

    async def human_release(self):
        """人类释放控制"""
        self._human_override = False
        self.state = AgentState.RUNNING
        logger.info("agent.human_released", agent=self.name)

    # ========== LLM认知引擎集成 ==========

    def _get_llm_client(self):
        """
        懒加载获取LLM客户端单例

        返回None表示LLM未启用或不可初始化。
        """
        if not settings.llm_enabled:
            return None
        if self._llm_client is None:
            try:
                from app.core.llm.client import get_llm_client
                self._llm_client = get_llm_client()
            except Exception as e:
                logger.warning(
                    "agent.llm_client_init_failed",
                    agent=self.name, error=str(e),
                )
                return None
        return self._llm_client

    async def llm_chat(self, messages: list[dict], **kwargs) -> str:
        """
        调用LLM进行对话，自动注入系统提示词

        降级策略：
        - LLM未启用（settings.llm_enabled=False）：记录警告并返回空字符串
        - LLM不可用（连接失败/超时/重试耗尽）：记录警告并返回空字符串
        - 调用方应根据返回值是否为空字符串判断是否降级到规则模式

        参数：
        - messages: OpenAI格式消息列表
            [{"role": "user", "content": "..."}]
            如未包含system角色消息，将自动注入系统提示词
        - **kwargs: 透传给LLM客户端的参数（temperature, max_tokens等）

        返回：LLM生成的文本内容（str）；LLM不可用时返回空字符串 ""
        """
        if not settings.llm_enabled:
            logger.debug("agent.llm_disabled", agent=self.name)
            return ""

        client = self._get_llm_client()
        if client is None:
            logger.warning("agent.llm_unavailable", agent=self.name,
                           reason="client_not_initialized")
            return ""

        # 自动注入系统提示词（若messages中未包含system角色）
        from app.core.llm.prompts import SYSTEM_PROMPT_BASE
        agent_system = (
            f"{SYSTEM_PROMPT_BASE}\n\n"
            f"当前Agent: {self.name}（域: {self.domain}）\n"
            f"能力: {', '.join(self.capabilities)}"
        )
        has_system = any(m.get("role") == "system" for m in messages)
        final_messages = (
            messages if has_system
            else [{"role": "system", "content": agent_system}] + messages
        )

        # 输入安全护栏检查
        combined_input = " ".join(
            m.get("content", "") for m in final_messages
        )
        action, threats = guardrails.check_input(
            combined_input,
            agent_did=self.identity.did if self.identity else "",
        )
        if action == GuardrailAction.BLOCK:
            logger.warning(
                "agent.llm_input_blocked",
                agent=self.name, threats=threats,
            )
            return ""

        try:
            response = await client.chat_completion(final_messages, **kwargs)

            # 输出安全护栏检查
            out_action, sanitized, out_threats = guardrails.check_output(
                response,
                agent_did=self.identity.did if self.identity else "",
            )
            if out_action == GuardrailAction.BLOCK:
                logger.warning(
                    "agent.llm_output_blocked",
                    agent=self.name, threats=out_threats,
                )
                return ""
            return sanitized if out_action == GuardrailAction.SANITIZE else response
        except Exception as e:
            logger.warning(
                "agent.llm_chat_failed",
                agent=self.name, error=str(e), error_type=type(e).__name__,
            )
            return ""

    async def llm_analyze(self, prompt: str, context: dict) -> dict:
        """
        调用LLM分析业务数据，返回结构化JSON

        自动完成以下工作：
        1. 将context序列化为JSON注入到用户消息中
        2. 注入系统提示词（要求返回合法JSON）
        3. 调用LLM并解析JSON响应
        4. 安全护栏过滤输入输出
        5. 解析失败时返回 {"error": "parse_failed", "raw": response}

        降级策略：
        - LLM未启用：返回 {"error": "llm_unavailable"}
        - LLM不可用：返回 {"error": "llm_unavailable"}
        - JSON解析失败：返回 {"error": "parse_failed", "raw": <原始响应>}
        - 调用方应检查返回dict中是否包含 "error" 键来判断是否降级

        参数：
        - prompt: 分析任务描述（自然语言）
        - context: 业务上下文数据（dict，将被序列化为JSON注入prompt）

        返回：
        - 成功：LLM返回的JSON对象（dict）
        - LLM不可用：{"error": "llm_unavailable"}
        - 解析失败：{"error": "parse_failed", "raw": <原始响应文本>}
        """
        if not settings.llm_enabled:
            logger.debug("agent.llm_disabled", agent=self.name)
            return {"error": "llm_unavailable"}

        client = self._get_llm_client()
        if client is None:
            logger.warning("agent.llm_unavailable", agent=self.name,
                           reason="client_not_initialized")
            return {"error": "llm_unavailable"}

        from app.core.llm.prompts import SYSTEM_PROMPT_BASE

        # 构建系统提示：要求返回JSON
        system_prompt = (
            f"{SYSTEM_PROMPT_BASE}\n\n"
            f"当前Agent: {self.name}（域: {self.domain}）\n"
            f"你必须返回合法的JSON格式响应，不要包含markdown代码块标记或额外说明文字。"
        )

        # 构建用户消息：上下文 + 任务
        try:
            context_json = json.dumps(context, ensure_ascii=False, default=str)
        except Exception:
            context_json = str(context)
        user_content = (
            f"## 业务上下文数据\n{context_json}\n\n"
            f"## 分析任务\n{prompt}\n\n"
            f"请基于上述上下文数据完成分析任务，返回合法JSON。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # 输入安全护栏检查
        action, threats = guardrails.check_input(
            user_content,
            agent_did=self.identity.did if self.identity else "",
        )
        if action == GuardrailAction.BLOCK:
            logger.warning(
                "agent.llm_analyze_input_blocked",
                agent=self.name, threats=threats,
            )
            return {"error": "llm_unavailable", "reason": "input_blocked"}

        try:
            response = await client.chat_completion(
                messages,
                response_format={"type": "json_object"},
            )

            # 输出安全护栏检查
            out_action, sanitized, out_threats = guardrails.check_output(
                response,
                agent_did=self.identity.did if self.identity else "",
            )
            if out_action == GuardrailAction.BLOCK:
                logger.warning(
                    "agent.llm_analyze_output_blocked",
                    agent=self.name, threats=out_threats,
                )
                return {"error": "llm_unavailable", "reason": "output_blocked"}
            content = sanitized if out_action == GuardrailAction.SANITIZE else response

            # 解析JSON
            parsed = self._parse_llm_json(content)
            if parsed is None:
                logger.warning(
                    "agent.llm_analyze_parse_failed",
                    agent=self.name, raw_length=len(content),
                )
                return {"error": "parse_failed", "raw": content}

            logger.info(
                "agent.llm_analyze_success",
                agent=self.name, keys=list(parsed.keys()) if isinstance(parsed, dict) else None,
            )
            return parsed
        except Exception as e:
            logger.warning(
                "agent.llm_analyze_failed",
                agent=self.name, error=str(e), error_type=type(e).__name__,
            )
            return {"error": "llm_unavailable", "reason": str(e)}

    @staticmethod
    def _parse_llm_json(content: str) -> Optional[dict]:
        """
        从LLM响应中解析JSON

        依次尝试：
        1. 直接json.loads
        2. 提取 ```json ... ``` 代码块
        3. 提取第一个 { ... } 区间

        返回：解析后的dict；解析失败返回None
        """
        if not content:
            return None
        # 尝试1：直接解析
        try:
            result = json.loads(content)
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            pass
        # 尝试2：提取markdown代码块
        import re
        code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if code_block:
            try:
                result = json.loads(code_block.group(1).strip())
                return result if isinstance(result, dict) else None
            except json.JSONDecodeError:
                pass
        # 尝试3：提取第一个 {...} 区间
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            try:
                result = json.loads(content[first_brace:last_brace + 1])
                return result if isinstance(result, dict) else None
            except json.JSONDecodeError:
                pass
        return None

    def get_status(self) -> dict:
        """获取Agent状态"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "domain": self.domain,
            "state": self.state.value,
            "health": self._health_status,
            "did": self.identity.did if self.identity else None,
            "capabilities": self.capabilities,
            "circuit_breaker": {
                "state": self.circuit_breaker.state,
                "failures": self.circuit_breaker.failure_count,
            },
            "stats": {
                "success_count": self._success_count,
                "error_count": self._error_count,
                "decisions": len(self._decision_history),
                "pending_approvals": len(self._pending_approvals),
            },
            "human_override": self._human_override,
            "last_health_check": self._last_health_check.isoformat(),
        }
