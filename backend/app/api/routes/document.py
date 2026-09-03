"""
文档生成 API 路由
================

提供文档生成的 HTTP API 端点，支持将业务 Agent 产生的结构化数据
渲染为标准办公文件（docx / xlsx / pptx / pdf）。

端点概览：

- ``POST /api/v1/document/generate``               生成文档（需认证）
- ``GET  /api/v1/document/download/{filename}``     下载已生成的文档（需认证）
- ``GET  /api/v1/document/templates``               获取可用模板列表（无需认证）
- ``GET  /api/v1/document/agents/{agent_id}/templates``  获取指定Agent支持的模板（需认证）

底层调用 ``app.core.document`` 模块的 ``DocumentGenerator`` 统一入口，
由其根据 ``format`` 分发到对应子生成器（docx / xlsx / pptx / pdf）。
"""
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.middleware.auth import get_current_user
from app.core.document import DocumentGenerator, document_generator

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/document", tags=["文档生成"])

# =============================================================================
# 常量定义
# =============================================================================

# 各格式对应的 MIME 类型（供 FileResponse 下载使用）
MIME_TYPES: Dict[str, str] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}

# 文档输出临时目录（与 DocumentGenerator 子生成器保持一致）
OUTPUT_DIR = tempfile.gettempdir()

# =============================================================================
# Agent 文档模板映射表
# =============================================================================

AGENT_TEMPLATES: Dict[str, List[Dict[str, str]]] = {
    "accounting": [
        {"template": "voucher", "format": "xlsx", "name": "会计凭证表"},
        {"template": "basic", "format": "pdf", "name": "财务月报"},
    ],
    "tax": [
        {"template": "tax", "format": "xlsx", "name": "税务申报表"},
        {"template": "basic", "format": "pdf", "name": "纳税申报报告"},
    ],
    "inventory": [
        {"template": "inventory", "format": "xlsx", "name": "库存盘点表"},
        {"template": "basic", "format": "xlsx", "name": "补货清单"},
    ],
    "procurement": [
        {"template": "contract", "format": "docx", "name": "采购合同"},
        {"template": "basic", "format": "pdf", "name": "采购订单"},
    ],
    "bi": [
        {"template": "business_analysis", "format": "pptx", "name": "经营分析PPT"},
        {"template": "basic", "format": "xlsx", "name": "数据报表"},
    ],
    "hr": [
        {"template": "personnel", "format": "docx", "name": "入职通知"},
        {"template": "basic", "format": "pdf", "name": "薪资单"},
    ],
    "payroll": [
        {"template": "payroll", "format": "xlsx", "name": "工资条"},
        {"template": "basic", "format": "pdf", "name": "薪酬汇总"},
    ],
    "invoice": [
        {"template": "invoice", "format": "pdf", "name": "增值税发票"},
        {"template": "basic", "format": "pdf", "name": "发票清单汇总"},
    ],
    "reimbursement": [
        {"template": "reimbursement", "format": "pdf", "name": "报销审批单"},
        {"template": "report", "format": "docx", "name": "报销单文档"},
    ],
    "audit": [
        {"template": "audit_report", "format": "pdf", "name": "审计报告"},
        {"template": "report", "format": "docx", "name": "合规报告"},
    ],
    "risk": [
        {"template": "risk_report", "format": "pdf", "name": "风险评估报告"},
        {"template": "report", "format": "docx", "name": "风控报告"},
    ],
    # ---- 以下为新增的 22 个 Agent 文档模板配置 ----
    "admin": [
        {"template": "notice", "format": "docx", "name": "系统通知"},
        {"template": "basic", "format": "pdf", "name": "系统管理报告"},
    ],
    "asset": [
        {"template": "asset", "format": "xlsx", "name": "资产清单"},
        {"template": "report", "format": "docx", "name": "资产盘点报告"},
    ],
    "budget": [
        {"template": "budget", "format": "xlsx", "name": "预算执行表"},
        {"template": "report", "format": "docx", "name": "预算分析报告"},
    ],
    "compliance": [
        {"template": "compliance_report", "format": "pdf", "name": "合规检查报告"},
        {"template": "report", "format": "docx", "name": "合规评估报告"},
    ],
    "contract": [
        {"template": "contract", "format": "docx", "name": "合同文档"},
        {"template": "basic", "format": "pdf", "name": "合同审批单"},
    ],
    "crm": [
        {"template": "customer", "format": "xlsx", "name": "客户清单"},
        {"template": "report", "format": "docx", "name": "客户分析报告"},
    ],
    "customer_service": [
        {"template": "basic", "format": "xlsx", "name": "工单统计表"},
        {"template": "report", "format": "docx", "name": "客服质量报告"},
    ],
    "demand": [
        {"template": "basic", "format": "xlsx", "name": "需求预测表"},
        {"template": "business_analysis", "format": "pptx", "name": "需求分析PPT"},
    ],
    "esg": [
        {"template": "report", "format": "docx", "name": "ESG报告"},
        {"template": "basic", "format": "pdf", "name": "ESG指标报告"},
    ],
    "knowledge": [
        {"template": "basic", "format": "docx", "name": "知识库摘要"},
        {"template": "basic", "format": "pdf", "name": "知识检索报告"},
    ],
    "legal": [
        {"template": "contract", "format": "docx", "name": "法律意见书"},
        {"template": "report", "format": "docx", "name": "法律风险评估"},
    ],
    "logistics": [
        {"template": "basic", "format": "xlsx", "name": "物流跟踪表"},
        {"template": "report", "format": "docx", "name": "物流效率报告"},
    ],
    "marketing": [
        {"template": "business_analysis", "format": "pptx", "name": "营销分析PPT"},
        {"template": "basic", "format": "xlsx", "name": "营销效果统计"},
    ],
    "performance": [
        {"template": "performance_report", "format": "pdf", "name": "绩效评估报告"},
        {"template": "employee", "format": "xlsx", "name": "绩效排名表"},
    ],
    "production": [
        {"template": "production", "format": "xlsx", "name": "生产计划表"},
        {"template": "status_report", "format": "pptx", "name": "生产汇报PPT"},
    ],
    "project": [
        {"template": "project", "format": "xlsx", "name": "项目进度表"},
        {"template": "project_report", "format": "pdf", "name": "项目总结报告"},
    ],
    "quality": [
        {"template": "quality", "format": "xlsx", "name": "质量检测表"},
        {"template": "report", "format": "docx", "name": "质量分析报告"},
    ],
    "recruitment": [
        {"template": "employee", "format": "xlsx", "name": "候选人清单"},
        {"template": "notice", "format": "docx", "name": "招聘通知"},
    ],
    "sales": [
        {"template": "customer", "format": "xlsx", "name": "销售客户表"},
        {"template": "business_analysis", "format": "pptx", "name": "销售业绩PPT"},
    ],
    "scheduler": [
        {"template": "basic", "format": "xlsx", "name": "调度排程表"},
        {"template": "status_report", "format": "pptx", "name": "调度汇报PPT"},
    ],
    "supply": [
        {"template": "supplier", "format": "xlsx", "name": "供应商清单"},
        {"template": "report", "format": "docx", "name": "供应分析报告"},
    ],
    "treasury": [
        {"template": "basic", "format": "xlsx", "name": "资金流水表"},
        {"template": "report", "format": "docx", "name": "资金分析报告"},
    ],
}


# =============================================================================
# Pydantic 请求/响应模型
# =============================================================================

class DocumentGenerateRequest(BaseModel):
    """文档生成请求"""
    agent_id: str = Field(..., description="调用Agent的ID")
    format: str = Field(..., description="输出格式: docx / xlsx / pptx / pdf")
    template_type: str = Field(..., description="模板类型: 如 contract, invoice, report 等")
    filename: str = Field(..., description="输出文件名（不含扩展名）")
    data: dict = Field(..., description="文档数据（结构化数据，传给生成器）")
    title: Optional[str] = Field(None, description="文档标题")


class DocumentGenerateResponse(BaseModel):
    """文档生成响应"""
    success: bool
    file_path: str
    filename: str
    format: str
    file_size: int = Field(..., description="文件大小（字节）")
    generated_at: str = Field(..., description="ISO格式时间戳")


# =============================================================================
# API 端点
# =============================================================================

@router.post("/generate", response_model=DocumentGenerateResponse)
async def generate_document(
    request: DocumentGenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    生成文档

    接收文档生成请求，调用 ``DocumentGenerator.generate()`` 生成对应格式的文件，
    返回文件元信息（路径、大小、时间戳等）。

    **权限要求**: 需要认证（登录用户）
    """
    logger.info(
        "document.api.generate",
        agent_id=request.agent_id,
        format=request.format,
        template_type=request.template_type,
        filename=request.filename,
        user_id=current_user.get("user_id"),
    )

    # 校验输出格式（SUPPORTED_FORMATS 为 dict[str, set[str]]，键为格式名）
    fmt = request.format.lower().strip()
    if fmt not in DocumentGenerator.SUPPORTED_FORMATS:
        supported = sorted(DocumentGenerator.SUPPORTED_FORMATS.keys())
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文档格式: {request.format}，支持: {', '.join(supported)}",
        )

    # 校验模板与Agent的匹配关系（可选校验：若Agent存在于映射表，则检查模板是否匹配）
    agent_templates = AGENT_TEMPLATES.get(request.agent_id, [])
    if agent_templates:
        valid_template = any(
            t["template"] == request.template_type for t in agent_templates
        )
        if not valid_template:
            logger.warning(
                "document.api.template_mismatch",
                agent_id=request.agent_id,
                template_type=request.template_type,
            )
            # 不阻断生成，仅记录警告（允许自定义模板类型）

    try:
        # 将 title 注入到 data 中（子生成器从 data["title"] 读取标题）
        doc_data = dict(request.data)
        if request.title and "title" not in doc_data:
            doc_data["title"] = request.title

        # 调用文档生成器（generate 返回文件绝对路径字符串）
        file_path = document_generator.generate(
            format=fmt,
            template_type=request.template_type,
            data=doc_data,
            filename=request.filename,
        )

        # 从返回路径中提取文件名
        filename = os.path.basename(file_path)
        # 计算文件大小
        file_size = os.path.getsize(file_path)
        generated_at = datetime.now(timezone.utc).isoformat()

        return DocumentGenerateResponse(
            success=True,
            file_path=file_path,
            filename=filename,
            format=fmt,
            file_size=file_size,
            generated_at=generated_at,
        )
    except ValueError as e:
        # 参数校验错误（不支持的格式/模板类型等）
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "document.api.generate_failed",
            error=str(e),
            error_type=type(e).__name__,
            agent_id=request.agent_id,
            template_type=request.template_type,
        )
        raise HTTPException(status_code=500, detail=f"文档生成失败: {type(e).__name__}: {e}")


@router.get("/download/{filename}")
async def download_document(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    """
    下载已生成的文档

    从临时输出目录读取指定文件名，以 ``FileResponse`` 返回文件流。

    **权限要求**: 需要认证（登录用户）
    """
    logger.info(
        "document.api.download",
        filename=filename,
        user_id=current_user.get("user_id"),
    )

    # 安全化文件名，防止目录穿越攻击
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    # 从临时目录查找文件（与 DocumentGenerator 子生成器输出目录一致）
    file_path = os.path.join(OUTPUT_DIR, safe_filename)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {safe_filename}")

    # 根据扩展名确定 MIME 类型
    ext = safe_filename.rsplit(".", 1)[-1].lower() if "." in safe_filename else ""
    media_type = MIME_TYPES.get(ext, "application/octet-stream")

    return FileResponse(
        path=file_path,
        filename=safe_filename,
        media_type=media_type,
    )


@router.get("/templates")
async def list_templates():
    """
    获取可用模板列表

    返回系统中所有支持的模板类型和格式组合，按 Agent 分类。
    同时返回 DocumentGenerator 底层支持的格式与模板映射。
    此端点无需认证。

    **权限要求**: 无（公开端点）
    """
    # 汇总所有 Agent 模板
    all_templates: List[Dict[str, Any]] = []
    for agent_id, templates in AGENT_TEMPLATES.items():
        for tmpl in templates:
            all_templates.append({
                "agent_id": agent_id,
                **tmpl,
            })

    # 获取 DocumentGenerator 底层支持的格式与模板
    # list_supported_formats() 返回 dict[str, list[str]]
    generator_formats = document_generator.list_supported_formats()

    return {
        "total": len(all_templates),
        "agents": list(AGENT_TEMPLATES.keys()),
        "supported_formats": sorted(DocumentGenerator.SUPPORTED_FORMATS.keys()),
        "generator_templates": generator_formats,
        "templates": all_templates,
    }


@router.get("/agents/{agent_id}/templates")
async def get_agent_templates(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    获取指定Agent支持的文档模板

    返回该Agent能生成的文档类型列表，包含模板名、格式和显示名称。

    **权限要求**: 需要认证（登录用户）
    """
    templates = AGENT_TEMPLATES.get(agent_id)

    if templates is None:
        raise HTTPException(
            status_code=404,
            detail=f"未找到Agent '{agent_id}' 的文档模板配置",
        )

    logger.info(
        "document.api.agent_templates",
        agent_id=agent_id,
        template_count=len(templates),
        user_id=current_user.get("user_id"),
    )

    return {
        "agent_id": agent_id,
        "total": len(templates),
        "templates": templates,
    }
