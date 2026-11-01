"""
认证模块 (Authentication)
=========================

Agent-Native ERP 系统的认证基础设施，Phase 1 RBAC 实现的核心组件。

提供三大能力：
- **密码哈希** (:class:`PasswordHasher`)：bcrypt / pbkdf2 双模式，零依赖降级
- **JWT 令牌** (:class:`JWTManager`)：PyJWT / 手动 HMAC-SHA256 双模式，零依赖降级
- **会话管理** (:class:`SessionManager`)：进程内会话跟踪与令牌黑名单

所有组件遵循 **零依赖启动** 原则：核心功能不依赖任何第三方库，
在 bcrypt / PyJWT 缺失时自动降级到 Python 标准库实现。

快速使用::

    from app.core.auth import PasswordHasher, JWTManager, SessionManager

    # 密码哈希
    hashed = PasswordHasher.hash_password("my-password")
    assert PasswordHasher.verify_password("my-password", hashed)

    # JWT 令牌
    from app.core.auth import jwt_manager
    token = jwt_manager.create_access_token(
        {"user_id": "u1", "username": "admin", "role_codes": ["admin"]}
    )

    # 会话管理
    from app.core.auth import session_manager
    session = session_manager.create_session("u1", token)
"""
from app.core.auth.password import PasswordHasher
from app.core.auth.jwt_manager import JWTManager, jwt_manager
from app.core.auth.session import SessionManager, session_manager

__all__ = [
    "PasswordHasher",
    "JWTManager",
    "jwt_manager",
    "SessionManager",
    "session_manager",
]
