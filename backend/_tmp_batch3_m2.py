import asyncio, sys
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text as _t
from app.core import database as db
from app.db.repositories.base import session_scope

async def main():
    await db.init_all()
    try:
        async with session_scope() as s:
            for tb in ("agent_customizations", "skill_definitions"):
                cols = [r[0] for r in (await s.execute(_t(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE "
                    "TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t ORDER BY ORDINAL_POSITION"),
                    {"t": tb})).fetchall()]
                print("%s_COLS=%s" % (tb, cols))
    finally:
        await db.close_all()
asyncio.run(main())
