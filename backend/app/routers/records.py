"""实时资料库路由（定稿防篡改 + 版本链 + 岗位权限）。"""
from __future__ import annotations

import hashlib
import json

from fastapi import Depends, APIRouter, File, HTTPException, UploadFile
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


@router.post("/upload-invoice")
async def upload_invoice(file: UploadFile = File(...),
                         user: dict = Depends(auth_user)):
    """PDF 发票上传识别（M13）：文本层提取 → LLM 结构化 → 自动创建发票资料。"""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 发票")
    from app.core import invoice_ocr
    from app.core.llm_gateway import get_default_provider, provider_chat
    from datetime import datetime, timezone

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "文件不能超过 10MB")
    text = invoice_ocr.extract_pdf_text(data)
    if not text.strip():
        raise HTTPException(422, "该 PDF 无文本层（疑似扫描件），"
                                 "请使用数电票/电子发票 PDF，或等待 OCR 服务接入")

    session = get_session()
    async with session as s:
        provider = await get_default_provider(s)
        if provider is None:
            raise HTTPException(503, "系统未接入可用模型，无法结构化识别")
        fields = await invoice_ocr.structure_invoice(
            text,
            lambda messages, system: provider_chat(
                s, provider, messages, system=system,
                username=user["username"]))
        if fields is None:
            raise HTTPException(422, "未能从 PDF 中识别出发票要素，请确认文件为发票")
        from app.models import BusinessDocument

        title = f"发票识别·{fields.get('销售方名称') or fields.get('购买方名称') or file.filename}"
        doc = BusinessDocument(
            doc_type="invoice", title=title[:120],
            owner_id=user["username"], department=user.get("department", ""),
            payload=json.dumps({"识别要素": fields, "来源文件": file.filename,
                                "识别方式": "PDF文本层+LLM结构化"}, ensure_ascii=False),
            status="draft")
        s.add(doc)
        from app.core.audit_chain import append as _audit
        await _audit(s, actor=user["username"],
                                 action="records.invoice_ocr",
                                 resource=f"record:{doc.title[:30]}",
                                 result="ok",
                                 details={"fields": fields})
        await s.commit()
        return {"id": doc.id, "title": doc.title, "fields": fields,
                "message": "识别成功并已创建发票资料（草稿）"}


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
