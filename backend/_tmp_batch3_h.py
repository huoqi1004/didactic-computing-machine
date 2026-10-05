# -*- coding: utf-8 -*-
"""第三批收尾取证：规则路径把创作类指令路由到 platform 域并真实产出文档；
同时核对 platform 资源在已落库 RBAC 中的权限行与角色授予。收尾删自建频道。"""
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
from app.core.config import settings  # noqa: E402
from app.db.repositories.base import session_scope  # noqa: E402

BASE = "http://127.0.0.1:8010"
MARK = "H1Skill取证"
LINES = []


def log(m):
    LINES.append(str(m))


def artifacts():
    d = Path(settings.document_output_dir)
    d = d if d.is_absolute() else Path(__file__).parent / d
    files = sorted(d.glob("*.docx"), key=lambda p: p.stat().st_mtime)
    return [(f.name, f.stat().st_size) for f in files]


async def main():
    await db.init_all()
    tok = jwt_manager.create_access_token(
        {"user_id": "user_admin", "username": "admin", "role_codes": ["cio"]},
        expires_minutes=15)
    await redis_store.save_session(tok, {
        "user_id": "user_admin", "username": "admin", "role_codes": ["cio"],
        "created_at": time.time(), "expires_at": time.time() + 900, "active": True}, ttl=900)
    hdr = {"Authorization": f"Bearer {tok}"}
    before = artifacts()
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=hdr, timeout=300) as c:
            r = await c.post("/api/v1/messaging/channels", json={
                "channel_type": "agent", "agent_id": "supervisor",
                "name": MARK, "member_ids": ["user_admin"]})
            b0 = r.json()
            cid = (b0.get("channel_id") or b0.get("id")
                   or (b0.get("channel") or {}).get("channel_id"))
            log("H0 channel=%s http=%s" % (cid, r.status_code))
            t0 = time.monotonic()
            r = await c.post(f"/api/v1/messaging/channels/{cid}/agent-command",
                             json={"command": "汇总本周经营进展并生成一份周报文档",
                                   "agent_id": "supervisor", "skill": "weekly_report"})
            b = r.json()
            el = time.monotonic() - t0
            content = ((b.get("response_message") or {}).get("content")) or ""
            log("H1 http=%s %.1fs reply_len=%d" % (r.status_code, el, len(content)))
            log("H2 reply=%s" % content[:700].replace("\n", " / "))
            after = artifacts()
            log("H3 artifacts_before=%d after=%d new=%s" % (
                len(before), len(after),
                [n for n, _ in after if n not in {x for x, _ in before}]))
            if after:
                log("H4 newest=%s size=%dB" % (after[-1][0], after[-1][1]))
            async with session_scope() as s:
                perm = (await s.execute(_t(
                    "SELECT permission_id, sensitivity FROM permissions "
                    "WHERE resource='platform' ORDER BY permission_id"))).fetchall()
                roles = [x[0] for x in (await s.execute(_t(
                    "SELECT DISTINCT rp.role_code FROM role_permission rp "
                    "JOIN permissions p ON p.permission_id=rp.permission_id "
                    "WHERE p.resource='platform' ORDER BY rp.role_code"))).fetchall()]
                aud = (await s.execute(_t(
                    "SELECT action, resource, result FROM audit_logs "
                    "WHERE actor_did='user_admin' AND action LIKE 'skill.%' "
                    "ORDER BY timestamp DESC LIMIT 2"))).fetchall()
            log("H5 platform_perm_rows=%d %s" % (len(perm), [p[0] for p in perm]))
            log("H6 platform_role_codes=%s" % roles)
            for a, res, rst in aud:
                log("H7 audit %s|%s|%s" % (a, res, rst))
    finally:
        await redis_store.drop_session(tok)
        try:
            async with session_scope() as s:
                ids = [x[0] for x in (await s.execute(_t(
                    "SELECT channel_id FROM chat_channels WHERE name IN (:n1,:n2)"),
                    {"n1": MARK, "n2": "第三批Skill取证"})).fetchall()]
                for x in ids:
                    await s.execute(_t("DELETE FROM chat_messages WHERE channel_id=:c"), {"c": x})
                    await s.execute(_t("DELETE FROM chat_members WHERE channel_id=:c"), {"c": x})
                    await s.execute(_t("DELETE FROM chat_channels WHERE channel_id=:c"), {"c": x})
                await s.commit()
            log("H8 cleaned_channels=%s" % ids)
        except Exception as e:  # noqa: BLE001
            log("H8 failed=%s" % str(e)[:200])
        await db.close_all()
    Path("_tmp_batch3_h.out").write_text("\n".join(LINES), encoding="utf-8")
    print("\n".join(LINES))


asyncio.run(main())
