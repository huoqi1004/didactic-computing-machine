# -*- coding: utf-8 -*-
"""第三批补漏复验（K 腿）：指名 skill 后 platform 域稳定进入委派集合并产出带数值的 docx。
连跑两次以证明分类抖动不再导致技能丢失；只删本轮自己发的两条消息（按 message_id）。"""
import asyncio
import json
import re
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
CMD = "汇总本周经营进展并生成一份周报文档"
LINES = []
MY_MSG_IDS = []
CID = None


def log(m):
    LINES.append(str(m))
    print(m, flush=True)


async def one_run(c, cid, tag):
    t0 = time.monotonic()
    r = await c.post(f"/api/v1/messaging/channels/{cid}/agent-command",
                     json={"command": CMD, "agent_id": "supervisor",
                           "skill": "weekly_report"})
    b = r.json()
    el = time.monotonic() - t0
    cmd_m = b.get("command_message") or {}
    resp_m = b.get("response_message") or {}
    for m in (cmd_m, resp_m):
        if m.get("message_id"):
            MY_MSG_IDS.append(m["message_id"])
    content = resp_m.get("content") or ""
    domains = re.findall(r"✅ (\w+)", content)
    nums = re.findall(r"[¥$]?\s?\d[\d,]{3,}(?:\.\d{1,2})?", content)
    log("%s http=%s %.1fs len=%d domains=%s" % (tag, r.status_code, el, len(content), domains))
    log("%s platform_hit=%s doc_link=%s numeric=%d placeholders=%d" % (
        tag, "platform" in domains,
        bool(re.search(r"/api/v1/document/download/", content)),
        len(nums), len(re.findall(r"待填写|待补|___", content))))
    log("%s nums_sample=%s" % (tag, nums[:6]))
    return content


async def main():
    await db.init_all()
    tok = jwt_manager.create_access_token(
        {"user_id": "user_admin", "username": "admin", "role_codes": ["cio"]},
        expires_minutes=15)
    await redis_store.save_session(tok, {
        "user_id": "user_admin", "username": "admin", "role_codes": ["cio"],
        "created_at": time.time(), "expires_at": time.time() + 900, "active": True}, ttl=900)
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=300,
                                     headers={"Authorization": f"Bearer {tok}"}) as c:
            r = await c.post("/api/v1/messaging/channels", json={
                "channel_type": "agent", "agent_id": "supervisor",
                "name": "K补漏复验", "member_ids": ["user_admin"]})
            b0 = r.json()
            cid = (b0.get("channel_id") or b0.get("id")
                   or (b0.get("channel") or {}).get("channel_id"))
            global CID
            CID = cid
            log("K0 channel=%s http=%s" % (cid, r.status_code))
            async with session_scope() as s:
                row = (await s.execute(_t(
                    "SELECT MIN(created_at), COUNT(*) FROM chat_messages "
                    "WHERE channel_id=:c"), {"c": cid})).first()
                log("K1 channel_preexisting_first_msg=%s total_before=%s" % (row[0], row[1]))
            for i in (1, 2):
                await one_run(c, cid, "K%d" % (i + 1))
    finally:
        await redis_store.drop_session(tok)
        try:
            async with session_scope() as s:
                for mid in MY_MSG_IDS:
                    await s.execute(_t(
                        "DELETE FROM chat_messages WHERE message_id=:mid"), {"mid": mid})
                await s.commit()
                left = (await s.execute(_t(
                    "SELECT COUNT(*) FROM chat_messages WHERE channel_id=:c"),
                    {"c": CID})).scalar()
            log("K9 deleted_my_messages=%s channel_msgs_left=%s" % (len(MY_MSG_IDS), left))
        except Exception as e:  # noqa: BLE001
            log("K9 failed=%s" % str(e)[:300])
        await db.close_all()
    Path("_tmp_batch3_k.out").write_text("\n".join(LINES), encoding="utf-8")
    print("IDS=%s" % json.dumps(MY_MSG_IDS))


asyncio.run(main())
