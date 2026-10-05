# -*- coding: utf-8 -*-
"""N 腿：启停两个方向都实测（停用即时生效 / 启用转人审），并取证待审批队列与落库行，收尾只删本腿自建行。"""
import asyncio
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402
from sqlalchemy import text as _t  # noqa: E402

from app.core import database as db  # noqa: E402
from app.core import redis_store  # noqa: E402
from app.core.auth.jwt_manager import jwt_manager  # noqa: E402
from app.db.repositories.base import session_scope  # noqa: E402

BASE = "http://127.0.0.1:8010"
NAME = "weekly_report"
LINES = []
T0 = time.time()


def log(m):
    LINES.append(str(m))
    print(m, flush=True)


async def catalog(c):
    r = await c.get("/api/v1/agent-ops/skills")
    for s in (r.json().get("skills") or []):
        if s.get("name") == NAME:
            return {k: s.get(k) for k in ("enabled", "valid", "published", "version")}
    return {"MISSING": True}


async def main():
    await db.init_all()
    tok = jwt_manager.create_access_token(
        {"user_id": "user_admin", "username": "admin", "role_codes": ["cio"]},
        expires_minutes=15)
    await redis_store.save_session(tok, {
        "user_id": "user_admin", "username": "admin", "role_codes": ["cio"],
        "created_at": time.time(), "expires_at": time.time() + 900, "active": True}, ttl=900)
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=60,
                                     headers={"Authorization": f"Bearer {tok}"}) as c:
            log("N0 catalog_before=%s" % await catalog(c))
            r = await c.put(f"/api/v1/agent-ops/skills/{NAME}/enabled",
                            json={"enabled": False, "comment": "N腿取证：停用"},
                            headers={"idempotency-key": f"n-off-{int(T0)}"})
            log("N1 disable http=%s body=%s" % (r.status_code, str(r.json())[:260]))
            log("N1 catalog_after_disable=%s" % await catalog(c))
            r = await c.put(f"/api/v1/agent-ops/skills/{NAME}/enabled",
                            json={"enabled": True, "comment": "N腿取证：启用需人审"},
                            headers={"idempotency-key": f"n-on-{int(T0)}"})
            body = r.json()
            log("N2 enable http=%s body=%s" % (r.status_code, str(body)[:320]))
            log("N2 catalog_after_enable=%s" % await catalog(c))
            r = await c.get("/api/v1/agent-ops/approvals")
            ab = r.json()
            items = ab.get("items") or ab.get("approvals") or ab.get("pending") or ab
            log("N3 approvals http=%s count=%s sample=%s" % (
                r.status_code, len(items) if isinstance(items, list) else "n/a",
                str(items[:2])[:300] if isinstance(items, list) else str(ab)[:300]))
    finally:
        await redis_store.drop_session(tok)
        try:
            async with session_scope() as s:
                rows = (await s.execute(_t(
                    "SELECT id, type, target, status, enabled, updated_by "
                    "FROM agent_customizations WHERE type='skill' AND target=:n "
                    "AND created_at >= FROM_UNIXTIME(:t0)"), {"n": NAME, "t0": T0 - 5})).fetchall()
                for r in rows:
                    log("N4 row id=%s type=%s target=%s status=%s enabled=%s by=%s" % r)
                ids = [r[0] for r in rows]
                for i in ids:
                    await s.execute(_t(
                        "DELETE FROM agent_customizations WHERE id=:i"), {"i": i})
                await s.commit()
                left = (await s.execute(_t(
                    "SELECT COUNT(*) FROM agent_customizations WHERE type='skill'"
                ))).scalar()
                ver = (await s.execute(_t(
                    "SELECT COUNT(*) FROM customization_versions WHERE type='skill' "
                    "AND target=:n AND created_at >= FROM_UNIXTIME(:t0)"),
                    {"n": NAME, "t0": T0 - 5})).fetchall()
                log("N5 deleted=%s skill_rows_left=%s versions_created=%s" % (
                    len(ids), left, ver[0][0] if ver else "n/a"))
        except Exception as e:  # noqa: BLE001
            log("N5 failed=%s" % str(e)[:300])
        await db.close_all()
    Path("_tmp_batch3_n.out").write_text("\n".join(LINES), encoding="utf-8")


asyncio.run(main())
