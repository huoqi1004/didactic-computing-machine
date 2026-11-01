"""新架构配置（全面重构 M0 骨架）。"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "ERP 数字化大脑 · 新架构"
    jwt_secret: str = ""            # 生产必须 ERP_JWT_SECRET
    jwt_expire_minutes: int = 120
    database_url: str = "sqlite+aiosqlite:///./erp_new.db"
    debug: bool = True

    model_config = {"env_file": ".env", "env_prefix": "ERP_", "extra": "ignore"}


settings = Settings()
