"""
业务指标模块（Prometheus 自定义指标）

定义 ERP 系统核心业务指标，通过 /metrics 端点暴露给 Prometheus 采集。

指标分类：
1. 事件总线指标 - 发布/消费/死信/滞后
2. Agent 执行指标 - 执行次数/耗时/成功率/失败率
3. Saga 协调器指标 - 事务完成/补偿/超时
4. LLM 调用指标 - 请求次数/耗时/token 使用量
5. 安全指标 - 认证失败/权限拒绝/审计告警
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, Info

# ===================================================================
# 1. 事件总线指标
# ===================================================================

EVENT_BUS_PUBLISHED = Counter(
    "erp_event_bus_published_total",
    "事件总线发布总数",
    ["event_type", "implementation"],
)

EVENT_BUS_DELIVERED = Counter(
    "erp_event_bus_delivered_total",
    "事件总线成功投递总数",
    ["event_type"],
)

EVENT_BUS_FAILED = Counter(
    "erp_event_bus_failed_total",
    "事件总线投递失败总数",
    ["event_type", "failure_reason"],
)

EVENT_BUS_DLQ = Counter(
    "erp_event_bus_dlq_total",
    "死信队列发布总数",
)

EVENT_BUS_LAG = Gauge(
    "erp_event_bus_lag",
    "消费者滞后量（最近估算）",
)

EVENT_BUS_IN_FLIGHT = Gauge(
    "erp_event_bus_in_flight",
    "当前在途消息数",
)

EVENT_BUS_DEGRADED = Gauge(
    "erp_event_bus_degraded",
    "事件总线是否降级到内存模式 (1=降级, 0=正常)",
)

# ===================================================================
# 2. Agent 执行指标
# ===================================================================

AGENT_EXECUTIONS = Counter(
    "erp_agent_executions_total",
    "Agent 执行总数",
    ["agent_id", "agent_type", "status"],  # status: success / failure / timeout
)

AGENT_EXECUTION_DURATION = Histogram(
    "erp_agent_execution_duration_seconds",
    "Agent 执行耗时（秒）",
    ["agent_id", "agent_type"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

AGENT_ACTIVE = Gauge(
    "erp_agent_active",
    "当前活跃 Agent 数量",
)

AGENT_STATE = Gauge(
    "erp_agent_state",
    "Agent 状态 (0=stopped, 1=running, 2=error)",
    ["agent_id"],
)

CUSTOMIZATION_DEGRADED = Gauge(
    "erp_customization_degraded",
    "定制层降级 (1=读不到定制层，按内置默认或旧缓存运行, 0=正常)",
)

# ===================================================================
# 3. Saga 协调器指标
# ===================================================================

SAGA_COMPLETED = Counter(
    "erp_saga_completed_total",
    "Saga 事务完成总数",
    ["saga_type", "result"],  # result: committed / rolled_back / timed_out
)

SAGA_DURATION = Histogram(
    "erp_saga_duration_seconds",
    "Saga 事务耗时（秒）",
    ["saga_type"],
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

SAGA_COMPENSATIONS = Counter(
    "erp_saga_compensations_total",
    "Saga 补偿操作执行总数",
    ["saga_type", "step"],
)

# ===================================================================
# 4. LLM 调用指标
# ===================================================================

LLM_REQUESTS = Counter(
    "erp_llm_requests_total",
    "LLM 请求总数",
    ["model", "status"],  # status: success / failure / rate_limited / timeout
)

LLM_DURATION = Histogram(
    "erp_llm_duration_seconds",
    "LLM 请求耗时（秒）",
    ["model"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

LLM_TOKENS = Counter(
    "erp_llm_tokens_total",
    "LLM Token 使用量",
    ["model", "type"],  # type: prompt / completion
)

# ===================================================================
# 5. 安全指标
# ===================================================================

AUTH_FAILURES = Counter(
    "erp_auth_failures_total",
    "认证失败总数",
    ["reason"],  # reason: invalid_token / expired / wrong_password / rate_limited
)

PERMISSION_DENIED = Counter(
    "erp_permission_denied_total",
    "权限拒绝总数",
    ["resource", "action"],
)

AUDIT_ALERTS = Counter(
    "erp_audit_alerts_total",
    "审计告警总数",
    ["severity"],
)

# ===================================================================
# 6. 系统信息
# ===================================================================

APP_INFO = Info(
    "erp_app",
    "应用信息",
)

# 初始化应用信息
APP_INFO.info({
    "version": "1.0.0",
    "framework": "fastapi",
    "architecture": "agent-native",
})
