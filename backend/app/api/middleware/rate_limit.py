"""
API 限流中间件
==============

基于滑动窗口 + 令牌桶算法的多级限流，保护后端 API 免受恶意请求冲击。

限流策略：
- 全局限流：每 IP 每分钟最多 120 次请求（通用保护）
- 认证限流：登录/刷新接口每 IP 每分钟最多 10 次（防暴力破解）
- 写操作限流：POST/PUT/DELETE 每 IP 每分钟最多 60 次（防滥用）
- LLM 接口限流：/conversation/chat 每用户每分钟最多 10 次（成本控制）

超限时返回 429 Too Many Requests，携带 Retry-After 头。

设计要点：
- 使用进程内 Dict 存储（单实例部署足够；多实例需替换为 Redis）
- 滑动窗口算法：记录最近 N 秒内的请求时间戳，超出阈值则拒绝
- 异步清理过期条目，避免内存泄漏
- 公开端点（/health, /metrics）不限流
"""
from __future__ import annotations

import time
import asyncio
from collections import defaultdict, deque
from typing import Optional

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

logger = structlog.get_logger()


class RateLimitRule:
    """单条限流规则。"""

    def __init__(
        self,
        name: str,
        max_requests: int,
        window_seconds: int,
        path_pattern: str = "",
        methods: Optional[set[str]] = None,
        key_func: Optional[str] = None,
    ):
        """
        :param name: 规则名称（用于日志和响应头）
        :param max_requests: 窗口内最大请求数
        :param window_seconds: 窗口大小（秒）
        :param path_pattern: 路径前缀匹配（空字符串=匹配所有）
        :param methods: 限制的 HTTP 方法集合（None=所有方法）
        :param key_func: 限流 key 来源 ("ip" 或 "user")
        """
        self.name = name
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.path_pattern = path_pattern
        self.methods = methods or set()
        self.key_func = key_func or "ip"

    def matches(self, path: str, method: str) -> bool:
        """检查请求是否匹配此规则。"""
        if self.path_pattern and not path.startswith(self.path_pattern):
            return False
        if self.methods and method not in self.methods:
            return False
        return True


class RateLimiter:
    """滑动窗口限流器。"""

    def __init__(self):
        # key (rule_name + client_key) → deque[timestamp]
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._cleanup_counter = 0

    async def check(self, rule: RateLimitRule, client_key: str) -> tuple[bool, int]:
        """
        检查请求是否允许通过。

        :return: (allowed, retry_after_seconds)
        """
        bucket_key = f"{rule.name}:{client_key}"
        now = time.monotonic()
        cutoff = now - rule.window_seconds

        async with self._lock:
            bucket = self._buckets[bucket_key]

            # 清除过期时间戳
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= rule.max_requests:
                # 计算需要等待的时间
                oldest = bucket[0] if bucket else now
                retry_after = int(oldest + rule.window_seconds - now) + 1
                return False, max(retry_after, 1)

            bucket.append(now)

            # 定期清理空桶（每 1000 次检查执行一次）
            self._cleanup_counter += 1
            if self._cleanup_counter >= 1000:
                self._cleanup_counter = 0
                self._cleanup_empty_buckets()

            return True, 0

    def _cleanup_empty_buckets(self):
        """清理空桶，避免内存泄漏。"""
        empty_keys = [k for k, v in self._buckets.items() if not v]
        for k in empty_keys:
            del self._buckets[k]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    API 限流中间件。

    安装顺序：在 AuthMiddleware 之后（需要 request.state.user），
    在 PermissionMiddleware 之前。
    """

    # 限流规则（按优先级排列，先匹配的先生效）
    RULES = [
        # 登录接口：防暴力破解
        RateLimitRule(
            name="auth",
            max_requests=10,
            window_seconds=60,
            path_pattern="/api/v1/auth",
            methods={"POST"},
            key_func="ip",
        ),
        # LLM 对话接口：成本控制（每用户每分钟 10 次）
        RateLimitRule(
            name="llm_chat",
            max_requests=10,
            window_seconds=60,
            path_pattern="/api/v1/conversation/chat",
            methods={"POST"},
            key_func="user",
        ),
        # 文档生成接口：资源密集型（每用户每分钟 10 次）
        RateLimitRule(
            name="doc_gen",
            max_requests=10,
            window_seconds=60,
            path_pattern="/api/v1/document/generate",
            methods={"POST"},
            key_func="user",
        ),
        # 写操作：防滥用
        RateLimitRule(
            name="write_ops",
            max_requests=60,
            window_seconds=60,
            methods={"POST", "PUT", "PATCH", "DELETE"},
            key_func="ip",
        ),
        # 全局限流：通用保护
        RateLimitRule(
            name="global",
            max_requests=120,
            window_seconds=60,
            key_func="ip",
        ),
    ]

    # 不限流的路径
    EXEMPT_PATHS = {
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/",
    }

    def __init__(self, app):
        super().__init__(app)
        self._limiter = RateLimiter()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # 豁免路径直接放行
        if path in self.EXEMPT_PATHS or path.startswith("/api/v1/health"):
            return await call_next(request)

        # 获取客户端标识
        client_ip = self._get_client_ip(request)
        user_id = getattr(request.state, "user", None)
        user_key = str(user_id.get("user_id", "")) if user_id else client_ip

        # 按规则顺序检查
        for rule in self.RULES:
            if not rule.matches(path, method):
                continue

            key = user_key if rule.key_func == "user" else client_ip
            allowed, retry_after = await self._limiter.check(rule, key)

            if not allowed:
                logger.warning(
                    "rate_limited",
                    rule=rule.name,
                    client_ip=client_ip,
                    user_id=user_key,
                    path=path,
                    method=method,
                    retry_after=retry_after,
                )
                return JSONResponse(
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "detail": f"请求过于频繁，请 {retry_after} 秒后重试",
                        "retry_after": retry_after,
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Rule": rule.name,
                    },
                )

        return await call_next(request)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """获取客户端真实 IP（支持反向代理）。"""
        # 优先从 X-Forwarded-For 获取（反向代理场景）
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        # 其次从 X-Real-IP 获取
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        # 回退到直连 IP
        return request.client.host if request.client else "unknown"
