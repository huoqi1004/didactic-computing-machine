"""
Alembic 迁移环境配置
====================

将 SQLAlchemy ORM 模型的 ``Base.metadata`` 注册到 Alembic，
使 ``alembic revision --autogenerate`` 能自动检测模型变更并生成迁移脚本。

关键设计：
- 从 ``app.db.models.Base`` 导入元数据，覆盖全部业务表
- 从 ``app.core.config.settings`` 读取数据库 URL（支持环境变量覆盖）
- 使用 pymysql 同步驱动（非 aiomysql 异步驱动），因为 Alembic 运行在同步上下文中
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# ============================================================
# 路径设置：确保能导入 app 包
# ============================================================
# alembic 通常在 backend/ 目录下执行，将该目录加入 sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

# ============================================================
# 导入应用配置和 ORM 模型元数据
# ============================================================
from app.core.config import settings  # noqa: E402
from app.db.models import Base  # noqa: E402

# 显式导入所有模型模块，确保元数据完整注册
# （否则未被导入的模型不会出现在 metadata 中）
import app.db.models  # noqa: F401, E402

# ============================================================
# Alembic 配置
# ============================================================
config = context.config

# 从应用配置覆盖数据库 URL（优先使用环境变量中的配置）
# 将 aiomysql 替换为 pymysql（Alembic 使用同步连接）
db_url = settings.mysql_url.replace("aiomysql", "pymysql")
config.set_main_option("sqlalchemy.url", db_url)

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标元数据：Alembic autogenerate 的对比基准
target_metadata = Base.metadata


# ============================================================
# 离线迁移：生成 SQL 脚本（不连接数据库）
# ============================================================
def run_migrations_offline() -> None:
    """以离线模式运行迁移，生成 SQL 脚本而不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ============================================================
# 在线迁移：直接连接数据库执行
# ============================================================
def run_migrations_online() -> None:
    """以在线模式运行迁移，直接连接数据库执行 DDL。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# ============================================================
# 入口
# ============================================================
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
