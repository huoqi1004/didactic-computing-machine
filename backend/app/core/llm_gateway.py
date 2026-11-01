"""模型网关：按 ModelProvider 路由（OpenAI/Claude 双协议）+ token 计量与额度。

- ``get_default_provider(session)``：is_default 优先，否则首个 enabled
- ``check_quota(session, username)``：额度停用/超限 → 抛 ``QuotaError``
- ``provider_chat(session, provider, messages, system, username)``：
  按协议调用并记录 token 用量，返回 ``(reply_text, tokens_used)``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog
from sqlalchemy import select

from app.models import ModelProvider, TokenQuota, TokenUsage

logger = structlog.get_logger()


class QuotaError(Exception):
    """用户 token 日额度已用尽或被停用。"""


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


async def get_default_provider(session) -> Optional[ModelProvider]:
    row = (await session.execute(
        select(ModelProvider).where(ModelProvider.is_default == True,  # noqa: E712
                                    ModelProvider.enabled == True)  # noqa: E712
        .limit(1))).scalar_one_or_none()
    if row is not None:
        return row
    return (await session.execute(
        select(ModelProvider).where(ModelProvider.enabled == True)  # noqa: E712
        .order_by(ModelProvider.id).limit(1))).scalar_one_or_none()


async def get_provider(session, model_id: int) -> Optional[ModelProvider]:
    if model_id <= 0:
        return await get_default_provider(session)
    row = (await session.execute(
        select(ModelProvider).where(ModelProvider.id == model_id))).scalar_one_or_none()
    return row if (row is not None and row.enabled) else await get_default_provider(session)


async def get_quota(session, username: str) -> dict:
    """用户当日用量/额度概览（无记录视为默认 100000）。"""
    q = (await session.execute(
        select(TokenQuota).where(TokenQuota.username == username))).scalar_one_or_none()
    limit = q.daily_limit if q else 100000
    enabled = q.enabled if q else True
    used_rows = (await session.execute(
        select(TokenUsage.tokens).where(TokenUsage.username == username,
                                        TokenUsage.usage_date == _today()))).scalars().all()
    used = sum(used_rows)
    return {"limit": limit, "used": used, "remaining": max(0, limit - used),
            "enabled": enabled}


async def check_quota(session, username: str) -> dict:
    info = await get_quota(session, username)
    if not info["enabled"]:
        raise QuotaError("您的模型使用额度已被停用，请联系系统管理员")
    if info["remaining"] <= 0:
        raise QuotaError("今日 token 额度已用尽，请明日再试或联系管理员调整")
    return info


async def record_usage(session, username: str, model_name: str, tokens: int) -> None:
    if tokens <= 0:
        return
    session.add(TokenUsage(username=username, model_name=model_name,
                           tokens=tokens, usage_date=_today()))
    await session.flush()


async def provider_chat(session, provider: ModelProvider,
                        messages: list[dict], system: str,
                        username: str) -> tuple[str, int]:
    """按协议调用模型并计量 token。返回 (回复文本, 本次 tokens)。"""
    quota = await check_quota(session, username)
    timeout = httpx.Timeout(60.0, connect=10.0)
    headers = {}
    if provider.protocol == "claude":
        headers = {"x-api-key": provider.api_key,
                   "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        body = {"model": provider.model_name, "max_tokens": 4096,
                "messages": messages}
        if system:
            body["system"] = system
        url = provider.base_url.rstrip("/") + "/v1/messages"
    else:  # openai 兼容
        headers = {"Authorization": f"Bearer {provider.api_key}",
                   "content-type": "application/json"}
        payload_messages = ([{"role": "system", "content": system}] if system
                            else []) + messages
        body = {"model": provider.model_name, "messages": payload_messages}
        url = provider.base_url.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning("gateway.provider_http_error", status=e.response.status_code,
                       provider=provider.name)
        raise RuntimeError(f"模型服务返回错误（{e.response.status_code}）") from e
    except Exception as e:
        logger.warning("gateway.provider_call_failed", provider=provider.name,
                       error=str(e))
        raise RuntimeError("模型服务暂不可用，请稍后重试或切换模型") from e

    if provider.protocol == "claude":
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks
                       if b.get("type") == "text").strip()
        usage = data.get("usage") or {}
        tokens = int(usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
    else:
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        tokens = int((data.get("usage") or {}).get("total_tokens", 0))

    if not text:
        raise RuntimeError("模型返回了空回复")
    # 超额保护：本次用量若超出剩余额度也记账（不截断，仅告警性记录）
    if tokens > quota["remaining"]:
        logger.warning("gateway.quota_exceeded_this_call",
                       username=username, tokens=tokens, remaining=quota["remaining"])
    await record_usage(session, username, provider.model_name, tokens)
    return text, tokens
