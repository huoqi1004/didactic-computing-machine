"""LLM 客户端（OpenAI 兼容 · httpx 直连，无 SDK 依赖）。

- ``llm_available()``：是否具备真实调用条件（enabled + 有 key）
- ``llm_chat(messages, system=...)``：单轮补全，返回文本；
  失败时按 ``settings.llm_fallback_to_rules`` 决定返回 ``None`` 还是抛出
- 模块级 ``httpx.AsyncClient`` 复用连接池，进程退出由 ``close()`` 释放
"""
from __future__ import annotations

from typing import Optional

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=settings.llm_timeout,
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def llm_available() -> bool:
    """LLM 是否可用：显式启用 + 已配置密钥。"""
    return bool(settings.llm_enabled and settings.llm_api_key)


async def llm_chat(messages: list[dict], system: str = "") -> Optional[str]:
    """调用 OpenAI 兼容补全接口，返回回复文本。

    :param messages: ``[{"role": "user"|"assistant", "content": ...}]``
    :param system: 可选系统提示词
    :return: 回复文本；LLM 未启用或调用失败且允许降级时返回 ``None``
    :raises RuntimeError: 调用失败且 ``llm_fallback_to_rules=False``
    """
    if not llm_available():
        return None

    payload_messages = ([{"role": "system", "content": system}] if system else []) + messages
    body = {
        "model": settings.llm_model,
        "messages": payload_messages,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }
    try:
        resp = await _get_client().post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return (content or "").strip() or None
    except Exception as e:
        logger.warning("llm.chat_failed", error=str(e), model=settings.llm_model)
        if settings.llm_fallback_to_rules:
            return None
        raise RuntimeError(f"LLM 调用失败: {e}") from e
