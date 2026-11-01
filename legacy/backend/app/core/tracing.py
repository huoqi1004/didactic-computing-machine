"""
分布式追踪模块（OpenTelemetry）

提供：
1. 自动 instrumentation FastAPI / HTTPX / SQLAlchemy / Redis
2. OTLP exporter 发送 span 到 Jaeger / Tempo / OTel Collector
3. 优雅降级：opentelemetry 未安装时自动跳过

启用方式（环境变量）::
    ERP_OTEL_ENABLED=true
    ERP_OTEL_ENDPOINT=http://localhost:4317
    ERP_OTEL_SERVICE_NAME=erp-backend
"""
from __future__ import annotations

from typing import Optional

import structlog

from app.core.config import settings

logger = structlog.get_logger()

# OpenTelemetry 是可选依赖
_OTEL_AVAILABLE = False
try:  # pragma: no cover
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    _OTEL_AVAILABLE = True
except ImportError:  # noqa: BLE001
    pass


_tracer_provider: Optional[object] = None
_initialized: bool = False


def init_tracing(app=None) -> bool:
    """
    初始化 OpenTelemetry 追踪。

    - 设置 TracerProvider 和 OTLP exporter
    - 自动 instrument FastAPI（如果传入 app）
    - 自动 instrument HTTPX / SQLAlchemy / Redis

    返回 ``True`` 表示成功初始化，``False`` 表示不可用（依赖未安装或未启用）。
    """
    global _tracer_provider, _initialized

    if _initialized:
        return _tracer_provider is not None
    _initialized = True

    if not _OTEL_AVAILABLE:
        logger.info("tracing.disabled", reason="opentelemetry_not_installed")
        return False

    if not settings.otel_enabled:
        logger.info("tracing.disabled", reason="otel_enabled=False")
        return False

    try:
        resource = Resource.create({
            "service.name": settings.otel_service_name,
            "service.version": settings.app_version,
            "deployment.environment": "production" if not settings.debug else "development",
        })

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(
            endpoint=settings.otel_endpoint,
            insecure=True,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer_provider = provider

        # 自动 instrumentation
        if app is not None:
            FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        RedisInstrumentor().instrument()

        logger.info("tracing.initialized",
                     service_name=settings.otel_service_name,
                     endpoint=settings.otel_endpoint)
        return True

    except Exception as e:  # noqa: BLE001
        logger.warning("tracing.init_failed", error=str(e), error_type=type(e).__name__)
        return False


def get_tracer():
    """获取全局 tracer（未初始化时返回 noop tracer）"""
    if _OTEL_AVAILABLE and _tracer_provider is not None:
        return trace.get_tracer(__name__)
    return None


def add_span_attributes(**kwargs):
    """向当前 span 添加属性（无活跃 span 时 noop）"""
    if not _OTEL_AVAILABLE or _tracer_provider is None:
        return
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attributes(kwargs)
