"""
评测趋势看板数据面测试（路线图 Phase F5.2）
===========================================

F5.2 的"按域一键触发 + 历史趋势"要成立，前提是历史不再只活在进程内存里。
本轮改动把每次评测落一行 ``eval_runs``，所以这里验的是数据面本身：

1. pytest 尾部文本解析只认 pytest 打出来的数字（解析不到 → None，不猜）；
2. bootstrap_schema 的差集逻辑真建出 eval_runs；
3. 落库→查询→按筛选词/来源过滤的闭环，含 running→pass/fail 的终态覆盖；
4. 门禁触发的评测同样进看板（source=gate）；
5. 路由契约：/evals/cases、/evals/history、/evals/status 在内存缺失时回落库行
   （服务重启后前端轮询不能报"任务不存在"）。

不真跑评测子进程（一轮 40s+）：start() 的参数传递用 monkeypatch 断言，
真实评测的 PASS/FAIL 口径由 test_agent_evals.py 自己保证。
"""
import pytest
import pytest_asyncio

from app.core.config import settings

# 真机测得的 pytest -q 输出尾部（原样抄，不编）
TAIL_ALL_PASS = ".....................................................................\n79 passed in 115.00s"
TAIL_DOMAIN = "........                                                         [100%]\n8 passed, 42 deselected in 3.21s"
TAIL_ONE_FAIL = "F.......\n1 failed, 7 passed, 42 deselected in 4.55s"
TAIL_NO_TESTS = "no tests ran in 0.01s"


@pytest_asyncio.fixture
async def ev_env(tmp_path, monkeypatch):
    """sqlite 库 + bootstrap（建出 eval_runs）+ RBAC 放行 + 内存态归零。"""
    import app.core.database as database
    from app.core.agent import eval_runner
    from app.core.rbac import rbac_engine

    db_file = tmp_path / "eval_trend_f52.db"
    monkeypatch.setattr(settings, "mysql_url",
                        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info, info
    # eval_runs 是本轮新增表：确认差集建表真的建出来了（否则落库全程静默失败）
    async with engine.begin() as conn:
        got = (await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='eval_runs'")).fetchall()
    assert got, "bootstrap_schema 未建出 eval_runs 表"

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True)
    eval_runner._runs.clear()
    yield engine
    eval_runner._runs.clear()
    await database.close_db()


def test_parse_pytest_tail_reads_only_what_pytest_printed():
    """解析口径：有数字就取数字，没数字就是 None（看板不能把猜测当质量指标）。"""
    from app.core.agent.eval_runner import parse_pytest_tail

    assert parse_pytest_tail(TAIL_ALL_PASS) == {
        "passed": 79, "failed": None, "errors": None,
        "skipped": None, "deselected": None, "duration_s": 115.0}

    d = parse_pytest_tail(TAIL_DOMAIN)
    assert (d["passed"], d["deselected"], d["duration_s"]) == (8, 42, 3.21)
    assert d["failed"] is None

    f = parse_pytest_tail(TAIL_ONE_FAIL)
    assert (f["passed"], f["failed"], f["deselected"]) == (7, 1, 42)

    # "no tests ran"：没有 passed/failed 字样，一个数字都不给
    n = parse_pytest_tail(TAIL_NO_TESTS)
    assert n["passed"] is None and n["failed"] is None and n["duration_s"] == 0.01

    e = parse_pytest_tail("")
    assert e["duration_s"] is None and e["passed"] is None

    # 单数 error（collection error 的写法）也要认
    assert parse_pytest_tail("1 error in 0.30s")["errors"] == 1


def test_parse_pytest_tail_does_not_read_pass_from_a_failed_summary():
    """
    旧实现用 ``"passed" in tail`` 判通过——"1 failed, 7 passed" 里也含 passed，
    会把失败的运行报成 PASS。终态口径必须来自 exit_code（见 start()），
    这里锁住解析函数不会自己造出 PASS 结论。
    """
    from app.core.agent.eval_runner import parse_pytest_tail

    d = parse_pytest_tail(TAIL_ONE_FAIL)
    assert d["passed"] == 7          # 数字照实取
    assert d["failed"] == 1          # 但失败数也照实取，两者不互相覆盖


async def test_records_land_in_db_and_history_reads_them_back(ev_env):
    """running 行 + 终态覆盖 + 趋势字段（passed/failed/duration/谁触发的）。"""
    from app.core.agent.eval_runner import _record_finish, _record_start, history, parse_pytest_tail

    await _record_start("eval-a1", "reimbursement", "manual", "user_cio")
    running = await history(domain="reimbursement")
    assert len(running) == 1, running
    assert running[0]["status"] == "running"
    assert running[0]["triggered_by"] == "user_cio"
    assert running[0]["passed"] is None            # 还没跑完，不提前给数

    await _record_finish("eval-a1", status="pass", counts=parse_pytest_tail(TAIL_DOMAIN),
                         exit_code=0, tail=TAIL_DOMAIN)
    done = (await history(domain="reimbursement"))[0]
    assert done["status"] == "pass"
    assert (done["passed"], done["deselected"], done["duration_s"], done["exit_code"]) \
        == (8, 42, 3.21, 0)
    assert done["finished_at"] and done["created_at"]
    assert done["tail"].endswith("8 passed, 42 deselected in 3.21s")

    # 失败的一轮：门禁要看的正是这一行
    await _record_start("eval-a2", "contract", "gate", "publish:user_cio")
    await _record_finish("eval-a2", status="fail", counts=parse_pytest_tail(TAIL_ONE_FAIL),
                         exit_code=1, tail=TAIL_ONE_FAIL)
    gates = await history(source="gate")
    assert [g["run_id"] for g in gates] == ["eval-a2"]
    assert (gates[0]["failed"], gates[0]["passed"], gates[0]["status"]) == (1, 7, "fail")

    # 按筛选词过滤：域下拉选 reimbursement 时不能把门禁的 contract 混进来
    only_reimb = await history(domain="reimbursement")
    assert {r["run_id"] for r in only_reimb} == {"eval-a1"}


async def test_history_degrades_without_db(monkeypatch):
    """存储不可用 → 空历史 + 落库静默跳过（评测本身仍要能跑，趋势看板只是少一行）。"""
    import app.db.repositories.base as base

    from app.core.agent import eval_runner

    monkeypatch.setattr(base, "has_db", lambda: False)
    assert await eval_runner.history() == []
    await eval_runner._record_start("eval-x", "hr", "manual", "u")
    await eval_runner._record_finish("eval-x", status="pass", counts={},
                                     exit_code=0, tail="")


async def test_case_domains_matches_the_eval_set(ev_env):
    """
    下拉数据源与用例集同源：7 个域、合计 50 条（与 tests/evals/agent_evals.json 实测一致），
    用例数按域递减排序。这里断言的是"看板上的用例数不是写死的"。
    """
    from app.core.agent.eval_runner import case_domains

    domains = case_domains()
    assert len(domains) == 7
    assert sum(d["cases"] for d in domains) == 50
    counts = {d["domain"]: d["cases"] for d in domains}
    assert counts["reimbursement"] == 8 and counts["hr"] == 7 and counts["crm"] == 6
    assert domains[0]["cases"] >= domains[-1]["cases"]


async def test_start_without_event_loop_is_skipped_and_not_persisted(ev_env, monkeypatch):
    """无事件循环时评测不会跑：内存标 SKIPPED，且不往趋势里塞一行假记录。"""
    import asyncio

    from app.core.agent import eval_runner

    class _NoLoopAsyncio:
        """只替掉 eval_runner 看到的那个 asyncio.get_running_loop，不动全局 asyncio。"""
        def __getattr__(self, name):
            return getattr(asyncio, name)

        def get_running_loop(self):
            raise RuntimeError("no loop")

    monkeypatch.setattr(eval_runner, "asyncio", _NoLoopAsyncio())
    run_id = eval_runner.start("crm", source="manual", triggered_by="user_cio")
    assert eval_runner.status(run_id)["summary"] == "SKIPPED"
    assert await eval_runner.history() == []


async def test_eval_routes_contract(ev_env):
    """路由契约：cases / history（按域与来源过滤）/ status 内存缺失时回落库行。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.api.routes import agent_ops as ops_routes
    from app.core.agent import eval_runner
    from app.core.agent.eval_runner import _record_finish, _record_start

    class FakeAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = {"user_id": "user_cio", "username": "cio"}
            return await call_next(request)

    app = FastAPI()
    app.include_router(ops_routes.router)
    app.add_middleware(FakeAuth)

    await _record_start("eval-b1", "payroll", "manual", "user_cio")
    await _record_finish("eval-b1", status="pass",
                         counts=eval_runner.parse_pytest_tail(TAIL_DOMAIN),
                         exit_code=0, tail=TAIL_DOMAIN)
    # 模拟服务重启：内存里没有这一轮，但库里有终态
    eval_runner._runs.clear()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        cases = (await client.get("/api/v1/agent-ops/evals/cases")).json()
        hist = (await client.get(
            "/api/v1/agent-ops/evals/history?domain=payroll")).json()
        other = (await client.get(
            "/api/v1/agent-ops/evals/history?domain=reimbursement")).json()
        st = (await client.get("/api/v1/agent-ops/evals/status/eval-b1"))
        missing = (await client.get("/api/v1/agent-ops/evals/status/eval-nope"))

    assert cases["total"] == 50 and len(cases["domains"]) == 7
    row = hist["runs"][0]
    assert row["run_id"] == "eval-b1" and row["status"] == "pass"
    assert row["passed"] == 8 and row["triggered_by"] == "user_cio"
    assert other["runs"] == []
    assert st.status_code == 200, await st.aread()
    body = st.json()
    assert body["status"] == "pass" and body["finished_at"]
    assert missing.status_code == 404


async def test_run_route_passes_caller_and_source(ev_env, monkeypatch):
    """POST /evals/run 把触发人带进落库字段（否则看板上的"谁跑的"永远空）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.api.routes import agent_ops as ops_routes
    from app.core.agent import eval_runner

    seen = {}

    def _fake_start(domain="", run_id="", source="manual", triggered_by=""):
        seen.update(domain=domain, source=source, triggered_by=triggered_by)
        return "eval-c1"

    monkeypatch.setattr(eval_runner, "start", _fake_start)

    class FakeAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = {"user_id": "user_cio", "username": "cio"}
            return await call_next(request)

    app = FastAPI()
    app.include_router(ops_routes.router)
    app.add_middleware(FakeAuth)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        r = await client.post("/api/v1/agent-ops/evals/run", json={"domain": "treasury"})

    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == "eval-c1"
    assert seen == {"domain": "treasury", "source": "manual", "triggered_by": "user_cio"}
