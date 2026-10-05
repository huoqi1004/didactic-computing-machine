# -*- coding: utf-8 -*-
"""补测 D 腿：管理端停用为何 403——先看服务端解析出的身份与权限，再按有权限的身份重试。"""
import asyncio
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app.core import database as db  # noqa: E402
from app.core import redis_store  # noqa: E402
from app.core.agent.skills import overlay_dir  # noqa: E402
from app.core.auth.jwt_manager import jwt_manager  # noqa: E402

BASE = "http://127.0.0.1:8010"
PROBE_SKILL = "zz_batch3_probe"
LINES = []


def log(m):
    LINES.append(str(m))


def mint(uid, uname, roles):
    tok = jwt_manager.create_access_token(
        {"user_id": uid, "username": uname, "role_codes": roles}, expires_minutes=15)
    return tok, {
        "user_id": uid, "username": uname, "role_codes": roles,
        "created_at": time.time(), "expires_at": time.time() + 900, "active": True}


async def main():
    await db.init_all()
    probe_path = Path(overlay_dir()) / f"{PROBE_SKILL}.yaml"
    toks = []
    try:
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text(
            f"name: {PROBE_SKILL}\ndisplay_name: 第三批取证\ndescription: d\n"
            "domain: platform\ntools:\n  - document_generate_docx\n"
            "instructions: 取证用临时技能\n", encoding="utf-8")
        await asyncio.sleep(1.0)

        for uid, uname, roles in (
                ("user_chairman", "chairman", ["chairman"]),
                ("user_admin", "admin", ["cio"]),
                ("user_ceo", "ceo", ["ceo"])):
            tok, sess = mint(uid, uname, roles)
            await redis_store.save_session(tok, sess, ttl=900)
            toks.append(tok)
            hdr = {"Authorization": f"Bearer {tok}"}
            async with httpx.AsyncClient(base_url=BASE, headers=hdr, timeout=30) as c:
                me = await c.get("/api/v1/auth/me")
                body = me.json() if me.status_code == 200 else me.text
                log("P1 %s me_http=%s body=%s" % (uid, me.status_code, str(body)[:400]))
                r = await c.put(f"/api/v1/agent-ops/skills/{PROBE_SKILL}/enabled",
                                 json={"enabled": False, "comment": "第三批验收取证"},
                                 headers={"Idempotency-Key": f"b3d2-{uid}-{int(time.time())}"})
                log("P2 %s disable_http=%s detail=%s" % (
                    uid, r.status_code, str(r.json())[:260] if r.status_code < 400 else r.text[:200]))
                if r.status_code < 400:
                    skills = (await c.get("/api/v1/agent-ops/skills")).json().get("skills", [])
                    hit = [s for s in skills if s.get("name") == PROBE_SKILL]
                    log("P3 after_disable present=%d enabled=%s count=%d" % (
                        len(hit), hit[0].get("enabled") if hit else "absent", len(skills)))
                    break
    finally:
        for t in toks:
            await redis_store.drop_session(t)
        if probe_path.exists():
            probe_path.unlink()
        from sqlalchemy import text as _t

        from app.core.agent.customization import customization_service
        from app.db.repositories.base import session_scope

        async with session_scope() as s:
            n1 = (await s.execute(_t(
                "DELETE FROM agent_customizations WHERE type='skill' AND target=:n"
            ), {"n": PROBE_SKILL})).rowcount
            n2 = (await s.execute(_t(
                "DELETE FROM customization_versions WHERE type='skill' AND target=:n"
            ), {"n": PROBE_SKILL})).rowcount
            await s.commit()
        await customization_service.bump_version_signal()
        log("P4 custom_rows_removed=%d version_rows=%d yaml=%s" % (
            n1, n2, not probe_path.exists()))
        await db.close_all()
    Path("_tmp_batch3_d2.out").write_text("\n".join(LINES), encoding="utf-8")
    print("\n".join(LINES))


asyncio.run(main())
