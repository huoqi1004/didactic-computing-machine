"""
AgentGovernor 治理面（路线图 Phase A / A3）
============================================

一切 Agent 工具调用的**强制通道**：RBAC → schema 校验 → 风控 →
dry-run 预演 → 分级审批路由（决策 D6）→ 执行（失败补偿）→ 审计哈希链 →
限流与计量。绕过治理面的调用路径不允许存在（路线图设计红线 1）。

执行语义：
- readonly：校验后直接执行；
- write_low：dry-run 留痕后直接执行（免审批，事后抽样审计）；
- write_high：未携带 approved=True 时**不执行**，返回审批需求
  （approval 载荷含效果预演），由编排层转 interrupt() 人审；
  人审通过后带 approved=True 重放调用才真正执行。

所有结果（成功/失败/拒绝/审批需求）均写审计哈希链。
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from typing import Any, Optional

import structlog

from app.core.agent.tools_v2 import (
    ToolContext,
    ToolRegistryV2,
    ToolResult,
    ToolRisk,
    tool_registry_v2,
)

logger = structlog.get_logger()

# 审计参数截断与脱敏
_MAX_AUDIT_ARGS_CHARS = 1000
_SENSITIVE_KEY_MARKS = ("password", "token", "secret", "api_key", "bank_account")

# 限流默认：每用户每分钟 30 次工具调用（编排防失控兜底，区别于 API 限流）
_RATE_LIMIT_PER_MIN = 30


def _sanitize_args(args: dict) -> dict:
    """审计用参数快照：脱敏 + 截断。"""
    safe: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if any(mark in str(key).lower() for mark in _SENSITIVE_KEY_MARKS):
            safe[str(key)] = "***"
        else:
            try:
                json.dumps(value)
                safe[str(key)] = value
            except (TypeError, ValueError):
                safe[str(key)] = str(value)[:200]
    text = json.dumps(safe, ensure_ascii=False, default=str)
    if len(text) > _MAX_AUDIT_ARGS_CHARS:
        return {"_truncated": text[:_MAX_AUDIT_ARGS_CHARS]}
    return safe


class AgentGovernor:
    """工具调用治理面（进程内单例）。"""

    def __init__(self, registry: ToolRegistryV2 | None = None):
        self.registry = registry or tool_registry_v2
        # 限流：user_id -> deque[monotonic 时间戳]
        self._rate_buckets: dict[str, deque] = defaultdict(deque)

    # ------------------------------------------------------------------
    async def execute(
        self,
        tool_name: str,
        args: dict,
        ctx: ToolContext,
        approved: bool = False,
        approval_comment: str = "",
    ) -> ToolResult:
        """治理面主入口：校验 → 预演 → 分级 → 执行 → 审计。"""
        from app.core.security.audit import audit_chain
        from app.core.observability.trace import (
            ensure_trace_id,
            extract_business_key,
            get_business_key,
            set_business_key,
        )

        spec = self.registry.get(tool_name)
        if spec is None:
            return ToolResult(ok=False, error=f"未知工具: {tool_name}")

        # 追踪键（G1.1）：ctx 显式携带优先，否则复用/新建上下文链路键；
        # business_key（业务根键）随执行结果更新（工具产生单据后登记）
        trace_id = ctx.trace_id or ensure_trace_id()
        bk_holder = {"key": ctx.business_key or get_business_key()}

        async def _audit(action_suffix: str, result: str, details: dict, level: str) -> str:
            try:
                entry_id = await audit_chain.log(
                    actor_did=ctx.user_id or "unknown",
                    action=f"tool.{tool_name}.{action_suffix}",
                    resource=f"{spec.resource}:{ctx.agent_key or 'agent'}",
                    result=result,
                    details=details,
                    risk_level=level,
                    trace_id=trace_id,
                    business_key=bk_holder["key"],
                )
                return entry_id
            except Exception as e:  # noqa: BLE001
                logger.warning("governor.audit_failed", tool=tool_name, error=str(e))
                return ""

        # 1. RBAC 权限（Agent 借用用户权限语境）
        from app.core.rbac import rbac_engine

        if not rbac_engine.check_permission(ctx.user_id, spec.resource, spec.action):
            self.registry.bump_stat(tool_name, "denied")
            # G3.4：高频越权滑动窗计数（超阈值告警）
            from app.core.observability.alerts import note_permission_denied
            note_permission_denied(ctx.user_id, spec.resource, spec.action)
            await _audit(
                "denied", "denied",
                {"user": ctx.user_id, "reason": f"缺少权限 {spec.resource}:{spec.action}"},
                "medium",
            )
            logger.warning("governor.permission_denied", tool=tool_name, user=ctx.user_id)
            return ToolResult(ok=False, error=f"权限不足：需要 {spec.resource}:{spec.action} 权限")
        # ---- 定制层接入（Phase F1）：工具启停/风险分级覆盖 ----
        # 失败安全：定制读取异常按"无覆盖"处理（不阻断工具调用）
        try:
            from app.core.agent.customization import customization_service

            await customization_service.ensure_fresh()
            if not customization_service.is_tool_enabled(tool_name):
                self.registry.bump_stat(tool_name, "denied")
                await _audit("disabled", "denied",
                             {"reason": "工具已被管理员停用"}, "medium")
                logger.warning("governor.tool_disabled", tool=tool_name)
                return ToolResult(ok=False, error=f"工具 {tool_name} 已被管理员停用")
            risk_override = customization_service.get_tool_risk_override(tool_name)
            if risk_override:
                spec = spec.__class__(
                    name=spec.name, description=spec.description,
                    handler=spec.handler, args_model=spec.args_model,
                    risk_level=__import__("app.core.agent.tools_v2",
                                          fromlist=["ToolRisk"]).ToolRisk(risk_override),
                    resource=spec.resource, action=spec.action,
                    compensation=spec.compensation, effects_fn=spec.effects_fn,
                    agent_domain=spec.agent_domain, tags=spec.tags,
                    idempotent=spec.idempotent,
                    business_key_field=spec.business_key_field,
                    rollback_precheck=spec.rollback_precheck,
                    impact_fn=spec.impact_fn,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("governor.customization_read_failed",
                           tool=tool_name, error=str(e))


        # 2. 限流（每用户滑窗）
        if not self._check_rate_limit(ctx.user_id):
            await _audit("rate_limited", "denied", {"user": ctx.user_id}, "low")
            return ToolResult(ok=False, error="工具调用过于频繁，请稍后重试")

        # 3. schema 校验
        try:
            validated = spec.validate_args(args or {})
        except ValueError as e:
            await _audit("invalid_args", "failure", {"error": str(e)}, "low")
            return ToolResult(ok=False, error=str(e))

        # 4. dry-run 预演内容（写操作强制生成，进审计与人审卡片）
        preview = spec.describe_effects(validated) if spec.risk_level.is_write else None

        # 5. 分级审批路由（决策 D6）
        if spec.risk_level == ToolRisk.WRITE_HIGH and not approved:
            self.registry.bump_stat(tool_name, "calls")
            # G5.3 影子执行（S2）：write_high 审批前生成影响面快照进审批单
            impact: dict = {}
            if spec.impact_fn is not None:
                try:
                    impact = await spec.impact_fn(validated) or {}
                except Exception as ie:  # noqa: BLE001
                    impact = {"error": f"影响面快照生成失败: {ie}"}
            impact_text = ""
            if impact and not impact.get("error"):
                impact_text = "\n影响面：" + "; ".join(
                    f"{k}={v}" for k, v in impact.items() if v is not None)
            self.registry.bump_stat(tool_name, "calls")
            await _audit(
                "approval_required", "pending",
                {"args": _sanitize_args(validated), "preview": preview,
                 "impact": impact},
                "medium",
            )
            return ToolResult(
                ok=False,
                approval={
                    "tool": tool_name,
                    "risk": spec.risk_level.value,
                    "args": _sanitize_args(validated),
                    "preview": (preview or "") + impact_text,
                    "impact": impact,
                    "permission": f"{spec.resource}:{spec.action}",
                },
            )

        # 6. 执行（异常时补偿）
        started = time.monotonic()
        self.registry.bump_stat(tool_name, "calls")
        try:
            data = await spec.handler(ctx, **validated)
            latency_ms = round((time.monotonic() - started) * 1000, 2)
            self.registry.bump_stat(tool_name, "total_ms", latency_ms)
            # 业务根键登记（G1.3）：工具产生/操作单据后，从结果提取单据号
            # 登记进追踪上下文与审计列——凭单据号即可反查全链路（闭环 P0-1）
            business_key = extract_business_key(data, spec.business_key_field or "")
            if business_key and business_key != bk_holder["key"]:
                bk_holder["key"] = business_key
                set_business_key(business_key)
            # G3.4：异常时段写操作告警（22:00~06:00，同用户+工具冷却 1h）
            if spec.risk_level.is_write:
                try:
                    from app.core.observability.alerts import note_off_hours_write
                    await note_off_hours_write(ctx.user_id, tool_name)
                except Exception:  # noqa: BLE001
                    pass
            audit_entry_id = await _audit(
                "executed", "success",
                {"args": _sanitize_args(validated), "latency_ms": latency_ms,
                 "risk": spec.risk_level.value,
                 "business_key": business_key,
                 "approved_by_human": spec.risk_level == ToolRisk.WRITE_HIGH},
                spec.risk_level.audit_risk,
            )
            # G4.2 账本登记（同步 await，红线 1）：写操作执行成功即落一行；
            # 失败不阻断业务——对账任务将补登记（backfilled）并 P0 告警
            if spec.risk_level.is_write:
                try:
                    from app.core.agent.operation_ledger import ledger_append

                    await ledger_append(
                        audit_entry_id=audit_entry_id, tool=tool_name,
                        actor=ctx.user_id, forward_args=_sanitize_args(validated),
                        forward_result=data, trace_id=trace_id,
                        business_key=business_key or bk_holder["key"],
                        domain=spec.agent_domain,
                    )
                except Exception as le:  # noqa: BLE001
                    logger.error("governor.ledger_append_failed",
                                 tool=tool_name, error=str(le))
            logger.info(
                "governor.executed", tool=tool_name, user=ctx.user_id,
                agent=ctx.agent_key, latency_ms=latency_ms, risk=spec.risk_level.value,
                trace_id=trace_id, business_key=business_key,
            )
            return ToolResult(
                ok=True, data=data,
                preview=preview,
                meta={"latency_ms": latency_ms, "risk": spec.risk_level.value,
                      "trace_id": trace_id, "business_key": business_key},
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = round((time.monotonic() - started) * 1000, 2)
            self.registry.bump_stat(tool_name, "failures")
            comp_result = None
            if spec.compensation is not None:
                try:
                    await spec.compensation(ctx, validated, None)
                    comp_result = "compensated"
                    logger.warning("governor.compensated", tool=tool_name)
                except Exception as ce:  # noqa: BLE001
                    comp_result = f"compensation_failed: {ce}"
                    logger.error("governor.compensation_failed", tool=tool_name, error=str(ce))
            await _audit(
                "failed", "failure",
                {"args": _sanitize_args(validated), "error": str(e),
                 "latency_ms": latency_ms, "compensation": comp_result},
                spec.risk_level.audit_risk,
            )
            return ToolResult(
                ok=False,
                error=f"{type(e).__name__}: {e}" + (f"（已回滚: {comp_result}）" if comp_result else ""),
                meta={"latency_ms": latency_ms},
            )

    # ------------------------------------------------------------------
    def _check_rate_limit(self, user_id: str) -> bool:
        import time as _time

        now = _time.monotonic()
        bucket = self._rate_buckets[user_id]
        while bucket and bucket[0] < now - 60.0:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_PER_MIN:
            return False
        bucket.append(now)
        return True

    def get_stats(self) -> dict:
        """工具调用统计（供工具目录 API）。"""
        return {
            "tools": self.registry.mcp_descriptors(),
            "stats": self.registry.stats,
        }


# 进程内单例
agent_governor = AgentGovernor()
