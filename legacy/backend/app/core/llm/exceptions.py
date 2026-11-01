"""
LLM异常定义
统一LLM认知引擎中所有异常类型，便于上层做精准降级与错误处理

异常层次：
    LLMError (基类)
    ├── LLMUnavailableError   LLM服务不可用（连接失败、服务宕机、配置缺失等）
    │   ├── LLMTimeoutError    请求超时
    │   └── LLMRateLimitError  速率限制触发
    └── LLMParseError          响应解析失败（JSON解析失败、响应结构异常等）

设计说明：
- LLMUnavailableError 及其子类表示"LLM暂时不可用"，上层应降级到规则模式
- LLMParseError 表示LLM有响应但无法解析，上层可选择重试或降级
- 所有异常均携带可读的 error message，便于 structlog 记录
"""
from typing import Optional


class LLMError(Exception):
    """
    LLM异常基类

    所有LLM认知引擎相关异常的根基类。
    上层代码可捕获此异常作为兜底处理。
    """

    def __init__(self, message: str = "", *, cause: Optional[Exception] = None):
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if self.cause:
            return f"{self.message} (cause: {self.cause})"
        return self.message


class LLMUnavailableError(LLMError):
    """
    LLM服务不可用

    触发场景：
    - LLM认知引擎未启用（settings.llm_enabled = False）
    - 网络连接失败、服务宕机
    - 配置缺失（base_url / api_key / model 未设置）
    - 重试耗尽仍无法获取响应

    上层处理策略：自动降级到规则引擎，记录警告日志
    """


class LLMTimeoutError(LLMUnavailableError):
    """
    LLM请求超时

    触发场景：
    - 请求超过 settings.llm_timeout（默认30秒）
    - 网络延迟过高

    上层处理策略：可重试或降级到规则引擎
    """


class LLMRateLimitError(LLMUnavailableError):
    """
    LLM速率限制触发

    触发场景：
    - 本地令牌桶速率限制（超过 settings.llm_rate_limit 每秒请求数）
    - LLM服务端返回 429 Too Many Requests

    上层处理策略：等待后重试或降级到规则引擎
    """


class LLMParseError(LLMError):
    """
    LLM响应解析失败

    触发场景：
    - LLM返回的内容不是合法JSON
    - 响应结构不符合预期（缺少必要字段）
    - 响应内容为空

    上层处理策略：返回 {"error": "parse_failed", "raw": response} 或降级
    """

    def __init__(
        self,
        message: str = "",
        *,
        raw: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message, cause=cause)
        self.raw = raw  # 原始响应内容，便于调试


__all__ = [
    "LLMError",
    "LLMUnavailableError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMParseError",
]
