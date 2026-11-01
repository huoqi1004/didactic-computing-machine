"""
全链路压测复验（路线图 Phase F5 / F5.3）
==========================================

为什么不用 locust：`tests/perf/locustfile.py` 是部署环境用的（locust 不在本仓库
venv 里，`import locust` 直接 ModuleNotFoundError）。F5.3 要的是一句命令就能
复跑、并把 p95/5xx/DB 锁写进结论的实测，所以这里用仓库已有的 httpx + asyncio
复刻同一套场景与权重，**并额外加上定制热更新并发场景**（locustfile 里原本没有）。

用法（后端已启动，默认 :8000；本项目验收用自起的 :8010，见 docs 运维手册）：
    ./.venv/Scripts/python.exe tests/perf/loadtest_f53.py \
        --host http://127.0.0.1:8010 --users 50 --spawn-rate 5 --run-time 60 \
        --server-log _tmp_f53_8010.log --out _tmp_f53_result.json

判据（与路线图 E5/F5.3 一致）：
- p95 响应时间 < settings.agent_slo_response_time_ms（默认 5000ms）；
  p95 只量**未被限流**的请求（429 是护栏命中，7ms 就返回，混进去会把容量误报成延迟）；
- 无 5xx；
- 服务端日志无 "database is locked"（--server-log 给了才检查，不给则标"未检查"）；
- 热更新收敛：写完**立刻**读一次生效视图，量服务端陈旧度（同进程写入立即生效；
  多实例部署收敛上界=版本信号 5s 节流，由 CI 用例
  tests/test_hot_reload_concurrency_f53.py::test_second_instance_converges_via_version_signal 守）；
- --ip-mode distinct（默认）给每个虚拟用户一个客户端 IP：限流按 IP 计
  （global 120/min、write 60/min），50 用户挤一个 IP 量到的是护栏不是容量。
  --ip-mode shared 专门用来复现"全部同 IP 时 429 命中多少"。

压测写入的定制行以 perf_f53_ 前缀命名，跑完默认删除（--keep-rows 保留取证）。
"""
import argparse
import asyncio
import contextlib
import os
import datetime
import json
import math
import random
import re
import sys
import time

import httpx

# 直接 `python tests/perf/loadtest_f53.py` 时 sys.path[0] 是本脚本目录，
# 收尾清理要 import app.* 找得到后端根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

API = "/api/v1"
HOT_PREFIX = "perf_f53_"
LOCK_PATTERNS = ("database is locked", "lock wait timeout", "Deadlock found")

SUPERVISOR_QUERIES = [
    "汇总一下报销和资金的情况",
    "查一下最近的报销单",
    "看看资金余额和支付排程",
    "技术部有哪些人",
    "A级客户有哪些",
]


def pct(values: list[float], q: float) -> float:
    """最近秩百分位（values 为空返回 0，不假造数字）。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))
    return s[idx]


class Stats:
    def __init__(self) -> None:
        self.by_task: dict[str, dict] = {}
        self.writes_recorded = 0                          # 热更新写入次数
        self.convergence: list[float] = []                # 写完→生效视图可见的秒数
        self.started_at = 0.0

    def hit(self, task: str, ms: float, status: int, error: str = "") -> None:
        b = self.by_task.setdefault(task, {"n": 0, "fail": 0, "throttled": 0,
                                           "throttled_by": {}, "ms": [], "ms_ok": [],
                                           "status": {}})
        b["n"] += 1
        bucket = "2xx" if 200 <= status < 300 else (
            "429" if status == 429 else
            "4xx" if 400 <= status < 500 else "5xx" if status >= 500 else "other")
        b["status"][bucket] = b["status"].get(bucket, 0) + 1
        if bucket == "429":
            # 429 是护栏生效，不是服务端故障；混进失败率会把"容量到顶"误报成
            # "服务不稳"，而且 429 几毫秒就返回，量出来的 p95 也不是业务延迟
            b["throttled"] += 1
            b["throttled_by"][error or "unknown"] = b["throttled_by"].get(error or "unknown", 0) + 1
            return
        b["ms"].append(ms)
        b["ms_ok"].append(ms)
        if bucket == "5xx" or bucket == "other" or (400 <= status < 500) or error:
            b["fail"] += 1
            b.setdefault("errors", []).append(f"{status} {error}"[:200])

    def summary(self) -> dict:
        out = {}
        for task, b in self.by_task.items():
            out[task] = {
                "requests": b["n"],
                "failures": b["fail"],
                "throttled_429": b["throttled"],
                "throttled_by": b["throttled_by"],
                "p50_ms": round(pct(b["ms"], 0.50), 1),
                "p95_ms": round(pct(b["ms"], 0.95), 1),
                "p99_ms": round(pct(b["ms"], 0.99), 1),
                "max_ms": round(max(b["ms"]), 1) if b["ms"] else 0.0,
                "p95_accepted_ms": round(pct(b["ms_ok"], 0.95), 1),
                "status_buckets": b["status"],
                "sample_errors": (b.get("errors") or [])[:3],
            }
        wall = time.perf_counter() - self.started_at if self.started_at else 0.0
        total = sum(b["n"] for b in self.by_task.values())
        out["_overall"] = {
            "requests": total,
            "throttled_429": sum(b["throttled"] for b in self.by_task.values()),
            "accepted": sum(b["n"] - b["throttled"] for b in self.by_task.values()),
            "throttled_by": {k: sum(b["throttled_by"].get(k, 0)
                                    for b in self.by_task.values())
                             for k in {kk for b in self.by_task.values()
                                       for kk in b["throttled_by"]}},
            "wall_clock_s": round(wall, 2),
            "rps": round(total / wall, 2) if wall > 0 else 0.0,
            "all_5xx": sum(b["status"].get("5xx", 0) for b in self.by_task.values()),
            "all_failures": sum(b["fail"] for b in self.by_task.values()),
            "hot_update_writes_recorded": self.writes_recorded,
            "hot_update_convergence_samples": len(self.convergence),
            "hot_update_convergence_p95_s": round(pct(self.convergence, 0.95), 3),
            "hot_update_convergence_max_s": round(max(self.convergence), 3)
            if self.convergence else None,
        }
        return out


class Shared:
    """跨虚拟用户共享：统计、待收敛写入、总调度频道。"""

    def __init__(self) -> None:
        self.stats = Stats()
        self.sup_channel: str | None = None
        self.pending_hot: dict[str, tuple[float, float]] = {}   # target -> (写入完成时刻, 值)


class Ctx:
    """每虚拟用户一份：client 共用，headers 各自（限流按 IP 计，客户端身份要独立）。"""

    def __init__(self, client: httpx.AsyncClient, shared: Shared,
                 headers: dict) -> None:
        self.client = client
        self.shared = shared
        self.headers = headers
        self.token = ""

    @property
    def stats(self) -> Stats:
        return self.shared.stats

    @property
    def pending_hot(self) -> dict:
        return self.shared.pending_hot

    @property
    def sup_channel(self):
        return self.shared.sup_channel


def _throttle_source(r) -> str:
    """区分两道护栏：IP 限流中间件 vs 租户 API 配额（都返 429，处置完全不同）。"""
    rule = r.headers.get("x-ratelimit-rule") if r is not None else None
    if rule:
        return f"rate_limit:{rule}"
    body = (r.text or "") if r is not None else ""
    if "配额" in body:
        return "tenant_quota"
    return "unknown"


async def timed(ctx: Ctx, task: str, fn) -> int:
    t0 = time.perf_counter()
    status, err = 0, ""
    try:
        r = await fn()
        status = r.status_code
        if status == 429:
            err = _throttle_source(r)
        elif status >= 400:
            err = r.text[:120]
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    ctx.stats.hit(task, (time.perf_counter() - t0) * 1000, status, err)
    return status


async def task_supervisor(ctx: Ctx) -> None:
    """跨域协同指令（LLM 分类 + 委派子域工具）。"""
    if not ctx.sup_channel:
        return
    await timed(ctx, "supervisor_cross_domain", lambda: ctx.client.post(
        f"{API}/messaging/channels/{ctx.sup_channel}/agent-command",
        headers=ctx.headers, json={"command": random.choice(SUPERVISOR_QUERIES)}, timeout=120))


async def task_workflow(ctx: Ctx) -> None:
    """报销审批流启动（低额自动完成，走 Checkpointer 写）。"""
    await timed(ctx, "workflow_start", lambda: ctx.client.post(
        f"{API}/agent-ops/workflows/reimbursement_approval/start",
        headers=ctx.headers,
        json={"input": {"employee_name": f"压测用户{random.randint(1, 999)}",
                        "category": "office",
                        "amount": round(random.uniform(50, 800), 2),
                        "description": "压测自动报销", "has_receipt": True}},
        timeout=60))


async def task_approvals(ctx: Ctx) -> None:
    await timed(ctx, "approval_list", lambda: ctx.client.get(
        f"{API}/agent-ops/approvals", params={"status": "pending"},
        headers=ctx.headers, timeout=30))


async def _scan_visibility(ctx: Ctx, r: httpx.Response) -> None:
    """把"还没在生效视图里出现的写入"与本次响应比对，命中即记一次收敛耗时。"""
    if r.status_code >= 400:
        return
    try:
        live = {c.get("target"): (c.get("overrides") or {})
                for c in (r.json().get("customizations") or [])}
    except (ValueError, AttributeError):
        return
    for tgt, (ts, val) in list(ctx.pending_hot.items()):
        cur = (live.get(tgt) or {}).get("temperature")
        if cur is not None and abs(float(cur) - val) < 1e-9:
            ctx.stats.convergence.append(time.perf_counter() - ts)
            del ctx.pending_hot[tgt]


async def _read_live_view(ctx: Ctx, task_name: str) -> None:
    async def _call():
        r = await ctx.client.get(f"{API}/agent-ops/customizations",
                                 headers=ctx.headers, timeout=30)
        await _scan_visibility(ctx, r)
        return r

    await timed(ctx, task_name, _call)


async def task_hot_update(ctx: Ctx, user_id: int) -> None:
    """
    定制热更新写（F5.3 新增场景）：管理端改阈值/Agent 覆盖，
    与读侧生效视图并发——缓存整体替换 vs 本地写入的丢写窗口就在这条路径上。
    """
    target = f"{HOT_PREFIX}agent_{user_id}"
    value = round(random.uniform(0.01, 0.99), 4)
    body = {"overrides": {"temperature": value}, "enabled": True,
            "comment": "F5.3 压测热更新"}

    async def _call():
        return await ctx.client.put(f"{API}/agent-ops/customizations/agent/{target}",
                                    json=body, headers=ctx.headers, timeout=30)

    status = await timed(ctx, "customization_hot_update", _call)
    if status != 200:
        return  # 被护栏挡下/失败的写没有落库，登记待收敛只会把 429 误报成"陈旧"
    # 写完立刻探一次生效视图：这样"收敛耗时"量的是服务端陈旧度，
    # 而不是本客户端下一次读的排队（只靠随机轮询读时，8s 那种数字是读间隔）
    ctx.pending_hot[target] = (time.perf_counter(), value)
    ctx.stats.writes_recorded += 1
    await _read_live_view(ctx, "customization_visibility_probe")


async def task_customization_read(ctx: Ctx) -> None:
    """生效视图读（合成缓存 -> 走版本信号节流重载路径）。"""
    await _read_live_view(ctx, "customization_read")


TASKS = [
    ("supervisor_cross_domain", 3),
    ("workflow_start", 2),
    ("approval_list", 1),
    ("customization_hot_update", 2),
    ("customization_read", 2),
]


async def run_user(ctx: Ctx, user_id: int, until: float, think: tuple[int, int]) -> None:
    picked = [name for name, w in TASKS for _ in range(w)]
    while time.perf_counter() < until:
        name = random.choice(picked)
        if name == "supervisor_cross_domain":
            await task_supervisor(ctx)
        elif name == "workflow_start":
            await task_workflow(ctx)
        elif name == "approval_list":
            await task_approvals(ctx)
        elif name == "customization_hot_update":
            await task_hot_update(ctx, user_id)
        else:
            await task_customization_read(ctx)
        await asyncio.sleep(random.uniform(*think))


async def login(ctx: Ctx, username: str, password: str) -> dict:
    r = await ctx.client.post(f"{API}/auth/login", headers=ctx.headers,
                              json={"username": username, "password": password})
    r.raise_for_status()
    data = r.json()
    ctx.token = data.get("access_token", "")
    if not ctx.token:
        raise RuntimeError(f"登录未返回 access_token：{list(data)}")
    ctx.client.headers["Authorization"] = f"Bearer {ctx.token}"
    return data


async def find_sup_channel(ctx: Ctx) -> None:
    r = await ctx.client.get(f"{API}/messaging/channels", headers=ctx.headers)
    if r.status_code >= 400:
        return
    channels = r.json().get("channels") or []
    ctx.shared.sup_channel = next((c["channel_id"] for c in channels
                                  if "总调度" in str(c.get("name") or "")), None)


def scan_server_log(path: str) -> dict:
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        return {"checked": False, "reason": str(e)}
    hits = {}
    for pat in LOCK_PATTERNS:
        n = len(re.findall(re.escape(pat), text, flags=re.I))
        if n:
            hits[pat] = n
    return {"checked": True, "lock_hits": hits, "log_lines": text.count("\n")}


async def cleanup_perf_rows() -> dict:
    """删掉压测写的定制行与版本流行（同库同配置：runner 与服务端读同一个 mysql_url）。"""
    from sqlalchemy import delete

    import app.core.database as database
    from app.db.models import agent_customizations, customization_versions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        # runner 自己不跑 lifespan，要先建引擎才能连到服务端用的那个库
        with contextlib.suppress(Exception):
            await database.init_db()
    if not has_db():
        return {"cleaned": False, "reason": "存储不可用（需与服务端同一 mysql_url 配置）"}
    like = f"{HOT_PREFIX}%"
    async with session_scope() as session:
        r1 = await session.execute(delete(agent_customizations).where(
            agent_customizations.c.target.like(like)))
        r2 = await session.execute(delete(customization_versions).where(
            customization_versions.c.target.like(like)))
    await database.close_db()
    return {"cleaned": True, "agent_customizations": r1.rowcount,
            "customization_versions": r2.rowcount}


async def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="F5.3 全链路压测（含定制热更新并发）")
    p.add_argument("--host", default="http://127.0.0.1:8000")
    p.add_argument("--users", type=int, default=50)
    p.add_argument("--spawn-rate", type=float, default=5.0, help="每秒起的用户数")
    p.add_argument("--run-time", type=float, default=60, help="稳态压测秒数（从首个用户起算）")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="admin123")
    p.add_argument("--no-llm", action="store_true",
                   help="跳过 agent-command（无 LLM Key 时避免把降级路径当业务失败）")
    p.add_argument("--server-log", default="", help="服务端日志路径，用于 DB 锁扫描")
    p.add_argument("--out", default="", help="结果 JSON 落盘路径")
    p.add_argument("--keep-rows", action="store_true", help="保留压测写入的定制行")
    p.add_argument("--threshold-ms", type=float, default=0,
                   help="p95 阈值；0 = 用 settings.agent_slo_response_time_ms")
    p.add_argument("--tasks", default="",
                   help="只跑这些场景（逗号分隔，如 approval_list,customization_read,"
                        "customization_hot_update）。用于把不依赖 LLM 的服务端容量"
                        "与依赖 LLM 的编排链路分开量——LLM 欠费（402 Insufficient "
                        "Balance）时那两条腿的延迟不是平台容量")
    p.add_argument("--ip-mode", choices=["distinct", "shared"], default="distinct",
                   help="distinct=每个虚拟用户一个客户端 IP（50 用户真机形态，"
                        "限流按 IP 计：global 120/min、write 60/min）；"
                        "shared=全部走同一 IP（量限流保护本身）")
    args = p.parse_args(argv)

    if args.no_llm:
        TASKS[:] = [t for t in TASKS if t[0] != "supervisor_cross_domain"]
    if args.tasks:
        want = {t.strip() for t in args.tasks.split(",") if t.strip()}
        unknown = want - {t[0] for t in TASKS}
        if unknown:
            print(f"未知场景：{sorted(unknown)}；可选 {[t[0] for t in TASKS]}", file=sys.stderr)
            return 2
        TASKS[:] = [t for t in TASKS if t[0] in want]

    try:
        from app.core.config import settings
        slo = args.threshold_ms or settings.agent_slo_response_time_ms
    except Exception:  # noqa: BLE001
        slo = args.threshold_ms or 5000

    shared = Shared()
    limits = httpx.Limits(max_connections=args.users * 2, max_keepalive_connections=args.users)

    def user_headers(i: int) -> dict:
        if args.ip_mode == "shared":
            return {}
        # 50 个并发用户 = 50 个客户端 IP（走反代时 XFF 就是这么来的）；
        # 全塞一个 IP 会被限流挡住，量到的是护栏不是容量
        return {"X-Forwarded-For": f"10.77.{i // 250}.{i % 250 + 1}"}

    async with httpx.AsyncClient(base_url=args.host, limits=limits,
                                 timeout=httpx.Timeout(30.0)) as client:
        ctx0 = Ctx(client, shared, user_headers(0))
        try:
            await login(ctx0, args.username, args.password)
        except Exception as e:  # noqa: BLE001
            print(f"登录失败：{e}", file=sys.stderr)
            return 2
        await find_sup_channel(ctx0)
        if args.no_llm is False and not shared.sup_channel:
            print("提示：未找到「总调度」频道，supervisor_cross_domain 场景将被跳过")

        started = time.perf_counter()
        shared.stats.started_at = started
        until = started + args.run_time
        tasks, spawn_interval = [], 1.0 / max(args.spawn_rate, 0.1)
        for i in range(args.users):
            # 同一账号、多个客户端身份：本环境只有 admin 一个可登录账号
            tasks.append(asyncio.create_task(
                run_user(Ctx(client, shared, user_headers(i)), i, until, (1, 3))))
            await asyncio.sleep(spawn_interval)
        print(f"已起 {args.users} 个并发用户（ramp {args.spawn_rate}/s），"
              f"稳态 {args.run_time}s …")
        await asyncio.gather(*tasks, return_exceptions=True)
        # 收尾再读一次：仍不可见的写入 = 陈旧超出了本次窗口，是要报出来的缺陷而不是样本缺失
        if shared.pending_hot:
            await _read_live_view(ctx0, "customization_visibility_probe")

    unresolved = len(shared.pending_hot)
    summary = shared.stats.summary()
    overall = summary["_overall"]
    log_scan = scan_server_log(args.server_log) if args.server_log else {
        "checked": False, "reason": "未提供 --server-log"}

    print("\n=== F5.3 压测结果（实测）===")
    print(f"目标 {args.host} · 并发 {args.users} · 墙钟 {overall['wall_clock_s']}s "
          f"· {overall['rps']} RPS")
    for task, m in summary.items():
        if task == "_overall":
            continue
        print(f"  {task:<26} n={m['requests']:<6} 失败={m['failures']:<4} "
              f"限流429={m['throttled_429']:<5} p50={m['p50_ms']:<8} "
              f"p95={m['p95_ms']:<9} p95(未限流)={m['p95_accepted_ms']:<9} "
              f"p99={m['p99_ms']:<9} max={m['max_ms']}  {m['status_buckets']}")
    print(f"  5xx 合计 {overall['all_5xx']}；429 合计 {overall['throttled_429']}"
          f"（{overall['throttled_by']}）；放行 {overall['accepted']} 次；"
          f"DB 锁扫描 {log_scan}")
    print(f"  热更新写入 {overall['hot_update_writes_recorded']} 次，"
          f"收敛样本 {overall['hot_update_convergence_samples']} 个，"
          f"p95={overall['hot_update_convergence_p95_s']}s，"
          f"max={overall['hot_update_convergence_max_s']}s，"
          f"窗口内仍不可见 {unresolved} 个 target")

    cleaned = {"cleaned": False, "reason": "--keep-rows"}
    if not args.keep_rows:
        try:
            cleaned = await cleanup_perf_rows()
        except Exception as e:  # noqa: BLE001
            cleaned = {"cleaned": False, "reason": f"{type(e).__name__}: {e}"}
    print(f"  压测数据清理：{cleaned}")

    verdict = []
    if overall["all_5xx"]:
        verdict.append(f"出现 {overall['all_5xx']} 次 5xx")
    # 每个场景各自量 p95（E5 判据是"p95 < SLO"，不是只量热更新那一条）
    for task, m in summary.items():
        if task == "_overall" or not m["requests"]:
            continue
        if m["p95_accepted_ms"] > slo:
            verdict.append(f"{task} p95(未限流) {m['p95_accepted_ms']}ms 超阈值 {slo}ms")
    if log_scan.get("checked") and log_scan.get("lock_hits"):
        verdict.append(f"服务端日志命中锁错误 {log_scan['lock_hits']}")
    if overall["all_failures"]:
        verdict.append(f"非 5xx 失败 {overall['all_failures']} 次（见 sample_errors）")
    if unresolved:
        verdict.append(f"{unresolved} 个热更新 target 在窗口内未在生效视图出现（陈旧）")

    result = {"meta": {"host": args.host, "users": args.users,
                       "run_time_s": args.run_time, "slo_ms": slo,
                       "unresolved_hot_updates": unresolved,
                       "started_at": datetime.datetime.now().isoformat(timespec="seconds")},
              "summary": summary, "db_lock_scan": log_scan,
              "cleanup": cleaned, "verdict": verdict or ["PASS"]}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  结果已写入 {args.out}")
    print(f"结论：{'PASS' if not verdict else 'FAIL → ' + '; '.join(verdict)}")
    return 0 if not verdict else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
