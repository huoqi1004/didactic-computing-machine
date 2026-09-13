"""
建库链路冒烟测试（评估报告 C-01 / 路线图任务 1.2、1.6①）
==========================================================

验证「models.py 唯一事实源 + bootstrap 兜底」建库链路：

1. 全新空库一次调用建出全部业务表（幂等：二次调用不再建表/补列）；
2. 存量旧表缺列时增量补列（ADD COLUMN），且存量数据保留、新列默认值生效；
3. 真实 MySQL 冒烟（配置 ERP_TEST_MYSQL_URL 时执行，覆盖 GET_LOCK 锁路径）：
   全新临时库建表 → 二次幂等 → 清理。

sqlite 用例无需外部依赖，始终执行；MySQL 用例仅在显式配置时执行。
"""
import os

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.database import (
    _bootstrap_schema_locked,
    bootstrap_schema,
    close_db,
    get_engine,
    init_db,
)
from app.db.models import Base


# ===========================================================================
# sqlite 全新库冒烟（始终执行）
# ===========================================================================

@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + 全局引擎重置。"""
    db_file = tmp_path / "erp_smoke.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await close_db()
    yield url
    await close_db()


async def test_fresh_db_bootstrap_creates_all_tables(sqlite_env):
    """全新空库：一次 bootstrap 建出全部业务表，二次调用幂等。"""
    engine = await init_db()
    assert engine is not None

    result = await bootstrap_schema()
    assert result["created"] == len(Base.metadata.tables)
    assert result["table_count"] == len(Base.metadata.tables)
    assert "error" not in result

    # 全部业务表真实存在（不是静默降级）
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        names = {r[0] for r in rows}
    assert set(Base.metadata.tables.keys()) <= names

    # 幂等：二次执行不再建表、不再补列
    again = await bootstrap_schema()
    assert again["created"] == 0
    assert again["added_columns"] == {}


async def test_bootstrap_reconciles_missing_column_and_preserves_data(sqlite_env):
    """存量旧表缺列：增量补列（含 must_change_password），存量行保留且默认值生效。"""
    engine = await init_db()
    # 模拟旧版本库：手工建 users 表（无 must_change_password 等新列）并插入一行
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE users (
                    user_id VARCHAR(64) PRIMARY KEY,
                    username VARCHAR(64) NOT NULL,
                    password_hash VARCHAR(256) NOT NULL,
                    full_name VARCHAR(128) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        await conn.execute(
            text(
                "INSERT INTO users (user_id, username, password_hash, full_name, "
                "status, created_at) VALUES "
                "('u_old', 'old_user', 'legacy_hash', '存量用户', 'active', '2026-01-01')"
            )
        )

    result = await bootstrap_schema()
    added = result["added_columns"].get("users", [])
    assert "must_change_password" in added

    # 存量数据保留，新列读到 server_default=1（首登强制改密）
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT must_change_password FROM users WHERE user_id='u_old'")
            )
        ).fetchone()
    assert row is not None, "补列后存量行丢失"
    assert int(row[0]) == 1

    # 再跑一次：无新增（幂等）
    again = await bootstrap_schema()
    assert again["added_columns"] == {}


async def test_schema_bootstrap_locked_sqlite_direct(sqlite_env):
    """锁内实现直接调用（sqlite 无顾问锁分支）同样幂等可用。"""
    await init_db()
    engine = get_engine()
    assert engine is not None
    first = await _bootstrap_schema_locked(engine)
    assert first["created"] == len(Base.metadata.tables)
    second = await _bootstrap_schema_locked(engine)
    assert second["created"] == 0


# ===========================================================================
# 真实 MySQL 冒烟（仅 ERP_TEST_MYSQL_URL 配置时执行）
# ===========================================================================

MYSQL_URL = os.environ.get("ERP_TEST_MYSQL_URL", "")


@pytest.mark.skipif(not MYSQL_URL, reason="ERP_TEST_MYSQL_URL 未配置，跳过 MySQL 冒烟")
async def test_fresh_mysql_bootstrap_smoke(monkeypatch):
    """真实 MySQL：全新临时库建表 → 锁路径 → 二次幂等 → 清理。

    需要 URL 账号具备建库权限（CI 中以 root 运行）；
    权限不足时显式跳过（与"静默降级"不同，跳过原因可被感知）。
    """
    db_name = MYSQL_URL.rsplit("/", 1)[1].split("?")[0]
    server_url = MYSQL_URL.rsplit("/", 1)[0] + "/"
    # 管理连接用同步驱动（aiomysql 仅支持异步）
    server_url = server_url.replace("aiomysql", "pymysql")
    admin = create_engine(server_url)
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
            conn.execute(
                text(
                    f"CREATE DATABASE `{db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
    except Exception as e:
        await close_db()
        pytest.skip(f"MySQL 冒烟跳过（管理连接/建库权限不足）：{e}")

    monkeypatch.setattr(settings, "mysql_url", MYSQL_URL)
    try:
        await close_db()
        engine = await init_db()
        assert engine is not None, "MySQL 引擎创建失败"

        result = await bootstrap_schema()
        assert "error" not in result
        assert result["created"] == len(Base.metadata.tables)

        # 二次执行幂等（GET_LOCK 重复获取 + 差集为空）
        again = await bootstrap_schema()
        assert again["created"] == 0
        assert again["added_columns"] == {}
    finally:
        await close_db()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
            conn.commit()
        admin.dispose()
