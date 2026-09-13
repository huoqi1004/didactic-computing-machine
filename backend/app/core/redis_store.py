"""
Redis 安全访问层（评估报告 A-01 / 路线图任务 2.5、2.6）
========================================================

为会话、令牌吊销、限流计数、RBAC 版本信号提供 Redis 读写助手。

设计要点（借鉴 LiteLLM 网关的 fail-open 模式）：
1. **降级不放大**：Redis 不可用时所有助手返回安全的默认值
   （读→未命中，写→忽略），调用方继续走进程内内存路径，
   单实例行为与 Redis 落地前完全一致；
2. **降级必须显式告警**：吸取 C-01"静默降级掩盖失败"的教训，
   失败告警按类别节流（60 秒一次），持续失败时日志可感知；
3. **多实例一致性**：会话/黑名单/限流计数以 Redis 为共享存储，
   任一实例的登录、登出、限流计数对所有实例可见；
4. **键安全**：令牌明文不落 Redis，键使用 SHA-256 摘要。

键空间约定：
- ``erp:sess:<token_hash>``  会话 JSON（TTL=会话剩余有效期）
- ``erp:bl:<token_hash>``    令牌吊销黑名单（TTL=7天）
- ``erp:rl:<rule>:<key>:<window>``  限流固定窗口计数（TTL=窗口大小）
- ``erp:rbac:version``       RBAC 数据版本（INCR 信号，触发跨实例重载）
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

import structlog

logger = structlog.get_logger()

# 失败告警节流：类别 -> 上次告警时间
_last_warn: dict[str, float] = {}
_WARN_INTERVAL_SECONDS = 60.0

SESSION_TTL_DEFAULT = 1800  # 会话无 exp 时的兜底 TTL（秒）
BLACKLIST_TTL = 7 * 24 * 3600  # 黑名单保留 7 天（JWT 最长有效期上界）


def _warn_throttled(category: str, event: str, **kwargs: Any) -> None:
    """同一类别的 Redis 故障告警每 60 秒最多记录一次。"""
    now = time.monotonic()
    last = _last_warn.get(category, 0.0)
    if now - last < _WARN_INTERVAL_SECONDS:
        return
    _last_warn[category] = now
    logger.warning(event, degraded="in_memory", **kwargs)


def _token_key(token: str) -> str:
    """令牌的 Redis 键（SHA-256 摘要，明文令牌不落 Redis）。"""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    return digest


async def get_client() -> Optional[Any]:
    """获取已初始化的 Redis 客户端，未初始化返回 None。"""
    from app.core.database import get_redis_client

    return get_redis_client()


# ===========================================================================
# 会话
# ===========================================================================

def _session_key(token: str) -> str:
    return f"erp:sess:{_token_key(token)}"


def _blacklist_key(token: str) -> str:
    return f"erp:bl:{_token_key(token)}"


async def save_session(token: str, session: dict, ttl: Optional[int] = None) -> bool:
    """写共享会话（JSON + TTL）。失败返回 False（调用方降级内存）。"""
    client = await get_client()
    if client is None:
        return False
    try:
        if ttl is None:
            ttl = SESSION_TTL_DEFAULT
        payload = {
            "user_id": session.get("user_id"),
            "created_at": session.get("created_at"),
            "expires_at": session.get("expires_at"),
            "active": session.get("active", True),
        }
        await client.set(_session_key(token), json.dumps(payload), ex=int(ttl))
        return True
    except Exception as e:
        _warn_throttled("session_write", "redis.session_write_failed", error=str(e))
        return False


async def load_session(token: str) -> Optional[dict]:
    """读共享会话，未命中/不可用返回 None。"""
    client = await get_client()
    if client is None:
        return None
    try:
        raw = await client.get(_session_key(token))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        _warn_throttled("session_read", "redis.session_read_failed", error=str(e))
        return None


async def drop_session(token: str) -> bool:
    """删除共享会话（登出）。"""
    client = await get_client()
    if client is None:
        return False
    try:
        await client.delete(_session_key(token))
        return True
    except Exception as e:
        _warn_throttled("session_drop", "redis.session_drop_failed", error=str(e))
        return False


async def blacklist_token(token: str, ttl: Optional[int] = None) -> bool:
    """将令牌加入共享吊销黑名单（TTL 默认 7 天）。"""
    client = await get_client()
    if client is None:
        return False
    try:
        await client.set(_blacklist_key(token), "1", ex=int(ttl or BLACKLIST_TTL))
        return True
    except Exception as e:
        _warn_throttled("blacklist_write", "redis.blacklist_write_failed", error=str(e))
        return False


async def is_blacklisted(token: str) -> bool:
    """检查令牌是否已被吊销。Redis 不可用时返回 False（降级为本机黑名单）。"""
    client = await get_client()
    if client is None:
        return False
    try:
        return bool(await client.exists(_blacklist_key(token)))
    except Exception as e:
        _warn_throttled("blacklist_read", "redis.blacklist_read_failed", error=str(e))
        return False


# ===========================================================================
# 限流计数（固定窗口）
# ===========================================================================

async def incr_window(key: str, window_seconds: int) -> Optional[int]:
    """
    固定窗口计数自增（多实例共享）。

    :param key: 完整限流键（如 ``erp:rl:auth:1.2.3.4``），窗口起点由本函数拼接
    :param window_seconds: 窗口大小（秒）
    :return: 窗口内累计请求数；Redis 不可用返回 None（调用方降级进程内窗口）
    """
    client = await get_client()
    if client is None:
        return None
    try:
        window = int(time.time() // window_seconds)
        bucket = f"{key}:{window}"
        count = await client.incr(bucket)
        if count == 1:
            await client.expire(bucket, window_seconds)
        return int(count)
    except Exception as e:
        _warn_throttled("rate_limit", "redis.rate_limit_failed", error=str(e))
        return None


async def window_ttl(key: str, window_seconds: int) -> int:
    """查询当前窗口剩余秒数（用于 429 Retry-After），不可用返回窗口大小。"""
    client = await get_client()
    if client is None:
        return window_seconds
    try:
        window = int(time.time() // window_seconds)
        ttl = await client.ttl(f"{key}:{window}")
        return int(ttl) if ttl and ttl > 0 else window_seconds
    except Exception:
        return window_seconds


# ===========================================================================
# RBAC 跨实例版本信号
# ===========================================================================

RBAC_VERSION_KEY = "erp:rbac:version"


async def get_rbac_version() -> Optional[int]:
    """读取 RBAC 数据版本号，Redis 不可用返回 None。"""
    client = await get_client()
    if client is None:
        return None
    try:
        raw = await client.get(RBAC_VERSION_KEY)
        return int(raw) if raw is not None else 0
    except Exception as e:
        _warn_throttled("rbac_version", "redis.rbac_version_read_failed", error=str(e))
        return None


async def bump_rbac_version() -> Optional[int]:
    """RBAC 数据变更后递增版本号（其他实例据此重载），返回新版本号。"""
    client = await get_client()
    if client is None:
        return None
    try:
        return int(await client.incr(RBAC_VERSION_KEY))
    except Exception as e:
        _warn_throttled("rbac_version", "redis.rbac_version_bump_failed", error=str(e))
        return None
