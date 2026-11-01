"""新架构配置（全面重构 M0 骨架）。"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "ERP 数字化大脑 · 新架构"
    jwt_secret: str = ""            # 生产必须 ERP_JWT_SECRET
    jwt_expire_minutes: int = 120
    database_url: str = "sqlite+aiosqlite:///./erp_new.db"
    debug: bool = True

    # LLM（OpenAI 兼容：Agnes / DeepSeek / Qwen 等）
    llm_base_url: str = "https://apihub.agnes-ai.com/v1"
    llm_model: str = "agnes-3.0-flash"
    llm_api_key: str = ""
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    llm_enabled: bool = False          # ERP_LLM_ENABLED=true 启用
    llm_fallback_to_rules: bool = True  # LLM 失败时降级到固定回复
    llm_timeout: int = 60

    model_config = {"env_file": ".env", "env_prefix": "ERP_", "extra": "ignore"}


settings = Settings()
