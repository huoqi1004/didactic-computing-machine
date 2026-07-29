"""
密码哈希与验证
================

基于 bcrypt 的安全密码哈希，未安装 bcrypt 时自动降级到
``hashlib.pbkdf2_hmac``，确保零依赖启动。

哈希格式自描述，``verify_password`` 可自动识别：
- **bcrypt**：``$2b$12$...``（bcrypt 原生格式）
- **pbkdf2**：``pbkdf2_sha256$<iterations>$<salt_hex>$<key_hex>``

设计要点
--------
1. **零依赖降级**：bcrypt 不可用时透明切换到标准库 ``hashlib``，无需额外安装。
2. **格式自描述**：哈希字符串内含算法标识与参数，正向兼容未来升级。
3. **安全参数**：pbkdf2 迭代次数遵循 OWASP 2023 推荐（600 000 次）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 运行时能力探测：bcrypt 可用性
# ---------------------------------------------------------------------------
try:
    import bcrypt as _bcrypt

    _HAS_BCRYPT = True
except ImportError:  # pragma: no cover - 环境相关
    _HAS_BCRYPT = False

# ---------------------------------------------------------------------------
# pbkdf2 降级参数（OWASP 2023 推荐）
# ---------------------------------------------------------------------------
_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT_BYTES = 32
_PBKDF2_KEY_BYTES = 32
_PBKDF2_PREFIX = "pbkdf2_sha256"


class PasswordHasher:
    """
    密码哈希器。

    优先使用 **bcrypt**（生产推荐，自适应计算成本）；
    未安装 bcrypt 时自动降级到 **hashlib.pbkdf2_hmac**（标准库零依赖）。

    所有方法均为 ``@staticmethod``，无需实例化即可使用::

        hashed = PasswordHasher.hash_password("my-secret")
        assert PasswordHasher.verify_password("my-secret", hashed)

    哈希格式：
    - bcrypt → ``$2b$12$<22字符salt><31字符hash>``
    - pbkdf2 → ``pbkdf2_sha256$600000$<salt_hex>$<key_hex>``
    """

    # bcrypt 计算轮数（仅 bcrypt 模式生效，值越高越慢越安全）
    BCRYPT_ROUNDS = 12

    # ===================================================================
    # 公开接口
    # ===================================================================

    @staticmethod
    def hash_password(plain: str) -> str:
        """
        对明文密码进行哈希。

        :param plain: 明文密码
        :return: 自描述哈希字符串（含算法标识、盐值、参数）
        """
        if not plain:
            raise ValueError("密码不能为空")

        if _HAS_BCRYPT:
            return PasswordHasher._hash_bcrypt(plain)
        return PasswordHasher._hash_pbkdf2(plain)

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        """
        验证明文密码与哈希是否匹配。

        自动识别哈希格式（bcrypt / pbkdf2），支持混合验证。
        验证失败返回 ``False``，不抛出异常。

        :param plain: 用户输入的明文密码
        :param hashed: 存储的哈希字符串
        :return: 匹配返回 ``True``，不匹配或格式无效返回 ``False``
        """
        if not plain or not hashed:
            return False

        try:
            if hashed.startswith("$2"):
                return PasswordHasher._verify_bcrypt(plain, hashed)
            if hashed.startswith(_PBKDF2_PREFIX):
                return PasswordHasher._verify_pbkdf2(plain, hashed)
            logger.warning("password.unknown_hash_format", prefix=hashed[:16])
            return False
        except Exception as exc:
            logger.warning("password.verify_error", error=str(exc))
            return False

    # ===================================================================
    # bcrypt 实现
    # ===================================================================

    @staticmethod
    def _hash_bcrypt(plain: str) -> str:
        """使用 bcrypt 哈希密码。"""
        salt = _bcrypt.gensalt(rounds=PasswordHasher.BCRYPT_ROUNDS)
        hashed = _bcrypt.hashpw(plain.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    @staticmethod
    def _verify_bcrypt(plain: str, hashed: str) -> bool:
        """使用 bcrypt 验证密码（恒定时间比较）。"""
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

    # ===================================================================
    # pbkdf2 降级实现（零依赖）
    # ===================================================================

    @staticmethod
    def _hash_pbkdf2(plain: str) -> str:
        """
        使用 ``hashlib.pbkdf2_hmac`` 哈希密码。

        输出格式：``pbkdf2_sha256$<iterations>$<salt_hex>$<key_hex>``
        """
        salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
        key = hashlib.pbkdf2_hmac(
            _PBKDF2_ALGORITHM,
            plain.encode("utf-8"),
            salt,
            _PBKDF2_ITERATIONS,
            dklen=_PBKDF2_KEY_BYTES,
        )
        return (
            f"{_PBKDF2_PREFIX}${_PBKDF2_ITERATIONS}"
            f"${salt.hex()}${key.hex()}"
        )

    @staticmethod
    def _verify_pbkdf2(plain: str, hashed: str) -> bool:
        """
        验证 pbkdf2 哈希。

        从哈希字符串解析盐值与迭代次数，重新计算并使用
        ``hmac.compare_digest`` 做恒定时间比较。
        """
        parts = hashed.split("$")
        if len(parts) != 4:
            return False

        _, iterations_str, salt_hex, key_hex = parts
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        stored_key = bytes.fromhex(key_hex)

        computed_key = hashlib.pbkdf2_hmac(
            _PBKDF2_ALGORITHM,
            plain.encode("utf-8"),
            salt,
            iterations,
            dklen=len(stored_key),
        )
        return hmac.compare_digest(computed_key, stored_key)

    # ===================================================================
    # 工具方法
    # ===================================================================

    @staticmethod
    def get_algorithm() -> str:
        """返回当前使用的哈希算法名称。"""
        return "bcrypt" if _HAS_BCRYPT else "pbkdf2_sha256"

    @staticmethod
    def needs_rehash(hashed: str) -> bool:
        """
        检查哈希是否需要重新计算（参数过时或算法降级）。

        用于渐进式升级：登录时检测旧哈希并重新哈希。
        """
        if _HAS_BCRYPT and hashed.startswith(_PBKDF2_PREFIX):
            return True  # bcrypt 可用但密码使用 pbkdf2 存储
        if hashed.startswith(_PBKDF2_PREFIX):
            parts = hashed.split("$")
            if len(parts) == 4 and int(parts[1]) < _PBKDF2_ITERATIONS:
                return True  # 迭代次数不足
        return False
