# -*- coding: utf-8 -*-
"""E 腿补充：把 agent-command 的完整响应体与频道内 Agent 回复读出来（判定 reply 是否真为空）。"""
import asyncio
import json
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
MARK = "E2Skill取证"
LINES = []


def log(m):
    LINES.append(str(m))


async def main():
    await db.init_all()
    tok = jwt_manager.create_access_token(
        {"user_id": "user_admin", "username": "admin", "role_codes": ["cio"]},
        expires_minutes=15)
    await redis_store.save_session(tok, {
        "user_id": "user_admin", "username": "admin", "role_codes": ["cio"],
        "created_at": time.time(), "expires_at": time.time() + 900, "active": True}, ttl=900)
    hdr = {"Authorization": f"Bearer {tok}"}
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=hdr, timeout=200) as c:
            r = await c.post("/api/v1/messaging/channels", json={
                "channel_type": "agent", "agent_id": "supervisor",
                "name": MARK, "member_ids": ["user_admin"]})
            b0 = r.json()
            cid = (b0.get("channel_id") or b0.get("id")
                   or (b0.get("channel") or {}).get("channel_id"))
            log("G0 channel=%s" % cid)
            t0 = time.monotonic()
            r = await c.post(f"/api/v1/messaging/channels/{cid}/agent-command",
                             json={"command": "汇总本周经营进展并生成一份周报文档",
                                   "agent_id": "supervisor", "skill": "weekly_report"})
            log("G1 http=%s %.1fs body=%s" % (
                r.status_code, time.monotonic() - t0,
                json.dumps(r.json(), ensure_ascii=False)[:1200]))
            await asyncio.sleep(1)
            async with session_scope() as s:
                rows = (await s.execute(_t(
                    "SELECT sender_type, LEFT(content, 300), created_at FROM chat_messages "
                    "WHERE channel_id=:c ORDER BY message_id DESC LIMIT 4"), {"c": cid})).fetchall()
            for i, (st, ct, ts) in enumerate(rows):
                log("G2[%d] %s %s | %s" % (i, st, ts, (ct or "")[:240]))
            async with session_scope() as s:
                art = (await s.execute(_t(
                    "SELECT action, resource, result FROM audit_logs "
                    "WHERE actor_did='user_admin' AND action IN "
                    "('tool.execute','skill.activated') ORDER BY timestamp DESC LIMIT 5"))).fetchall()
            for a, res, rst in art:
                log("G3 %s|%s|%s" % (a, res, rst))
    finally:
        await redis_store.drop_session(tok)
        try:
            async with session_scope() as s:
                ids = [x[0] for x in (await s.execute(_t(
                    "SELECT channel_id FROM chat_channels WHERE name=:n"), {"n": MARK})).fetchall()]
                for x in ids:
                    await s.execute(_t("DELETE FROM chat_messages WHERE channel_id=:c"), {"c": x})
                    await s.execute(_t("DELETE FROM chat_members WHERE channel_id=:c"), {"c": x})
                    await s.execute(_t("DELETE FROM chat_channels WHERE channel_id=:c"), {"c": x})
                await s.commit()
            log("G4 cleaned=%s" % ids)
        except Exception as e:  # noqa: BLE001
            log("G4 failed=%s" % str(e)[:160])
        await db.close_all()
    Path("_tmp_batch3_g.out").write_text("\n".join(LINES), encoding="utf-8")
    print("\n".join(LINES))


asyncio.run(main())
