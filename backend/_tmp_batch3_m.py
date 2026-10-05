# -*- coding: utf-8 -*-
"""收尾体检：我只读取当前状态（定制行 / overlay 文件 / 技能启停 / 今日产物），不做删除。"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text as _t  # noqa: E402

from app.core import database as db  # noqa: E402
from app.core.agent.skills import skill_registry  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.repositories.base import session_scope  # noqa: E402


async def main():
    await db.init_all()
    try:
        async with session_scope() as s:
            cust = (await s.execute(_t(
                "SELECT id, type, target, environment, enabled, status, updated_by, "
                "created_at FROM agent_customizations WHERE type IN ('skill','mcp','tool') "
                "ORDER BY created_at DESC LIMIT 12"))).fetchall()
            print("CUSTOM_ROWS=%d" % len(cust))
            for r in cust:
                print("  cust id=%s type=%s target=%s env=%s enabled=%s status=%s by=%s at=%.19s"
                      % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]))
            sk = (await s.execute(_t(
                "SELECT name, version, status, created_by, created_at "
                "FROM skill_definitions ORDER BY created_at DESC LIMIT 10"))).fetchall()
            print("SKILL_DEF_ROWS=%d" % len(sk))
            for r in sk:
                print("  def name=%s version=%s status=%s by=%s at=%.19s" % r)
        ov = Path(settings.skills_overlay_dir)
        ov = ov if ov.is_absolute() else Path.cwd() / ov
        print("OVERLAY_DIR=%s exists=%s" % (ov, ov.exists()))
        if ov.exists():
            for p in sorted(ov.rglob("*")):
                if p.is_file():
                    print("  overlay file=%s size=%d" % (p, p.stat().st_size))
        names = ["data_brief", "docx_report", "meeting_notes", "weekly_report",
                 "xlsx_report", "supervision_plan"]
        for n in names:
            got = skill_registry.get(n)
            print("  skill %-16s enabled_lookup=%s domain=%s" % (
                n, got is not None, getattr(got, "domain", "-")))
        d = Path(settings.document_output_dir)
        d = d if d.is_absolute() else Path.cwd() / d
        print("DOC_DIR=%s" % d)
        for p in sorted(d.glob("*.docx")):
            print("  docx %.19s %8dB %s" % (__import__("datetime").datetime.fromtimestamp(
                p.stat().st_mtime), p.stat().st_size, p.name))
    finally:
        await db.close_all()


asyncio.run(main())
