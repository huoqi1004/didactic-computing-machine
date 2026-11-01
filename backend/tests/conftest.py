import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    import app.core.database as database
    db_file = tmp_path / "m0_test.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    from app.main import app
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.core.database import get_session
    from app.seed import seed
    session = get_session()
    async with session as s:
        await seed(s)
        await s.commit()
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await database.close_db()
