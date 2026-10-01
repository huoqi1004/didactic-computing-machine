"""
结构化日志配置
==============

配置 structlog 输出 JSON 格式日志，便于 Loki / ELK 等日志聚合系统采集。

输出格式示例：
{
  "timestamp": "2026-07-30T10:30:00.000Z",
  "level": "info",
  "event": "app.started",
  "logger": "app.main",
  "request_id": "abc123",
  "user_id": "U001",
  "path": "/api/v1/inventory",
  "method": "GET",
  "duration_ms": 42,
  ...
}

部署时配合 Promtail 采集 stdout 日志发送到 Loki。
"""
from __future__ import annotations

import logging
import re
import sys
import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request


# 需要脱敏的字段名模式（不区分大小写）
_SENSITIVE_KEY_PATTERNS = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|credit|card|cvv|ssn)",
    re.IGNORECASE,
)

# 脱敏后的显示值
_MASKED_VALUE = "***REDACTED***"


def _mask_sensitive(logger, method_name, event_dict):
    """structlog 处理器：递归遮蔽敏感字段值"""
    return _mask_dict(event_dict)


def _mask_dict(d: dict) -> dict:
    """递归遮蔽字典中的敏感字段"""
    masked = {}
    for key, value in d.items():
        if isinstance(value, str) and _SENSITIVE_KEY_PATTERNS.search(key):
            masked[key] = _MASKED_VALUE
        elif isinstance(value, dict):
            masked[key] = _mask_dict(value)
        elif isinstance(value, (list, tuple)):
            masked[key] = [
                _mask_dict(v) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            masked[key] = value
    return masked


def configure_logging(debug: bool = False) -> None:
    """
    配置 structlog + 标准 logging 的统一结构化日志。

    :param debug: 调试模式输出更详细日志（含调试级）
    """
    log_level = logging.DEBUG if debug else logging.INFO

    # 共享的 timestamper
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # structlog 处理链
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        _mask_sensitive,  # 敏感字段脱敏
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if debug:
        # 开发模式：彩色控制台输出
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        # 生产模式：JSON 输出（便于 Loki / ELK 采集）
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 同时配置标准 logging 库，使 uvicorn / sqlalchemy 等库的日志也走 structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            timestamper,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # 调整第三方库日志级别
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiomysql").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    请求上下文中间件：为每个请求注入 request_id，并记录请求日志。

    - 生成唯一 request_id 注入 structlog contextvars
    - 记录请求开始/结束日志（含耗时、状态码）
    - 将 request_id 写入响应头 X-Request-Id
    """

    async def dispatch(self, request: Request, call_next):
        # 生成或复用 request_id
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4())[:12])

        # 链路键：续传上游 X-Trace-Id（W3C 兼容 32 hex），无则生成（G1.1）
        from app.core.observability.trace import (
            bind_trace,
            is_valid_trace_id,
            new_trace_id,
        )

        trace_id = request.headers.get("X-Trace-Id", "")
        if not is_valid_trace_id(trace_id):
            trace_id = new_trace_id()

        # 注入 structlog 上下文
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )
        # trace 键双写：module contextvar（get_trace_id() 取值）+ structlog（日志行携带）
        bind_trace(trace_id=trace_id)

        # 从已认证的用户中提取 user_id
        user = getattr(request.state, "user", None)
        if user:
            structlog.contextvars.bind_contextvars(
                user_id=user.get("user_id", ""),
                username=user.get("username", ""),
            )

        logger = structlog.get_logger("request")

        # 记录请求开始
        logger.info("request.started")

        # 执行请求
        import time
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "request.failed",
                duration_ms=duration_ms,
                error_type=type(e).__name__,
                error=str(e),
            )
            raise

        # 记录请求完成
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "request.completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        # 写入响应头
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Trace-Id"] = trace_id
        return response
