"""
启动期安全检查（P1-3/P1-4 整改：fail-closed）
==============================================

- JWT 密钥：生产（非 debug）未配置/弱熵 → **拒绝启动**；debug 生成临时密钥
  （重启全员登出，仅限开发环境）；
- CORS：生产环境剥离 localhost/127.0.0.1 开发源并显式告警。

fail-closed 原则：安全配置缺位时"起不来"优于"带病上线"。
"""
from __future__ import annotations

import secrets

import structlog

logger = structlog.get_logger()


def validate_jwt_secret(secret: str, debug: bool) -> str:
    """校验 JWT 签名密钥，返回可用密钥；生产配置缺位时抛 RuntimeError。"""
    if not secret:
        if debug:
            ephemeral = secrets.token_urlsafe(32)
            logger.warning(
                "startup.jwt_secret_ephemeral",
                impact="开发模式使用临时密钥，重启后所有已签发令牌失效",
            )
            return ephemeral
        logger.error(
            "startup.jwt_secret_missing",
            fix="设置环境变量 ERP_JWT_SECRET（≥16 字符随机串）后重启",
        )
        raise RuntimeError(
            "生产环境未配置 ERP_JWT_SECRET，拒绝启动（fail-closed）——"
            "以空/弱密钥签发的令牌可被任意伪造")
    if len(secret) < 16:
        if debug:
            logger.warning("startup.jwt_secret_weak_debug", length=len(secret))
            return secret
        logger.error("startup.jwt_secret_weak", length=len(secret),
                     fix="更换为 ≥16 字符随机串（如 secrets.token_urlsafe(32)）")
        raise RuntimeError(
            f"ERP_JWT_SECRET 熵不足（{len(secret)} 字符 < 16），拒绝启动（fail-closed）")
    return secret


def sanitize_cors_origins(origins: list[str], debug: bool) -> list[str]:
    """生产环境剥离 localhost/127.0.0.1 开发源（显式告警，留痕）。"""
    if debug:
        return origins
    dev_sources = [o for o in origins
                   if "localhost" in o or "127.0.0.1" in o]
    if dev_sources:
        logger.warning(
            "cors.dev_origins_stripped_in_production",
            stripped=dev_sources,
            fix="生产 CORS 经 ERP_CORS_ORIGINS 显式配置为前端正式域名",
        )
    return [o for o in origins if o not in dev_sources]
