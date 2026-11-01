"""
会话管理
========

双层会话跟踪与令牌黑名单，提供用户登录后的会话生命周期管理。

- **会话存储**：进程内 ``dict`` 为 L1 + Redis 为共享 L2
  （键为令牌 SHA-256 摘要，TTL=会话剩余有效期）
- **令牌黑名单**：已注销的令牌加入黑名单（本机 + Redis 共享），
  即使 JWT 尚未过期也立即失效——**跨实例登出即时生效**（D-03）
- **过期清理**：``cleanup_expired()`` 扫描并移除过期会话与黑名单条目

设计要点
--------
1. **多实例一致**（路线图 2.5/2.6）：A 实例登录的会话在 B 实例可查
   （Redis 兜底读）；A 实例登出后 B 实例的旧令牌立即失效
   （共享黑名单）。Redis 不可用时自动降级为纯内存行为，
   降级有节流告警（见 ``app/core/redis_store``）。
2. **防重放**：``get_session`` 同时检查黑名单与会话存在性，双重保障注销即时生效。
3. **键安全**：令牌明文不写 Redis，键使用 SHA-256 摘要。
"""
from __future__ import annotations

import asyncio
import json
import time
import structlog
from datetime import datetime, timezone
from typing import Optional

from app.core import redis_store

logger = structlog.get_logger()


class SessionManager:
    """
    会话管理器。

    管理用户会话的创建、查询、注销与过期清理。
    进程内存为 L1，Redis 为共享 L2（可用时）。

    会话信息结构::

        {
            "user_id": "u1",
            "token": "eyJ...",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-01-01T00:30:00+00:00",
            "active": True,
        }

    用法（异步接口，认证中间件/路由中调用）::

        mgr = SessionManager()
        session = mgr.create_session("u1", access_token)
        assert await mgr.get_session(access_token) is not None
        await mgr.invalidate_session(access_token)
        assert await mgr.get_session(access_token) is None
    """

    def __init__(self):
        # 活跃会话：token -> 会话信息（L1）
        self._sessions: dict[str, dict] = {}
        # 令牌黑名单：token -> 注销时间戳（用于过期清理）（L1）
        self._blacklist: dict[str, int] = {}
        # L1 回填去重：避免同一 Redis 会话反复写日志
        self._hydrated_from_redis: set[str] = set()

    # ===================================================================
    # 公开接口
    # ===================================================================

    def create_session(self, user_id: str, token: str) -> dict:
        """
        创建新会话（同步：写 L1，异步调度写 Redis L2）。

        从 JWT 令牌中提取过期时间，记录会话信息。

        :param user_id: 用户唯一标识
        :param token: 已签发的 JWT 访问令牌
        :return: 会话信息字典
        """
        if not user_id or not token:
            raise ValueError("user_id 和 token 不能为空")

        # 从令牌载荷中提取过期时间（不验证签名，签名已由调用方验证）
        exp = self._extract_exp(token)
        now = datetime.now(timezone.utc)

        session = {
            "user_id": user_id,
            "token": token,
            "created_at": now.isoformat(),
            "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
            if exp
            else None,
            "active": True,
        }

        self._sessions[token] = session
        # 若令牌之前被拉黑（边缘情况），从本机黑名单移除
        self._blacklist.pop(token, None)
        self._hydrated_from_redis.discard(token)

        # 异步写 Redis L2（fire-and-forget：失败仅告警，不影响登录主流程）
        self._schedule_redis_write(self._persist_session(token, session))

        logger.info(
            "session.created",
            user_id=user_id,
            active_sessions=len(self._sessions),
        )
        return session

    async def get_session(self, token: str) -> Optional[dict]:
        """
        查询会话（异步：L1 优先，Redis L2 兜底）。

        检查顺序：本机黑名单 → 共享黑名单（其他实例的登出对本实例生效）
        → 本机会话 → 共享会话（其他实例的登录对本实例可见）。

        :param token: JWT 访问令牌
        :return: 会话信息字典，不存在或已失效返回 ``None``
        """
        if not token:
            return None

        # 1. 检查本机黑名单
        if token in self._blacklist:
            logger.debug("session.token_blacklisted", token_prefix=token[:16])
            return None

        # 2. 检查共享黑名单（Redis）
        if await redis_store.is_blacklisted(token):
            # 回填本机黑名单，后续请求不再打 Redis
            self._blacklist[token] = int(time.time())
            logger.debug("session.token_blacklisted_remote", token_prefix=token[:16])
            return None

        # 3. 检查本机会话
        session = self._sessions.get(token)
        if session is None:
            # 4. Redis L2 兜底（其他实例创建的会话）
            session = await redis_store.load_session(token)
            if session is not None and token not in self._hydrated_from_redis:
                self._hydrated_from_redis.add(token)
                self._sessions[token] = session
                logger.debug(
                    "session.hydrated_from_redis",
                    user_id=session.get("user_id"),
                )

        if session is None:
            return None

        # 5. 检查过期
        if self._is_expired(session):
            logger.debug("session.expired", user_id=session.get("user_id"))
            return None

        return session

    async def invalidate_session(self, token: str) -> bool:
        """
        注销会话（令牌拉黑，本机 + Redis 共享）。

        将令牌加入黑名单并从活跃会话中移除，即使 JWT 尚未过期也立即失效。

        :param token: 要注销的 JWT 令牌
        :return: 本机会话存在返回 ``True``，不存在返回 ``False``
                 （无论本机是否存在，共享黑名单都会写入，防止重放）
        """
        if not token:
            return False

        session = self._sessions.pop(token, None)
        self._hydrated_from_redis.discard(token)
        # 无论会话是否存在，都将令牌加入黑名单（防止重放）
        self._blacklist[token] = int(time.time())

        # 共享黑名单 + 删除共享会话（等待完成，保证登出对其他实例立即可见）
        await redis_store.blacklist_token(token)
        await redis_store.drop_session(token)

        if session:
            logger.info(
                "session.invalidated",
                user_id=session.get("user_id"),
                active_sessions=len(self._sessions),
            )
            return True

        logger.debug("session.invalidate_not_found", token_prefix=token[:16])
        return False

    def cleanup_expired(self) -> int:
        """
        清理过期会话与过期黑名单条目（本机 L1）。

        应在应用层定时调用（如每 5 分钟），防止内存无限增长。
        Redis L2 的会话/黑名单由 TTL 自动过期，无需清理。

        :return: 清理的条目总数（会话 + 黑名单）
        """
        now_ts = int(time.time())
        removed_sessions = 0
        removed_blacklist = 0

        # 清理过期会话
        expired_tokens = [
            token for token, session in self._sessions.items()
            if self._is_expired(session)
        ]
        for token in expired_tokens:
            self._sessions.pop(token, None)
            self._hydrated_from_redis.discard(token)
            removed_sessions += 1

        # 清理过期黑名单条目（黑名单中超过 7 天的条目视为安全移除，
        # 因为原始 JWT 必然已过期）
        blacklist_ttl = 7 * 24 * 3600  # 7 天
        expired_blacklist = [
            token for token, revoked_ts in self._blacklist.items()
            if now_ts - revoked_ts > blacklist_ttl
        ]
        for token in expired_blacklist:
            self._blacklist.pop(token, None)
            removed_blacklist += 1

        total_removed = removed_sessions + removed_blacklist
        if total_removed > 0:
            logger.info(
                "session.cleanup",
                expired_sessions=removed_sessions,
                expired_blacklist=removed_blacklist,
                active_sessions=len(self._sessions),
                blacklist_size=len(self._blacklist),
            )

        return total_removed

    # ===================================================================
    # 查询接口
    # ===================================================================

    def get_user_sessions(self, user_id: str) -> list[dict]:
        """
        获取指定用户的所有活跃会话（本机 L1 视图）。

        :param user_id: 用户唯一标识
        :return: 活跃会话列表
        """
        return [
            session for session in self._sessions.values()
            if session.get("user_id") == user_id and not self._is_expired(session)
        ]

    async def invalidate_user_sessions(self, user_id: str) -> int:
        """
        注销指定用户的所有会话（全端登出，本机视图 + 共享黑名单）。

        :param user_id: 用户唯一标识
        :return: 注销的会话数量（本机视图）
        """
        tokens = [
            token for token, session in self._sessions.items()
            if session.get("user_id") == user_id
        ]
        count = 0
        for token in tokens:
            await self.invalidate_session(token)
            count += 1
        if count > 0:
            logger.info("session.user_sessions_invalidated", user_id=user_id, count=count)
        return count

    def get_stats(self) -> dict:
        """获取会话管理统计信息（本机 L1）。"""
        active_count = sum(
            1 for session in self._sessions.values() if not self._is_expired(session)
        )
        return {
            "total_sessions": len(self._sessions),
            "active_sessions": active_count,
            "blacklist_size": len(self._blacklist),
        }

    # ===================================================================
    # 内部方法
    # ===================================================================

    def _schedule_redis_write(self, coro) -> None:
        """调度 fire-and-forget 的 Redis 写入（失败仅告警，不抛出）。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 无事件循环（纯同步场景）：跳过 Redis 写入，保持内存行为
            return
        task = asyncio.create_task(coro)
        # 防止任务被 GC；异常在 _persist_session 内部消化
        self._pending = getattr(self, "_pending", set())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _persist_session(self, token: str, session: dict) -> None:
        """将会话写入 Redis L2（TTL=剩余有效期）。"""
        ttl = SESSION_DEFAULT_TTL
        expires_at = session.get("expires_at")
        if expires_at:
            try:
                remaining = (
                    datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)
                ).total_seconds()
                if remaining > 0:
                    ttl = int(remaining)
            except (ValueError, TypeError):
                pass
        await redis_store.save_session(token, session, ttl=ttl)

    @staticmethod
    def _extract_exp(token: str) -> Optional[int]:
        """
        从 JWT 中提取 ``exp`` 声明（不验证签名）。

        仅读取载荷部分，因为令牌在调用 ``create_session`` 前已由
        :class:`JWTManager` 完成签名与过期验证。
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload_b64 = parts[1]
            # 补齐 base64url padding
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            import base64

            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes)
            return payload.get("exp")
        except Exception:
            return None

    @staticmethod
    def _is_expired(session: dict) -> bool:
        """检查会话是否已过期（基于会话记录的 ``expires_at``）。"""
        expires_at_str = session.get("expires_at")
        if not expires_at_str:
            return False  # 无过期时间则不过期
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            return datetime.now(timezone.utc) > expires_at
        except (ValueError, TypeError):
            return False  # 解析失败视为未过期，避免误删


SESSION_DEFAULT_TTL = 1800  # 与 jwt_expire_minutes=30 对齐的兜底 TTL（秒）

# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

session_manager = SessionManager()
