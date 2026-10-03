"""
生产化整改 P1 测试（production-acceptance-review-2026-10-03 整改回签）
======================================================================

覆盖：P1-3 JWT 密钥 fail-closed、P1-4 CORS 生产护栏、P1-2 登录防爆破
（递进锁定+成功清零）、P1-5 权限模型（ceo 通配收紧/专用角色补授权/
log_auditor 不变）。P1-1（会话 Redis 双层）经复核为已达成，见回签章。
"""
import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.security.startup_checks import sanitize_cors_origins, validate_jwt_secret


# ===========================================================================
# P1-3 JWT 密钥 fail-closed
# ===========================================================================

def test_jwt_missing_in_production_refuses():
    with pytest.raises(RuntimeError, match="ERP_JWT_SECRET"):
        validate_jwt_secret("", debug=False)


def test_jwt_missing_in_debug_generates_ephemeral():
    s1 = validate_jwt_secret("", debug=True)
    s2 = validate_jwt_secret("", debug=True)
    assert len(s1) >= 32 and s1 != s2, "debug 应生成临时随机密钥（每次不同）"


def test_jwt_weak_secret_in_production_refuses():
    with pytest.raises(RuntimeError, match="熵不足"):
        validate_jwt_secret("short", debug=False)


def test_jwt_valid_secret_passthrough():
    secret = "x" * 32
    assert validate_jwt_secret(secret, debug=False) == secret
    assert validate_jwt_secret(secret, debug=True) == secret


# ===========================================================================
# P1-4 CORS 生产护栏
# ===========================================================================

def test_cors_strips_localhost_in_production():
    origins = ["http://localhost:5173", "http://127.0.0.1:3000",
               "https://erp.example.com"]
    out = sanitize_cors_origins(origins, debug=False)
    assert out == ["https://erp.example.com"]


def test_cors_keeps_localhost_in_debug():
    origins = ["http://localhost:5173", "https://erp.example.com"]
    assert sanitize_cors_origins(origins, debug=True) == origins


# ===========================================================================
# P1-2 登录防爆破
# ===========================================================================

def _login_key(username="bruteforced_user", ip="10.0.0.1"):
    return f"{username}|{ip}"


def test_login_lockout_progressive_and_reset():
    from app.api.routes.auth import (_LOGIN_FAIL_THRESHOLD,
                                     _login_lock_remaining,
                                     _record_login_failure)
    key = _login_key("lock_user_a")

    # 阈值前：不锁定
    for i in range(_LOGIN_FAIL_THRESHOLD - 1):
        assert _record_login_failure(key) == 0
    assert _login_lock_remaining(key) == 0

    # 第 5 次：触发 2s 锁定；窗口内再失败 → 指数递增（4s，封顶 60s）
    assert _record_login_failure(key) >= 2
    assert _login_lock_remaining(key) >= 1
    assert _record_login_failure(key) >= 2  # 锁定窗口内继续失败 → 更长锁定

    # 成功登录清零
    from app.api.routes import auth as auth_mod

    auth_mod._login_failures.pop(key, None)
    auth_mod._login_lock_until.pop(key, None)
    assert _login_lock_remaining(key) == 0


def test_login_lockout_isolates_users_and_ips():
    from app.api.routes.auth import (_LOGIN_FAIL_THRESHOLD,
                                     _login_lock_remaining,
                                     _record_login_failure)
    for i in range(_LOGIN_FAIL_THRESHOLD):
        _record_login_failure(_login_key("lock_user_b", "10.0.0.2"))
    assert _login_lock_remaining(_login_key("lock_user_b", "10.0.0.2")) > 0
    # 其他用户 / 其他 IP 不受影响
    assert _login_lock_remaining(_login_key("lock_user_b", "10.0.0.3")) == 0
    assert _login_lock_remaining(_login_key("other_user", "10.0.0.2")) == 0


async def test_login_route_returns_429_after_threshold(sqlite_env, monkeypatch):
    """路由级：连续失败达阈值 → 429 + Retry-After（而非一直 401）。"""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.routes.auth import _LOGIN_FAIL_THRESHOLD, login, LoginRequest

    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.66"))
    pwd = "definitely-wrong"
    codes = []
    for i in range(_LOGIN_FAIL_THRESHOLD + 1):
        try:
            await login(LoginRequest(username="admin", password=pwd), request=request)
            codes.append(200)
        except HTTPException as e:
            codes.append(e.status_code)
    # 前 4 次 401；第 5 次（达到阈值）即锁定返回 429，其后持续 429
    assert codes[:_LOGIN_FAIL_THRESHOLD - 1] == [401] * (_LOGIN_FAIL_THRESHOLD - 1)
    assert all(c == 429 for c in codes[_LOGIN_FAIL_THRESHOLD - 1:]), codes


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "hardening_g.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


@pytest.fixture(autouse=True)
def _reset_lock_state():
    from app.api.routes import auth as auth_mod

    auth_mod._login_failures.clear()
    auth_mod._login_lock_until.clear()
    yield
    auth_mod._login_failures.clear()
    auth_mod._login_lock_until.clear()


# ===========================================================================
# P1-5 权限模型
# ===========================================================================

def test_seed_ceo_system_wildcard_removed():
    """ceo 兜底通配收紧：system 只读，管理台操作权收归 cio（决策留痕见 seed）。"""
    from app.core.rbac.seed import ROLE_PERMISSION_MATRIX

    assert ROLE_PERMISSION_MATRIX["ceo"]["system"] == {"read"}
    assert ROLE_PERMISSION_MATRIX["cio"]["system"] == {"read", "create", "update",
                                                      "delete", "approve", "manage"}


def test_seed_dedicated_customization_roles_can_enter_admin():
    """专用角色补 system:manage（进管理台）+ agent 权限——职责分离名实一致。"""
    from app.core.rbac.seed import ROLE_PERMISSION_MATRIX

    admin = ROLE_PERMISSION_MATRIX["customization_admin"]
    approver = ROLE_PERMISSION_MATRIX["customization_approver"]
    assert "manage" in admin["system"] and "read" in admin["agent"]
    assert "manage" in approver["system"]
    assert {"read", "approve"} <= approver["agent"]


def test_seed_log_auditor_readonly_unchanged():
    from app.core.rbac.seed import ROLE_PERMISSION_MATRIX

    la = ROLE_PERMISSION_MATRIX["log_auditor"]
    assert set(la.keys()) == {"operationlog", "operation_rollback", "audit_verify"}
    assert "read" in la["operationlog"] and "run" in la["audit_verify"]
