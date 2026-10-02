"""
工具协议 v2（路线图 Phase A / A2）
====================================

把业务方法封装为可被 LLM 编排调用的"一等工具"，每个工具声明：

- **参数 schema**（pydantic 模型）：LLM 幻觉参数在治理面被强制拦截；
- **风险分级**：readonly / write_low（免审批+事后抽样审计）/
  write_high（强制人审门，决策 D6）；
- **权限要求**（resource:action，RBAC）：Agent 借用用户的权限语境；
- **补偿函数**（可选）：写工具失败时的幂等回滚（可回滚才可执行红线）；
- **效果描述函数**（写工具必填）：dry-run 预演内容，进人审卡片与审计；
- **MCP 导出接口**（预留）：Phase D 经 FastMCP 导出，无需重写工具。

工具不直接执行——统一经 :class:`~app.core.agent.governor.AgentGovernor`
治理通道（见 A3）。本模块只负责声明、注册与查询。
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

import structlog

logger = structlog.get_logger()


class ToolRisk(str, Enum):
    """工具风险分级（决策 D6：write_low 免审+事后抽样；write_high 强制人审）。"""

    READONLY = "readonly"
    WRITE_LOW = "write_low"
    WRITE_HIGH = "write_high"

    @property
    def is_write(self) -> bool:
        return self != ToolRisk.READONLY

    @property
    def audit_risk(self) -> str:
        """映射到审计链的 risk_level。"""
        return {"readonly": "low", "write_low": "medium", "write_high": "high"}[self.value]


@dataclass
class ToolContext:
    """一次工具调用的上下文（由编排层注入，治理面用于权限与审计归属）。"""

    user_id: str
    username: str = ""
    agent_key: str = ""
    thread_id: str = ""
    channel_id: str = ""
    # 追踪键（G1.1）：编排层构造 ctx 时可显式带上；空值时治理面自动
    # 回落到 observability.trace 的 contextvars（HTTP 中间件已绑定）
    trace_id: str = ""
    business_key: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    """治理面执行结果（编排层据此决定继续/人审/回复错误）。"""

    ok: bool
    data: Any = None
    error: str | None = None
    dry_run: bool = False
    preview: str | None = None
    # 非空 = 需要人审（write_high 且未 approved）：编排层转 interrupt()
    approval: dict | None = None
    meta: dict = field(default_factory=dict)

    def for_llm(self) -> str:
        """转工具回传给 LLM 的文本（控制体积、脱敏）。"""
        if self.approval is not None:
            return (
                f"[需人工审批] 该操作（{self.approval.get('tool')}）风险等级为 "
                f"{self.approval.get('risk')}，已提交人工审批。\n效果预览：{self.approval.get('preview')}"
            )
        if not self.ok:
            return f"[执行失败] {self.error}"
        try:
            text = json.dumps(self.data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(self.data)
        if len(text) > 6000:
            text = text[:6000] + "…(截断)"
        prefix = "[dry-run 预演] " if self.dry_run else ""
        return f"{prefix}{text}"


# 处理器签名：async def handler(ctx: ToolContext, **validated_args) -> Any
ToolHandler = Callable[..., Awaitable[Any]]
# 补偿签名：async def compensation(ctx, args: dict, result: Any) -> Any
# result 为处理器成功返回值（异常场景为 None）；携带单据标识可实现精确回滚
ToolCompensation = Callable[["ToolContext", dict, Any], Awaitable[Any]]
# 效果描述签名：def effects(args: dict) -> str（人类可读，进审批卡片与审计）
ToolEffectsFn = Callable[[dict], str]
# 回滚前置检查签名（G4.3，红线 6）：
# async def precheck(ctx, args: dict, result: Any) -> dict
# 返回 {"blocked": bool, "dependencies": [...], "message": str}——
# blocked=True 时回滚默认拒绝（下游引用清单随审批单展示）
ToolRollbackPrecheck = Callable[["ToolContext", dict, Any], Awaitable[dict]]

# MCP 导出接口占位（Phase D 落地）：返回 (name, description, args_model, risk)
ToolMcpExporter = Callable[["ToolSpecV2"], dict]


@dataclass
class ToolSpecV2:
    """工具协议 v2 规格。"""

    name: str
    description: str
    handler: ToolHandler
    args_model: type                      # pydantic BaseModel 子类
    risk_level: ToolRisk
    resource: str                         # RBAC 资源（如 "reimbursement"）
    action: str                           # RBAC 动作（如 "read"/"create"）
    compensation: Optional[ToolCompensation] = None
    effects_fn: Optional[ToolEffectsFn] = None
    agent_domain: str = ""                # 归属域（finance/hr/sales/...）
    tags: list[str] = field(default_factory=list)
    # G1.4 幂等声明（红线 2）：补偿函数以单据状态机为准实现幂等——
    # 已回滚/已冲正则 no-op 并返回既有结果；写工具未声明拒绝注册
    idempotent: bool = False
    # G1.3 业务根键提取：result 中携带单据号的字段名（显式声明优先，
    # 空则按常见字段名启发式扫描）
    business_key_field: Optional[str] = None
    # G4.3 回滚前置检查（红线 6）：有下游引用的单据默认拒绝回滚；
    # 未登记 = 无下游依赖检查（回滚直接进审批）
    rollback_precheck: Optional[ToolRollbackPrecheck] = None

    # ------------------------------------------------------------------
    def validate_args(self, args: dict) -> dict:
        """pydantic 校验并归一化参数（校验失败抛 ValueError）。"""
        try:
            model = self.args_model(**args)
            return model.model_dump(exclude_none=True)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"参数校验失败: {e}") from e

    def describe_effects(self, args: dict) -> str:
        """dry-run 预演描述（写工具推荐提供；只读工具固定文案）。"""
        if self.risk_level == ToolRisk.READONLY:
            return "只读查询，不产生数据变更"
        if self.effects_fn is not None:
            try:
                return str(self.effects_fn(args))
            except Exception:  # noqa: BLE001
                return "效果预览生成失败"
        return f"写操作（{self.risk_level.value}），未提供效果描述"

    def to_mcp_descriptor(self) -> dict:
        """MCP 导出描述（Phase D：FastMCP 以此注册同名工具，无双重维护）。"""
        return {
            "name": self.name,
            "description": self.description,
            "args_schema": self.args_model.model_json_schema(),
            "risk_level": self.risk_level.value,
            "permission": f"{self.resource}:{self.action}",
            "domain": self.agent_domain,
            "has_compensation": self.compensation is not None,
        }


class ToolRegistryV2:
    """工具注册表 v2（进程内单例，governor 与 runtime 共享）。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpecV2] = {}
        # 调用统计：name -> {calls, failures, total_ms, denied}
        self.stats: dict[str, dict[str, float]] = {}

    def register(self, spec: ToolSpecV2) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        # E2 沙箱守卫：任意代码执行类工具默认禁用（需容器隔离 + 开关）
        if "code-exec" in spec.tags:
            from app.core.config import settings

            if not settings.sandbox_enabled:
                raise ValueError(
                    f"工具 {spec.name} 属代码执行类（code-exec），沙箱未启用"
                    "（ERP_SANDBOX_ENABLED=false），拒绝注册"
                )
        if spec.risk_level.is_write and spec.compensation is None:
            # 红线：可回滚才可执行——写工具必须提供补偿函数
            raise ValueError(f"写工具缺少补偿函数: {spec.name}（可回滚才可执行）")
        if spec.risk_level.is_write and spec.effects_fn is None:
            raise ValueError(f"写工具缺少效果描述函数: {spec.name}")
        if spec.risk_level.is_write and not spec.idempotent:
            # G1.4（红线 2）：补偿重放经账本状态机门禁后仍可能到达补偿函数
            # （重试/人工重放），不幂等的补偿会二次修改数据
            raise ValueError(
                f"写工具补偿未声明幂等: {spec.name}"
                "（补偿须以单据状态机为准幂等，已回滚则 no-op）"
            )
        self._tools[spec.name] = spec
        logger.info(
            "tool_registry_v2.registered",
            tool=spec.name,
            risk=spec.risk_level.value,
            permission=f"{spec.resource}:{spec.action}",
            domain=spec.agent_domain,
        )

    def get(self, name: str) -> Optional[ToolSpecV2]:
        return self._tools.get(name)

    def list_specs(self, domain: Optional[str] = None) -> list[ToolSpecV2]:
        specs = list(self._tools.values())
        if domain:
            specs = [s for s in specs if s.agent_domain == domain]
        return sorted(specs, key=lambda s: s.name)

    def mcp_descriptors(self, domain: Optional[str] = None) -> list[dict]:
        return [s.to_mcp_descriptor() for s in self.list_specs(domain)]

    def bump_stat(self, name: str, key: str, delta: float = 1) -> None:
        stat = self.stats.setdefault(name, {"calls": 0, "failures": 0, "total_ms": 0, "denied": 0})
        stat[key] = stat.get(key, 0) + delta


def erp_tool(
    name: str,
    description: str,
    args_model: type,
    risk_level: ToolRisk = ToolRisk.READONLY,
    resource: str = "",
    action: str = "read",
    compensation: Optional[ToolCompensation] = None,
    effects_fn: Optional[ToolEffectsFn] = None,
    agent_domain: str = "",
    idempotent: bool = False,
    business_key_field: Optional[str] = None,
    rollback_precheck: Optional[ToolRollbackPrecheck] = None,
    registry: Optional[ToolRegistryV2] = None,
) -> Callable[[ToolHandler], ToolHandler]:
    """
    工具注册装饰器。

    用法::

        class QueryReimbursementsArgs(BaseModel):
            status: str | None = None
            limit: int = 20

        @erp_tool(
            name="reimbursement_query_list",
            description="查询报销单列表，可按状态过滤",
            args_model=QueryReimbursementsArgs,
            risk_level=ToolRisk.READONLY,
            resource="reimbursement", action="read",
            agent_domain="finance",
        )
        async def query_reimbursements(ctx: ToolContext, status: str | None = None, limit: int = 20):
            return ...

    处理器第一个参数固定为 :class:`ToolContext`（由治理面注入）。
    补偿函数签名为 ``(ctx, validated_args, handler_result)``。
    """
    reg = registry if registry is not None else tool_registry_v2

    def decorator(fn: ToolHandler) -> ToolHandler:
        if not inspect.iscoroutinefunction(fn):
            raise ValueError(f"工具处理器必须为 async 函数: {name}")
        reg.register(
            ToolSpecV2(
                name=name,
                description=description,
                handler=fn,
                args_model=args_model,
                risk_level=risk_level,
                resource=resource or "agent",
                action=action,
                compensation=compensation,
                effects_fn=effects_fn,
                agent_domain=agent_domain,
                idempotent=idempotent,
                business_key_field=business_key_field,
                rollback_precheck=rollback_precheck,
            )
        )
        return fn

    return decorator


# 进程内单例
tool_registry_v2 = ToolRegistryV2()
