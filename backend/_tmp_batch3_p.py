# -*- coding: utf-8 -*-
"""P 腿：关闭遗留待批项（skill/weekly_report 的启用提案）——走产品 decide 通道，
顺带取证"人审闭环"。先确认谁能审批，再决定用哪个身份签 token。"""
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
    from sqlalchemy import text as _t

    from app.db.repositories.base import session_scope

    async with session_scope() as s:
        holders = (await s.execute(_t(
            "SELECT rp.role_code, p.resource, p.action FROM role_permission rp "
            "JOIN permissions p ON p.permission_id = rp.permission_id "
            "WHERE p.resource='customization' ORDER BY rp.role_code, p.action"
        ))).fetchall()
        byrole = {}
        for r in holders:
            byrole.setdefault(r[0], set()).add(r[2])
        log("P0 customization_roles=%s" % {k: sorted(v) for k, v in sorted(byrole.items())})
        pend = (await s.execute(_t(
            "SELECT id, type, target, status, changed_by, approved_by "
            "FROM customization_versions WHERE status='pending_approval' "
            "ORDER BY id DESC LIMIT 10"
        ))).fetchall()
        log("P1 pending_versions=%d" % len(pend))
        for r in pend:
            log("P1  id=%s type=%s target=%s status=%s changed_by=%s approved_by=%s"
                % (r[0], r[1], r[2], r[3], r[4], r[5]))

    approve = {r[0] for r in holders if r[2] in ("approve", "*")}
    read = {r[0] for r in holders if r[2] in ("read", "*")}
    usable = sorted(approve & read)
    log("P2 can_decide=%s" % usable)
    role = "chairman" if "chairman" in usable else (usable[0] if usable else None)
    if role is None:
        log("P3 no_role_with_customization_approve -> abort")
        await db.close_all()
        return

    tok = jwt_manager.create_access_token(
        {"user_id": "user_%s" % role, "username": role, "role_codes": [role]},
        expires_minutes=15)
    now = time.time()
    await redis_store.save_session(tok, {
        "user_id": "user_%s" % role, "username": role, "role_codes": [role],
        "created_at": now, "expires_at": now + 900, "active": True}, ttl=900)
    hdr = {"Authorization": f"Bearer {tok}"}
    log("P3 acting_as=%s" % role)

    async with httpx.AsyncClient(base_url=BASE, headers=hdr, timeout=40) as c:
        r = await c.get("/api/v1/agent-ops/customizations/versions?status=pending_approval")
        before = (r.json() or {}).get("versions", []) if r.status_code == 200 else []
        log("P4 http=%s pending_before=%d ids=%s"
            % (r.status_code, len(before), [v["id"] for v in before]))

        r = await c.post("/api/v1/agent-ops/customizations/skill/weekly_report/decide",
                         json={"approved": False,
                               "comment": "第三批收尾：本批不放宽启用，驳回遗留提案"})
        body = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else r.text
        log("P5 decide_http=%s body=%s" % (r.status_code, str(body)[:400]))

        r = await c.get("/api/v1/agent-ops/customizations/versions?status=pending_approval")
        after = (r.json() or {}).get("versions", []) if r.status_code == 200 else []
        log("P6 pending_after=%d ids=%s" % (len(after), [v["id"] for v in after]))

        r = await c.get("/api/v1/agent-ops/skills")
        hit = [s for s in r.json().get("skills", []) if s.get("name") == "weekly_report"]
        log("P7 weekly_report http=%s present=%d enabled=%s valid=%s"
            % (r.status_code, len(hit),
               hit[0].get("enabled") if hit else None,
               hit[0].get("valid") if hit else None))

    async with session_scope() as s:
        rows = (await s.execute(_t(
            "SELECT id, type, target, status, comment, approved_by FROM customization_versions "
            "WHERE type='skill' AND target='weekly_report' ORDER BY id"
        ))).fetchall()
        log("P8 versions_now=%d" % len(rows))
        for r in rows:
            log("P8  id=%s status=%s approved_by=%s comment=%s"
                % (r[0], r[3], r[5], (r[4] or "")[:40]))
        live = (await s.execute(_t(
            "SELECT id, status, enabled, updated_by FROM agent_customizations "
            "WHERE type='skill' AND target='weekly_report'"
        ))).fetchall()
        log("P9 live_custom_rows=%d %s"
            % (len(live), [(r[0], r[1], r[2], r[3]) for r in live]))
    await db.close_all()

    from pathlib import Path
    Path("_tmp_batch3_p.out").write_text("\n".join(LINES), encoding="utf-8")
    print("\n".join(LINES))


asyncio.run(main())
