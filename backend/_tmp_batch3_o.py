# -*- coding: utf-8 -*-
"""清理 N 腿自建定制行（不删 customization_versions 留痕），并核对本批 platform 授权面。"""
import asyncio, sys
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text as _t
from app.core import database as db
from app.db.repositories.base import session_scope

async def main():
    await db.init_all()
    try:
        async with session_scope() as s:
            rows = (await s.execute(_t(
                "SELECT id, type, target, status, enabled, updated_by, created_at "
                "FROM agent_customizations WHERE type='skill'"))).fetchall()
            print("O1 skill_rows_before=%s" % [tuple(r) for r in rows])
            ids = [r[0] for r in rows]
            for i in ids:
                await s.execute(_t("DELETE FROM agent_customizations WHERE id=:i"), {"i": i})
            await s.commit()
            left = (await s.execute(_t(
                "SELECT COUNT(*) FROM agent_customizations WHERE type='skill'"))).scalar()
            ver = (await s.execute(_t(
                "SELECT id, type, target, status, comment, changed_by FROM "
                "customization_versions WHERE target='weekly_report' ORDER BY id"))).fetchall()
            print("O2 skill_rows_after=%s" % left)
            print("O3 versions_kept=%d" % len(ver))
            for v in ver:
                print("   ver id=%s type=%s target=%s status=%s by=%s comment=%s"
                      % (v[0], v[1], v[2], v[3], v[5], str(v[4])[:60]))
            gate = (await s.execute(_t(
                "SELECT GROUP_CONCAT(DISTINCT rp.role_code ORDER BY rp.role_code) "
                "FROM role_permission rp JOIN permissions p ON p.permission_id=rp.permission_id "
                "WHERE p.permission_id='platform:read'"))).scalar()
            excl = (await s.execute(_t(
                "SELECT DISTINCT rp.role_code FROM role_permission rp "
                "JOIN permissions p ON p.permission_id=rp.permission_id "
                "WHERE p.resource='platform' AND p.action<>'read' ORDER BY rp.role_code"))).fetchall()
            print("O4 db_platform_read_roles=%s" % gate)
            print("O5 db_platform_nonread_roles=%s" % [x[0] for x in excl])
    finally:
        await db.close_all()
asyncio.run(main())
