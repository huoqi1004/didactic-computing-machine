"""
种子密码治理测试（评估报告 D-04 / 路线图任务 1.4）
====================================================

验证：
1. 种子用户不再使用"{username}123"规律弱密码（admin123 等登录必须失败）；
2. 随机模式下每个用户生成独立强密码，且可用其通过密码校验；
3. ERP_SEED_PASSWORD 环境变量注入模式下全部用户共用注入密码；
4. 全部种子用户标记 must_change_password，修改密码后标记清除。
"""
import pytest

from app.core.auth import PasswordHasher
from app.core.config import settings
from app.core.rbac.engine import RBACEngine
from app.core.rbac.seed import (
    SEED_PASSWORDS,
    SEED_USERS,
    _build_seed_users,
)


# 已知的旧弱密码样本（D-04 列举的"职位+123"模式）
_WEAK_PASSWORD_SAMPLES = ["admin123", "chairman123", "ceo123", "cho123", "staff123"]


async def test_seed_users_no_weak_password():
    """全部种子用户的密码哈希不匹配任何旧弱密码。"""
    assert len(SEED_USERS) >= 24
    for user in SEED_USERS:
        username = user["username"]
        # 旧密码模式：{username}123
        assert not PasswordHasher.verify_password(
            f"{username}123", user["password_hash"]
        ), f"种子用户 {username} 仍在使用弱密码 {{username}}123"
    # 典型弱密码样本逐一验证（对任意用户都不匹配）
    for user in SEED_USERS:
        for weak in _WEAK_PASSWORD_SAMPLES:
            assert not PasswordHasher.verify_password(
                weak, user["password_hash"]
            )


async def test_seed_users_must_change_password():
    """全部种子用户标记 must_change_password。"""
    for user in SEED_USERS:
        assert user.get("must_change_password") is True, (
            f"种子用户 {user['username']} 缺少首登强制改密标记"
        )


async def test_random_passwords_generated_and_usable():
    """随机模式下每个种子用户都有独立强密码，且能通过密码校验。"""
    usernames = {u["username"] for u in SEED_USERS}
    assert set(SEED_PASSWORDS.keys()) >= usernames
    # 独立性：24 个用户密码互不相同
    assert len(set(SEED_PASSWORDS.values())) == len(SEED_PASSWORDS)
    # 强度：token_urlsafe(9) 产生 12 字符密码
    for password in SEED_PASSWORDS.values():
        assert len(password) >= 12
    # 可用性：随机密码能通过对应哈希的校验
    for user in SEED_USERS:
        assert PasswordHasher.verify_password(
            SEED_PASSWORDS[user["username"]], user["password_hash"]
        )


async def test_env_seed_password_mode(monkeypatch):
    """ERP_SEED_PASSWORD 注入模式下全部用户共用注入密码。"""
    monkeypatch.setattr(settings, "seed_password", "TestEnvPass#2026")
    try:
        users = _build_seed_users()
        assert len(users) == len(SEED_USERS)
        for user in users:
            assert PasswordHasher.verify_password(
                "TestEnvPass#2026", user["password_hash"]
            )
            assert not PasswordHasher.verify_password(
                f"{user['username']}123", user["password_hash"]
            )
    finally:
        monkeypatch.undo()


async def test_change_password_clears_flag():
    """修改密码后 must_change_password 标记清除，新密码生效。"""
    engine = RBACEngine()
    engine.add_user(
        user_id="u_test_pw",
        username="pw_tester",
        password_hash=PasswordHasher.hash_password(SEED_PASSWORDS["staff"]),
        full_name="改密测试",
        department="信息部",
        email="pw_tester@erp.com",
        must_change_password=True,
    )
    assert engine.get_user("u_test_pw")["must_change_password"] is True

    new_hash = PasswordHasher.hash_password("NewStrongPass#2026")
    engine.update_user("u_test_pw", password_hash=new_hash, must_change_password=False)

    updated = engine.get_user("u_test_pw")
    assert updated["must_change_password"] is False
    assert PasswordHasher.verify_password("NewStrongPass#2026", new_hash)
