# -*- coding: utf-8 -*-
"""第三批真机取证（HTTP 腿，进程外驱动 :8010 实例）：工具目录 / Skill 目录 /
overlay 热加载（不重启）/ 管理端停开即时生效。只写临时技能，收尾自删。"""
import asyncio
import json
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


def log(msg):
    LINES.append(str(msg))


async def main():
    await db.init_all()
    tok = jwt_manager.create_access_token(
        {"user_id": "user_chairman", "username": "chairman",
         "role_codes": ["chairman"]}, expires_minutes=20)
    now = time.time()
    await redis_store.save_session(tok, {
        "user_id": "user_chairman", "username": "chairman",
        "role_codes": ["chairman"], "created_at": now,
        "expires_at": now + 1200, "active": True}, ttl=1200)

    hdr = {"Authorization": f"Bearer {tok}"}
    probe_path = Path(overlay_dir()) / f"{PROBE_SKILL}.yaml"
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=hdr, timeout=40) as c:
            r = await c.get("/api/v1/agent-ops/tools")
            payload = r.json()
            tools = payload.get("tools") or []
            log("A1 tools_http=%s total=%d" % (r.status_code, len(tools)))
            want = {"erp_readonly_query", "document_generate_docx",
                    "document_generate_xlsx"}
            for t in tools:
                if t.get("name") in want:
                    log("A2 tool=%s keys=%s" % (t.get("name"), sorted(t.keys())[:8]))

            r = await c.get("/api/v1/agent-ops/skills")
            skills = r.json().get("skills", [])
            log("B1 skills_http=%s count=%d" % (r.status_code, len(skills)))
            log("B2 invalid=%d disabled=%d" % (
                sum(1 for s in skills if not s.get("valid")),
                sum(1 for s in skills if not s.get("enabled"))))
            plat = [s for s in skills if s.get("domain") == "platform"]
            log("B3 platform_domain_skills=%s" % sorted(s["name"] for s in plat))
            for s in plat:
                log("B4 %s valid=%s enabled=%s tools=%s" % (
                    s["name"], s.get("valid"), s.get("enabled"), s.get("tools")))

            # —— 热加载：往发布目录写一个技能，不重启应出现在目录 ——
            probe_path.parent.mkdir(parents=True, exist_ok=True)
            probe_path.write_text(
                f"name: {PROBE_SKILL}\ndisplay_name: 第三批取证\ndescription: d\n"
                "domain: platform\ntools:\n  - document_generate_docx\n"
                "instructions: 取证用临时技能\n",
                encoding="utf-8")
            await asyncio.sleep(1.0)
            r = await c.get("/api/v1/agent-ops/skills")
            after = r.json().get("skills", [])
            hit = [s for s in after if s.get("name") == PROBE_SKILL]
            log("C1 after_write count=%d probe_found=%d valid=%s tools=%s" % (
                len(after), len(hit),
                hit[0].get("valid") if hit else None,
                hit[0].get("tools") if hit else None))

            # —— 管理端停用（收紧）：即时生效 ——
            r = await c.put(f"/api/v1/agent-ops/skills/{PROBE_SKILL}/enabled",
                            json={"enabled": False, "comment": "第三批验收取证"},
                            headers={"Idempotency-Key": f"b3-{int(now)}"})
            body = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else r.text
            log("D1 disable_http=%s status=%s thread=%s" % (
                r.status_code, (body or {}).get("status") if isinstance(body, dict) else body,
                (body or {}).get("thread_id") if isinstance(body, dict) else ""))
            r = await c.get("/api/v1/agent-ops/skills")
            hit2 = [s for s in r.json().get("skills", []) if s.get("name") == PROBE_SKILL]
            log("D2 after_disable enabled=%s present=%d" % (
                hit2[0].get("enabled") if hit2 else "absent", len(hit2)))

            # —— 停用后请求指名该技能：入口判定必须给出拒绝原因而不是静默 ——
            r = await c.get("/api/v1/agent-ops/skills")
            names = [s["name"] for s in r.json().get("skills", [])]
            log("E1 names=%s" % sorted(names))
    finally:
        if probe_path.exists():
            probe_path.unlink()
            log("F1 probe_yaml_removed=%s" % (not probe_path.exists()))
        # 自建定制行必须清掉（停用是即时生效的 active 行，不会随 YAML 消失）
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
        await customization_service.bump_version_signal()
        log("F2 custom_rows_removed=%d version_rows_removed=%d" % (n1, n2))
        await db.close_all()

    Path("_tmp_batch3_api_probe.out").write_text("\n".join(LINES), encoding="utf-8")
    print("\n".join(LINES))


asyncio.run(main())
