"""
LLM客户端
对接OpenAI兼容API，支持私有化部署的Qwen-72B/DeepSeek/Llama4等模型

特性：
- 同步与异步调用
- 流式输出（SSE）
- 函数调用 / Tool Use
- 内置重试、超时、速率限制
- 基于httpx异步HTTP客户端
- 配置从settings读取（llm_base_url, llm_model, llm_api_key, llm_temperature,
  llm_max_tokens, llm_timeout, llm_max_retries, llm_rate_limit）

所有网络异常均被捕获并向上抛出LLMUnavailableError（或其子类），
便于上层做降级处理；响应解析失败抛出LLMParseError。
"""
import asyncio
import json
import time
from typing import Any, AsyncIterator, Iterator, Optional

import httpx
import structlog

from app.core.config import settings
from app.core.llm.exceptions import (
    LLMError,
    LLMUnavailableError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMParseError,
)

logger = structlog.get_logger()


# ========== 向后兼容的异常别名 ==========
# exceptions.py 为规范异常定义；此处保留旧名称以兼容现有引用
# （decision_engine.py 等模块仍 import LLMClientError）
LLMClientError = LLMError


class LLMConnectionError(LLMUnavailableError):
    """LLM连接异常（向后兼容，继承自LLMUnavailableError）"""


class LLMResponseError(LLMError):
    """LLM响应异常（向后兼容，继承自LLMError）"""


# 关闭思考模式的透传参数。2026-10-04 真机实测（_tmp_thinking_variants 五路对照）：
# ``{"thinking": {"type": "disabled"}}`` 在本供应商 agnes-3.0-flash 上是**空参数**——
# reasoning_content 照旧 174 片，而 delta.content 一片没有，正文整段落进思考字段；
# ``chat_template_kwargs.enable_thinking=false`` 才真正关闭（reasoning 0 片、正文首片 4.4s）。
NO_THINK_EXTRA: dict = {"chat_template_kwargs": {"enable_thinking": False}}


class LLMClient:
    """
    LLM客户端 - OpenAI兼容API封装

    支持同步/异步/流式/函数调用四种调用模式。
    内置指数退避重试、请求超时、令牌桶速率限制。

    配置优先级（从高到低）：
    1. 构造函数显式参数
    2. settings 全局配置（llm_base_url, llm_model, llm_timeout, ...）
    3. 类内置默认值
    """

    # 默认重试配置
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_BASE_DELAY = 0.5  # 秒
    DEFAULT_RETRY_MAX_DELAY = 8.0   # 秒

    # 默认速率限制：每秒最大请求数（令牌桶填充速率）
    DEFAULT_RATE_LIMIT_PER_SECOND = 10

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        rate_limit: Optional[int] = None,
        # 向后兼容：旧参数名 rate_limit_per_minute
        rate_limit_per_minute: Optional[int] = None,
    ):
        # 从settings读取默认值，允许显式参数覆盖（便于测试与多实例场景）
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.temperature = (
            temperature if temperature is not None else settings.llm_temperature
        )
        self.max_tokens = max_tokens or settings.llm_max_tokens
        # 超时：优先用显式参数，其次读 settings.llm_timeout，最后兜底 30s
        self.timeout = (
            timeout if timeout is not None else float(settings.llm_timeout)
        )
        # 最大重试次数：优先用显式参数，其次读 settings.llm_max_retries
        self.max_retries = (
            max_retries if max_retries is not None else settings.llm_max_retries
        )
        # 速率限制（每秒请求数）：
        #   1. 显式 rate_limit 参数
        #   2. 向后兼容 rate_limit_per_minute（转换为每秒）
        #   3. settings.llm_rate_limit
        if rate_limit is not None:
            self.rate_limit_per_second = rate_limit
        elif rate_limit_per_minute is not None:
            self.rate_limit_per_second = max(1, rate_limit_per_minute // 60)
        else:
            self.rate_limit_per_second = settings.llm_rate_limit

        # 令牌桶速率限制状态
        # 容量 = 每秒速率（允许1秒的突发），填充速率 = rate_limit_per_second tokens/s
        self._token_bucket_capacity = float(self.rate_limit_per_second)
        self._token_bucket_rate = float(self.rate_limit_per_second)
        self._token_bucket_tokens = self._token_bucket_capacity
        self._token_bucket_last = time.monotonic()
        self._rate_lock = asyncio.Lock()

        # 异步客户端（懒加载，避免在构造时建立连接）
        self._async_client: Optional[httpx.AsyncClient] = None
        # 同步客户端（懒加载）
        self._sync_client: Optional[httpx.Client] = None

        logger.info(
            "llm_client.initialized",
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            max_retries=self.max_retries,
            rate_limit_per_second=self.rate_limit_per_second,
        )

    # ========== 客户端生命周期管理 ==========

    def _get_async_client(self) -> httpx.AsyncClient:
        """获取异步HTTP客户端（懒加载）"""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers=self._build_headers(),
                # httpx 默认 keepalive_expiry=5s，撑不到下一轮对话：用户读一眼答案的
                # 功夫连接就被池回收，下一次请求重新 TCP+TLS 握手。流式首 token 归因
                # 实测这段是本侧唯一还能压的开销（100-300ms），拉到 30s 复用连接。
                limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=30.0),
            )
        return self._async_client

    def _get_sync_client(self) -> httpx.Client:
        """获取同步HTTP客户端（懒加载）"""
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers=self._build_headers(),
            )
        return self._sync_client

    def _build_headers(self) -> dict:
        """构建请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def close(self):
        """关闭异步客户端"""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
            self._async_client = None

    async def prewarm(self) -> bool:
        """提前完成 DNS + TCP + TLS 握手，让第一轮对话不再付冷启动的成本。

        只压掉握手这一段（实测约 100-300ms），供应商自己的排队压不掉；
        失败只告警——预热是优化项，不是启动前置条件。
        """
        try:
            client = self._get_async_client()
            await client.get("/models")
            logger.info("llm_client.prewarmed", base_url=self.base_url)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("llm_client.prewarm_failed",
                           base_url=self.base_url, error=str(e)[:200],
                           error_type=type(e).__name__)
            return False

    def close_sync(self):
        """关闭同步客户端"""
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None

    # ========== 速率限制（令牌桶算法） ==========

    async def _check_rate_limit(self):
        """
        异步速率限制检查（令牌桶算法）

        令牌以 rate_limit_per_second 的速率填充，桶容量为 rate_limit_per_second
        （允许1秒的突发请求）。每次请求消耗1个令牌，令牌不足时抛出
        LLMRateLimitError。
        """
        async with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._token_bucket_last
            # 按时间填充令牌
            self._token_bucket_tokens = min(
                self._token_bucket_capacity,
                self._token_bucket_tokens + elapsed * self._token_bucket_rate,
            )
            self._token_bucket_last = now
            if self._token_bucket_tokens < 1.0:
                raise LLMRateLimitError(
                    f"超出速率限制：每秒 {self.rate_limit_per_second} 次请求"
                )
            self._token_bucket_tokens -= 1.0

    def _check_rate_limit_sync(self):
        """同步速率限制检查（令牌桶算法）"""
        now = time.monotonic()
        elapsed = now - self._token_bucket_last
        self._token_bucket_tokens = min(
            self._token_bucket_capacity,
            self._token_bucket_tokens + elapsed * self._token_bucket_rate,
        )
        self._token_bucket_last = now
        if self._token_bucket_tokens < 1.0:
            raise LLMRateLimitError(
                f"超出速率限制：每秒 {self.rate_limit_per_second} 次请求"
            )
        self._token_bucket_tokens -= 1.0

    # ========== 异步调用 ==========

    async def chat_async(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[Any] = None,
        response_format: Optional[dict] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """
        异步聊天补全

        参数：
        - messages: OpenAI格式消息列表 [{"role": "system", "content": "..."}, ...]
        - tools: 函数调用工具定义（OpenAI function calling 格式）
        - tool_choice: 工具选择策略（"auto" / "none" / 具体函数）
        - response_format: 响应格式（如 {"type": "json_object"}）
        - extra: 其他透传给API的参数

        返回：原始响应JSON（OpenAI ChatCompletion格式）
        """
        payload = self._build_payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            extra=extra,
        )

        await self._check_rate_limit()

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._get_async_client()
                response = await client.post("/chat/completions", json=payload)
                self._raise_for_status(response)
                data = response.json()
                logger.debug(
                    "llm_client.async_success",
                    model=payload.get("model"),
                    attempt=attempt,
                    tokens=data.get("usage", {}).get("total_tokens"),
                )
                return data
            except LLMRateLimitError:
                # 速率限制直接抛出，不重试
                raise
            except (LLMTimeoutError, LLMConnectionError) as e:
                last_error = e
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "llm_client.async_retry",
                    attempt=attempt,
                    error=str(e),
                    delay=delay,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPStatusError as e:
                # 5xx可重试，4xx不重试
                if e.response.status_code >= 500:
                    last_error = LLMResponseError(
                        f"服务端错误 {e.response.status_code}: {e.response.text}"
                    )
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        "llm_client.async_retry_5xx",
                        attempt=attempt,
                        status=e.response.status_code,
                        delay=delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise LLMResponseError(
                        f"客户端错误 {e.response.status_code}: {e.response.text}"
                    ) from e
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = (
                    LLMTimeoutError(f"请求超时: {e}")
                    if isinstance(e, httpx.TimeoutException)
                    else LLMConnectionError(f"连接失败: {e}")
                )
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "llm_client.async_retry",
                    attempt=attempt,
                    error=str(last_error),
                    delay=delay,
                )
                await asyncio.sleep(delay)
            except Exception as e:
                last_error = LLMUnavailableError(f"未知LLM调用异常: {e}", cause=e)
                logger.error("llm_client.async_unknown_error", error=str(e))
                raise last_error

        # 重试耗尽：抛出LLMUnavailableError，便于上层降级
        if last_error is None:
            last_error = LLMUnavailableError("LLM调用失败：重试耗尽")
        elif not isinstance(last_error, LLMUnavailableError):
            # 将非Unavailable异常包装为LLMUnavailableError
            last_error = LLMUnavailableError(str(last_error), cause=last_error)
        logger.warning(
            "llm_client.async_retries_exhausted",
            max_retries=self.max_retries,
            error=str(last_error),
        )
        raise last_error

    async def chat_async_simple(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        异步简化调用：传入prompt字符串，返回纯文本响应

        参数：
        - prompt: 用户输入
        - system: 可选系统提示
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = await self.chat_async(messages, **kwargs)
        return self._extract_content(data)

    async def chat_async_json(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """
        异步JSON调用：返回解析后的JSON对象
        自动启用response_format=json_object
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs.setdefault("response_format", {"type": "json_object"})
        data = await self.chat_async(messages, **kwargs)
        content = self._extract_content(data)
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMParseError(
                f"LLM响应非合法JSON: {e}; 原始内容: {content[:500]}",
                raw=content,
                cause=e,
            ) from e

    async def chat_async_stream(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra: Optional[dict] = None,
        scene: str = "stream",
        cost_domain: str = "",
    ) -> AsyncIterator[str]:
        """
        异步流式输出 - 逐个token返回

        用法：
            async for chunk in client.chat_async_stream(messages):
                print(chunk, end="")

        末帧 usage 不返回给调用方，直接按域计入运行时日累计——流式流量与
        chat_completion 走同一套成本口径（裁决 D-E），否则流式上线后成本可见性断档。
        """
        payload = self._build_payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
        )
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        await self._check_rate_limit()

        client = self._get_async_client()
        # 正文分片可能在思考之后才开始（甚至整轮都没有），思考分片先攒着：
        # 收流时若正文为空，就把思考内容整体补出，与 _extract_content 的回退口径一致，
        # 否则调用方看到空串会误判"模型没答"而落降级。
        content_seen = False
        reasoning_tail: list[str] = []
        try:
            async with client.stream(
                "POST", "/chat/completions", json=payload
            ) as response:
                self._raise_for_status(response)
                async for line in response.aiter_lines():
                    chunk, reasoning, usage = self._parse_sse_chunk(line)
                    if usage:
                        self._record_cost_domain(scene=scene,
                                                 cost_domain=cost_domain,
                                                 usage=usage)
                        logger.info(
                            "llm_client.chat_stream_usage",
                            model=self.model, scene=scene,
                            total_tokens=usage.get("total_tokens"),
                        )
                    if chunk:
                        content_seen = True
                        yield chunk
                    elif reasoning:
                        reasoning_tail.append(reasoning)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise (
                LLMTimeoutError(f"流式请求超时: {e}")
                if isinstance(e, httpx.TimeoutException)
                else LLMConnectionError(f"流式连接失败: {e}")
            )
        if not content_seen and reasoning_tail:
            fallback_text = "".join(reasoning_tail)
            logger.info(
                "llm_client.chat_stream_content_from_reasoning",
                model=self.model, scene=scene, chars=len(fallback_text),
            )
            yield fallback_text

    async def function_call_async(
        self,
        prompt: str,
        tools: list[dict],
        *,
        system: Optional[str] = None,
        tool_choice: Any = "auto",
        **kwargs,
    ) -> dict:
        """
        异步函数调用 - 让LLM选择并调用工具

        参数：
        - prompt: 用户输入
        - tools: OpenAI格式函数定义
            [{"type": "function", "function": {"name": "...", "description": "...",
              "parameters": {...}}}]
        - tool_choice: "auto" / "none" / {"type": "function", "function": {"name": "..."}}

        返回：包含content和tool_calls的字典
        {
            "content": str | None,
            "tool_calls": [{"id": "...", "name": "...", "arguments": {...}}]
        }
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = await self.chat_async(
            messages, tools=tools, tool_choice=tool_choice, **kwargs
        )
        return self._extract_tool_calls(data)

    # ========== 标准化接口（Agent基类调用入口） ==========

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
        extra: Optional[dict] = None,
        scene: str = "default",
        cost_domain: str = "",
    ) -> str:
        """
        异步聊天补全 - 返回纯文本响应

        这是Agent基类 BaseAgent.llm_chat() 的底层调用入口。
        自动应用 settings 中的默认 temperature / max_tokens。

        参数：
        - messages: OpenAI格式消息列表
            [{"role": "system", "content": "..."},
             {"role": "user", "content": "..."}]
        - temperature: 采样温度（None=使用默认 settings.llm_temperature）
        - max_tokens: 最大生成token数（None=使用默认 settings.llm_max_tokens）
        - response_format: 响应格式（如 {"type": "json_object"} 启用JSON模式）
        - extra: 额外透传参数（如 {"thinking": {"type": "disabled"}} 禁用思考模式）

        返回：LLM生成的文本内容（str）

        异常：
        - LLMUnavailableError: LLM服务不可用（连接失败/超时/重试耗尽）
        - LLMRateLimitError: 速率限制触发
        - LLMError: 其他LLM错误
        """
        start_time = time.monotonic()
        try:
            data = await self.chat_async(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                extra=extra,
            )
            content = self._extract_content(data)
            elapsed = time.monotonic() - start_time
            usage = data.get("usage", {})
            logger.info(
                "llm_client.chat_completion",
                model=self.model,
                scene=scene,
                elapsed_ms=round(elapsed * 1000, 2),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                success=True,
            )
            # 裁决 D-E：创作类长文本 token 消耗必须可归集——scene 标签进日志，
            # 实际 token 数按域计入运行时日累计（P2-7 全成本报表口径的数据源）。
            # 计量失败绝不允许打断模型调用本身（对话可用性优先于成本统计）。
            self._record_cost_domain(scene=scene, cost_domain=cost_domain, usage=usage)
            return content
        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.warning(
                "llm_client.chat_completion",
                model=self.model,
                elapsed_ms=round(elapsed * 1000, 2),
                success=False,
                error=str(e),
            )
            raise

    async def chat_with_functions(
        self,
        messages: list[dict],
        functions: list[dict],
        temperature: Optional[float] = None,
    ) -> dict:
        """
        异步函数调用 - 让LLM选择并调用函数

        这是Agent基类进行工具调用 / 函数调用的底层入口。
        兼容OpenAI Function Calling 格式。

        参数：
        - messages: OpenAI格式消息列表
        - functions: 函数定义列表（OpenAI tools格式）
            [{"type": "function",
              "function": {"name": "...", "description": "...",
                            "parameters": {...}}}]
        - temperature: 采样温度（None=使用默认）

        返回：包含 content 和 tool_calls 的字典
        {
            "content": str | None,           # LLM文本回复（可能为空）
            "tool_calls": [                   # LLM选择的函数调用
                {"id": "...", "name": "...", "arguments": {...}}
            ]
        }

        异常：
        - LLMUnavailableError: LLM服务不可用
        - LLMRateLimitError: 速率限制
        - LLMError: 其他错误
        """
        start_time = time.monotonic()
        try:
            data = await self.chat_async(
                messages,
                tools=functions,
                tool_choice="auto",
                temperature=temperature,
            )
            result = self._extract_tool_calls(data)
            elapsed = time.monotonic() - start_time
            logger.info(
                "llm_client.chat_with_functions",
                model=self.model,
                elapsed_ms=round(elapsed * 1000, 2),
                tool_call_count=len(result.get("tool_calls", [])),
                success=True,
            )
            return result
        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.warning(
                "llm_client.chat_with_functions",
                model=self.model,
                elapsed_ms=round(elapsed * 1000, 2),
                success=False,
                error=str(e),
            )
            raise

    # ========== 同步调用 ==========

    def chat_sync(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[Any] = None,
        response_format: Optional[dict] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """同步聊天补全（接口与chat_async一致）"""
        payload = self._build_payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            extra=extra,
        )

        self._check_rate_limit_sync()

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._get_sync_client()
                response = client.post("/chat/completions", json=payload)
                self._raise_for_status(response)
                data = response.json()
                logger.debug(
                    "llm_client.sync_success",
                    model=payload.get("model"),
                    attempt=attempt,
                )
                return data
            except LLMRateLimitError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = (
                    LLMTimeoutError(f"请求超时: {e}")
                    if isinstance(e, httpx.TimeoutException)
                    else LLMConnectionError(f"连接失败: {e}")
                )
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "llm_client.sync_retry", attempt=attempt, delay=delay
                )
                time.sleep(delay)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    last_error = LLMResponseError(
                        f"服务端错误 {e.response.status_code}"
                    )
                    time.sleep(self._backoff_delay(attempt))
                else:
                    raise LLMResponseError(
                        f"客户端错误 {e.response.status_code}: {e.response.text}"
                    ) from e
            except Exception as e:
                raise LLMUnavailableError(f"未知LLM调用异常: {e}", cause=e) from e

        # 重试耗尽
        if last_error is None:
            last_error = LLMUnavailableError("LLM调用失败：重试耗尽")
        elif not isinstance(last_error, LLMUnavailableError):
            last_error = LLMUnavailableError(str(last_error), cause=last_error)
        raise last_error

    def chat_sync_simple(self, prompt: str, *, system: Optional[str] = None, **kwargs) -> str:
        """同步简化调用"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        data = self.chat_sync(messages, **kwargs)
        return self._extract_content(data)

    def chat_sync_json(self, prompt: str, *, system: Optional[str] = None, **kwargs) -> dict:
        """同步JSON调用"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs.setdefault("response_format", {"type": "json_object"})
        data = self.chat_sync(messages, **kwargs)
        content = self._extract_content(data)
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMParseError(
                f"LLM响应非合法JSON: {e}; 原始内容: {content[:500]}",
                raw=content,
                cause=e,
            ) from e

    # ========== 内部辅助方法 ==========

    def _build_payload(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[Any] = None,
        response_format: Optional[dict] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """构建请求体"""
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format

        # DeepSeek V4 思考模式：通过 extra body 传入 thinking 参数
        # V4-Pro 默认开启思考模式；显式控制以确保行为一致
        # 注意：思考模式下 temperature/top_p 等参数不生效，由模型自主控制推理深度
        if hasattr(settings, "llm_thinking_enabled") and settings.llm_thinking_enabled:
            thinking_type = "enabled"
        else:
            thinking_type = "disabled"
        payload["thinking"] = {"type": thinking_type}

        if extra:
            payload.update(extra)
        return payload

    def _backoff_delay(self, attempt: int) -> float:
        """指数退避延迟（带最大值限制）"""
        delay = self.DEFAULT_RETRY_BASE_DELAY * (2 ** (attempt - 1))
        return min(delay, self.DEFAULT_RETRY_MAX_DELAY)

    def _raise_for_status(self, response: httpx.Response):
        """根据HTTP状态码抛出对应异常"""
        if response.status_code == 429:
            raise LLMRateLimitError("LLM服务返回429速率限制")
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code}",
                request=response.request,
                response=response,
            )

    def _extract_content(self, data: dict) -> str:
        """
        从响应中提取文本内容

        DeepSeek 思考模式下，响应可能包含两个字段：
        - content: 最终回答（可能为空，如果模型将所有token用于推理）
        - reasoning_content: 推理过程

        当 content 为空时，回退到 reasoning_content，确保不丢失响应。
        """
        try:
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            if content:
                return content
            # 思考模式下 content 可能为空，回退到 reasoning_content
            reasoning = message.get("reasoning_content") or ""
            if reasoning:
                logger.debug("llm_client.content_fallback_to_reasoning",
                             reasoning_length=len(reasoning))
                return reasoning
            return ""
        except (KeyError, IndexError) as e:
            raise LLMResponseError(
                f"无法从LLM响应中提取content: {e}; 原始: {str(data)[:500]}"
            ) from e

    def _extract_tool_calls(self, data: dict) -> dict:
        """从响应中提取函数调用信息"""
        try:
            message = data["choices"][0]["message"]
            content = message.get("content")
            tool_calls_raw = message.get("tool_calls") or []
            tool_calls = []
            for tc in tool_calls_raw:
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {"_raw": args_str}
                tool_calls.append(
                    {
                        "id": tc.get("id"),
                        "name": fn.get("name"),
                        "arguments": args,
                    }
                )
            return {"content": content, "tool_calls": tool_calls}
        except (KeyError, IndexError) as e:
            raise LLMResponseError(
                f"无法提取tool_calls: {e}; 原始: {str(data)[:500]}"
            ) from e

    def _record_cost_domain(self, *, scene: str, cost_domain: str,
                            usage: dict) -> None:
        """按域计入运行时日累计（P2-7 全成本报表口径的数据源）。

        计量失败绝不允许打断模型调用本身——对话可用性优先于成本统计。
        """
        if not cost_domain:
            return
        tokens = int((usage or {}).get("total_tokens") or 0)
        if tokens <= 0:
            return
        try:
            from app.core.agent.runtime import agent_runtime

            agent_runtime._record_tokens(cost_domain, tokens)
        except Exception as e:  # noqa: BLE001
            logger.warning("llm_client.token_accounting_failed",
                           scene=scene, domain=cost_domain, error=str(e))

    def _parse_sse_chunk(self, line: str) -> tuple[Optional[str], Optional[str], Optional[dict]]:
        """解析 SSE 行，返回（正文增量, 思考增量, usage）。usage 只在末帧出现。"""
        if not line or not line.startswith("data: "):
            return None, None, None
        payload = line[len("data: "):].strip()
        if payload == "[DONE]":
            return None, None, None
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return None, None, None
        delta = (obj.get("choices") or [{}])[0].get("delta", {}) or {}
        return delta.get("content"), delta.get("reasoning_content"), obj.get("usage")

    def _parse_sse_line(self, line: str) -> Optional[str]:
        """解析SSE流式响应行，返回增量文本（无内容则返回None）"""
        return self._parse_sse_chunk(line)[0]


# 全局LLM客户端单例（懒加载，仅在首次访问时创建）
_llm_client_instance: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局LLM客户端单例"""
    global _llm_client_instance
    if _llm_client_instance is None:
        _llm_client_instance = LLMClient()
    return _llm_client_instance


# 模块级单例（保持与其他模块一致的命名约定）
# 注意：此单例仅在显式import时创建，不依赖网络连接
llm_client = LLMClient()
