"""待办路由（首页对话框下方卡片）。"""
from __future__ import annotations

from fastapi import Depends, APIRouter
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/todos", tags=["待办"])


@router.get("")
async def list_todos(user: dict = Depends(auth_user)):
    from app.models import Todo

    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(Todo).where(Todo.user_id == user["username"],
                               Todo.status == "open")
            .order_by(Todo.created_at.desc()))).scalars().all()
    return {"items": [{"id": t.id, "source": t.source, "title": t.title,
                       "link": t.link} for t in rows]}
