"""
LLM增强的决策引擎
将大语言模型能力融入Agent决策流程

特性：
- LLMDecisionEngine.decide(): 统一决策入口
- 置信度评估：要求LLM输出confidence字段，低于阈值则降级
- 多候选方案生成与打分：让LLM生成多个方案并打分排序
- 安全护栏检查：调用现有guardrails过滤输入输出
- 降级策略：LLM不可用时回退到规则引擎（fallback_fn）

设计原则：
- LLM默认关闭（settings.llm_enabled = False）
- 所有LLM调用均有try/except降级到规则
- 使用structlog记录每次调用
- 不破坏现有测试：LLM关闭时行为完全等同于规则引擎
"""
import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

import structlog

from app.core.config import settings
from app.core.llm.client import LLMClient, LLMClientError, llm_client
from app.core.llm.prompts import PromptManager, prompt_manager
from app.core.security.guardrails import (
    GuardrailAction,
    guardrails,
)

logger = structlog.get_logger()


# 降级时使用的默认决策结构
FALLBACK_DECISION: dict = {
    "decision": "fallback_to_rules",
    "action": "使用规则引擎决策",
    "confidence": 0.0,
    "reasoning": "LLM不可用，已降级到规则引擎",
    "source": "rules",
}

# 多候选方案的默认数量
DEFAULT_NUM_CANDIDATES = 3

# 置信度阈值：低于此值则降级
DEFAULT_CONFIDENCE_THRESHOLD = 0.5


class LLMDecisionEngine:
    """
    LLM增强的决策引擎

    将LLM调用、prompt渲染、安全护栏、降级策略整合为统一的决策接口。
    Agent通过decide()方法获得LLM增强决策，无需直接操作LLM客户端。

    使用示例：
        engine = LLMDecisionEngine()
        result = await engine.decide(
            context={"product_id": "P001", "current_qty": 30},
            agent_type="inventory",
            prompt_template=PromptManager.INVENTORY_REPLENISHMENT,
            fallback_fn=lambda ctx: {"action": "rule_based_reorder", ...},
        )
    """

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        prompts: Optional[PromptManager] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        enabled: Optional[bool] = None,
        fallback_to_rules: Optional[bool] = None,
    ):
        # 允许显式注入便于测试；默认从settings读取
        self._client = client or llm_client
        self._prompts = prompts or prompt_manager
        self.confidence_threshold = confidence_threshold
        # enabled未显式传入时从全局settings读取（默认False，保护测试）
        self._enabled = (
            enabled if enabled is not None else settings.llm_enabled
        )
        self._fallback_to_rules = (
            fallback_to_rules
            if fallback_to_rules is not None
            else settings.llm_fallback_to_rules
        )

        logger.info(
            "llm_decision_engine.initialized",
            enabled=self._enabled,
            fallback_to_rules=self._fallback_to_rules,
            confidence_threshold=self.confidence_threshold,
        )

    # ========== 配置开关 ==========

    @property
    def enabled(self) -> bool:
        """LLM是否启用"""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """运行时切换LLM开关（主要用于测试）"""
        self._enabled = enabled
        logger.info("llm_decision_engine.toggled", enabled=enabled)

    @property
    def fallback_to_rules(self) -> bool:
        """是否在LLM失败时降级到规则"""
        return self._fallback_to_rules

    # ========== 核心决策方法 ==========

    async def decide(
        self,
        context: dict,
        agent_type: str,
        prompt_template: str,
        *,
        fallback_fn: Optional[Callable[[dict], dict]] = None,
        fallback_async_fn: Optional[
            Callable[[dict], Awaitable[dict]]
        ] = None,
        extra_variables: Optional[dict] = None,
    ) -> dict:
        """
        LLM增强决策主入口

        参数：
        - context: 决策上下文数据（业务相关）
        - agent_type: Agent类型标识（inventory/accounting/general等），用于日志
        - prompt_template: prompt模板名称（参考PromptManager常量）
        - fallback_fn: 同步规则降级函数，接收context返回决策dict
        - fallback_async_fn: 异步规则降级函数（优先级高于fallback_fn）
        - extra_variables: 额外的模板变量（与context合并用于渲染）

        返回：决策dict，至少包含以下字段：
        {
            "action": str,            # 决策动作
            "confidence": float,      # 置信度 0-1
            "reasoning": str,         # 决策理由
            "source": "llm"|"rules"|"fallback",  # 决策来源
            ...                       # 其他业务字段
        }
        """
        # 1. 检查LLM开关
        if not self._enabled:
            logger.debug(
                "llm_decision_engine.disabled",
                agent_type=agent_type,
                prompt_template=prompt_template,
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason="llm_disabled",
            )

        # 2. 渲染prompt
        try:
            variables = {**context}
            if extra_variables:
                variables.update(extra_variables)
            system_prompt, user_prompt = self._prompts.render_full(
                prompt_template, variables
            )
        except Exception as e:
            logger.error(
                "llm_decision_engine.prompt_render_failed",
                agent_type=agent_type,
                error=str(e),
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason=f"prompt_render_failed: {e}",
            )

        # 3. 输入安全护栏检查
        action, threats = guardrails.check_input(
            user_prompt, agent_did=f"llm_engine:{agent_type}"
        )
        if action == GuardrailAction.BLOCK:
            logger.warning(
                "llm_decision_engine.input_blocked",
                agent_type=agent_type,
                threats=threats,
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason="input_blocked_by_guardrails",
            )

        # 4. 调用LLM
        try:
            decision = await self._client.chat_async_json(
                user_prompt, system=system_prompt
            )
        except LLMClientError as e:
            logger.warning(
                "llm_decision_engine.llm_call_failed",
                agent_type=agent_type,
                error=str(e),
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason=f"llm_call_failed: {e}",
            )
        except Exception as e:
            logger.error(
                "llm_decision_engine.unexpected_error",
                agent_type=agent_type,
                error=str(e),
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason=f"unexpected_error: {e}",
            )

        # 5. 校验LLM响应结构
        if not isinstance(decision, dict):
            logger.warning(
                "llm_decision_engine.invalid_response_type",
                agent_type=agent_type,
                response_type=type(decision).__name__,
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason="invalid_response_type",
            )

        # 6. 提取并校验置信度
        confidence = self._extract_confidence(decision)
        decision["confidence"] = confidence
        decision["source"] = "llm"

        # 7. 输出安全护栏检查
        output_str = json.dumps(decision, ensure_ascii=False)
        out_action, sanitized, out_threats = guardrails.check_output(
            output_str, agent_did=f"llm_engine:{agent_type}"
        )
        if out_action == GuardrailAction.BLOCK:
            logger.warning(
                "llm_decision_engine.output_blocked",
                agent_type=agent_type,
                threats=out_threats,
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason="output_blocked_by_guardrails",
            )

        # 8. 置信度过低则降级
        if confidence < self.confidence_threshold:
            logger.info(
                "llm_decision_engine.low_confidence_fallback",
                agent_type=agent_type,
                confidence=confidence,
                threshold=self.confidence_threshold,
            )
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason=f"low_confidence: {confidence}",
            )

        logger.info(
            "llm_decision_engine.decision_made",
            agent_type=agent_type,
            confidence=confidence,
            source="llm",
        )
        return decision

    # ========== 多候选方案生成与打分 ==========

    async def generate_candidates(
        self,
        context: dict,
        agent_type: str,
        prompt_template: str,
        *,
        num_candidates: int = DEFAULT_NUM_CANDIDATES,
        extra_variables: Optional[dict] = None,
        scoring_fn: Optional[Callable[[dict], float]] = None,
    ) -> list[dict]:
        """
        生成多个候选决策方案并打分排序

        参数：
        - num_candidates: 候选方案数量
        - scoring_fn: 自定义打分函数，接收候选dict返回分数（0-1）
                      默认使用confidence字段

        返回：按分数降序排列的候选列表，每个候选包含"_score"字段
        """
        if not self._enabled:
            logger.debug(
                "llm_decision_engine.candidates_disabled",
                agent_type=agent_type,
            )
            return []

        # 渲染prompt一次，多次调用（每次temperature略变可获得多样性）
        try:
            variables = {**context}
            if extra_variables:
                variables.update(extra_variables)
            system_prompt, user_prompt = self._prompts.render_full(
                prompt_template, variables
            )
        except Exception as e:
            logger.error(
                "llm_decision_engine.candidates_render_failed",
                agent_type=agent_type,
                error=str(e),
            )
            return []

        candidates: list[dict] = []
        # 并发发起多次调用
        async def _one_call(idx: int) -> Optional[dict]:
            try:
                # 略微提高temperature以增加多样性
                temp = min(1.0, self._client.temperature + 0.1 * idx)
                result = await self._client.chat_async_json(
                    user_prompt, system=system_prompt, temperature=temp
                )
                if isinstance(result, dict):
                    result["_candidate_index"] = idx
                    return result
            except Exception as e:
                logger.warning(
                    "llm_decision_engine.candidate_failed",
                    agent_type=agent_type,
                    index=idx,
                    error=str(e),
                )
            return None

        tasks = [_one_call(i) for i in range(num_candidates)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        for r in results:
            if r is not None:
                candidates.append(r)

        # 打分
        for c in candidates:
            c["_score"] = (
                scoring_fn(c) if scoring_fn else self._extract_confidence(c)
            )

        # 按分数降序
        candidates.sort(key=lambda c: c.get("_score", 0), reverse=True)

        logger.info(
            "llm_decision_engine.candidates_generated",
            agent_type=agent_type,
            count=len(candidates),
        )
        return candidates

    async def decide_with_candidates(
        self,
        context: dict,
        agent_type: str,
        prompt_template: str,
        *,
        fallback_fn: Optional[Callable[[dict], dict]] = None,
        fallback_async_fn: Optional[
            Callable[[dict], Awaitable[dict]]
        ] = None,
        num_candidates: int = DEFAULT_NUM_CANDIDATES,
        scoring_fn: Optional[Callable[[dict], float]] = None,
        extra_variables: Optional[dict] = None,
    ) -> dict:
        """
        多候选方案决策：生成多个候选，选择得分最高的作为最终决策

        相比decide()，此方法通过多候选提升决策质量，但调用成本更高。
        """
        if not self._enabled:
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason="llm_disabled",
            )

        candidates = await self.generate_candidates(
            context,
            agent_type,
            prompt_template,
            num_candidates=num_candidates,
            scoring_fn=scoring_fn,
            extra_variables=extra_variables,
        )

        if not candidates:
            return await self._fallback(
                context, agent_type, fallback_fn, fallback_async_fn,
                reason="no_candidates_generated",
            )

        best = candidates[0]
        best["source"] = "llm_candidates"
        best["candidate_count"] = len(candidates)
        logger.info(
            "llm_decision_engine.best_candidate_selected",
            agent_type=agent_type,
            score=best.get("_score"),
            candidate_count=len(candidates),
        )
        return best

    # ========== 内部辅助方法 ==========

    async def _fallback(
        self,
        context: dict,
        agent_type: str,
        fallback_fn: Optional[Callable[[dict], dict]],
        fallback_async_fn: Optional[Callable[[dict], Awaitable[dict]]],
        reason: str,
    ) -> dict:
        """
        降级到规则引擎

        优先级：
        1. fallback_async_fn（异步规则函数）
        2. fallback_fn（同步规则函数）
        3. FALLBACK_DECISION（默认降级结构）

        所有降级结果都会被标记 source 字段。
        """
        if not self._fallback_to_rules and fallback_fn is None and fallback_async_fn is None:
            # 不允许降级且无降级函数：返回默认降级结构
            result = dict(FALLBACK_DECISION)
            result["reason"] = reason
            return result

        try:
            if fallback_async_fn is not None:
                result = await fallback_async_fn(context)
            elif fallback_fn is not None:
                result = fallback_fn(context)
            else:
                result = dict(FALLBACK_DECISION)
                result["reason"] = reason

            # 确保降级结果带有source标记
            if isinstance(result, dict):
                result.setdefault("source", "rules")
                result.setdefault("confidence", 0.0)
                result.setdefault("_fallback_reason", reason)
            logger.info(
                "llm_decision_engine.fallback_to_rules",
                agent_type=agent_type,
                reason=reason,
            )
            return result
        except Exception as e:
            logger.error(
                "llm_decision_engine.fallback_failed",
                agent_type=agent_type,
                error=str(e),
            )
            result = dict(FALLBACK_DECISION)
            result["reason"] = f"fallback_failed: {e}; original: {reason}"
            return result

    def _extract_confidence(self, decision: dict) -> float:
        """
        从LLM响应中提取并校验置信度

        - 缺失则默认0.0
        - 越界则裁剪到[0, 1]
        """
        try:
            raw = decision.get("confidence", 0.0)
            confidence = float(raw)
        except (TypeError, ValueError):
            confidence = 0.0
        # 裁剪到合法范围
        if confidence < 0:
            confidence = 0.0
        elif confidence > 1:
            confidence = 1.0
        return confidence


# 全局决策引擎单例
llm_decision_engine = LLMDecisionEngine()
