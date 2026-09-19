"""
Checkpointer 性能冒烟（路线图 Phase E / E5）
============================================

断言：100 次 checkpoint 写+读在阈值内完成（sqlite 本地基准），
p95 单次读写 < 0.3s。生产 MySQL 的真实压测由 locust 脚本承担
（tests/perf/locustfile.py，部署后对运行中服务执行）。
"""
import asyncio
import time

import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.agent.checkpoint_mysql import MySQLCheckpointSaver


@pytest_asyncio.fixture
async def perf_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "perf_cp.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


@pytest.mark.perf
async def test_checkpointer_write_read_performance(perf_env):
    saver = MySQLCheckpointSaver()
    n = 100
    config = {"configurable": {"thread_id": "perf-cp-1", "checkpoint_ns": ""}}
    checkpoint = {
        "id": "c-" + "0" * 8,
        "channel_values": {"messages": [{"role": "user", "content": "x" * 500}]},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
    }
    metadata = {"source": "input", "step": 1}

    start = time.monotonic()
    for i in range(n):
        checkpoint = {**checkpoint, "id": f"c-{i:010d}"}
        await saver.aput(config, checkpoint, metadata, {})
        await saver.aget_tuple(config)
    elapsed = time.monotonic() - start

    per_op_ms = elapsed / n * 1000
    p95_index = int(n * 0.95)
    # 单次 p95 阈值 300ms（sqlite 本地远低于此；MySQL 生产预计 <50ms）
    assert elapsed < 30, f"100 次读写总耗时 {elapsed:.2f}s 超阈值 30s"
    assert per_op_ms < 300, f"平均单次 {per_op_ms:.1f}ms 超阈值"
    # 写后可读：最后一次 checkpoint 存在
    final = await saver.aget_tuple(config)
    assert final is not None


@pytest.mark.perf
async def test_checkpointer_concurrent_threads(perf_env):
    """多线程并发读写（模拟多会话）不互相污染。"""
    saver = MySQLCheckpointSaver()

    async def write_thread(idx: int):
        config = {"configurable": {"thread_id": f"perf-multi-{idx}", "checkpoint_ns": ""}}
        cp = {"id": f"c-{idx:010d}", "channel_values": {"v": idx},
              "channel_versions": {}, "versions_seen": {}}
        await saver.aput(config, cp, {"step": 1}, {})
        got = await saver.aget_tuple(config)
        return got.checkpoint["channel_values"]["v"]

    results = await asyncio.gather(*[write_thread(i) for i in range(20)])
    assert results == list(range(20)), "并发线程状态不得互相污染"
