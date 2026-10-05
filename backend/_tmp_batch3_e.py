# -*- coding: utf-8 -*-
"""第三批真机 E 腿：频道 Agent 指令带 skill → 激活留痕 / 拒绝留痕（HTTP 端到端）。
收尾自删自建频道与消息。"""
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
LINES = []
MARK = "第三批Skill取证"


def log(m):
    LINES.append(str(m))


async def main():
    await db.init_all()
    tok = jwt_manager.create_access_token(
        {"user_id": "user_admin", "username": "admin", "role_codes": ["cio"]},
        expires_minutes=15)
    await redis_store.save_session(tok, {
        "user_id": "user_admin", "username": "admin", "role_codes": ["cio"],
        "created_at": time.time(), "expires_at": time.time() + 900, "active": True},
        ttl=900)
    hdr = {"Authorization": f"Bearer {tok}"}
    channel_id = None
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=hdr, timeout=200) as c:
            r = await c.post("/api/v1/messaging/channels", json={
                "channel_type": "agent", "agent_id": "supervisor",
                "name": MARK, "member_ids": ["user_admin"]})
            body = r.json() if r.status_code < 400 else {}
            channel_id = (body.get("channel_id") or body.get("id")
                          or (body.get("channel") or {}).get("channel_id"))
            log("E0 channel_http=%s id=%s" % (r.status_code, channel_id))
            if not channel_id:
                log("E0 body=%s" % str(body or r.text)[:400])
                return

            for label, skill in (("activated", "weekly_report"),
                                 ("denied_case", "not_a_skill")):
                t0 = time.monotonic()
                r = await c.post(f"/api/v1/messaging/channels/{channel_id}/agent-command",
                                 json={"command": f"{MARK} 统计报销金额分布并生成本周周报文档",
                                       "agent_id": "supervisor", "skill": skill})
                el = time.monotonic() - t0
                b = r.json() if r.status_code < 400 else {"detail": r.text[:200]}
                content = ((b.get("message") or {}).get("content")
                           or b.get("reply") or b.get("content") or "")
                log("E1[%s] http=%s %.1fs reply_len=%d head=%s" % (
                    label, r.status_code, el, len(str(content)), str(content)[:220]))

            async with session_scope() as s:
                rows = (await s.execute(_t(
                    "SELECT action, resource, result, risk_level, details FROM audit_logs "
                    "WHERE actor_did='user_admin' AND action LIKE 'skill.%' "
                    "ORDER BY timestamp DESC, entry_id DESC LIMIT 6"))).fetchall()
            log("E2 audit_rows=%d" % len(rows))
            for a, res, rst, rk, det in rows[:6]:
                log("E3 %s|%s|%s|%s|%s" % (a, res, rst, rk, str(det)[:200]))
    finally:
        await redis_store.drop_session(tok)
        Path("_tmp_batch3_e.out").write_text("\n".join(LINES), encoding="utf-8")
        try:
            async with session_scope() as s:
                ids = [r[0] for r in (await s.execute(_t(
                    "SELECT channel_id FROM chat_channels WHERE name=:n"),
                    {"n": MARK})).fetchall()]
                for cid in ids:
                    nm = (await s.execute(_t(
                        "DELETE FROM chat_messages WHERE channel_id=:c"), {"c": cid})).rowcount
                    nmm = (await s.execute(_t(
                        "DELETE FROM chat_members WHERE channel_id=:c"), {"c": cid})).rowcount
                    nc = (await s.execute(_t(
                        "DELETE FROM chat_channels WHERE channel_id=:c"), {"c": cid})).rowcount
                    log("E4 cleanup channel=%s msgs=%d members=%d chan=%d" % (
                        cid, nm, nmm, nc))
                await s.commit()
        except Exception as e:  # noqa: BLE001
            log("E4 cleanup_failed=%s" % str(e)[:200])
        await db.close_all()
    print("\n".join(LINES))


asyncio.run(main())
