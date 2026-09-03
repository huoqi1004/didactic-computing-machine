"""
文档生成模块 (Document Generation Module)
=========================================

为各业务 Agent 提供标准办公文件（docx / xlsx / pptx / pdf）的统一生成能力。

模块组成：
1. generator.py      - DocumentGenerator 主类，统一入口，按 format 分发
2. docx_generator.py - Word 文档生成器（python-docx）
3. xlsx_generator.py - Excel 表格生成器（openpyxl）
4. pptx_generator.py - PowerPoint 演示文稿生成器（python-pptx）
5. pdf_generator.py  - PDF 文档生成器（reportlab）

设计原则
--------
- 统一入口：``DocumentGenerator.generate(format, template_type, data, filename)``
- 数据驱动：每个生成器接收 ``data: dict`` 结构化数据
- 表格统一格式：``{"headers": [...], "rows": [[...]]}``
- 失败安全：生成逻辑包裹在 try-except 中，失败抛出带描述的 ValueError
- 临时输出：文件保存到 ``tempfile.gettempdir()``，返回绝对路径

使用方式::

    from app.core.document import DocumentGenerator, document_generator

    # 使用全局单例
    path = document_generator.generate(
        format="docx",
        template_type="contract",
        data={
            "contract_no": "CG-2026-001",
            "party_a": {"name": "甲方公司"},
            "party_b": {"name": "乙方公司"},
            "items": {"headers": ["序号", "名称", "金额"], "rows": [["1", "物资A", "10000"]]},
            "total_amount_number": 10000,
            "total_amount": "壹万元整",
            "terms": ["按期交货", "验收合格后付款"],
        },
        filename="采购合同",
    )
"""
from app.core.document.docx_generator import DocxGenerator, docx_generator
from app.core.document.generator import (
    DocumentGenerator,
    document_generator,
)
from app.core.document.pdf_generator import PdfGenerator, pdf_generator
from app.core.document.pptx_generator import PptxGenerator, pptx_generator
from app.core.document.xlsx_generator import XlsxGenerator, xlsx_generator

__all__ = [
    # 主入口
    "DocumentGenerator",
    "document_generator",
    # 子生成器类
    "DocxGenerator",
    "XlsxGenerator",
    "PptxGenerator",
    "PdfGenerator",
    # 子生成器单例
    "docx_generator",
    "xlsx_generator",
    "pptx_generator",
    "pdf_generator",
]
