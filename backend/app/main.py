"""ERP 数字化大脑 · 新架构入口（Octop 式模块化，全面重构 M0）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core import database
    from app.models import Base

    engine = await database.init_db()
    if engine is not None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app.state.session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        from app.seed import seed
        from app.core.database import get_session
        session = get_session()
        if session is not None:
            async with session as s:
                result = await seed(s)
                await s.commit()
            logger.info("app.schema_ready", seed=result)
    yield
    from app.core import llm
    await llm.close()
    logger.info("app.stopped")


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# 路由
from app.routers import (admin, auth, conversation, dashboard, health,  # noqa: E402
                         im, knowledge, market, records, todos)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(conversation.router)
app.include_router(knowledge.router)
app.include_router(records.router)
app.include_router(im.router)
app.include_router(todos.router)
app.include_router(market.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
