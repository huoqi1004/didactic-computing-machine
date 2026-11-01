"""知识库路由（M9 · 三级权限体系 + 个人条目共享申请流）。

权限矩阵（严格分层）：
- 公司知识库（scope=company）：全员可读，仅 sys_admin 可增删改
- 部门知识库（scope=department）：本部门成员可读，仅本部门 dept_admin 可增删改
- 个人知识库（scope=personal）：仅所有者本人可读可维护；
  其他人与 AI（对话能力装配）均不可感知（_can_read / 装配过滤双保险）
- 共享申请：所有者可将个人条目申请共享至部门（dept_admin 审批）或
  公司（sys_admin 审批），批准后条目 scope 变更生效
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/knowledge", tags=["知识库"])


# ============ 权限判定 ============

def _can_read(entry, user: dict) -> bool:
    if entry.scope == "company":
        return True
    if entry.scope == "department":
        return entry.department == user.get("department", "")
    return entry.owner_id == user["username"]  # personal


def _can_write(entry, user: dict) -> bool:
    if entry.scope == "company":
        return user["role"] == "sys_admin"
    if entry.scope == "department":
        return user["role"] == "dept_admin" and entry.department == user.get("department", "")
    return entry.owner_id == user["username"]  # personal


def _entry_dto(r) -> dict:
    return {"id": r.id, "title": r.title, "scope": r.scope,
            "tags": r.tags, "is_public": r.is_public, "owner": r.owner_id,
            "department": r.department, "content": r.content[:400]}


# ============ 列表与检索 ============

@router.get("")
async def list_entries(scope: str = "company", user: dict = Depends(auth_user)):
    """按范围列出（company=公司库 / department=本部门库 / personal=我的私有库）。"""
    from app.models import KnowledgeEntry

    session = get_session()
    async with session as s:
        q = select(KnowledgeEntry).order_by(KnowledgeEntry.updated_at.desc())
        if scope == "personal":
            q = q.where(KnowledgeEntry.scope == "personal",
                        KnowledgeEntry.owner_id == user["username"])
        elif scope == "department":
            q = q.where(KnowledgeEntry.scope == "department",
                        KnowledgeEntry.department == user.get("department", ""))
        else:
            q = q.where(KnowledgeEntry.scope == "company")
        rows = (await s.execute(q)).scalars().all()
    return {"items": [_entry_dto(r) for r in rows if _can_read(r, user)]}


@router.get("/search")
async def search_entries(q: str = Query(..., min_length=1), scope: str = "",
                         user: dict = Depends(auth_user)):
    """全文检索（LIKE 口径；仅返回当前用户有权可见的条目）。"""
    from app.models import KnowledgeEntry

    session = get_session()
    async with session as s:
        query = select(KnowledgeEntry).where(
            KnowledgeEntry.title.contains(q) | KnowledgeEntry.content.contains(q))
        rows = (await s.execute(query)).scalars().all()
    visible = [r for r in rows if _can_read(r, user)
               and (not scope or r.scope == scope)]
    return {"items": [{"id": r.id, "title": r.title, "scope": r.scope,
                       "tags": r.tags} for r in visible], "total": len(visible)}


# ============ 增删改（严格分层权限） ============

class KnowledgeCreate(BaseModel):
    title: str
    content: str
    scope: str = "personal"  # personal / department / company
    tags: str = ""
    is_public: bool = False


@router.post("")
async def create_entry(req: KnowledgeCreate, user: dict = Depends(auth_user)):
    if req.scope not in ("personal", "department", "company"):
        raise HTTPException(400, "非法范围")
    # 写权限前置校验
    if req.scope == "company" and user["role"] != "sys_admin":
        raise HTTPException(403, "公司知识库仅系统管理员可维护")
    if req.scope == "department" and user["role"] != "dept_admin":
        raise HTTPException(403, "部门知识库仅部门管理员可维护")
    from app.models import KnowledgeEntry

    session = get_session()
    async with session as s:
        entry = KnowledgeEntry(
            scope=req.scope, owner_id=user["username"],
            department="" if req.scope == "company" else user["department"],
            title=req.title, content=req.content, tags=req.tags,
            is_public=req.is_public)
        s.add(entry)
        await s.commit()
        return {"id": entry.id, "title": entry.title}


class KnowledgeUpdate(BaseModel):
    title: str = ""
    content: str = ""
    tags: str = ""
    is_public: bool | None = None


async def _owned_entry(s, entry_id: int, user: dict):
    from app.models import KnowledgeEntry

    entry = (await s.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))).scalar_one_or_none()
    if entry is None:
        raise HTTPException(404, "条目不存在")
    if not _can_write(entry, user):
        scope_cn = {"company": "公司知识库仅系统管理员",
                    "department": "部门知识库仅本部门管理员",
                    "personal": "个人知识库仅所有者本人"}
        raise HTTPException(403, f"{scope_cn[entry.scope]}可维护")
    return entry


@router.put("/{entry_id}")
async def update_entry(entry_id: int, req: KnowledgeUpdate,
                       user: dict = Depends(auth_user)):
    session = get_session()
    async with session as s:
        entry = await _owned_entry(s, entry_id, user)
        if req.title:
            entry.title = req.title
        if req.content:
            entry.content = req.content
        if req.tags:
            entry.tags = req.tags
        if req.is_public is not None:
            entry.is_public = req.is_public
        entry.updated_at = datetime.now(timezone.utc)
        await s.commit()
    return {"id": entry_id, "status": "updated"}


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, user: dict = Depends(auth_user)):
    session = get_session()
    async with session as s:
        entry = await _owned_entry(s, entry_id, user)
        await s.delete(entry)
        await s.commit()
    return {"status": "deleted"}


# ============ 个人条目共享申请（→ 部门 / → 公司） ============

class ShareRequestCreate(BaseModel):
    target: str  # department / company


@router.post("/{entry_id}/share-request")
async def create_share_request(entry_id: int, req: ShareRequestCreate,
                               user: dict = Depends(auth_user)):
    """所有者将个人条目申请共享：target=department→部门管理员审批；
    target=company→系统管理员审批。"""
    if req.target not in ("department", "company"):
        raise HTTPException(400, "target 仅支持 department / company")
    from app.models import KnowledgeEntry, KnowledgeShareRequest

    session = get_session()
    async with session as s:
        entry = (await s.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))).scalar_one_or_none()
        if entry is None:
            raise HTTPException(404, "条目不存在")
        if entry.owner_id != user["username"] or entry.scope != "personal":
            raise HTTPException(403, "仅可申请共享本人的个人知识条目")
        dup = (await s.execute(
            select(KnowledgeShareRequest).where(
                KnowledgeShareRequest.entry_id == entry_id,
                KnowledgeShareRequest.status == "pending"))).scalars().first()
        if dup is not None:
            raise HTTPException(409, "该条目已有待审批的共享申请")
        row = KnowledgeShareRequest(entry_id=entry_id, requester=user["username"],
                                    target_scope=req.target, status="pending",
                                    requester_department=user.get("department", ""))
        s.add(row)
        await s.commit()
        return {"id": row.id, "status": "pending",
                "message": "共享申请已提交，等待审批"}


@router.get("/share-requests")
async def list_share_requests(user: dict = Depends(auth_user)):
    """待我审批的共享申请 + 我发起的申请状态。"""
    from app.models import KnowledgeEntry, KnowledgeShareRequest

    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(KnowledgeShareRequest).order_by(
                KnowledgeShareRequest.id.desc()).limit(100))).scalars().all()
        entries = {e.id: e for e in (await s.execute(
            select(KnowledgeEntry))).scalars().all()}

        def dto(r) -> dict:
            e = entries.get(r.entry_id)
            return {"id": r.id, "entry_id": r.entry_id,
                    "entry_title": e.title if e else "(已删除)",
                    "requester": r.requester,
                    "requester_department": r.requester_department,
                    "target_scope": r.target_scope, "status": r.status,
                    "reviewer": r.reviewer}

        if user["role"] == "sys_admin":
            to_review = [r for r in rows if r.status == "pending"]
        elif user["role"] == "dept_admin":
            dept = user.get("department", "")
            to_review = [r for r in rows if r.status == "pending"
                         and r.target_scope == "department"
                         and r.requester_department == dept]
        else:
            to_review = []
        mine = [r for r in rows if r.requester == user["username"]]
        return {"to_review": [dto(r) for r in to_review],
                "mine": [dto(r) for r in mine]}


class ShareReview(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/share-requests/{request_id}/review")
async def review_share_request(request_id: int, req: ShareReview,
                               user: dict = Depends(auth_user)):
    """审批共享申请：批准后条目 scope 变更为目标范围并即刻生效。"""
    from app.models import KnowledgeEntry, KnowledgeShareRequest

    session = get_session()
    async with session as s:
        r = (await s.execute(select(KnowledgeShareRequest).where(
            KnowledgeShareRequest.id == request_id))).scalar_one_or_none()
        if r is None or r.status != "pending":
            raise HTTPException(404, "申请不存在或已处理")
        entry = (await s.execute(select(KnowledgeEntry).where(
            KnowledgeEntry.id == r.entry_id))).scalar_one_or_none()
        # 审批权限：部门目标→申请者所在部门的 dept_admin；公司目标→sys_admin
        if r.target_scope == "department":
            if not (user["role"] == "dept_admin"
                    and r.requester_department == user.get("department", "")):
                raise HTTPException(403, "仅该部门的部门管理员可审批")
        else:
            if user["role"] != "sys_admin":
                raise HTTPException(403, "仅系统管理员可审批公司级共享")
        r.status = "approved" if req.approve else "rejected"
        r.reviewer = user["username"]
        r.review_comment = req.comment[:200]
        if req.approve and entry is not None:
            entry.scope = "company" if r.target_scope == "company" else "department"
            if r.target_scope == "company":
                entry.is_public = True
                entry.department = ""
            else:
                entry.department = r.requester_department
            entry.updated_at = datetime.now(timezone.utc)
        await s.commit()
    return {"id": r.id, "status": r.status,
            "message": "已批准并生效" if req.approve else "已拒绝"}
