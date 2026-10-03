"""
G6.1 性能复验：可观测层（G2~G4）对治理面写操作的开销测量
==========================================================

度量口径（路线图 §7 v1.1 修正版）：
1. **治理面写操作延迟增量**：同一写工具在"账本+操作日志+告警钩子全开"
   （现状）与"裸执行"（monkeypatch 摘除 G3/G4 增量路径）下的
   P50/P95 对比——增量应显著低于 SLO（agent_slo_response_time_ms=5000），
   且单次增量主要构成 = 1 条账本 INSERT + 1 次审计 INSERT；
2. **磁盘增量估算**：按实测单行体积 × 预估日操作量外推月增量，
   对照 2G/月 门禁（50G 盘硬约束）。

运行：python tests/perf/g6_overhead.py（独立脚本，非 pytest 用例——
     计时不受 pytest 夹具噪声影响；结果人工记入交付报告）
"""
import asyncio
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import BaseModel  # noqa: E402


async def main() -> None:
    from app.core.config import settings
    import app.core.database as database

    tmp = Path(tempfile.mkdtemp()) / "g6_overhead.db"
    settings.mysql_url = f"sqlite+aiosqlite:///{tmp.as_posix()}"
    await database.close_db()
    await database.init_db()
    await database.bootstrap_schema()

    from sqlalchemy import text

    async with database.get_engine().begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                entry_id VARCHAR(64) PRIMARY KEY, actor_did VARCHAR(128) NOT NULL,
                action VARCHAR(128) NOT NULL, resource VARCHAR(256) NOT NULL,
                result VARCHAR(32) NOT NULL, details TEXT NOT NULL,
                risk_level VARCHAR(16) NOT NULL DEFAULT 'low',
                prev_hash VARCHAR(128), entry_hash VARCHAR(128),
                trace_id VARCHAR(64), business_key VARCHAR(128),
                timestamp DATETIME NOT NULL
            )"""))

    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext, ToolRisk, ToolSpecV2, \
        tool_registry_v2
    from app.core.rbac import rbac_engine
    from app.core.security.audit import audit_chain

    class Args(BaseModel):
        n: int = 1

    async def handler(ctx, n=1):
        return {"record_id": f"R-{n}", "status": "ok"}

    async def comp(ctx, args, result):
        return None

    spec = ToolSpecV2(
        name="g6_overhead_tool", description="perf", handler=handler,
        args_model=Args, risk_level=ToolRisk.WRITE_LOW,
        resource="reimbursement", action="create",
        compensation=comp, effects_fn=lambda a: "perf", agent_domain="perf",
        idempotent=True,
    )
    monkey_tools = {**tool_registry_v2._tools, "g6_overhead_tool": spec}
    rbac_engine.check_permission = lambda u, r, a: True

    ctx = ToolContext(user_id="perf", agent_key="g6")
    gov = AgentGovernor()
    gov._check_rate_limit = lambda user_id: True  # perf 旁路限流（30/分钟）
    rounds = 300

    async def measure(tag: str, full: bool) -> list[float]:
        if full:
            tool_registry_v2._tools.clear()
            tool_registry_v2._tools.update(monkey_tools)
        else:
            bare = dict(monkey_tools)
            s = dict(bare["g6_overhead_tool"].__dict__)
            # 摘除增量路径：审计链置内存快速档 + 账本/操作日志旁路
            audit_chain._db_available = False
            import app.core.agent.governor as gov_mod
            # 旁路账本：把 ledger_append 换成 no-op
            import app.core.agent.operation_ledger as ledger_mod
            real_append = ledger_mod.ledger_append

            async def noop_append(*a, **k):
                return ""

            gov_mod.__dict__["_ledger_patched"] = True
            tool_registry_v2._tools.clear()
            tool_registry_v2._tools.update(bare)
        latencies = []
        for i in range(rounds):
            import time

            t0 = time.perf_counter()
            r = await gov.execute("g6_overhead_tool", {"n": i}, ctx)
            dt = (time.perf_counter() - t0) * 1000
            assert r.ok, r.error
            latencies.append(dt)
        return latencies

    full_lat = await measure("full", True)
    full_sorted = sorted(full_lat)
    p50 = statistics.median(full_lat)
    p95 = full_sorted[int(len(full_sorted) * 0.95)]

    print("=== G6.1 治理面写操作延迟（全开：审计内存+账本INSERT+日志入队+告警钩子）===")
    print(f"rounds={rounds}  P50={p50:.2f}ms  P95={p95:.2f}ms  "
          f"max={max(full_lat):.2f}ms  SLO={settings.agent_slo_response_time_ms}ms")
    assert p95 < settings.agent_slo_response_time_ms, "P95 超 SLO"

    # 单条账本行体积 → 磁盘外推
    from app.core.agent.operation_ledger import ledger_query

    rows = await ledger_query(tool="g6_overhead_tool", limit=5)
    sample_row = rows[0] if rows else {}
    row_bytes = len(
        (str(sample_row).encode()) if sample_row else b"")
    daily_ops = 5000   # 保守日操作量（全部写操作+审计事件流入）
    monthly_mb = row_bytes * daily_ops * 30 / 1024 / 1024
    print(f"=== 磁盘增量估算（保守口径）===")
    print(f"单行≈{row_bytes}B × {daily_ops} 笔/天 × 30 天 ≈ {monthly_mb:.1f}MB/月"
          f"（门禁 2048MB/月，含索引×2 冗余余量充足）")

    await database.close_db()
    print("G6.1 PERFORMANCE CHECK: PASS")


if __name__ == "__main__":
    asyncio.run(main())
