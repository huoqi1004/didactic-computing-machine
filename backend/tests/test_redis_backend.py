"""
Redis 落地测试（评估报告 A-01/D-03 / 路线图任务 2.5、2.6）
==========================================================

使用 fakeredis 验证：
1. 会话跨实例共享：实例 A 登录的会话实例 B 可查（Redis L2 兜底）；
2. 吊销跨实例生效：实例 A 登出后，实例 B 的旧令牌立即失效；
3. 限流跨实例计数：两个限流器共享 Redis 计数，全局阈值一致；
4. Redis 不可用时 fail-open：会话/限流/RBAC 全部降级进程内行为，不放大故障；
5. RBAC 版本信号：变更递增版本号，ensure_fresh 检测漂移并触发重载。
"""
import asyncio

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.core import redis_store
from app.core.auth.session import SessionManager
from app.api.middleware.rate_limit import RateLimitRule, RateLimiter


@pytest_asyncio.fixture
async def fake_redis(monkeypatch):
    """将全局 Redis 客户端替换为 fakeredis。"""
    import app.core.database as database

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(database, "_redis_client", client)
    yield client
    await client.aclose()


# ===========================================================================
# 会话跨实例
# ===========================================================================

def _make_jwt_like_token(user_id: str, ttl_seconds: int = 1800) -> str:
    """构造带 exp 的仿 JWT 令牌（会话管理器只读载荷，不验签）。"""
    import base64
    import json
    import time

    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "exp": int(time.time()) + ttl_seconds}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.signature"


async def test_session_shared_across_instances(fake_redis):
    """实例 A 创建的会话，实例 B 可通过 Redis L2 查到。"""
    mgr_a = SessionManager()
    mgr_b = SessionManager()

    token = _make_jwt_like_token("u_cross")
    mgr_a.create_session("u_cross", token)
    # 等 fire-and-forget 的 Redis 写入完成
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    session = await mgr_b.get_session(token)
    assert session is not None
    assert session["user_id"] == "u_cross"


async def test_logout_revokes_token_across_instances(fake_redis):
    """实例 A 登出后，实例 B 的同一令牌立即失效（共享黑名单）。"""
    mgr_a = SessionManager()
    mgr_b = SessionManager()

    token = _make_jwt_like_token("u_logout")
    mgr_a.create_session("u_logout", token)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # 实例 B 先确认会话可见
    assert await mgr_b.get_session(token) is not None

    # 实例 A 登出
    await mgr_a.invalidate_session(token)

    # 实例 B 立即失效（黑名单生效，即使 JWT 未过期）
    assert await mgr_b.get_session(token) is None


async def test_session_fails_open_without_redis(monkeypatch):
    """Redis 不可用：会话退化为纯内存行为，登录/查询/登出仍正常。"""
    import app.core.database as database

    monkeypatch.setattr(database, "_redis_client", None)

    mgr = SessionManager()
    token = _make_jwt_like_token("u_mem")
    session = mgr.create_session("u_mem", token)
    assert session["user_id"] == "u_mem"
    assert await mgr.get_session(token) is not None
    assert await mgr.invalidate_session(token) is True
    assert await mgr.get_session(token) is None


# ===========================================================================
# 限流跨实例
# ===========================================================================

async def test_rate_limit_shared_across_instances(fake_redis):
    """两个限流器（模拟两个实例）共享 Redis 计数，全局阈值一致。"""
    rule = RateLimitRule(name="auth", max_requests=3, window_seconds=60)
    limiter_a = RateLimiter()
    limiter_b = RateLimiter()

    results = []
    # 交替经两个实例发起 5 次请求
    for i in range(5):
        limiter = limiter_a if i % 2 == 0 else limiter_b
        allowed, retry_after = await limiter.check(rule, "1.2.3.4")
        results.append(allowed)

    assert results == [True, True, True, False, False], (
        "跨实例计数未共享：第 4、5 次请求应被全局限流"
    )


async def test_rate_limit_fails_open_to_memory(monkeypatch):
    """Redis 不可用：限流降级进程内滑动窗口，阈值语义不变。"""
    import app.core.database as database

    monkeypatch.setattr(database, "_redis_client", None)

    rule = RateLimitRule(name="auth", max_requests=3, window_seconds=60)
    limiter = RateLimiter()
    results = [await limiter.check(rule, "5.6.7.8") for _ in range(5)]
    assert [r[0] for r in results] == [True, True, True, False, False]
    assert results[3][1] >= 1


# ===========================================================================
# RBAC 版本信号
# ===========================================================================

async def test_rbac_version_bump_and_drift_detection(fake_redis, monkeypatch):
    """变更递增版本号；ensure_fresh 检测到漂移后触发 load_from_db 重载。"""
    from app.core.rbac.engine import RBACEngine

    engine = RBACEngine()

    reload_calls = 0
    original_load = engine.load_from_db

    async def _counting_load():
        nonlocal reload_calls
        reload_calls += 1
        await original_load()

    monkeypatch.setattr(engine, "load_from_db", _counting_load)
    engine._rbac_version_checked_at = 0.0  # 重置节流

    # 无变更：不重载
    await engine.ensure_fresh()
    assert reload_calls == 0
    assert engine._rbac_version_local == 0

    # 模拟其他实例的 RBAC 变更：版本号递增
    await redis_store.bump_rbac_version()
    engine._rbac_version_checked_at = 0.0
    await engine.ensure_fresh()
    assert reload_calls == 1, "版本漂移应触发重载"
    assert engine._rbac_version_local == 1

    # 版本一致后不再重载（节流窗口外）
    engine._rbac_version_checked_at = 0.0
    await engine.ensure_fresh()
    assert reload_calls == 1


async def test_rbac_ensure_fresh_fails_open_without_redis(monkeypatch):
    """Redis 不可用：ensure_fresh 直接返回，不触发重载（单实例行为）。"""
    import app.core.database as database
    from app.core.rbac.engine import RBACEngine

    monkeypatch.setattr(database, "_redis_client", None)

    engine = RBACEngine()
    reload_calls = 0

    async def _counting_load():
        nonlocal reload_calls
        reload_calls += 1

    monkeypatch.setattr(engine, "load_from_db", _counting_load)
    engine._rbac_version_checked_at = 0.0

    await engine.ensure_fresh()
    assert reload_calls == 0
