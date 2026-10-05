# -*- coding: utf-8 -*-
"""第三批补漏取证：
I1 platform:read 入口资格放开后，cio 经 supervisor+weekly_report 真取到数（不再全是"待补"）；
I2 audit._save_verify_state 补 orjson 后，verify_chain 点位/报告真的落库。
收尾删自建频道。"""
import asyncio
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
from app.core.security.audit import audit_chain  # noqa: E402
from app.db.repositories.base import session_scope  # noqa: E402

BASE = "http://127.0.0.1:8010"
MARK = "I1Skill补漏取证"
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
        async with httpx.AsyncClient(base_url=BASE, headers=hdr, timeout=300) as c:
            r = await c.post("/api/v1/messaging/channels", json={
                "channel_type": "agent", "agent_id": "supervisor",
                "name": MARK, "member_ids": ["user_admin"]})
            b0 = r.json()
            cid = (b0.get("channel_id") or b0.get("id")
                   or (b0.get("channel") or {}).get("channel_id"))
            log("I0 channel=%s http=%s" % (cid, r.status_code))
            t0 = time.monotonic()
            r = await c.post(f"/api/v1/messaging/channels/{cid}/agent-command",
                             json={"command": "汇总本周经营进展并生成一份周报文档",
                                   "agent_id": "supervisor", "skill": "weekly_report"})
            b = r.json()
            el = time.monotonic() - t0
            content = ((b.get("response_message") or {}).get("content")) or ""
            log("I1 http=%s %.1fs reply_len=%d" % (r.status_code, el, len(content)))
            log("I2 perm_denied=%s" % ("platform:read" in content))
            nums = re.findall(r"\d[\d,]*\.\d{2}|\d[\d,]{2,}", content)
            log("I3 numeric_tokens_found=%d sample=%s" % (len(nums), nums[:8]))
            dl = re.search(r"\((/api/v1/document/download/[^)]+)\)", content)
            log("I4 artifact_link=%s" % (dl.group(1) if dl else "NONE"))
            if dl:
                rr = await c.get(dl.group(1))
                log("I5 download http=%s bytes=%d" % (rr.status_code, len(rr.content)))
            async with session_scope() as s:
                has = (await s.execute(_t(
                    "SELECT COUNT(*) FROM role_permission rp "
                    "JOIN permissions p ON p.permission_id=rp.permission_id "
                    "WHERE p.resource='platform' AND rp.role_code='cio'"))).scalar()
                roles = [x[0] for x in (await s.execute(_t(
                    "SELECT DISTINCT rp.role_code FROM role_permission rp "
                    "JOIN permissions p ON p.permission_id=rp.permission_id "
                    "WHERE p.permission_id='platform:read' ORDER BY rp.role_code"))).fetchall()]
                acts = [x[0] for x in (await s.execute(_t(
                    "SELECT DISTINCT p.permission_id FROM role_permission rp "
                    "JOIN permissions p ON p.permission_id=rp.permission_id "
                    "WHERE p.resource='platform' AND rp.role_code='cio' "
                    "ORDER BY p.permission_id"))).fetchall()]
                aud = (await s.execute(_t(
                    "SELECT action, result FROM audit_logs "
                    "WHERE actor_did='user_admin' AND action IN ('skill.activated',"
                    "'tool.invoked') ORDER BY timestamp DESC LIMIT 6"))).fetchall()
            log("I6 cio_platform_perm=%s rows=%s" % (has, acts))
            log("I7 platform:read role_count=%d roles=%s" % (len(roles), roles))
            for a_, r_ in aud:
                log("I8 audit %s|%s" % (a_, r_))
    finally:
        await redis_store.drop_session(tok)
        try:
            async with session_scope() as s:
                ids = [x[0] for x in (await s.execute(_t(
                    "SELECT channel_id FROM chat_channels WHERE name=:n"),
                    {"n": MARK})).fetchall()]
                for x in ids:
                    await s.execute(_t("DELETE FROM chat_messages WHERE channel_id=:c"), {"c": x})
                    await s.execute(_t("DELETE FROM chat_members WHERE channel_id=:c"), {"c": x})
                    await s.execute(_t("DELETE FROM chat_channels WHERE channel_id=:c"), {"c": x})
                await s.commit()
            log("I9 cleaned_channels=%s" % ids)
        except Exception as e:  # noqa: BLE001
            log("I9 failed=%s" % str(e)[:200])

    # ---- I10/I11：审计校验点位落库（orjson 修复取证）----
    try:
        async with session_scope() as s:
            await s.execute(_t(
                "CREATE TABLE IF NOT EXISTS audit_verify_state (id INT PRIMARY KEY, "
                "last_incremental_at DATETIME(6), last_full_at DATETIME(6), "
                "last_report JSON, updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6))"))
            await s.commit()
        before = (await audit_chain._get_verify_state(db.get_engine())).get("last_incremental_at")
        report = await audit_chain.verify_chain(full=False)
        state = await audit_chain._get_verify_state(db.get_engine())
        after = state.get("last_incremental_at")
        raw = state.get("last_report")
        log("I10 verify_chain is_valid=%s checked=%s saved_before=%s saved_after=%s" % (
            report.get("is_valid"), report.get("checked"), before, after))
        log("I11 last_report_persisted=%s len=%s head=%s" % (
            raw is not None, len(str(raw or "")), str(raw or "")[:120]))
    except Exception as e:  # noqa: BLE001
        log("I10/I11 failed=%s" % str(e)[:300])
    finally:
        await db.close_all()
    Path("_tmp_batch3_i.out").write_text("\n".join(LINES), encoding="utf-8")
    print("\n".join(LINES))


asyncio.run(main())
