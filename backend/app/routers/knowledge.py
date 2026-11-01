"""知识库路由（个人/部门/公司三层）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/knowledge", tags=["知识库"])


class KnowledgeCreate(BaseModel):
    title: str
    content: str
    scope: str = "personal"  # personal / department / company
    tags: str = ""
    is_public: bool = False


@router.get("")
async def list_entries(scope: str = "", user: dict = Depends(auth_user)):
    session = get_session()
    from app.models import KnowledgeEntry

    async with session as s:
        q = select(KnowledgeEntry).order_by(KnowledgeEntry.updated_at.desc())
        if scope == "personal":
            q = q.where(KnowledgeEntry.owner_id == user["username"],
                        KnowledgeEntry.scope == "personal")
        elif scope == "department":
            q = q.where(KnowledgeEntry.department == user["department"],
                        KnowledgeEntry.scope.in_(["department", "company"]))
        else:
            q = q.where(KnowledgeEntry.scope == "company")
        rows = (await s.execute(q)).scalars().all()
    return {"items": [{"id": r.id, "title": r.title, "scope": r.scope,
                       "tags": r.tags, "is_public": r.is_public,
                       "owner": r.owner_id} for r in rows]}


@router.post("")
async def create_entry(req: KnowledgeCreate, user: dict = Depends(auth_user)):
    from app.models import KnowledgeEntry

    session = get_session()
    async with session as s:
        entry = KnowledgeEntry(
            scope=req.scope, owner_id=user["username"],
            department=user["department"], title=req.title,
            content=req.content, tags=req.tags, is_public=req.is_public)
        s.add(entry)
        await s.commit()
        return {"id": entry.id, "title": entry.title}
