"""initial_schema

Revision ID: c309befbdf7e
Revises:
Create Date: 2026-07-29 20:53:07.936906+08:00

以 ``app/db/models.py`` 的 ``Base.metadata`` 为唯一事实源的真实基线迁移
（评估报告 C-01/C-03：原迁移 upgrade() 为 pass 空壳，建库链路断裂）。

- ``upgrade()``：``create_all(checkfirst=True)`` 建出全部业务表，
  已存在的表自动跳过，因此对存量库执行 ``alembic upgrade head`` 幂等安全；
- ``downgrade()``：``drop_all`` 反向清理（仅用于测试环境重建）。

存量环境（已有表但无 alembic_version）的纳管方式：``alembic stamp head``。
运行期兜底建表/补列由 ``app/core/database.py bootstrap_schema`` 负责，
与本迁移同源（同一份 metadata），幂等兼容。
"""
import sys
from pathlib import Path
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c309befbdf7e"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_metadata():
    """导入全部 ORM 模型并返回 Base.metadata。

    alembic env.py 已将 backend 目录加入 sys.path，这里兜底处理
    直接以脚本方式执行迁移的场景。
    """
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    import app.db.models  # noqa: F401  确保全部表模型注册到 metadata
    from app.db.models import Base

    return Base.metadata


def upgrade() -> None:
    _base_metadata().create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    _base_metadata().drop_all(bind=op.get_bind(), checkfirst=True)
