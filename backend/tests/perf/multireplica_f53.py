"""
F5.3 §2.4 补测：多实例定制缓存收敛（两个真实进程，同库同 Redis）
================================================================

为什么要单独跑：`loadtest_f53.py` 的 50 并发全部打到**同一个进程**，
量到的是"版本信号 + 节流重载"在同进程内的表现；路线图 §2.4 承诺的是
**跨实例失效**，那必须有第二个进程才能证明。两个场景：

1. 跨实例收敛：A 生效一笔变更，B 侧的生效视图多久能看到（B 从未写过这个 key）；
2. 双实例同 target 并发写：进程内那把 `_write_lock` 只在自己的循环里有效，
   两个实例互相看不见。这里量的是"最后 bump 者已认领自己的版本号，
   于是不再重载"这个残余窗口到底会不会留下**持久**陈旧。

执行：先起两个进程（同一份代码、同一个 MySQL、同一个 Redis）
  ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
  ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011
再跑
  ./.venv/Scripts/python.exe tests/perf/multireplica_f53.py --rounds 4 --soak 30

数字全部来自真机；本机 :8000 是用户自己的实例，这里不碰。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

API = "/api/v1"
PREFIX = "perf_f53_mp"


def same(a, b) -> bool:
    """按 1e-9 容差比温度这类数值；None 只与 None 相等。"""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-9
    return a == b


def ip(tag: str, n: int) -> dict:
    """换源 IP：单 IP 会被 120/min 全局限流吃掉，量不到真实收敛。"""
    return {"X-Forwarded-For": f"10.88.{n}.{tag}"}


async def login(client: httpx.AsyncClient, base: str, user: str, pwd: str) -> dict:
    r = await client.post(f"{base}{API}/auth/login",
                          json={"username": user, "password": pwd}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def put(client: httpx.AsyncClient, base: str, auth: dict, src: dict,
              target: str, value: float, enabled: bool = True) -> int:
    r = await client.put(f"{base}{API}/agent-ops/customizations/agent/{target}",
                         headers={**auth, **src},
                         json={"overrides": {"temperature": value},
                               "enabled": enabled, "comment": "F5.3 多实例补测"},
                         timeout=30)
    return r.status_code


async def view(client: httpx.AsyncClient, base: str, auth: dict, src: dict,
               target: str) -> tuple[float | None, int]:
    """本实例的生效视图（GET 内部会 ensure_fresh，所以每次读就是一次漂移检测机会）。"""
    r = await client.get(f"{base}{API}/agent-ops/customizations",
                         headers={**auth, **src}, timeout=30)
    if r.status_code != 200:
        return None, r.status_code
    for c in r.json().get("customizations", []):
        if c.get("target") == target:
            return (c.get("overrides") or {}).get("temperature"), 200
    return None, 200


async def case_convergence(ctx, args, out: dict) -> None:
    """A 写、B 读：B 从未持有该 key，看版本信号把它拉齐要多久。"""
    lat = []
    for r in range(args.rounds):
        target = f"{PREFIX}_conv{r}"
        value = 0.5 + r / 100.0
        status = await put(ctx, args.a, out["auth"], ip("a", r), target, value)
        if status != 200:
            lat.append(None)
            continue
        t0 = time.perf_counter()
        seen = None
        while time.perf_counter() - t0 < 12.0:
            await asyncio.sleep(args.poll)
            got, st = await view(ctx, args.b, out["auth"], ip("b", r), target)
            if st != 200:
                continue
            if got is not None and abs(got - value) < 1e-9:
                seen = time.perf_counter() - t0
                break
        lat.append(seen)
        # 写完立刻从 A 自己读一次：A 是本进程写的，应当恒为热值（对照组）
        self_val, _ = await view(ctx, args.a, out["auth"], ip("a2", r), target)
        out.setdefault("writer_side_ok", []).append(
            self_val is not None and abs(self_val - value) < 1e-9)
    ok = [x for x in lat if x is not None]
    out["convergence_rounds"] = args.rounds
    out["convergence_seen"] = len(ok)
    out["convergence_latency_s"] = {
        "each": [None if x is None else round(x, 3) for x in lat],
        "p50": round(sorted(ok)[len(ok) // 2], 3) if ok else None,
        "max": round(max(ok), 3) if ok else None,
    }
    print(f"[1] 跨实例收敛 {len(ok)}/{args.rounds}，"
          f"延迟 {out['convergence_latency_s']['each']}（B 侧节流窗口 5s 是设计值）")


async def case_dual_write(ctx, args, out: dict) -> None:
    """两个实例同时改同一个 target：进程内锁不跨进程，看谁被留在旧值上。"""
    target = f"{PREFIX}_race"
    n = args.writers
    tasks = []
    for i in range(n):
        tasks.append(put(ctx, args.a, out["auth"], ip(f"wa{i}", 1), target, 0.1 + i / 1000))
        tasks.append(put(ctx, args.b, out["auth"], ip(f"wb{i}", 2), target, 0.2 + i / 1000))
    codes = await asyncio.gather(*tasks)
    out["dual_writes"] = {"sent": len(codes), "accepted": sum(c == 200 for c in codes),
                          "throttled_429": sum(c == 429 for c in codes)}

    # 库真相（脚本与服务端同一份 settings，直连同一个 MySQL）
    db_val = await db_truth(target)
    out["db_truth"] = db_val
    await asyncio.sleep(1.0)
    va, _ = await view(ctx, args.a, out["auth"], ip("ra", 1), target)
    vb, _ = await view(ctx, args.b, out["auth"], ip("rb", 2), target)
    out["after_1s"] = {"A": va, "B": vb, "A_ok": same(va, db_val),
                       "B_ok": same(vb, db_val)}

    # 陈旧会不会自己好：不再产生任何变更，纯等 soak 秒后再读一次
    await asyncio.sleep(args.soak)
    va2, _ = await view(ctx, args.a, out["auth"], ip("sa", 1), target)
    vb2, _ = await view(ctx, args.b, out["auth"], ip("sb", 2), target)
    out[f"after_{args.soak}s"] = {"A": va2, "B": vb2,
                                  "A_ok": same(va2, db_val), "B_ok": same(vb2, db_val)}
    print(f"[2] 双实例并发写 {out['dual_writes']}；库={db_val}；"
          f"1s 后 A={va} B={vb}；{args.soak}s 后 A={va2} B={vb2}")
    stale = {name: v for name, v in (("A", va2), ("B", vb2))
             if v is not None and not same(v, db_val)}
    out["persistently_stale_instances"] = stale

    # 陈旧能不能被「下一次任意全局变更」冲掉：版本信号是全局单键，
    # 只要有人 bump，未认领该版本的实例下一次读就会重载。
    if stale:
        bump_target = f"{PREFIX}_bump"
        await put(ctx, args.a, out["auth"], ip("bm", 3), bump_target, 0.999)
        t0 = time.perf_counter()
        fixed = {}
        while time.perf_counter() - t0 < 12.0 and len(fixed) < len(stale):
            await asyncio.sleep(args.poll)
            for name, base in (("A", args.a), ("B", args.b)):
                if name in fixed:
                    continue
                cur, st = await view(ctx, base, out["auth"], ip(f"fx{name}", 4), target)
                if st == 200 and same(cur, db_val):
                    fixed[name] = round(time.perf_counter() - t0, 3)
        out["after_external_bump"] = {"healed_in_s": fixed,
                                      "still_stale": [n for n in stale if n not in fixed]}
        print(f"[2b] 外部 bump 后修复 {fixed}，仍陈旧 {out['after_external_bump']['still_stale']}")


async def db_truth(target: str) -> float | None:
    import app.core.database as database
    from app.db.models import agent_customizations
    from app.db.repositories.base import has_db, session_scope
    from sqlalchemy import select

    if not has_db():
        await database.init_db()
    async with session_scope() as s:
        rows = (await s.execute(select(agent_customizations).where(
            agent_customizations.c.target == target))).all()
    if not rows:
        return None
    return (rows[0].overrides or {}).get("temperature")


async def cleanup(ctx, args) -> dict:
    import app.core.database as database
    from app.db.models import agent_customizations, customization_versions
    from app.db.repositories.base import has_db, session_scope
    from sqlalchemy import delete

    if not has_db():
        await database.init_db()
    async with session_scope() as s:
        r1 = await s.execute(delete(agent_customizations).where(
            agent_customizations.c.target.like(f"{PREFIX}%")))
        r2 = await s.execute(delete(customization_versions).where(
            customization_versions.c.target.like(f"{PREFIX}%")))
    return {"agent_customizations": r1.rowcount, "customization_versions": r2.rowcount}


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--a", default="http://127.0.0.1:8010")
    p.add_argument("--b", default="http://127.0.0.1:8011")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="admin123")
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--writers", type=int, default=8)
    p.add_argument("--poll", type=float, default=0.3)
    p.add_argument("--soak", type=float, default=30)
    p.add_argument("--out", default="")
    args = p.parse_args()

    out: dict = {"a": args.a, "b": args.b, "started_at": time.strftime("%F %T")}
    async with httpx.AsyncClient() as ctx:
        out["auth"] = await login(ctx, args.a, args.username, args.password)
        for base, name in ((args.a, "A"), (args.b, "B")):
            r = await ctx.get(f"{base}{API}/health", headers=ip("h", 9), timeout=15)
            out[f"health_{name}"] = r.status_code
            print(f"{name} {base} health -> {r.status_code}")
        if not all(str(out.get(f"health_{n}")).startswith("2") for n in "AB"):
            print("两个实例没都起来，终止（不猜结论）")
            return 2
        await case_convergence(ctx, args, out)
        await case_dual_write(ctx, args, out)
        out["cleaned"] = await cleanup(ctx, args)
    out["finished_at"] = time.strftime("%F %T")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
