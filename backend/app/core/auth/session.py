"""
会话管理
========

进程内会话跟踪与令牌黑名单，提供用户登录后的会话生命周期管理。

- **会话存储**：内存 ``dict``，键为 token，值为会话信息（``user_id``、``expires_at`` 等）
- **令牌黑名单**：已注销的令牌加入黑名单，即使 JWT 尚未过期也立即失效
- **过期清理**：``cleanup_expired()`` 扫描并移除过期会话与黑名单条目

设计要点
--------
1. **零依赖**：纯内存实现，不依赖 Redis / 数据库；未来可通过依赖注入替换为分布式存储。
2. **防重放**：``get_session`` 同时检查黑名单与会话存在性，双重保障注销即时生效。
3. **轻量级**：会话信息从 JWT 载荷中提取 ``exp``，无需额外配置。
"""
from __future__ import annotations

import json
import time
import structlog
from datetime import datetime, timezone
from typing import Optional

logger = structlog.get_logger()


class SessionManager:
    """
    会话管理器。

    管理用户会话的创建、查询、注销与过期清理。
    所有状态存储在进程内存中，适合单实例部署与测试环境。

    会话信息结构::

        {
            "user_id": "u1",
            "token": "eyJ...",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-01-01T00:30:00+00:00",
            "active": True,
        }

    用法::

        mgr = SessionManager()
        session = mgr.create_session("u1", access_token)
        assert mgr.get_session(access_token) is not None
        mgr.invalidate_session(access_token)
        assert mgr.get_session(access_token) is None
    """

    def __init__(self):
        # 活跃会话：token -> 会话信息
        self._sessions: dict[str, dict] = {}
        # 令牌黑名单：token -> 注销时间戳（用于过期清理）
        self._blacklist: dict[str, int] = {}

    # ===================================================================
    # 公开接口
    # ===================================================================

    def create_session(self, user_id: str, token: str) -> dict:
        """
        创建新会话。

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

        # 若令牌之前被拉黑（边缘情况），从黑名单移除
        self._blacklist.pop(token, None)

        logger.info(
            "session.created",
            user_id=user_id,
            active_sessions=len(self._sessions),
        )
        return session

    def get_session(self, token: str) -> Optional[dict]:
        """
        查询会话。

        同时检查令牌是否在黑名单中、会话是否存在、会话是否已过期。
        任一条件不满足返回 ``None``。

        :param token: JWT 访问令牌
        :return: 会话信息字典，不存在或已失效返回 ``None``
        """
        if not token:
            return None

        # 1. 检查黑名单
        if token in self._blacklist:
            logger.debug("session.token_blacklisted", token_prefix=token[:16])
            return None

        # 2. 检查会话存在
        session = self._sessions.get(token)
        if session is None:
            return None

        # 3. 检查过期
        if self._is_expired(session):
            logger.debug("session.expired", user_id=session.get("user_id"))
            return None

        return session

    def invalidate_session(self, token: str) -> bool:
        """
        注销会话（令牌拉黑）。

        将令牌加入黑名单并从活跃会话中移除，即使 JWT 尚未过期也立即失效。

        :param token: 要注销的 JWT 令牌
        :return: 注销成功返回 ``True``，会话不存在返回 ``False``
        """
        if not token:
            return False

        session = self._sessions.pop(token, None)
        # 无论会话是否存在，都将令牌加入黑名单（防止重放）
        self._blacklist[token] = int(time.time())

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
        清理过期会话与过期黑名单条目。

        应在应用层定时调用（如每 5 分钟），防止内存无限增长。

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
        获取指定用户的所有活跃会话。

        :param user_id: 用户唯一标识
        :return: 活跃会话列表
        """
        return [
            session for session in self._sessions.values()
            if session.get("user_id") == user_id and not self._is_expired(session)
        ]

    def invalidate_user_sessions(self, user_id: str) -> int:
        """
        注销指定用户的所有会话（全端登出）。

        :param user_id: 用户唯一标识
        :return: 注销的会话数量
        """
        tokens = [
            token for token, session in self._sessions.items()
            if session.get("user_id") == user_id
        ]
        count = 0
        for token in tokens:
            self.invalidate_session(token)
            count += 1
        if count > 0:
            logger.info("session.user_sessions_invalidated", user_id=user_id, count=count)
        return count

    def get_stats(self) -> dict:
        """获取会话管理统计信息。"""
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


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

session_manager = SessionManager()
