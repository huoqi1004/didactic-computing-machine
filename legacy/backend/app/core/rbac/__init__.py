"""
RBAC 权限控制模块（Phase 1）

基于角色的访问控制（Role-Based Access Control）引擎，
内存存储、零依赖启动。

核心导出：
    - RBACEngine:   权限引擎类（可实例化用于隔离测试）
    - rbac_engine:   全局单例（已加载种子数据：23 角色 + 204 权限 + 16 部门 + 3 默认用户）
    - load_seed_data: 种子数据加载函数
    - SEED_ROLES / SEED_PERMISSIONS / SEED_DEPARTMENTS / SEED_USERS: 种子数据常量

用法示例::

    from app.core.rbac import rbac_engine

    # 检查权限
    if rbac_engine.check_permission("user_admin", "system", "manage"):
        ...

    # 获取用户全部权限
    perms = rbac_engine.get_user_permissions("user_ceo")

    # 按用户名查找用户（登录认证）
    user = rbac_engine.get_user_by_username("admin")
"""
from app.core.rbac.engine import RBACEngine
from app.core.rbac.seed import (
    SEED_DEPARTMENTS,
    SEED_PERMISSIONS,
    SEED_ROLES,
    SEED_USERS,
    ACTIONS,
    RESOURCES,
    ROLE_PERMISSION_MATRIX,
    load_seed_data,
)

# 全局 RBAC 引擎单例（已加载种子数据）
rbac_engine = RBACEngine()
load_seed_data(rbac_engine)

__all__ = [
    # 引擎
    "RBACEngine",
    "rbac_engine",
    # 种子数据加载
    "load_seed_data",
    # 种子数据常量
    "SEED_ROLES",
    "SEED_PERMISSIONS",
    "SEED_DEPARTMENTS",
    "SEED_USERS",
    "RESOURCES",
    "ACTIONS",
    "ROLE_PERMISSION_MATRIX",
]
