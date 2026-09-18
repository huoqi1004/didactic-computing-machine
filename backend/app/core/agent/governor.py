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

        spec = self.registry.get(tool_name)
        if spec is None:
            return ToolResult(ok=False, error=f"未知工具: {tool_name}")

        async def _audit(action_suffix: str, result: str, details: dict, level: str) -> None:
            try:
                await audit_chain.log(
                    actor_did=ctx.user_id or "unknown",
                    action=f"tool.{tool_name}.{action_suffix}",
                    resource=f"{spec.resource}:{ctx.agent_key or 'agent'}",
                    result=result,
                    details=details,
                    risk_level=level,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("governor.audit_failed", tool=tool_name, error=str(e))

        # 1. RBAC 权限（Agent 借用用户权限语境）
        from app.core.rbac import rbac_engine

        if not rbac_engine.check_permission(ctx.user_id, spec.resource, spec.action):
            self.registry.bump_stat(tool_name, "denied")
            await _audit(
                "denied", "denied",
                {"user": ctx.user_id, "reason": f"缺少权限 {spec.resource}:{spec.action}"},
                "medium",
            )
            logger.warning("governor.permission_denied", tool=tool_name, user=ctx.user_id)
            return ToolResult(ok=False, error=f"权限不足：需要 {spec.resource}:{spec.action} 权限")

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
            await _audit(
                "approval_required", "pending",
                {"args": _sanitize_args(validated), "preview": preview},
                "medium",
            )
            return ToolResult(
                ok=False,
                approval={
                    "tool": tool_name,
                    "risk": spec.risk_level.value,
                    "args": _sanitize_args(validated),
                    "preview": preview,
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
            await _audit(
                "executed", "success",
                {"args": _sanitize_args(validated), "latency_ms": latency_ms,
                 "risk": spec.risk_level.value,
                 "approved_by_human": spec.risk_level == ToolRisk.WRITE_HIGH},
                spec.risk_level.audit_risk,
            )
            logger.info(
                "governor.executed", tool=tool_name, user=ctx.user_id,
                agent=ctx.agent_key, latency_ms=latency_ms, risk=spec.risk_level.value,
            )
            return ToolResult(
                ok=True, data=data,
                preview=preview,
                meta={"latency_ms": latency_ms, "risk": spec.risk_level.value},
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
