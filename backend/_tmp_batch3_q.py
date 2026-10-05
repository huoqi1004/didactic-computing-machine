# -*- coding: utf-8 -*-
"""Q 腿：核实驳回后的终态——用**新进程**从 DB 重建定制缓存，确认
weekly_report 的 enabled 回到"无定制行=启用"；并查人审卡片是否已关闭
（:8010 上那次 enabled=False 是我用裸 SQL 删行、未 bump 版本信号造成的进程内陈旧缓存）。"""
import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app.core import database as db  # noqa: E402
from app.core import redis_store  # noqa: E402
from app.core.auth.jwt_manager import jwt_manager  # noqa: E402

BASE = "http://127.0.0.1:8010"
LINES = []


def log(msg):
    LINES.append(str(msg))


async def main():
    await db.init_all()
    from app.core.agent.customization import customization_service
    from app.core.agent.skills import skill_registry

    await customization_service.ensure_fresh()
    log("Q1 cache_entries=%d skill_weekly_report_entry=%s"
        % (len(customization_service._cache),
           customization_service.get_override("skill", "weekly_report")))

    sk = skill_registry.get("weekly_report")
    log("Q2 fresh_process: present=%s enabled=%s valid=%s domain=%s"
        % (sk is not None, getattr(sk, "enabled", None),
           getattr(sk, "valid", None), getattr(sk, "domain", None)))
    plat = skill_registry.list_skills(domain="platform", include_disabled=True)
    log("Q3 platform_skills=%s" % sorted(
        "%s:enabled=%s,valid=%s" % (s.name, s.enabled, s.valid) for s in plat))

    tok = jwt_manager.create_access_token(
        {"user_id": "user_chairman", "username": "chairman",
         "role_codes": ["chairman"]}, expires_minutes=10)
    now = time.time()
    await redis_store.save_session(tok, {
        "user_id": "user_chairman", "username": "chairman",
        "role_codes": ["chairman"], "created_at": now,
        "expires_at": now + 600, "active": True}, ttl=600)
    async with httpx.AsyncClient(base_url=BASE, timeout=30,
                                 headers={"Authorization": f"Bearer {tok}"}) as c:
        r = await c.get("/api/v1/agent-ops/approvals")
        body = r.json() if r.status_code == 200 else {}
        rows = body.get("approvals") or []
        mine = [a for a in rows if "weekly_report" in str(a.get("thread_id", ""))]
        log("Q4 approvals_http=%s total=%d weekly_report_cards=%s"
            % (r.status_code, len(rows),
               [(a.get("thread_id"), a.get("status")) for a in mine]))
        r2 = await c.get("/api/v1/agent-ops/customizations/versions?status=pending_approval")
        log("Q5 http_pending_left=%s"
            % len((r2.json() or {}).get("versions", []) if r2.status_code == 200 else []))
        r3 = await c.get("/api/v1/agent-ops/customizations")
        live = (r3.json() or {}).get("customizations", []) if r3.status_code == 200 else []
        log("Q6 http_live_customizations=%d skill_rows=%s"
            % (len(live), [x for x in live if str(x.get("type")) == "skill"]))

    await db.close_all()
    from pathlib import Path
    Path("_tmp_batch3_q.out").write_text("\n".join(LINES), encoding="utf-8")
    print("\n".join(LINES))


asyncio.run(main())
