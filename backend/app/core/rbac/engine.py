"""
RBAC 权限引擎（MySQL 持久化 + 内存缓存混合架构）
================================================

基于角色的访问控制（Role-Based Access Control）核心引擎。

架构
----
本引擎采用 ``MySQL 持久化 + 内存缓存`` 的混合架构：

1. **内存缓存层**：启动时（或首次访问时）从 MySQL 加载全部 用户/角色/权限/部门
   数据到内存 Dict，所有读操作直接走缓存，零 DB 查询、零延迟。
2. **MySQL 持久化层**：所有写操作在更新缓存后，通过 ``fire-and-forget`` 方式
   异步写入 MySQL（使用 SQLAlchemy 2.0 async + aiomysql）。
3. **同步兼容**：所有公共方法保持 **同步签名**，便于现有路由（同步调用
   ``rbac_engine.check_permission()`` 等）无需改动；写操作内部通过
   ``asyncio.create_task`` 调度异步 DB 写入。

权限模型
--------
``资源:动作`` 二维权限，如 ``accounting:read`` / ``treasury:approve``。
``manage`` 动作隐含该资源的全部动作（通配）。

数据模型
--------
    USER          -> user_id, username, password_hash, full_name, department, email, ...
    ROLE          -> role_id, role_code, role_name, level, parent_role_id, description
    PERMISSION    -> permission_id, resource, action, description, sensitivity
    USER_ROLE     -> user_id ↔ role_code
    ROLE_PERM     -> role_code ↔ permission_id
    DEPARTMENT    -> department_id, department_code, department_name, parent_department_id

注意：``User`` 表的 ``department_id`` 列存储的是部门「名称字符串」（兼容现有
种子数据），而非 ``departments`` 表的外键。

用法
----
    # 应用启动时预加载缓存
    await rbac_engine.load_from_db()

    # 同步读 / 写（路由层无需改动）
    rbac_engine.check_permission("user_admin", "system", "manage")
    rbac_engine.add_user(...)
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session_factory
from app.db.models import (
    Department,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)

logger = structlog.get_logger()


class RBACEngine:
    """
    RBAC 权限引擎（MySQL 持久化 + 内存缓存）

    内存缓存结构
    -------------
    - ``_users_cache``:            ``{user_id: {user fields}}``
    - ``_username_cache``:         ``{username: user_id}``  用户名快速反查
    - ``_roles_cache``:            ``{role_code: {role fields}}``
    - ``_permissions_cache``:      ``{permission_id: {permission fields}}``
    - ``_role_permissions_cache``: ``{role_code: {permission_id, ...}}``
    - ``_user_roles_cache``:       ``{user_id: {role_code, ...}}``
    - ``_departments_cache``:      ``{department_id: {department fields}}``
    - ``_initialized``:            是否已从 DB 加载缓存

    向后兼容
    --------
    历史路由代码（``auth.py`` / ``rbac.py``）直接访问了 ``rbac_engine._users``、
    ``rbac_engine._role_permissions``、``rbac_engine._permissions`` 等私有属性。
    为保证向后兼容，这些旧名称作为 **指向同一对象** 的别名保留，对缓存 dict / set
    的原地修改（``.clear()`` / ``d[k]=v`` / ``.add()`` / ``.discard()``）同时生效。
    """

    # 缓存字段名 → User 表列名 的映射（update_user 异步落库时使用）
    _USER_FIELD_TO_COLUMN: dict[str, str] = {
        "username": "username",
        "password_hash": "password_hash",
        "full_name": "full_name",
        # 缓存中的 department 存的是部门名称字符串，对应 User.department_id 列
        "department": "department_id",
        "email": "email",
        "phone": "phone",
        "position": "position",
        "status": "status",
        "last_login": "last_login",
    }

    def __init__(self) -> None:
        # ---- 内存缓存（主存储）----
        self._users_cache: dict[str, dict[str, Any]] = {}
        self._username_cache: dict[str, str] = {}
        self._roles_cache: dict[str, dict[str, Any]] = {}
        self._permissions_cache: dict[str, dict[str, Any]] = {}
        self._user_roles_cache: dict[str, set[str]] = {}
        self._role_permissions_cache: dict[str, set[str]] = {}
        self._departments_cache: dict[str, dict[str, Any]] = {}

        # ---- 是否已从 DB 加载缓存 ----
        self._initialized: bool = False

        # ---- 异步 DB 写入任务跟踪（避免被 GC 回收）----
        self._pending_tasks: set[asyncio.Task] = set()

        # ---- 向后兼容别名（指向同一对象）----
        # 历史路由代码直接访问 rbac_engine._users / _role_permissions / _permissions 等，
        # 这里保留同名引用，使缓存 dict / set 的原地修改双向生效。
        self._users = self._users_cache
        self._username_index = self._username_cache
        self._roles = self._roles_cache
        self._permissions = self._permissions_cache
        self._user_roles = self._user_roles_cache
        self._role_permissions = self._role_permissions_cache
        self._departments = self._departments_cache

    # ===================================================================
    # 异步辅助方法
    # ===================================================================

    def _get_session(self) -> AsyncSession:
        """
        从全局 session factory 获取一个异步会话。

        调用方负责在 ``finally`` 中 ``await session.close()``，
        或通过 :meth:`_session_scope` 上下文管理器使用。

        Raises:
            RuntimeError: 数据库会话工厂未初始化（需先调用 ``init_db()``）
        """
        factory = get_session_factory()
        if factory is None:
            raise RuntimeError(
                "数据库会话工厂未初始化，请先在应用启动时调用 init_db()"
            )
        return factory()

    @asynccontextmanager
    async def _session_scope(self):
        """
        异步会话作用域：自动提交 / 回滚 / 关闭。

        用法::

            async with self._session_scope() as session:
                session.add(obj)

        正常退出自动 ``commit``；发生异常自动 ``rollback`` 并重新抛出。
        """
        session = self._get_session()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    def _fire_and_forget(self, coro) -> None:
        """
        调度一个协程异步执行（fire-and-forget）。

        - 若当前有运行中的事件循环（如 FastAPI 请求上下文），
          使用 ``loop.create_task`` 调度，并跟踪任务直至完成。
        - 若无运行中的事件循环（如模块导入阶段的种子数据加载），
          关闭协程并跳过 DB 写入——此时数据仅写入缓存，启动后可通过
          ``await load_from_db()`` 从 DB 重新加载（或由迁移脚本播种 DB）。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中的事件循环：关闭协程以避免 "coroutine never awaited" 警告
            try:
                coro.close()
            except Exception:
                pass
            logger.debug("rbac.fire_and_forget_no_loop")
            return

        # 数据库会话工厂未初始化（应用导入/启动早期，init_db() 尚未执行）时，
        # DB 写入必然失败，直接关闭协程跳过——数据仅保留在内存缓存中，
        # 待 lifespan 中 init_db() 完成后由 persist_seed_data() 统一播种。
        if get_session_factory() is None:
            try:
                coro.close()
            except Exception:
                pass
            logger.debug("rbac.fire_and_forget_no_factory")
            return

        task = loop.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def load_from_db(self) -> None:
        """
        从 MySQL 加载全部 用户 / 角色 / 权限 / 部门 / 映射 数据到内存缓存。

        安全策略
        --------
        采用 **先加载到临时结构、成功后原子替换** 的模式：

        1. 将 DB 数据加载到临时 dict / set 中；
        2. 若加载成功且临时结构中有用户数据，清空当前缓存并替换为 DB 数据；
        3. 若加载失败或 DB 为空（未播种），**保留现有缓存**（可能是种子数据）。

        这确保了即使 DB 不可用或尚未播种，内存中的种子数据也不会丢失。

        在应用启动时调用::

            await rbac_engine.load_from_db()

        若会话工厂未初始化（DB 不可用），记录警告并直接返回。
        """
        if get_session_factory() is None:
            logger.warning("rbac.load_from_db_no_factory")
            return

        # ---- 临时结构：先加载到这里，成功后再替换到缓存 ----
        tmp_users: dict[str, dict[str, Any]] = {}
        tmp_username: dict[str, str] = {}
        tmp_roles: dict[str, dict[str, Any]] = {}
        tmp_permissions: dict[str, dict[str, Any]] = {}
        tmp_user_roles: dict[str, set[str]] = {}
        tmp_role_permissions: dict[str, set[str]] = {}
        tmp_departments: dict[str, dict[str, Any]] = {}

        try:
            async with self._session_scope() as session:
                # 1. 加载权限定义
                result = await session.execute(select(Permission))
                for perm in result.scalars():
                    tmp_permissions[perm.permission_id] = {
                        "permission_id": perm.permission_id,
                        "resource": perm.resource,
                        "action": perm.action,
                        "description": perm.description or "",
                        "sensitivity": perm.sensitivity or "normal",
                    }

                # 2. 加载角色定义
                result = await session.execute(select(Role))
                for role in result.scalars():
                    tmp_roles[role.role_code] = {
                        "role_id": role.role_id,
                        "role_code": role.role_code,
                        "role_name": role.role_name,
                        "level": role.level,
                        "parent_role_id": role.parent_role_id,
                        "description": role.description or "",
                    }
                    tmp_role_permissions[role.role_code] = set()

                # 3. 加载部门
                result = await session.execute(select(Department))
                for dept in result.scalars():
                    tmp_departments[dept.department_id] = {
                        "department_id": dept.department_id,
                        "department_code": dept.department_code,
                        "department_name": dept.department_name,
                        "parent_department_id": dept.parent_department_id,
                    }

                # 4. 加载用户
                result = await session.execute(select(User))
                for u in result.scalars():
                    tmp_users[u.user_id] = {
                        "user_id": u.user_id,
                        "username": u.username,
                        "password_hash": u.password_hash,
                        "full_name": u.full_name,
                        # department_id 列存的是部门名称字符串
                        "department": u.department_id,
                        "email": u.email,
                        "phone": u.phone,
                        "position": u.position,
                        "status": u.status,
                        "created_at": u.created_at,
                        "last_login": u.last_login,
                    }
                    tmp_username[u.username] = u.user_id
                    tmp_user_roles[u.user_id] = set()

                # 5. 加载用户-角色映射
                result = await session.execute(select(UserRole))
                for ur in result.scalars():
                    if ur.user_id in tmp_user_roles:
                        tmp_user_roles[ur.user_id].add(ur.role_code)

                # 6. 加载角色-权限映射
                result = await session.execute(select(RolePermission))
                for rp in result.scalars():
                    if rp.role_code in tmp_role_permissions:
                        tmp_role_permissions[rp.role_code].add(rp.permission_id)
        except Exception as e:
            logger.error("rbac.load_from_db_failed", error=str(e))
            return

        # ---- 安全检查：DB 为空时保留现有缓存（可能是种子数据）----
        if not tmp_users:
            logger.warning(
                "rbac.load_from_db_empty",
                message="DB 无用户数据，保留现有内存缓存（可能是种子数据）",
            )
            return

        # ---- 原子替换：清空缓存并写入 DB 数据 ----
        # 使用 clear()+update() 原地修改，保持向后兼容别名引用不变
        self._users_cache.clear()
        self._users_cache.update(tmp_users)
        self._username_cache.clear()
        self._username_cache.update(tmp_username)
        self._roles_cache.clear()
        self._roles_cache.update(tmp_roles)
        self._permissions_cache.clear()
        self._permissions_cache.update(tmp_permissions)
        self._user_roles_cache.clear()
        for k, v in tmp_user_roles.items():
            self._user_roles_cache[k] = v
        self._role_permissions_cache.clear()
        for k, v in tmp_role_permissions.items():
            self._role_permissions_cache[k] = v
        self._departments_cache.clear()
        self._departments_cache.update(tmp_departments)

        self._initialized = True
        logger.info(
            "rbac.loaded_from_db",
            users=len(self._users_cache),
            roles=len(self._roles_cache),
            permissions=len(self._permissions_cache),
            departments=len(self._departments_cache),
            user_role_mappings=sum(len(v) for v in self._user_roles_cache.values()),
            role_permission_mappings=sum(len(v) for v in self._role_permissions_cache.values()),
        )

    async def async_load_from_db(self) -> None:
        """
        ``load_from_db`` 的语义别名，供应用启动时显式调用。

        与 :meth:`load_from_db` 完全等价，提供更具可读性的调用形式::

            await rbac_engine.async_load_from_db()
        """
        await self.load_from_db()

    async def _ensure_initialized(self) -> None:
        """
        确保缓存已从 DB 加载（懒加载）。

        供异步调用方使用。注意：同步读方法（如 :meth:`get_user`）不会触发此方法，
        它们直接读取缓存——因此请务必在应用启动时调用 ``await load_from_db()``。
        """
        if not self._initialized:
            await self.load_from_db()

    # ===================================================================
    # 异步 DB 写入方法（内部使用，由 fire-and-forget 调度）
    # ===================================================================

    async def _db_add_user(self, user: dict) -> None:
        """将用户写入 MySQL（已存在则跳过）。"""
        try:
            async with self._session_scope() as session:
                existing = await session.get(User, user["user_id"])
                if existing is not None:
                    return
                session.add(
                    User(
                        user_id=user["user_id"],
                        username=user["username"],
                        password_hash=user["password_hash"],
                        full_name=user["full_name"],
                        department_id=user.get("department"),
                        email=user.get("email"),
                        phone=user.get("phone"),
                        position=user.get("position"),
                        status=user.get("status", "active"),
                        created_at=user.get("created_at") or datetime.now(timezone.utc),
                        last_login=user.get("last_login"),
                    )
                )
        except Exception as e:
            logger.error(
                "rbac.db_add_user_failed",
                user_id=user.get("user_id"),
                error=str(e),
            )

    async def _db_update_user(self, user_id: str, fields: dict) -> None:
        """更新 MySQL 中的用户字段。"""
        if not fields:
            return
        col_values: dict[str, Any] = {}
        for key, value in fields.items():
            col = self._USER_FIELD_TO_COLUMN.get(key)
            if col is not None:
                col_values[col] = value
        if not col_values:
            return
        try:
            async with self._session_scope() as session:
                stmt = (
                    update(User)
                    .where(User.user_id == user_id)
                    .values(**col_values)
                )
                await session.execute(stmt)
        except Exception as e:
            logger.error(
                "rbac.db_update_user_failed",
                user_id=user_id,
                fields=list(fields.keys()),
                error=str(e),
            )

    async def _db_delete_user(self, user_id: str) -> None:
        """从 MySQL 删除用户及其角色映射。"""
        try:
            async with self._session_scope() as session:
                await session.execute(
                    delete(UserRole).where(UserRole.user_id == user_id)
                )
                await session.execute(delete(User).where(User.user_id == user_id))
        except Exception as e:
            logger.error("rbac.db_delete_user_failed", user_id=user_id, error=str(e))

    async def _db_add_role(self, role: dict) -> None:
        """将角色写入 MySQL（已存在则跳过）。"""
        try:
            async with self._session_scope() as session:
                existing = await session.get(Role, role["role_id"])
                if existing is not None:
                    return
                session.add(
                    Role(
                        role_id=role["role_id"],
                        role_code=role["role_code"],
                        role_name=role["role_name"],
                        level=role["level"],
                        parent_role_id=role.get("parent_role_id"),
                        description=role.get("description", ""),
                    )
                )
        except Exception as e:
            logger.error(
                "rbac.db_add_role_failed",
                role_code=role.get("role_code"),
                error=str(e),
            )

    async def _db_add_permission(self, perm: dict) -> None:
        """将权限写入 MySQL（已存在则跳过）。"""
        try:
            async with self._session_scope() as session:
                existing = await session.get(Permission, perm["permission_id"])
                if existing is not None:
                    return
                session.add(
                    Permission(
                        permission_id=perm["permission_id"],
                        resource=perm["resource"],
                        action=perm["action"],
                        description=perm.get("description", ""),
                        sensitivity=perm.get("sensitivity", "normal"),
                    )
                )
        except Exception as e:
            logger.error(
                "rbac.db_add_permission_failed",
                permission_id=perm.get("permission_id"),
                error=str(e),
            )

    async def _db_add_department(self, dept: dict) -> None:
        """将部门写入 MySQL（已存在则跳过）。"""
        try:
            async with self._session_scope() as session:
                existing = await session.get(Department, dept["department_id"])
                if existing is not None:
                    return
                session.add(
                    Department(
                        department_id=dept["department_id"],
                        department_code=dept["department_code"],
                        department_name=dept["department_name"],
                        parent_department_id=dept.get("parent_department_id"),
                    )
                )
        except Exception as e:
            logger.error(
                "rbac.db_add_department_failed",
                department_id=dept.get("department_id"),
                error=str(e),
            )

    async def _db_assign_role(self, user_id: str, role_code: str) -> None:
        """写入 user_role 映射（已存在则跳过）。"""
        try:
            async with self._session_scope() as session:
                stmt = select(UserRole).where(
                    UserRole.user_id == user_id,
                    UserRole.role_code == role_code,
                )
                result = await session.execute(stmt)
                if result.scalars().first() is not None:
                    return
                session.add(UserRole(user_id=user_id, role_code=role_code))
        except Exception as e:
            logger.error(
                "rbac.db_assign_role_failed",
                user_id=user_id,
                role_code=role_code,
                error=str(e),
            )

    async def _db_revoke_role(self, user_id: str, role_code: str) -> None:
        """删除 user_role 映射。"""
        try:
            async with self._session_scope() as session:
                await session.execute(
                    delete(UserRole).where(
                        UserRole.user_id == user_id,
                        UserRole.role_code == role_code,
                    )
                )
        except Exception as e:
            logger.error(
                "rbac.db_revoke_role_failed",
                user_id=user_id,
                role_code=role_code,
                error=str(e),
            )

    async def _db_assign_permission(self, role_code: str, permission_id: str) -> None:
        """写入 role_permission 映射（已存在则跳过）。"""
        try:
            async with self._session_scope() as session:
                stmt = select(RolePermission).where(
                    RolePermission.role_code == role_code,
                    RolePermission.permission_id == permission_id,
                )
                result = await session.execute(stmt)
                if result.scalars().first() is not None:
                    return
                session.add(
                    RolePermission(role_code=role_code, permission_id=permission_id)
                )
        except Exception as e:
            logger.error(
                "rbac.db_assign_permission_failed",
                role_code=role_code,
                permission_id=permission_id,
                error=str(e),
            )

    # ===================================================================
    # 用户管理
    # ===================================================================

    def add_user(
        self,
        user_id: str,
        username: str,
        password_hash: str,
        full_name: str,
        department: str,
        email: str,
    ) -> dict:
        """
        新增用户。

        先写入内存缓存，再通过 fire-and-forget 异步写入 MySQL。

        Args:
            user_id:       用户唯一标识
            username:      登录用户名（唯一）
            password_hash: 已哈希的密码（由 PasswordHasher 生成）
            full_name:     用户全名
            department:    所属部门名称
            email:         邮箱地址

        Returns:
            新创建的用户信息字典（不含 password_hash）

        Raises:
            ValueError: user_id 或 username 已存在
        """
        if user_id in self._users_cache:
            raise ValueError(f"用户ID已存在: {user_id}")
        if username in self._username_cache:
            raise ValueError(f"用户名已存在: {username}")

        now = datetime.now(timezone.utc)
        user = {
            "user_id": user_id,
            "username": username,
            "password_hash": password_hash,
            "full_name": full_name,
            "department": department,
            "email": email,
            "phone": None,
            "position": None,
            "status": "active",
            "created_at": now,
            "last_login": None,
        }
        self._users_cache[user_id] = user
        self._username_cache[username] = user_id
        self._user_roles_cache[user_id] = set()

        # 异步写入 DB（fire-and-forget）
        self._fire_and_forget(self._db_add_user(user))

        logger.info(
            "rbac.user_added",
            user_id=user_id,
            username=username,
            department=department,
        )
        return self._user_to_dict(user)

    def get_user(self, user_id: str) -> dict | None:
        """
        按 user_id 获取用户信息（从内存缓存同步返回）。

        Returns:
            用户信息字典，不存在返回 None
        """
        user = self._users_cache.get(user_id)
        if user is None:
            return None
        return self._user_to_dict(user)

    def get_user_by_username(self, username: str) -> dict | None:
        """
        按用户名获取用户信息（登录认证用，从内存缓存同步返回）。

        Returns:
            用户信息字典，不存在返回 None
        """
        user_id = self._username_cache.get(username)
        if user_id is None:
            return None
        return self._user_to_dict(self._users_cache[user_id])

    def update_user(self, user_id: str, **fields) -> dict:
        """
        更新用户信息。

        先更新内存缓存，再通过 fire-and-forget 异步写入 MySQL。

        支持更新的字段：username, password_hash, full_name, department,
        email, phone, position, status, last_login

        Args:
            user_id: 用户ID
            **fields: 要更新的字段

        Returns:
            更新后的用户信息字典

        Raises:
            KeyError: 用户不存在
            ValueError: 新用户名与其他用户冲突
        """
        user = self._users_cache.get(user_id)
        if user is None:
            raise KeyError(f"用户不存在: {user_id}")

        allowed_fields = {
            "username", "password_hash", "full_name", "department",
            "email", "phone", "position", "status", "last_login",
        }

        applied: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed_fields:
                logger.warning("rbac.user_update_ignored_field", user_id=user_id, field=key)
                continue
            # 用户名变更时维护索引
            if key == "username" and value != user["username"]:
                if value in self._username_cache:
                    raise ValueError(f"用户名已存在: {value}")
                del self._username_cache[user["username"]]
                self._username_cache[value] = user_id
            user[key] = value
            applied[key] = value

        if applied:
            self._fire_and_forget(self._db_update_user(user_id, applied))

        logger.info("rbac.user_updated", user_id=user_id, fields=list(fields.keys()))
        return self._user_to_dict(user)

    def delete_user(self, user_id: str) -> bool:
        """
        删除用户（同时清理角色映射与缓存，并异步删除 DB 记录）。

        Returns:
            删除成功返回 True，用户不存在返回 False
        """
        user = self._users_cache.get(user_id)
        if user is None:
            return False

        username = user["username"]
        del self._users_cache[user_id]
        del self._username_cache[username]
        self._user_roles_cache.pop(user_id, None)

        # 异步删除 DB 记录（含 user_role 映射）
        self._fire_and_forget(self._db_delete_user(user_id))

        logger.info("rbac.user_deleted", user_id=user_id, username=username)
        return True

    def list_users(self) -> list[dict]:
        """返回全部用户信息列表（从内存缓存）。"""
        return [self._user_to_dict(u) for u in self._users_cache.values()]

    # ===================================================================
    # 角色-用户分配
    # ===================================================================

    def assign_role(self, user_id: str, role_code: str) -> bool:
        """
        为用户分配角色。

        更新缓存后异步写入 user_role 表。

        Args:
            user_id:    用户ID
            role_code:  角色代码

        Returns:
            分配成功返回 True；用户或角色不存在返回 False
        """
        if user_id not in self._users_cache:
            logger.warning("rbac.assign_role_user_not_found", user_id=user_id)
            return False
        if role_code not in self._roles_cache:
            logger.warning("rbac.assign_role_not_found", role_code=role_code)
            return False

        self._user_roles_cache[user_id].add(role_code)

        # 异步写入 user_role 表
        self._fire_and_forget(self._db_assign_role(user_id, role_code))

        logger.info("rbac.role_assigned", user_id=user_id, role_code=role_code)
        return True

    def revoke_role(self, user_id: str, role_code: str) -> bool:
        """
        收回用户的角色。

        更新缓存后异步删除 user_role 表记录。

        Returns:
            收回成功返回 True；用户不存在或未分配该角色返回 False
        """
        if user_id not in self._users_cache:
            return False
        roles = self._user_roles_cache.get(user_id, set())
        if role_code not in roles:
            return False
        roles.discard(role_code)

        # 异步删除 user_role 表记录
        self._fire_and_forget(self._db_revoke_role(user_id, role_code))

        logger.info("rbac.role_revoked", user_id=user_id, role_code=role_code)
        return True

    def get_user_roles(self, user_id: str) -> list[dict]:
        """
        获取用户已分配的角色列表（从内存缓存）。

        Returns:
            角色信息字典列表；用户不存在返回空列表
        """
        if user_id not in self._users_cache:
            return []
        role_codes = self._user_roles_cache.get(user_id, set())
        return [
            self._role_to_dict(self._roles_cache[rc])
            for rc in sorted(role_codes)
            if rc in self._roles_cache
        ]

    # ===================================================================
    # 权限查询与检查
    # ===================================================================

    def get_user_permissions(self, user_id: str) -> set[str]:
        """
        获取用户的所有权限（聚合所有角色的权限取并集，从内存缓存）。

        返回 ``resource:action`` 字符串集合，如 ``{"accounting:read", "treasury:approve"}``。

        Returns:
            权限ID集合；用户不存在返回空集合
        """
        if user_id not in self._users_cache:
            return set()

        role_codes = self._user_roles_cache.get(user_id, set())
        permissions: set[str] = set()
        for role_code in role_codes:
            permissions.update(self._role_permissions_cache.get(role_code, set()))
        return permissions

    def check_permission(self, user_id: str, resource: str, action: str) -> bool:
        """
        检查用户是否拥有指定资源的指定操作权限（从内存缓存）。

        ``manage`` 动作隐含该资源的全部动作：
        若用户拥有 ``resource:manage``，则对任意 action 均返回 True。

        Args:
            user_id:  用户ID
            resource: 资源名称（如 ``accounting``）
            action:   动作名称（如 ``read`` / ``approve``）

        Returns:
            有权限返回 True，否则返回 False
        """
        permissions = self.get_user_permissions(user_id)

        # 精确匹配
        if f"{resource}:{action}" in permissions:
            return True

        # manage 通配：拥有 manage 则隐含全部动作
        if action != "manage" and f"{resource}:manage" in permissions:
            return True

        return False

    # ===================================================================
    # 角色管理
    # ===================================================================

    def add_role(self, role: dict) -> dict:
        """
        新增角色。

        先写入内存缓存，再通过 fire-and-forget 异步写入 MySQL。

        Args:
            role: 角色定义字典，包含 role_id, role_code, role_name,
                  level, parent_role_id, description

        Returns:
            新创建的角色信息字典

        Raises:
            ValueError: role_code 已存在
        """
        role_code = role["role_code"]
        if role_code in self._roles_cache:
            raise ValueError(f"角色代码已存在: {role_code}")

        role_data = {
            "role_id": role["role_id"],
            "role_code": role_code,
            "role_name": role["role_name"],
            "level": role["level"],
            "parent_role_id": role.get("parent_role_id"),
            "description": role.get("description", ""),
        }
        self._roles_cache[role_code] = role_data
        self._role_permissions_cache[role_code] = set()

        # 异步写入 DB
        self._fire_and_forget(self._db_add_role(role_data))

        logger.info(
            "rbac.role_added",
            role_code=role_code,
            role_name=role_data["role_name"],
            level=role_data["level"],
        )
        return self._role_to_dict(role_data)

    def get_role(self, role_code: str) -> dict | None:
        """
        按 role_code 获取角色信息（从内存缓存）。

        Returns:
            角色信息字典，不存在返回 None
        """
        role = self._roles_cache.get(role_code)
        if role is None:
            return None
        return self._role_to_dict(role)

    def list_roles(self) -> list[dict]:
        """返回全部角色信息列表（按 level 升序，从内存缓存）。"""
        roles = sorted(
            self._roles_cache.values(),
            key=lambda r: (r["level"], r["role_code"]),
        )
        return [self._role_to_dict(r) for r in roles]

    # ===================================================================
    # 权限管理
    # ===================================================================

    def add_permission(self, permission: dict) -> dict:
        """
        新增权限定义。

        先写入内存缓存，再通过 fire-and-forget 异步写入 MySQL。

        Args:
            permission: 权限字典，包含 permission_id, resource, action,
                        description, sensitivity

        Returns:
            新创建的权限信息字典

        Raises:
            ValueError: permission_id 已存在
        """
        perm_id = permission["permission_id"]
        if perm_id in self._permissions_cache:
            raise ValueError(f"权限ID已存在: {perm_id}")

        perm_data = {
            "permission_id": perm_id,
            "resource": permission["resource"],
            "action": permission["action"],
            "description": permission.get("description", ""),
            "sensitivity": permission.get("sensitivity", "normal"),
        }
        self._permissions_cache[perm_id] = perm_data

        # 异步写入 DB
        self._fire_and_forget(self._db_add_permission(perm_data))

        return dict(perm_data)

    def list_permissions(self) -> list[dict]:
        """返回全部权限定义列表（按 resource、action 排序，从内存缓存）。"""
        perms = sorted(
            self._permissions_cache.values(),
            key=lambda p: (p["resource"], p["action"]),
        )
        return [dict(p) for p in perms]

    def assign_permission_to_role(self, role_code: str, permission_id: str) -> bool:
        """
        将权限分配给角色。

        更新缓存后异步写入 role_permission 表。

        Args:
            role_code:    角色代码
            permission_id: 权限ID（``resource:action`` 格式）

        Returns:
            分配成功返回 True；角色或权限不存在返回 False
        """
        if role_code not in self._roles_cache:
            logger.warning("rbac.assign_perm_role_not_found", role_code=role_code)
            return False
        if permission_id not in self._permissions_cache:
            logger.warning("rbac.assign_perm_not_found", permission_id=permission_id)
            return False

        self._role_permissions_cache[role_code].add(permission_id)

        # 异步写入 role_permission 表
        self._fire_and_forget(self._db_assign_permission(role_code, permission_id))

        return True

    # ===================================================================
    # 部门管理
    # ===================================================================

    def add_department(self, department: dict) -> dict:
        """
        新增部门。

        先写入内存缓存，再通过 fire-and-forget 异步写入 MySQL。

        Args:
            department: 部门字典，包含 department_id, department_code,
                       department_name, parent_department_id

        Returns:
            新创建的部门信息字典

        Raises:
            ValueError: department_id 已存在
        """
        dept_id = department["department_id"]
        if dept_id in self._departments_cache:
            raise ValueError(f"部门ID已存在: {dept_id}")

        dept_data = {
            "department_id": dept_id,
            "department_code": department["department_code"],
            "department_name": department["department_name"],
            "parent_department_id": department.get("parent_department_id"),
        }
        self._departments_cache[dept_id] = dept_data

        # 异步写入 DB
        self._fire_and_forget(self._db_add_department(dept_data))

        return dict(dept_data)

    def list_departments(self) -> list[dict]:
        """返回全部部门信息列表（从内存缓存）。"""
        return [dict(d) for d in self._departments_cache.values()]

    # ===================================================================
    # 辅助方法
    # ===================================================================

    def get_stats(self) -> dict:
        """获取 RBAC 引擎统计信息（基于内存缓存）。"""
        return {
            "users": len(self._users_cache),
            "roles": len(self._roles_cache),
            "permissions": len(self._permissions_cache),
            "departments": len(self._departments_cache),
            "role_permission_mappings": sum(len(v) for v in self._role_permissions_cache.values()),
            "user_role_mappings": sum(len(v) for v in self._user_roles_cache.values()),
            "initialized": self._initialized,
        }

    def clear(self) -> None:
        """
        清空所有内存数据（测试隔离用）。

        仅清空缓存，不删除 DB 数据。重置 ``_initialized`` 标记。
        由于向后兼容别名指向同一对象，``.clear()`` 同时清空新旧名称。
        """
        self._clear_caches()
        self._initialized = False
        logger.info("rbac.cache_cleared")

    def _clear_caches(self) -> None:
        """清空所有缓存 dict / set（原地清空，保留别名引用关系）。"""
        self._users_cache.clear()
        self._username_cache.clear()
        self._roles_cache.clear()
        self._permissions_cache.clear()
        self._role_permissions_cache.clear()
        self._user_roles_cache.clear()
        self._departments_cache.clear()

    # ------------------------------------------------------------------
    # 内部转换方法
    # ------------------------------------------------------------------

    @staticmethod
    def _user_to_dict(user: dict) -> dict:
        """将内部用户存储转换为返回字典（隐藏 password_hash）。"""
        return {
            "user_id": user["user_id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "department": user["department"],
            "email": user["email"],
            "phone": user.get("phone"),
            "position": user.get("position"),
            "status": user["status"],
            "created_at": user["created_at"].isoformat() if isinstance(user["created_at"], datetime) else user["created_at"],
            "last_login": user["last_login"].isoformat() if isinstance(user.get("last_login"), datetime) else user.get("last_login"),
        }

    @staticmethod
    def _role_to_dict(role: dict) -> dict:
        """将内部角色存储转换为返回字典。"""
        return dict(role)
