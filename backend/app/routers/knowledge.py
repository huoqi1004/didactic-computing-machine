"""知识库路由（个人/部门/公司三层）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
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

class KnowledgeUpdate(BaseModel):
    title: str = ""
    content: str = ""
    tags: str = ""
    is_public: bool = None


@router.get("/search")
async def search_entries(q: str = Query(..., min_length=1), scope: str = "",
                         user: dict = Depends(auth_user)):
    """全文检索（LIKE 口径，M1；语义检索后续接入）。"""
    from app.models import KnowledgeEntry

    session = get_session()
    async with session as s:
        q = select(KnowledgeEntry).where(
            KnowledgeEntry.title.contains(q) | KnowledgeEntry.content.contains(q))
        if scope:
            q = q.where(KnowledgeEntry.scope == scope)
        if user["role"] == "employee":
            q = q.where((KnowledgeEntry.owner_id == user["username"]) |
                        (KnowledgeEntry.scope == "company") |
                        (KnowledgeEntry.department == user["department"]))
        rows = (await s.execute(q)).scalars().all()
    return {"items": [{"id": r.id, "title": r.title, "scope": r.scope,
                       "tags": r.tags} for r in rows], "total": len(rows)}


@router.put("/{entry_id}")
async def update_entry(entry_id: int, req: KnowledgeUpdate,
                       user: dict = Depends(auth_user)):
    from app.models import KnowledgeEntry
    from datetime import datetime, timezone

    session = get_session()
    async with session as s:
        entry = (await s.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))).scalar_one_or_none()
        if entry is None:
            raise HTTPException(404, "条目不存在")
        if entry.owner_id != user["username"] and user["role"] not in ("dept_admin", "sys_admin"):
            raise HTTPException(403, "仅所有者或管理员可编辑")
        if req.title: entry.title = req.title
        if req.content: entry.content = req.content
        if req.tags: entry.tags = req.tags
        if req.is_public is not None: entry.is_public = req.is_public
        entry.updated_at = datetime.now(timezone.utc)
        await s.commit()
    return {"id": entry_id, "status": "updated"}


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, user: dict = Depends(auth_user)):
    from app.models import KnowledgeEntry

    session = get_session()
    async with session as s:
        entry = (await s.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))).scalar_one_or_none()
        if entry is None:
            raise HTTPException(404, "条目不存在")
        if entry.owner_id != user["username"] and user["role"] not in ("dept_admin", "sys_admin"):
            raise HTTPException(403, "仅所有者或管理员可删除")
        await s.delete(entry)
        await s.commit()
    return {"status": "deleted"}
