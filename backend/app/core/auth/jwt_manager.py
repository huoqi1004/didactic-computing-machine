"""
JWT 令牌管理
=============

基于 PyJWT 的访问令牌 / 刷新令牌签发与验证，
未安装 PyJWT 时自动降级到标准库手动实现（``hmac`` + ``hashlib`` + ``base64`` + ``json``），
确保零依赖启动。

令牌载荷规范
------------
所有令牌包含以下标准声明与业务声明：

+------------+--------+------------------------------------------+
| 字段       | 类型   | 说明                                     |
+============+========+==========================================+
| ``user_id``  | str   | 用户唯一标识                             |
| ``username`` | str   | 用户名                                   |
| ``role_codes`` | list[str] | 角色编码列表（用于 RBAC 鉴权）       |
| ``type``     | str   | 令牌类型：``"access"`` 或 ``"refresh"``   |
| ``iat``      | int   | 签发时间（Unix 时间戳，秒）              |
| ``exp``      | int   | 过期时间（Unix 时间戳，秒）              |
| ``jti``      | str   | 令牌唯一标识（防止同秒签发的相同载荷令牌碰撞） |
+------------+--------+------------------------------------------+
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import structlog
from typing import Optional

from app.core.config import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 运行时能力探测：PyJWT 可用性
# ---------------------------------------------------------------------------
try:
    import jwt as _pyjwt

    _HAS_PYJWT = True
except ImportError:  # pragma: no cover - 环境相关
    _HAS_PYJWT = False

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 刷新令牌默认有效期：7 天（分钟）
REFRESH_TOKEN_EXPIRE_MINUTES = 7 * 24 * 60

# 令牌类型
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


class JWTManager:
    """
    JWT 令牌管理器。

    优先使用 **PyJWT**（生产推荐，完整 JWT 规范支持）；
    未安装时自动降级到 **标准库手动实现**（HMAC-SHA256 + base64url）。

    使用 ``settings.jwt_secret`` 作为签名密钥，``settings.jwt_algorithm``
    作为签名算法（默认 ``HS256``）。

    用法::

        manager = JWTManager()
        token = manager.create_access_token(
            {"user_id": "u1", "username": "admin", "role_codes": ["admin"]}
        )
        payload = manager.verify_token(token)  # dict | None
    """

    def __init__(
        self,
        secret: Optional[str] = None,
        algorithm: Optional[str] = None,
        expire_minutes: Optional[int] = None,
    ):
        self._secret = secret or settings.jwt_secret
        self._algorithm = algorithm or settings.jwt_algorithm
        self._expire_minutes = expire_minutes or settings.jwt_expire_minutes

        if _HAS_PYJWT:
            logger.debug("jwt_manager.using_pyjwt", algorithm=self._algorithm)
        else:
            logger.info(
                "jwt_manager.pyjwt_unavailable",
                fallback="manual_hmac_sha256",
                algorithm=self._algorithm,
            )

    # ===================================================================
    # 公开接口
    # ===================================================================

    def create_access_token(
        self, data: dict, expires_minutes: Optional[int] = None
    ) -> str:
        """
        签发访问令牌（access token）。

        :param data: 业务声明，应包含 ``user_id``、``username``、``role_codes``
        :param expires_minutes: 自定义有效期（分钟），默认使用 ``settings.jwt_expire_minutes``
        :return: JWT 字符串
        """
        minutes = expires_minutes if expires_minutes is not None else self._expire_minutes
        payload = self._build_payload(data, TOKEN_TYPE_ACCESS, minutes)
        token = self._encode(payload)
        logger.debug(
            "jwt_manager.access_token_created",
            user_id=data.get("user_id"),
            expires_in_minutes=minutes,
        )
        return token

    def create_refresh_token(self, data: dict) -> str:
        """
        签发刷新令牌（refresh token）。

        有效期固定为 7 天（``REFRESH_TOKEN_EXPIRE_MINUTES``）。

        :param data: 业务声明，应包含 ``user_id``、``username``、``role_codes``
        :return: JWT 字符串
        """
        payload = self._build_payload(data, TOKEN_TYPE_REFRESH, REFRESH_TOKEN_EXPIRE_MINUTES)
        token = self._encode(payload)
        logger.debug(
            "jwt_manager.refresh_token_created",
            user_id=data.get("user_id"),
            expires_in_minutes=REFRESH_TOKEN_EXPIRE_MINUTES,
        )
        return token

    def decode_token(self, token: str) -> dict:
        """
        解码并验证令牌（签名 + 过期时间）。

        :param token: JWT 字符串
        :return: 令牌载荷字典
        :raises ValueError: 令牌格式无效、签名不匹配或已过期
        """
        if not token:
            raise ValueError("令牌不能为空")

        if _HAS_PYJWT:
            return self._decode_pyjwt(token)
        return self._decode_manual(token)

    def verify_token(self, token: str) -> Optional[dict]:
        """
        验证令牌并返回载荷。

        与 :meth:`decode_token` 的区别：验证失败返回 ``None`` 而非抛出异常。

        :param token: JWT 字符串
        :return: 验证通过返回载荷字典，失败返回 ``None``
        """
        try:
            return self.decode_token(token)
        except Exception as exc:
            logger.debug("jwt_manager.verify_failed", error=str(exc))
            return None

    # ===================================================================
    # 载荷构建
    # ===================================================================

    @staticmethod
    def _build_payload(data: dict, token_type: str, expires_minutes: int) -> dict:
        """
        构建标准 JWT 载荷。

        合并业务声明与标准声明（``jti`` / ``iat`` / ``exp`` / ``type``）。
        ``jti``（JWT ID）确保同一秒签发的相同载荷令牌也不碰撞。
        """
        now = int(time.time())
        payload = {
            # 业务声明
            "user_id": data.get("user_id"),
            "username": data.get("username"),
            "role_codes": data.get("role_codes", []),
            # 令牌类型
            "type": token_type,
            # 标准声明
            "jti": secrets.token_hex(16),
            "iat": now,
            "exp": now + expires_minutes * 60,
        }
        # 合并额外的自定义声明（不覆盖标准字段）
        for key, value in data.items():
            if key not in payload:
                payload[key] = value
        return payload

    # ===================================================================
    # PyJWT 实现
    # ===================================================================

    def _encode_pyjwt(self, payload: dict) -> str:
        """使用 PyJWT 编码令牌。"""
        return _pyjwt.encode(payload, self._secret, algorithm=self._algorithm)

    def _decode_pyjwt(self, token: str) -> dict:
        """使用 PyJWT 解码并验证令牌。"""
        try:
            payload = _pyjwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
            )
            return payload
        except _pyjwt.ExpiredSignatureError:
            raise ValueError("令牌已过期")
        except _pyjwt.InvalidTokenError as exc:
            raise ValueError(f"令牌无效: {exc}")

    # ===================================================================
    # 手动实现（零依赖降级）
    # ===================================================================

    def _encode(self, payload: dict) -> str:
        """编码令牌（自动选择 PyJWT 或手动实现）。"""
        if _HAS_PYJWT:
            return self._encode_pyjwt(payload)
        return self._encode_manual(payload)

    def _encode_manual(self, payload: dict) -> str:
        """
        手动实现 JWT 编码（HMAC-SHA256）。

        流程：
        1. 构建 header ``{"alg": "HS256", "typ": "JWT"}``
        2. base64url 编码 header 与 payload
        3. HMAC-SHA256 签名 ``header_b64.payload_b64``
        4. 拼接为 ``header_b64.payload_b64.signature_b64``
        """
        header = {"alg": self._algorithm, "typ": "JWT"}
        header_json = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

        header_b64 = _b64url_encode(header_json)
        payload_b64 = _b64url_encode(payload_json)

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = self._sign(signing_input)
        sig_b64 = _b64url_encode(signature)

        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def _decode_manual(self, token: str) -> dict:
        """
        手动实现 JWT 解码与验证。

        验证签名（恒定时间比较）与过期时间。
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("令牌格式无效：应有 3 段")

        header_b64, payload_b64, sig_b64 = parts

        # 1. 验证签名
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = self._sign(signing_input)
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise ValueError("令牌签名无效")

        # 2. 解析载荷
        payload_json = _b64url_decode(payload_b64)
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"令牌载荷解析失败: {exc}")

        # 3. 验证过期时间
        exp = payload.get("exp")
        if exp is not None:
            now = int(time.time())
            if exp < now:
                raise ValueError("令牌已过期")

        return payload

    def _sign(self, data: bytes) -> bytes:
        """使用配置的算法对数据进行 HMAC 签名。"""
        algorithm = hashlib.sha256
        if self._algorithm == "HS384":
            algorithm = hashlib.sha384
        elif self._algorithm == "HS512":
            algorithm = hashlib.sha512
        return hmac.new(self._secret.encode("utf-8"), data, algorithm).digest()

    # ===================================================================
    # 工具方法
    # ===================================================================

    def get_algorithm(self) -> str:
        """返回当前使用的签名算法。"""
        return self._algorithm

    @staticmethod
    def get_implementation() -> str:
        """返回当前使用的 JWT 实现名称。"""
        return "pyjwt" if _HAS_PYJWT else "manual_hmac_sha256"


# ---------------------------------------------------------------------------
# base64url 辅助函数（无填充，URL 安全）
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    """base64url 编码（去除 ``=`` 填充）。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """base64url 解码（自动补齐 ``=`` 填充）。"""
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

jwt_manager = JWTManager()
