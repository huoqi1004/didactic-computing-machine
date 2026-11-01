"""
评测集运行器（路线图 Phase F / F2.6 + F3.5 发布门禁 + F5.2 趋势看板共用）
==========================================================================

按域子集异步跑 ``tests/test_agent_evals.py``（CI 脚本 LLM 模式，零 LLM 成本），
run_id 轮询取结果。管理端「触发评测」与「DSL 发布门禁」走同一实现，
避免两套 subprocess 语义。

F5.2 起每次运行落一行 ``eval_runs``：内存 ``_runs`` 只服务"本次轮询"，
进程重启或多实例部署后趋势看板仍要有历史，所以历史真相源在库里。
DB 不可用时退回纯内存（与定制层一致的降级语义），只在日志告警。
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger()

_EVALS_TARGET = "tests/test_agent_evals.py"
_EVALS_CASES_PATH = os.path.join("tests", "evals", "agent_evals.json")
_runs: dict[str, dict] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _backend_dir() -> str:
    return os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def parse_pytest_tail(tail: str) -> dict:
    """
    从 pytest ``-q`` 尾部文本解析计数（F5.2 趋势看板的数据口径）。

    只认 pytest 自己打出来的数字，解析不到就是 None —— 看板宁可显示"未取到"
    也不能把猜出来的用例数当成质量指标。
    """
    def _n(label: str) -> Optional[int]:
        m = re.search(rf"(\d+)\s+{label}\b", tail or "")
        return int(m.group(1)) if m else None

    out = {"passed": _n("passed"), "failed": _n("failed"),
           "errors": _n(r"errors?"), "skipped": _n("skipped"),
           "deselected": _n("deselected")}
    m = re.search(r"in\s+(\d+(?:\.\d+)?)s", tail or "")
    out["duration_s"] = float(m.group(1)) if m else None
    return out


def case_domains() -> list[dict]:
    """评测集按域用例数（管理端"按域触发"的下拉数据源）。"""
    import json

    path = os.path.join(_backend_dir(), _EVALS_CASES_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            cases = json.load(f).get("cases") or []
    except Exception as e:  # noqa: BLE001
        logger.warning("eval.cases_unreadable", path=path, error=str(e))
        return []
    counts: dict[str, int] = {}
    for c in cases:
        counts[str(c.get("domain") or "")] = counts.get(str(c.get("domain") or ""), 0) + 1
    return [{"domain": d, "cases": n}
            for d, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


async def _record_start(run_id: str, domain: str, source: str,
                        triggered_by: str) -> None:
    """先落一行 running：评测动辄几十秒，看板要能看到在跑的那次。"""
    from app.db.models import eval_runs
    from app.db.repositories.base import dialect_upsert_stmt, has_db, session_scope

    if not has_db():
        return
    try:
        async with session_scope() as session:
            await session.execute(dialect_upsert_stmt(
                eval_runs,
                {"run_id": run_id, "domain": domain, "source": source,
                 "status": "running", "triggered_by": triggered_by,
                 "created_at": _utcnow(), "tail": ""},
                conflict_cols=["run_id"]))
    except Exception as e:  # noqa: BLE001
        logger.warning("eval.record_start_failed", run_id=run_id, error=str(e))


async def _record_finish(run_id: str, *, status: str, counts: dict,
                         exit_code: Optional[int], tail: str) -> None:
    from sqlalchemy import update

    from app.db.models import eval_runs
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return
    try:
        async with session_scope() as session:
            await session.execute(
                update(eval_runs).where(eval_runs.c.run_id == run_id).values(
                    status=status, passed=counts.get("passed"),
                    failed=counts.get("failed"), errors=counts.get("errors"),
                    skipped=counts.get("skipped"), deselected=counts.get("deselected"),
                    duration_s=counts.get("duration_s"), exit_code=exit_code,
                    tail=tail, finished_at=_utcnow()))
    except Exception as e:  # noqa: BLE001
        logger.warning("eval.record_finish_failed", run_id=run_id, error=str(e))


async def history(domain: Optional[str] = None, source: Optional[str] = None,
                  limit: int = 50) -> list[dict]:
    """评测历史（新→旧），F5.2 趋势看板数据源。"""
    from sqlalchemy import select

    from app.db.models import eval_runs
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return []
    async with session_scope() as session:
        stmt = select(eval_runs).order_by(eval_runs.c.id.desc()).limit(limit)
        if domain:
            stmt = stmt.where(eval_runs.c.domain == domain)
        if source:
            stmt = stmt.where(eval_runs.c.source == source)
        rows = (await session.execute(stmt)).all()
    return [dict(_row_to_dict(r), tail=(r.tail or "")[-1000:]) for r in rows]


def _python_bin() -> str:
    root = _backend_dir()
    cand = os.path.join(root, ".venv", "Scripts", "python.exe") if os.name == "nt" \
        else os.path.join(root, ".venv", "bin", "python")
    return cand if os.path.exists(cand) else "python"


def start(domain: str = "", run_id: str = "", source: str = "manual",
          triggered_by: str = "") -> str:
    """
    启动一轮按域评测（非阻塞），返回 run_id。

    source: manual（管理端一键触发）/ gate（DSL 发布门禁），落库后趋势看板按此分列。
    """
    run_id = run_id or f"eval-{os.urandom(5).hex()}"
    _runs[run_id] = {"run_id": run_id, "domain": domain, "source": source,
                     "finished": False, "summary": "running"}

    async def _run():
        await _record_start(run_id, domain, source, triggered_by)
        proc = await asyncio.create_subprocess_exec(
            _python_bin(), "-m", "pytest", _EVALS_TARGET,
            "-k", domain or "eval_case", "--tb=no", "-q",
            cwd=_backend_dir(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = out.decode("utf-8", errors="replace")
        tail = text[-2000:]
        # exit_code 为准：pytest 失败摘要里也会出现 "passed" 字样，字符串判定会把
        # "8 passed, 1 failed" 读成通过。
        ok = proc.returncode == 0
        _runs[run_id] = {
            "run_id": run_id, "domain": domain, "source": source, "finished": True,
            "exit_code": proc.returncode,
            "summary": ("PASS" if ok else "FAIL"),
            "tail": tail,
        }
        await _record_finish(run_id, status=("pass" if ok else "fail"),
                             counts=parse_pytest_tail(tail),
                             exit_code=proc.returncode, tail=tail)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 同步上下文（无事件循环）跑不了子进程，也写不了库：只留内存态，
        # 这种情况不会进趋势看板，看板上"未取到"比编一行历史更诚实。
        _runs[run_id] = {"run_id": run_id, "domain": domain, "source": source,
                         "finished": True, "summary": "SKIPPED",
                         "tail": "无事件循环，未触发评测"}
        return run_id
    asyncio.ensure_future(_run())
    return run_id


def status(run_id: str) -> Optional[dict]:
    return _runs.get(run_id)


async def record_of(run_id: str) -> Optional[dict]:
    """按 run_id 取库里的历史行（进程重启后内存没了，轮询仍要能收敛到终态）。"""
    from sqlalchemy import select

    from app.db.models import eval_runs
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return None
    async with session_scope() as session:
        row = (await session.execute(
            select(eval_runs).where(eval_runs.c.run_id == run_id))).first()
    if row is None:
        return None
    return _row_to_dict(row)


def _row_to_dict(r) -> dict:
    return {
        "run_id": r.run_id, "domain": r.domain, "source": r.source,
        "status": r.status, "passed": r.passed, "failed": r.failed,
        "errors": r.errors, "skipped": r.skipped, "deselected": r.deselected,
        "duration_s": r.duration_s, "exit_code": r.exit_code,
        "triggered_by": r.triggered_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "tail": r.tail or "",
    }


async def wait(run_id: str, timeout: float = 900.0) -> dict:
    """轮询等待评测结束（发布门禁用）；超时返回最后状态。"""
    deadline = asyncio.get_running_loop().time() + timeout
    info = _runs.get(run_id) or {}
    while not info.get("finished") and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(2.0)
        info = _runs.get(run_id) or {}
    return info
