"""
全链路追踪上下文（G1.1）
========================

三层键模型（roadmap-logging-sandbox §3.1）：

- **business_key**（根键）：业务单据号，单据全生命周期稳定；
- **trace_id**（链路实例键）：一次交互链路，32 位十六进制
  （与 W3C traceparent 的 trace-id 段同格式，将来切 OpenTelemetry 不改表）；
- request_id / thread_id / entry_id 等既有键复用，不在此管理。

传播规则：

- HTTP 层：中间件续传 ``X-Trace-Id``（无则生成），绑定 contextvars；
- 治理面/工作流：入口 ``ensure_trace_id()`` 复用上下文已有值，无则新建
  （一次业务横跨多个请求属多条 trace，靠 business_key 归组）；
- 跨异步任务边界（事件总线/后台任务）：contextvars 不自动继承，
  用 :func:`trace_context` 显式注入（G1.1 验收点）。

所有键经 ``structlog.contextvars`` 同步绑定，日志行自动携带。
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any, AsyncIterator, Optional
from uuid import uuid4

import structlog

logger = structlog.get_logger()

_trace_id_var: ContextVar[str] = ContextVar("erp_trace_id", default="")
_business_key_var: ContextVar[str] = ContextVar("erp_business_key", default="")

# W3C trace-id 校验：32 位十六进制（全零非法，本模块生成路径不会产生）
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def new_trace_id() -> str:
    """生成 32 位十六进制 trace_id（W3C traceparent 兼容格式）。"""
    return uuid4().hex


def is_valid_trace_id(value: str) -> bool:
    return bool(_TRACE_ID_RE.match(value or ""))


def get_trace_id() -> str:
    """当前上下文的 trace_id（可能为空——未进入任何追踪入口）。"""
    return _trace_id_var.get()


def get_business_key() -> str:
    return _business_key_var.get()


def bind_trace(trace_id: str = "", business_key: str = "") -> None:
    """绑定追踪键到 contextvars + structlog（空值跳过，不清除既有绑定）。"""
    bound: dict[str, str] = {}
    if trace_id:
        _trace_id_var.set(trace_id)
        bound["trace_id"] = trace_id
    if business_key:
        _business_key_var.set(business_key)
        bound["business_key"] = business_key
    if bound:
        structlog.contextvars.bind_contextvars(**bound)


def ensure_trace_id(business_key: str = "") -> str:
    """取当前 trace_id，缺失则新建并绑定（治理面/工作流入口用）。

    幂等：一次请求链路内多次调用返回同一 trace_id。
    """
    trace_id = _trace_id_var.get() or new_trace_id()
    bind_trace(trace_id=trace_id, business_key=business_key)
    return trace_id


def set_business_key(business_key: str) -> None:
    """工具执行产生单据后登记根键（随审计/日志行落列）。"""
    if business_key:
        bind_trace(business_key=business_key)


@asynccontextmanager
async def trace_context(trace_id: str = "", business_key: str = "") -> AsyncIterator[None]:
    """跨异步任务边界的显式追踪注入（P2-2 验收点）。

    contextvars 不随 ``asyncio.create_task`` 自动继承到新任务的**修改**，
    且事件总线/线程池回调解耦后上下文丢失——用本包装在目标协程内重建::

        tid = get_trace_id()
        asyncio.create_task(_run_with(trace_context(tid), coro()))
    """
    trace_id = trace_id or _trace_id_var.get()
    business_key = business_key or _business_key_var.get()
    tokens: list[Token] = [
        _trace_id_var.set(trace_id),
        _business_key_var.set(business_key),
    ]
    if trace_id or business_key:
        structlog.contextvars.bind_contextvars(
            **({"trace_id": trace_id} if trace_id else {}),
            **({"business_key": business_key} if business_key else {}),
        )
    try:
        yield
    finally:
        _trace_id_var.reset(tokens[0])
        _business_key_var.reset(tokens[1])


# 业务单据标识的常见字段名（结果字典启发式提取；显式声明优先）
_BUSINESS_KEY_FIELDS = (
    "reimbursement_id", "transfer_id", "contract_id", "order_id",
    "customer_id", "employee_id", "payroll_id", "structure_id",
    "document_id", "record_id", "request_id", "approval_id",
    "reimbursement_no", "transfer_no", "contract_no", "order_no",
)


def extract_business_key(result: Any, explicit_field: str = "") -> str:
    """从工具执行结果提取业务单据标识（G1.3 登记链路用）。

    显式 ``business_key_field`` 声明优先；否则按常见字段名启发式扫描
    （result 为 dict 时按声明序取首个非空值；嵌套一层 dict 也扫）。
    提取不到返回空串——追踪键缺失是可接受的降级，不阻断业务。
    """
    if not isinstance(result, dict):
        return ""
    candidates = [explicit_field] if explicit_field else []
    candidates += [f for f in _BUSINESS_KEY_FIELDS if f != explicit_field]
    for field in candidates:
        if not field:
            continue
        value = result.get(field)
        if isinstance(value, (str, int)) and str(value):
            return str(value)
    for value in result.values():
        if isinstance(value, dict):
            nested = extract_business_key(value)
            if nested:
                return nested
    return ""
