"""
F5.4 定制层故障降级——真机演练脚本（路线图 Phase F5 退出标准第 5 条）
======================================================================

CI（tests/test_customization_degradation_f54.py）用 monkeypatch 注入失败，
这里用**真的坏实例**跑一遍，回答运维真正会问的三个问题：

1. 定制层读不到时，服务是不是一样的不可用？→ 期望：/health 200、生效视图 200 且为空、
   各执行点回退内置默认（0 不可用）；
2. 这件事看得见吗？→ 期望：`GET /api/v1/agent-ops/customizations/degradation`
   报 degraded=true/mode=defaults，且 `/metrics` 上 `erp_customization_degraded 1`；
3. 清空生效表能不能回出厂（不重启）？→ 在健康实例上写一条再撤掉，看生效视图回到默认。

执行（两个实例都要先起来，同 .env、同 Redis）：
  健康： ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
  坏库： ERP_MYSQL_URL="mysql+aiomysql://erp:erp_password@127.0.0.1:3306/erp_f54_no_such_db?charset=utf8mb4" \\
          ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8012
  演练： ./.venv/Scripts/python.exe tests/perf/drill_f54_degradation.py

坏库那条腿用的是**不存在的 schema**（MySQL 报 1044 access denied），零 DDL、
不碰 erp_db 里任何业务数据；健康那条腿只增删脚本自己建的 `drill_f54_` 前缀行。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

API = "/api/v1"
DRILL_TARGET = "drill_f54_probe_agent"


async def probe(client: httpx.AsyncClient, url: str, headers: dict | None = None,
                method: str = "get", json_body: dict | None = None) -> tuple[int, object]:
    try:
        r = await client.request(method, url, headers=headers or {},
                                 json=json_body, timeout=20)
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"
    ctype = r.headers.get("content-type", "")
    body = r.json() if "json" in ctype else r.text[:400]
    return r.status_code, body


async def gauge_value(client: httpx.AsyncClient, base: str, name: str) -> object:
    """从 /metrics 抓一个 gauge 的当前值（Prometheus 文本格式，全文匹配）。"""
    try:
        r = await client.get(f"{base}/metrics", timeout=20)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    text = r.text          # 别走 probe()：那里为省事截了 400 字符，指标名会被截没
    if r.status_code != 200:
        return f"metrics http {r.status_code}"
    m = re.search(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)$",
                  text, re.MULTILINE)
    return float(m.group(1)) if m else "absent"


async def leg_broken(client: httpx.AsyncClient, base: str, auth: dict, out: dict) -> None:
    """坏库实例：读不到定制层 → 回退内置默认 + 显式告警 + 服务照旧可用。"""
    res: dict = {}
    res["health"] = (await probe(client, f"{base}{API}/health"))[0]
    res["degraded_gauge"] = await gauge_value(client, base, "erp_customization_degraded")

    code, body = await probe(client, f"{base}{API}/agent-ops/customizations/degradation", auth)
    res["degradation_endpoint"] = {"http": code,
                                   "body": body if code == 200 else str(body)[:200]}
    code, body = await probe(client, f"{base}{API}/agent-ops/customizations", auth)
    res["active_view"] = {
        "http": code,
        "entries": (len(body["customizations"]) if isinstance(body, dict)
                    and "customizations" in body else str(body)[:200]),
    }
    # 降级期间管理端还能不能写？（写要落库，期望是明确失败而不是悄悄成功）
    code, body = await probe(client, f"{base}{API}/agent-ops/customizations/agent/{DRILL_TARGET}",
                             auth, method="put",
                             json_body={"overrides": {"temperature": 0.42},
                                        "enabled": True, "comment": "F5.4 演练-降级期写入"})
    res["write_while_degraded"] = {"http": code, "body": str(body)[:200]}
    out["broken"] = res
    print(f"[坏库 {base}] health={res['health']} gauge={res['degraded_gauge']} "
          f"degradation={res['degradation_endpoint']['http']} "
          f"view={res['active_view']} write={res['write_while_degraded']['http']}")


async def leg_healthy(client: httpx.AsyncClient, base: str, auth: dict, out: dict) -> None:
    """健康实例：写一条自建的 agent 定制 → 生效 → 撤掉 → 回出厂，全程不重启。"""
    res: dict = {"base_entries": None}
    code, body = await probe(client, f"{base}{API}/agent-ops/customizations/degradation", auth)
    res["degradation_before"] = {"http": code, "body": body}
    if code == 200 and isinstance(body, dict):
        res["base_entries"] = body.get("cache_entries")

    code, body = await probe(client, f"{base}{API}/agent-ops/customizations/agent/{DRILL_TARGET}",
                             auth, method="put",
                             json_body={"overrides": {"temperature": 0.42},
                                        "enabled": True, "comment": "F5.4 演练-出厂回退"})
    res["apply"] = {"http": code, "body": str(body)[:200]}

    async def view() -> object:
        c, b = await probe(client, f"{base}{API}/agent-ops/customizations", auth)
        if c != 200 or not isinstance(b, dict):
            return f"http {c}"
        return [x for x in b["customizations"] if x.get("target") == DRILL_TARGET]

    res["view_after_apply"] = await view()

    # 撤掉生效行 = enabled:false（PUT 的删除语义），不是留一条 disabled 行
    code, body = await probe(client, f"{base}{API}/agent-ops/customizations/agent/{DRILL_TARGET}",
                             auth, method="put",
                             json_body={"overrides": {"temperature": 0.42},
                                        "enabled": False, "comment": "F5.4 演练-回出厂"})
    res["revert"] = {"http": code, "body": str(body)[:200]}
    res["view_after_revert"] = await view()

    code, body = await probe(client, f"{base}{API}/agent-ops/customizations/degradation", auth)
    res["degradation_after"] = {"http": code, "body": body}
    res["degraded_gauge"] = await gauge_value(client, base, "erp_customization_degraded")
    # "撤掉生效"的判据不是列表变空——停用条目仍会带着 enabled:false 出现在
    # 生效视图里（启停态要能查），真正的判据是"不再有 enabled 的覆盖"。
    out["healthy"] = res
    print(f"[健康 {base}] apply={res['apply']['http']} "
          f"生效视图(应用后)={res['view_after_apply']} "
          f"回出厂后={res['view_after_revert']} gauge={res['degraded_gauge']}")


async def purge_drill_rows() -> dict:
    """删掉演练自己建的生效行；变更历史（customization_versions）按红线 3 保留。"""
    import app.core.database as database
    from app.db.models import agent_customizations
    from app.db.repositories.base import has_db, session_scope
    from sqlalchemy import delete

    if not has_db():
        await database.init_db()
    async with session_scope() as session:
        r = await session.execute(delete(agent_customizations).where(
            agent_customizations.c.target.like("drill_f54_%")))
    return {"agent_customizations_deleted": r.rowcount}


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--healthy", default="http://127.0.0.1:8010")
    p.add_argument("--broken", default="http://127.0.0.1:8012")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="admin123")
    p.add_argument("--out", default="")
    args = p.parse_args()

    out: dict = {"started_at": time.strftime("%F %T")}
    async with httpx.AsyncClient() as client:
        code, body = await probe(client, f"{args.healthy}{API}/auth/login",
                                 method="post",
                                 json_body={"username": args.username, "password": args.password})
        if code != 200 or not isinstance(body, dict) or "access_token" not in body:
            print(f"登录失败（http {code}），演练需要一条真实管理端令牌，终止")
            return 2
        auth = {"Authorization": f"Bearer {body['access_token']}"}
        print(f"登录 :{args.healthy} -> 200（令牌同时写进共享 Redis 会话）")

        await leg_healthy(client, args.healthy, auth, out)
        await leg_broken(client, args.broken, auth, out)

    out["purged"] = await purge_drill_rows()
    out["finished_at"] = time.strftime("%F %T")
    healthy = out["healthy"]
    broken = out["broken"]
    verdict = {
        "0_不可用_坏库仍在服务": broken["health"] == 200
            and broken["active_view"]["http"] == 200,
        "显式告警_状态端点可读": (broken["degradation_endpoint"]["http"] == 200
                    and isinstance(broken["degradation_endpoint"]["body"], dict)
                    and broken["degradation_endpoint"]["body"].get("degraded") is True),
        "显式告警_metrics有指标": broken["degraded_gauge"] == 1.0,
        "回退内置默认_生效视图为空": broken["active_view"]["entries"] == 0,
        "降级模式说实话": (isinstance(broken["degradation_endpoint"]["body"], dict)
                    and broken["degradation_endpoint"]["body"].get("mode") == "defaults"),
        "出厂回退_应用后生效": any(x.get("enabled") for x in healthy["view_after_apply"]),
        "出厂回退_撤掉后无生效覆盖": not any(
            x.get("enabled") for x in healthy["view_after_revert"]),
        "出厂回退_不误报故障": healthy["degradation_after"]["http"] == 200
            and isinstance(healthy["degradation_after"]["body"], dict)
            and healthy["degradation_after"]["body"].get("degraded") is False,
    }
    out["verdict"] = verdict
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("演练判据：", json.dumps(verdict, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(verdict.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
