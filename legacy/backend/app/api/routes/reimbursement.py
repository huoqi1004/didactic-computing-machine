"""
报销发票上传识别路由（财务模块）
================================

端点列表：
    POST /api/v1/reimbursement/invoices/upload           上传发票 PDF，解析票面要素并返回报销表单预填建议
    GET  /api/v1/reimbursement/invoices                  查询发票识别件列表（本人；财务可查全部）
    GET  /api/v1/reimbursement/invoices/{invoice_id}     发票识别件详情（含完整解析结果）
    POST /api/v1/reimbursement/invoices/{invoice_id}/link  手工关联报销单（提交报销后自动关联，此为补偿接口）

识别流程：
    上传 PDF → 校验（扩展名/魔数/大小）→ 线程池解析文本层（不阻塞事件循环）
    → 提取票面要素（发票种类/抬头/抬头类型/税号/开票日期/号码/价税合计/销售方/备注）
    → 落库 reimbursement_invoices → 返回 parsed + prefill 供前端预填表单。

权限：上传/关联需 ``reimbursement:create``（dept_staff 及以上均具备）；
查询需登录（本人数据）。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.middleware.auth import get_current_user, require_permission
from app.core.config import settings
from app.core.invoice_parser import build_prefill, extract_pdf_text, parse_invoice_text
from app.core.rbac import rbac_engine
from app.core.security.audit import audit_chain
from app.db.repositories.base import has_db
from app.db.repositories.reimbursement_repository import reimbursement_invoice_repository

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/reimbursement", tags=["报销发票识别"])

_PDF_MAGIC = b"%PDF-"


# ===================================================================
# 请求/响应模型
# ===================================================================

class LinkInvoiceRequest(BaseModel):
    """发票关联报销单请求"""
    reimbursement_id: str = Field(..., min_length=1, max_length=64, description="报销单ID")


# ===================================================================
# 内部助手
# ===================================================================

def _validate_upload(file: UploadFile, content: bytes) -> None:
    """上传校验：扩展名 + PDF 魔数 + 大小上限。失败抛 400/413。"""
    filename = file.filename or "invoice.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 格式发票文件")
    max_bytes = settings.invoice_max_upload_mb * 1024 * 1024
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小上限 {settings.invoice_max_upload_mb}MB",
        )
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400, detail="文件内容不是有效的 PDF")


def _save_pdf_file(content: bytes, invoice_id: str) -> str:
    """保存 PDF 到配置目录（文件名由服务端生成，无路径注入面）。"""
    upload_dir = Path(settings.invoice_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{invoice_id}.pdf"
    file_path.write_bytes(content)
    return str(file_path)


def _can_view_all(user: dict) -> bool:
    """财务/管理角色可查看全部发票识别件（复用 crm:manage 之外的资金管理权限）。"""
    permissions = rbac_engine.get_user_permissions(user["user_id"])
    return "reimbursement:manage" in permissions or "treasury:read" in permissions


# ===================================================================
# 路由
# ===================================================================

@router.post("/invoices/upload", summary="上传发票 PDF 并识别（返回报销表单预填）")
async def upload_invoice(
    file: UploadFile = File(..., description="发票 PDF 文件（数电票/电子普票/电子专票等带文本层的 PDF）"),
    current_user: dict = Depends(require_permission("reimbursement", "create")),
):
    """
    上传发票 PDF，识别票面固定要素并生成报销表单预填建议。

    返回：
    - ``invoice_id``：发票识别件 ID（提交报销时作为 invoice_id 传入自动关联）；
    - ``parsed``：票面要素（发票种类/抬头/抬头类型/税号/开票日期/号码/价税合计/销售方/备注）；
    - ``prefill``：表单预填建议（category/amount/description/has_receipt 等）；
    - ``reliable=false`` 时提示用户人工填写（扫描件无文本层或版式未识别）。
    """
    content = await file.read()
    _validate_upload(file, content)

    # 解析与文件落盘均为同步 IO/CPU 操作，放入线程池避免阻塞事件循环
    raw_text = await run_in_threadpool(extract_pdf_text, content)
    parsed = await run_in_threadpool(parse_invoice_text, raw_text)

    invoice_id = f"RINV-{uuid4().hex[:12]}"
    file_path = None
    try:
        file_path = await run_in_threadpool(_save_pdf_file, content, invoice_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("invoice_upload.save_file_failed", error=str(e))

    prefill = build_prefill(parsed, invoice_id=invoice_id)

    # 落库（DB 不可用时跳过持久化，仅返回解析结果；节流告警由 repository 输出）
    if has_db():
        await reimbursement_invoice_repository.upsert(
            {
                "invoice_id": invoice_id,
                "file_name": file.filename or "invoice.pdf",
                "file_size": len(content),
                "file_path": file_path,
                "uploaded_by": current_user.get("user_id"),
                "uploaded_by_name": current_user.get("username", ""),
                "raw_text": raw_text[:20000],  # 防超长文本撑爆行
                "parsed_json": parsed,
                "status": "parsed",
                **{k: parsed.get(k) for k in (
                    "invoice_type", "invoice_title", "buyer_type", "taxpayer_id",
                    "invoice_date", "invoice_number", "invoice_code",
                    "total_amount", "tax_amount", "amount_without_tax",
                    "seller_name", "remark", "confidence",
                )},
            },
            key_field="invoice_id",
        )
        try:
            await audit_chain.log(
                actor_did=current_user.get("user_id", "unknown"),
                action="reimbursement.invoice.uploaded",
                resource=f"reimbursement_invoice:{invoice_id}",
                result="success",
                details={
                    "file_name": file.filename,
                    "file_size": len(content),
                    "invoice_type": parsed.get("invoice_type"),
                    "invoice_number": parsed.get("invoice_number"),
                    "total_amount": parsed.get("total_amount"),
                    "reliable": parsed.get("reliable"),
                },
            )
        except Exception as audit_err:  # noqa: BLE001
            logger.warning("invoice_upload.audit_failed", error=str(audit_err))

    logger.info(
        "invoice_upload.parsed",
        invoice_id=invoice_id,
        uploaded_by=current_user.get("user_id"),
        invoice_type=parsed.get("invoice_type"),
        confidence=parsed.get("confidence"),
        reliable=parsed.get("reliable"),
    )

    message = "识别完成，请核对预填内容后提交"
    if not parsed.get("reliable"):
        message = "识别置信度较低（可能是扫描件或版式未识别），请核对并人工补全"

    return {
        "invoice_id": invoice_id,
        "file_name": file.filename,
        "parsed": parsed,
        "prefill": prefill,
        "message": message,
    }


@router.get("/invoices", summary="查询发票识别件列表")
async def list_invoices(
    skip: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(50, ge=1, le=200, description="每页数量上限"),
    current_user: dict = Depends(get_current_user),
):
    """查询发票识别件。普通用户仅见本人上传；具备管理权限者可见全部。"""
    view_all = _can_view_all(current_user)
    uploaded_by = None if view_all else current_user["user_id"]
    invoices = await reimbursement_invoice_repository.list_mine(
        uploaded_by=uploaded_by, limit=skip + limit
    )
    total = len(invoices)
    return {"total": total, "invoices": invoices[skip:skip + limit]}


@router.get("/invoices/{invoice_id}", summary="发票识别件详情")
async def get_invoice(
    invoice_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取发票识别件详情（含完整解析结果与原始文本）。"""
    record = await reimbursement_invoice_repository.get_full_by_id(invoice_id)
    if record is None:
        raise HTTPException(status_code=404, detail="发票识别件不存在")
    if (
        record.get("uploaded_by") != current_user["user_id"]
        and not _can_view_all(current_user)
    ):
        raise HTTPException(status_code=403, detail="无权查看他人上传的发票")
    return record


@router.post("/invoices/{invoice_id}/link", summary="手工关联报销单")
async def link_invoice(
    invoice_id: str,
    req: LinkInvoiceRequest,
    current_user: dict = Depends(require_permission("reimbursement", "create")),
):
    """将发票识别件关联到报销单（正常流程在提交报销时自动关联，此为补偿接口）。"""
    record = await reimbursement_invoice_repository.get_by_id(invoice_id)
    if record is None:
        raise HTTPException(status_code=404, detail="发票识别件不存在")
    if record.get("status") == "linked":
        return {
            "message": "发票已关联报销单",
            "reimbursement_id": record.get("reimbursement_id"),
            "already_linked": True,
        }
    linked = await reimbursement_invoice_repository.mark_linked(
        invoice_id, req.reimbursement_id
    )
    if not linked:
        raise HTTPException(status_code=400, detail="关联失败（发票可能已关联或存储不可用）")
    logger.info(
        "invoice_link.manual",
        invoice_id=invoice_id,
        reimbursement_id=req.reimbursement_id,
        operator=current_user.get("user_id"),
    )
    return {"message": "关联成功", "invoice_id": invoice_id,
            "reimbursement_id": req.reimbursement_id}
