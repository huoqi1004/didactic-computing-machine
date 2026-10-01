"""
定制热更新并发复验（路线图 Phase F5 / F5.3 的"含定制热更新并发场景"腿）
==========================================================================

F5.3 的压测有两半：HTTP 全链路（tests/perf/loadtest_f53.py，需要真跑服务）
和**热更新一致性**——后者是纯进程内并发语义，必须在 CI 里可复现，
否则"50 并发下改定制会不会读到半新半旧的缓存"只能靠人肉看日志。

这里测的是 `CustomizationService` 的缓存/版本信号三条不变量：
1. 收敛：写一次生效变更后，另一个实例经版本信号能看到（多实例失效，§2.4）；
2. 不丢写：重载快照与本进程写入交错时，写入不能被永久掩掉；
3. 审计完整：并发写 100% 落版本流（红线 3）。

DB 用一次性 sqlite（settings.mysql_url 覆盖），版本信号用 fakeredis——
fakeredis 就是"两个实例共用一份 Redis"的等价物，不需要本机起 redis。
"""
import asyncio
import contextlib
import time

import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def f53_env(tmp_path, monkeypatch):
    """临时 sqlite + fakeredis 版本信号 + RBAC 放行；返回可复用的会话构造器。"""
    import fakeredis.aioredis

    import app.core.database as database

    db_file = tmp_path / "hot_reload_f53.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    # 版本信号：fakeredis 充当多实例共享的那一份 Redis
    monkeypatch.setattr(database, "_redis_client",
                        fakeredis.aioredis.FakeRedis(decode_responses=True),
                        raising=False)

    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    from app.core.agent.customization import CustomizationService

    def new_svc() -> CustomizationService:
        return CustomizationService()

    yield new_svc

    client = database._redis_client
    if client is not None and hasattr(client, "aclose"):
        await client.aclose()
    monkeypatch.setattr(database, "_redis_client", None, raising=False)
    await database.close_db()


async def _rows(ctype: str, target: str) -> list[dict]:
    from sqlalchemy import select

    from app.db.models import agent_customizations

    from app.db.repositories.base import session_scope

    async with session_scope() as session:
        rs = (await session.execute(
            select(agent_customizations).where(
                agent_customizations.c.type == ctype,
                agent_customizations.c.target == target)
        )).all()
    return [dict(r._mapping) for r in rs]


async def _version_rows(ctype: str, target: str) -> list[dict]:
    from sqlalchemy import select

    from app.db.models import customization_versions

    from app.db.repositories.base import session_scope

    async with session_scope() as session:
        rs = (await session.execute(
            select(customization_versions).where(
                customization_versions.c.type == ctype,
                customization_versions.c.target == target)
        )).all()
    return [dict(r._mapping) for r in rs]


async def _write(svc, target: str, value: float, by: str = "user_admin") -> dict:
    """agent 类型变更恒为收紧（即时生效），是并发场景最干净的写入原语。"""
    return await svc.apply_change("agent", target, {"temperature": value},
                                  changed_by=by, comment="F5.3")


# ===========================================================================
# 1. 版本信号口径：str/int 归一 + 节流计数
# ===========================================================================

async def test_version_signal_normalized_to_int(f53_env):
    """
    redis 存 int、读回 str（redis-py decode_responses）——"7" != 7 恒真，
    于是本进程每写完一次都要多重载一遍缓存（多一次全表读）。
    归一成 int 后，无漂移就不许重载。
    """
    from app.core import redis_store

    svc = f53_env()
    assert await svc._read_version_signal() is None, "无信号时必须返回 None（走降级分支）"

    await redis_store.bump_key_value("erp:cust:version")
    v = await svc._read_version_signal()
    assert isinstance(v, int) and v >= 1, f"版本信号必须是 int，实测 {v!r}"

    reloads = []
    real_reload = svc._reload

    async def spy():
        reloads.append(1)
        return await real_reload()

    svc._reload = spy
    await svc.ensure_fresh()          # 首次：必须加载
    assert len(reloads) == 1
    await svc.ensure_fresh()          # 节流窗口内：不看信号
    assert len(reloads) == 1
    svc._checked_at = 0.0             # 强制越过节流
    await svc.ensure_fresh()
    assert len(reloads) == 1, "无漂移却重载：版本比较口径又漂回 str/int 了"
    await redis_store.bump_key_value("erp:cust:version")
    svc._checked_at = 0.0
    await svc.ensure_fresh()
    assert len(reloads) == 2, "有漂移必须重载"


# ===========================================================================
# 2. 收敛：另一实例经版本信号看到生效变更
# ===========================================================================

async def test_second_instance_converges_via_version_signal(f53_env):
    """A 写、B 读：B 只在版本漂移时重载（§2.4 多实例失效），收敛上界=5 秒节流。"""
    a, b = f53_env(), f53_env()
    await b.ensure_fresh()
    assert b.get_override("agent", "conv_agent") is None

    r = await _write(a, "conv_agent", 0.2)
    assert r["status"] == "applied", r
    assert a.get_override("agent", "conv_agent")["overrides"] == {"temperature": 0.2}
    assert b.get_override("agent", "conv_agent") is None, "B 不该在没检查信号前就看到"

    b._checked_at = 0.0
    await b.ensure_fresh()
    got = b.get_override("agent", "conv_agent")
    assert got and got["overrides"] == {"temperature": 0.2}, f"B 未收敛：{b._cache}"
    assert b._cache_version == a._cache_version, "两实例版本号须一致（同一信号源）"


# ===========================================================================
# 3. 不丢写：重载快照与本进程写入交错
# ===========================================================================

async def test_write_during_reload_window_is_not_permanently_lost(f53_env, monkeypatch):
    """
    热更新最危险的交错：_reload 已经读完 DB 行、还没把新 dict 挂上去时，
    本进程完成了一次生效写（_set_cache 写进**旧** dict）。
    整体替换 `self._cache = cache` 会掩掉这次写。

    判据不是"不许掩一下"（DB 里始终是对的，瞬时陈旧可接受），
    而是**不能永久丢**：下一次 ensure_fresh 必须看到漂移并把它读回来。
    丢写一旦永久化，缓存就长期停在缺那次写的状态（旧代码用读**后**的版本号
    认领旧快照，正是这个后果）。
    """
    import app.db.repositories.base as repo_base

    gate = asyncio.Event()
    in_window = asyncio.Event()
    hold = {"armed": False}
    real_scope = repo_base.session_scope

    @contextlib.asynccontextmanager
    async def gated_scope():
        async with real_scope() as session:
            yield session
        if hold["armed"]:
            # 只在 _reload 的会话上布防一次：行已读完、提交已完成，
            # 正是"新快照即将覆盖旧 dict"的那一毫秒
            hold["armed"] = False
            in_window.set()
            # 带上限地等：测试逻辑出错时这是一条失败，而不是把 CI 挂死
            await asyncio.wait_for(gate.wait(), 30)

    monkeypatch.setattr(repo_base, "session_scope", gated_scope, raising=True)

    svc = f53_env()
    await _write(svc, "race_agent", 0.1)      # 先有一条 active 行，保证重载非空

    from app.core import redis_store

    # 模拟"另一实例刚做过生效变更"：版本号漂移，本进程这一次 ensure_fresh 真会去重载
    # （自己写的变更已被 _cache_version 认领，不会触发重载——那正是修好后的语义）
    await redis_store.bump_key_value("erp:cust:version")
    svc._checked_at = 0.0
    hold["armed"] = True
    reload_task = asyncio.create_task(svc.ensure_fresh())
    await asyncio.wait_for(in_window.wait(), 10)   # 确认已停在重载窗口内
    assert not reload_task.done(), "reload 应当卡在重载窗口里"

    r = await _write(svc, "race_agent", 0.9)  # 窗口内完成一次生效写
    assert r["status"] == "applied", r
    assert svc.get_override("agent", "race_agent")["overrides"] == {"temperature": 0.9}

    gate.set()
    await asyncio.wait_for(reload_task, 10)

    stale = svc.get_override("agent", "race_agent")
    lost = stale is None or stale["overrides"].get("temperature") != 0.9
    # 这一句是"已知瞬时窗口"的记录，不是失败：整体替换语义下必然可能发生
    print(f"F5.3 重载窗口瞬时丢写={lost}（DB 真相始终正确）")

    svc._checked_at = 0.0
    await svc.ensure_fresh()
    got = svc.get_override("agent", "race_agent")
    assert got and got["overrides"] == {"temperature": 0.9}, \
        f"永久丢写：缓存停在 {got}，DB 里是 0.9"
    rows = await _rows("agent", "race_agent")
    assert len(rows) == 1 and rows[0]["overrides"]["temperature"] == 0.9, rows


# ===========================================================================
# 4. 并发写：50 路同 target + 读侧并发
# ===========================================================================

async def test_concurrent_writes_keep_db_and_cache_consistent(f53_env):
    """
    50 并发写同一 target（F5.3 场景：管理员连点/多实例同时下发）。

    不变量：DB 只有 1 行生效配置（唯一键 (type,target,environment)）、
    版本流 50 行（红线 3：100% 审计）、缓存值必须是某次真实写入的值且
    与 DB 收敛后一致。
    """
    svc = f53_env()
    n = 50
    started = time.perf_counter()
    results = await asyncio.gather(*[_write(svc, "stress_agent", i * 0.01)
                                     for i in range(n)])
    elapsed = time.perf_counter() - started

    assert [r["status"] for r in results] == ["applied"] * n, \
        f"并发写出现非 applied：{[r for r in results if r['status'] != 'applied']}"
    rows = await _rows("agent", "stress_agent")
    assert len(rows) == 1, f"同 target 必须收敛为 1 行生效配置，实测 {len(rows)}"
    versions = await _version_rows("agent", "stress_agent")
    assert len(versions) == n, f"红线 3：{n} 次变更须 {n} 行版本流，实测 {len(versions)}"

    cache_val = (svc.get_override("agent", "stress_agent") or {}).get(
        "overrides", {}).get("temperature")
    assert cache_val in {i * 0.01 for i in range(n)}, f"缓存值不是任何一次真实写入：{cache_val}"

    # 不靠"再重载一次"补救：并发写结束后本进程缓存就该等于库里那行。
    # 落库顺序与写缓存顺序错位时，最后 bump 的不一定是最后提交的 → 版本信号看不见
    # 这次分歧，缓存会永久停在被后一笔顶掉的那个值（F5.3 实测：缓存 0.27 / 库 0.32）。
    db_val = rows[0]["overrides"]["temperature"]
    assert cache_val == db_val, f"缓存({cache_val}) 与 DB({db_val}) 分歧（同 target 写未串行）"

    svc._checked_at = 0.0
    await svc.ensure_fresh()
    converged = (svc.get_override("agent", "stress_agent") or {})["overrides"]["temperature"]
    assert abs(converged - db_val) < 1e-9, f"重载后缓存({converged}) 与 DB({db_val}) 不一致"

    per_op_ms = elapsed / n * 1000
    print(f"F5.3 并发写 n={n} 总耗时 {elapsed:.2f}s 单次均值 {per_op_ms:.1f}ms"
          f"（sqlite 单文件，写者互斥）")
    assert per_op_ms < settings.agent_slo_response_time_ms, \
        f"单次变更均值 {per_op_ms:.0f}ms 超 SLO {settings.agent_slo_response_time_ms}ms"


async def test_concurrent_readers_see_no_torn_cache(f53_env):
    """
    写侧热更新与读侧并发：读到的必须是"某一次完整覆盖"，不能是半新半旧。

    读路径全是同步字典查找（不 await），asyncio 下天然原子；
    这条用例守的是这个约束被破坏的那天（例如把 get_override 改成 async 读库）。
    """
    svc = f53_env()
    n = 50
    valid = {i * 0.01 for i in range(n)}

    torn, read_count = [], 0
    stop = asyncio.Event()

    async def reader():
        nonlocal read_count
        # 跟着写侧一起跑（写完才停），否则 20 次空转在读侧还没轮到热更新就结束了
        while not stop.is_set():
            entry = svc.get_override("agent", "mix_agent")
            if entry is not None:
                read_count += 1
                v = entry["overrides"]["temperature"]
                if v not in valid:
                    torn.append(v)
            await asyncio.sleep(0.005)

    writers = [_write(svc, "mix_agent", i * 0.01) for i in range(n)]
    t0 = time.perf_counter()
    readers = [asyncio.create_task(reader()) for _ in range(2)]
    got = await asyncio.gather(*writers)
    stop.set()
    await asyncio.gather(*readers)
    elapsed = time.perf_counter() - t0

    assert not torn, f"读到不存在的覆盖值（撕裂）：{torn[:3]}"
    assert read_count > 0, "读侧一次都没命中，等于没测"
    assert len(got) == n and all(g["status"] == "applied" for g in got), got
    print(f"F5.3 读写混合 {n} 写 + 2 读循环，总耗时 {elapsed:.2f}s，有效读 {read_count} 次")


# ===========================================================================
# 5. 降级：版本信号不可用 / 重载失败不得影响生效读
# ===========================================================================

async def test_reload_failure_keeps_last_known_cache(f53_env, monkeypatch):
    """DB 读失败时保留旧缓存（失败安全），并且不进入重试风暴。"""
    import app.db.repositories.base as repo_base

    svc = f53_env()
    await _write(svc, "safe_agent", 0.5)
    svc._checked_at = 0.0
    await svc.ensure_fresh()
    before = dict(svc.get_override("agent", "safe_agent") or {})

    real_scope = repo_base.session_scope

    @contextlib.asynccontextmanager
    async def broken_scope():
        raise OSError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(repo_base, "session_scope", broken_scope, raising=True)
    from app.core import redis_store

    await redis_store.bump_key_value("erp:cust:version")   # 制造漂移，逼它真去重载
    svc._checked_at = 0.0
    await svc.ensure_fresh()
    assert svc.get_override("agent", "safe_agent") == before, "重载失败必须保留旧缓存"

    v_before = svc._cache_version
    svc._checked_at = 0.0
    await svc.ensure_fresh()
    assert svc._cache_version == v_before, "失败后认领本次版本，避免每次请求都重试（重试风暴）"
    monkeypatch.setattr(repo_base, "session_scope", real_scope, raising=True)


async def test_cache_serves_reads_when_redis_unavailable(f53_env, monkeypatch):
    """Redis 挂掉：生效路径照常读缓存，不抛错、不清空（F5.4 的前置不变量）。"""
    import app.core.database as database
    import fakeredis.aioredis

    svc = f53_env()
    await _write(svc, "no_redis_agent", 0.7)

    monkeypatch.setattr(database, "_redis_client", None, raising=False)
    svc._checked_at = 0.0
    await svc.ensure_fresh()
    got = svc.get_override("agent", "no_redis_agent")
    assert got and got["overrides"] == {"temperature": 0.7}, \
        f"信号不可用时缓存被误清空：{got}"

    # 信号读回非数字（键被别的组件占用/人工写脏）：当 None 处理，不许抛
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await fake.set("erp:cust:version", "not-a-number")
    monkeypatch.setattr(database, "_redis_client", fake, raising=False)
    assert await svc._read_version_signal() is None
    await fake.aclose()
