# -*- coding: utf-8 -*-
import asyncio, sys
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text as _t
from app.core import database as db
from app.db.repositories.base import session_scope

async def main():
    await db.init_all()
    try:
        async with session_scope() as s:
            cols = [r[0] for r in (await s.execute(_t(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='chat_channels'"))).fetchall()]
            print("CHANNEL_COLS=%s" % cols)
            rows = (await s.execute(_t(
                "SELECT * FROM chat_channels WHERE channel_id='CH-8d43324bbebf'"))).mappings().all()
            for r in rows:
                print("CHANNEL=%s" % {k: v for k, v in dict(r).items()
                                      if k in ("channel_id", "name", "channel_name", "agent_id",
                                              "channel_type", "created_by")})
            m = (await s.execute(_t(
                "SELECT sender_type, sender_id, LEFT(content,900), created_at FROM chat_messages "
                "WHERE channel_id='CH-8d43324bbebf' ORDER BY created_at DESC LIMIT 4"))).fetchall()
            for x in m:
                print("MSG[%s|%s|%.19s]: %s" % (x[0], x[1], str(x[3]), str(x[2])[:900].replace("\n", " / ")))
            n = (await s.execute(_t(
                "SELECT channel_id, name FROM chat_channels WHERE name LIKE '%取证%'"))).fetchall()
            print("MARKED_CHANNELS=%s" % n)
    finally:
        await db.close_all()
asyncio.run(main())
