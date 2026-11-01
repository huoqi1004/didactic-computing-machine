"""实时资料库路由（定稿防篡改 + 版本链 + 岗位权限）。"""
from __future__ import annotations

import hashlib
import json

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/records", tags=["实时资料库"])

DOC_TYPES = {"invoice", "expense", "tax", "contract", "asset", "inventory", "payslip"}


class RecordCreate(BaseModel):
    doc_type: str
    title: str
    payload: dict = {}


@router.get("")
async def list_records(user: dict = Depends(auth_user)):
    """资料库列表（本人创建的 + 本部门的，供对话能力选择/资料页使用）。"""
    from app.models import BusinessDocument

    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(BusinessDocument).where(
                (BusinessDocument.owner == user["username"])
                | (BusinessDocument.department == user["department"]))
            .order_by(BusinessDocument.id.desc()).limit(100))).scalars().all()
    return {"items": [{"id": r.id, "doc_type": r.doc_type, "title": r.title,
                       "status": r.status, "department": r.department}
                      for r in rows]}


@router.post("")
async def create_record(req: RecordCreate, user: dict = Depends(auth_user)):
    """创建草稿（可编辑）。"""
    if req.doc_type not in DOC_TYPES:
        raise HTTPException(400, f"不支持的资料类型: {req.doc_type}")
    from app.models import BusinessDocument
    from datetime import datetime, timezone

    session = get_session()
    async with session as s:
        doc = BusinessDocument(
            doc_type=req.doc_type, title=req.title,
            owner_id=user["username"], department=user["department"],
            payload=json.dumps(req.payload, ensure_ascii=False))
        s.add(doc)
        await s.commit()
        return {"id": doc.id, "status": doc.status}


@router.post("/{doc_id}/finalize")
async def finalize_record(doc_id: int, user: dict = Depends(auth_user)):
    """定稿：落时间戳+哈希+水印，不可直接删改。"""
    from sqlalchemy import select

    from app.models import BusinessDocument
    from datetime import datetime, timezone

    session = get_session()
    async with session as s:
        doc = (await s.execute(
            select(BusinessDocument).where(BusinessDocument.id == doc_id))).scalar_one_or_none()
        if doc is None:
            raise HTTPException(404, "文档不存在")
        if doc.status == "final":
            raise HTTPException(409, "已定稿，如需修改请走审批流程")
        if doc.owner_id != user["username"] and user["role"] != "sys_admin":
            raise HTTPException(403, "仅文档所有者可定稿")
        payload_bytes = doc.payload.encode()
        content_hash = hashlib.sha256(payload_bytes).hexdigest()
        now = datetime.now(timezone.utc)
        watermark = f"责任人:{doc.owner_id} | 定稿时间:{now.isoformat()[:19]} | 文档ID:{doc.id}"
        doc.status = "final"
        doc.content_hash = content_hash
        doc.watermark = watermark
        doc.finalized_at = now
        await s.commit()
        return {"id": doc.id, "status": "final", "content_hash": content_hash,
                "watermark": watermark}


@router.post("/{doc_id}/amend")
async def amend_record(doc_id: int, req: RecordCreate,
                       user: dict = Depends(auth_user)):
    """修改已定稿文档（须审批通过后出新版本，旧版本永久留存）。"""
    from sqlalchemy import select

    from app.models import BusinessDocument, DocumentVersion
    from datetime import datetime, timezone

    session = get_session()
    async with session as s:
        doc = (await s.execute(
            select(BusinessDocument).where(BusinessDocument.id == doc_id))).scalar_one_or_none()
        if doc is None:
            raise HTTPException(404, "文档不存在")
        if doc.status != "final":
            raise HTTPException(409, "非定稿状态无需走修改审批")
        if user["role"] not in ("dept_admin", "sys_admin"):
            raise HTTPException(403, "修改已定稿文档需部门管理员或系统管理员审批")
        # 留旧版本
        s.add(DocumentVersion(
            document_id=doc.id, version=doc.version, payload=doc.payload,
            changed_by=doc.owner_id, change_reason="修改前版本",
            approved_by=user["username"]))
        #出新版本
        doc.version += 1
        doc.payload = json.dumps(req.payload, ensure_ascii=False)
        doc.content_hash = hashlib.sha256(doc.payload.encode()).hexdigest()
        await s.commit()
        return {"id": doc.id, "version": doc.version,
                "message": "修改已生效，旧版本永久留存"}
