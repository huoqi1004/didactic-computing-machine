"""
文档生成器统一入口 (Unified Document Generator Entry)
=====================================================

提供 ``DocumentGenerator`` 主类，作为各业务 Agent 生成标准办公文件的
统一入口。根据 ``format`` 参数分发到对应的子生成器：

- docx → DocxGenerator   （python-docx，Word 文档）
- xlsx → XlsxGenerator   （openpyxl，Excel 表格）
- pptx → PptxGenerator   （python-pptx，PowerPoint 演示文稿）
- pdf  → PdfGenerator    （reportlab，PDF 文档）

使用方式::

    from app.core.document import DocumentGenerator

    generator = DocumentGenerator()
    path = generator.generate(
        format="docx",
        template_type="contract",
        data={...},
        filename="采购合同",
    )

所有子生成器均接收 ``data: dict``（结构化数据）与 ``filename: str``
（输出文件名），生成的文件保存到系统临时目录并返回文件绝对路径。

表格数据统一格式：
    {"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}
"""
from __future__ import annotations

import structlog

from app.core.document.docx_generator import DocxGenerator, docx_generator
from app.core.document.pdf_generator import PdfGenerator, pdf_generator
from app.core.document.pptx_generator import PptxGenerator, pptx_generator
from app.core.document.xlsx_generator import XlsxGenerator, xlsx_generator

logger = structlog.get_logger()


class DocumentGenerator:
    """
    文档生成器统一入口

    通过 ``generate(format, template_type, data, filename)`` 方法，
    根据 ``format`` 分发到对应的子生成器。

    支持的格式与模板：

    +--------+----------------------+--------------------------------------------------+
    | format | template_type        | 说明                                             |
    +========+======================+==================================================+
    | docx   | basic                | 基础文档（标题 + 段落 + 表格）                   |
    | docx   | contract             | 采购合同                                         |
    | docx   | report               | 审计 / 风控报告                                  |
    | docx   | personnel            | 入职通知 / 薪资单                                |
    | docx   | proposal             | 方案文档（背景/目标/方案/预算/时间表）           |
    | docx   | meeting              | 会议纪要（议题/决议/待办事项）                   |
    | docx   | notice               | 通知公告（标题/接收人/正文/落款）                |
    +--------+----------------------+--------------------------------------------------+
    | xlsx   | basic                | 基础表格（数据行 + 表头）                        |
    | xlsx   | voucher              | 会计凭证表                                       |
    | xlsx   | inventory            | 库存盘点表                                       |
    | xlsx   | payroll              | 工资条表                                         |
    | xlsx   | tax                  | 税务申报表                                       |
    | xlsx   | budget               | 预算执行表（预算/实际/差异）                     |
    | xlsx   | project              | 项目进度表（任务/负责人/进度/状态）              |
    | xlsx   | asset                | 资产清单表（编号/名称/原值/折旧/净值）           |
    | xlsx   | customer             | 客户清单表（编号/名称/联系人/等级）              |
    | xlsx   | employee             | 员工花名册（工号/姓名/部门/职位/状态）           |
    | xlsx   | supplier             | 供应商清单（编号/名称/联系人/评级）              |
    | xlsx   | quality              | 质量检测表（批次/检测项/标准值/实测值/结果）     |
    | xlsx   | production           | 生产计划表（产品/计划量/实际量/完成率/状态）     |
    +--------+----------------------+--------------------------------------------------+
    | pptx   | basic                | 基础演示文稿（标题页 + 内容页）                  |
    | pptx   | business_analysis    | 经营分析 PPT（图表 + 数据表格）                  |
    | pptx   | status_report        | 状态汇报 PPT（概览/关键指标/风险/计划）          |
    +--------+----------------------+--------------------------------------------------+
    | pdf    | basic                | 基础 PDF（标题 + 段落 + 表格）                   |
    | pdf    | invoice              | 增值税发票                                       |
    | pdf    | reimbursement        | 报销单                                           |
    | pdf    | audit_report         | 审计报告                                         |
    | pdf    | risk_report          | 风控报告                                         |
    | pdf    | compliance_report    | 合规报告                                         |
    | pdf    | performance_report   | 绩效报告                                         |
    | pdf    | project_report       | 项目报告                                         |
    +--------+----------------------+--------------------------------------------------+
    """

    # 支持的文档格式 → 子生成器实例
    _GENERATORS = {
        "docx": docx_generator,
        "xlsx": xlsx_generator,
        "pptx": pptx_generator,
        "pdf": pdf_generator,
    }

    # 各格式支持的模板类型（用于校验与文档查询）
    SUPPORTED_FORMATS: dict[str, set[str]] = {
        "docx": set(DocxGenerator.SUPPORTED_TEMPLATES.keys()),
        "xlsx": set(XlsxGenerator.SUPPORTED_TEMPLATES.keys()),
        "pptx": set(PptxGenerator.SUPPORTED_TEMPLATES.keys()),
        "pdf": set(PdfGenerator.SUPPORTED_TEMPLATES.keys()),
    }

    def generate(
        self,
        format: str,
        template_type: str,
        data: dict,
        filename: str,
    ) -> str:
        """
        生成标准办公文档。

        Args:
            format:         文档格式（docx / xlsx / pptx / pdf）
            template_type:  模板类型（见类文档表格）
            data:           文档所需结构化数据
            filename:       输出文件名（不带路径，扩展名可省略）

        Returns:
            生成的文件绝对路径

        Raises:
            ValueError: 文档格式不支持，或子生成器生成失败
        """
        fmt = (format or "").lower().strip()
        generator = self._GENERATORS.get(fmt)
        if generator is None:
            supported = list(self._GENERATORS.keys())
            raise ValueError(
                f"不支持的文档格式: {format!r}，支持: {supported}"
            )

        logger.info(
            "document.generate_request",
            format=fmt,
            template_type=template_type,
            filename=filename,
        )

        try:
            path = generator.generate(
                template_type=template_type,
                data=data or {},
                filename=filename,
            )
            logger.info(
                "document.generated",
                format=fmt,
                template_type=template_type,
                filename=filename,
                path=path,
            )
            return path
        except ValueError:
            raise
        except Exception as e:
            logger.error(
                "document.generate_failed",
                format=fmt,
                template_type=template_type,
                filename=filename,
                error=str(e),
            )
            raise ValueError(
                f"文档生成失败（format={fmt}, template={template_type}）: {e}"
            ) from e

    def list_supported_formats(self) -> dict[str, list[str]]:
        """返回各文档格式支持的模板类型列表，便于 Agent 自省调用。"""
        return {
            fmt: sorted(templates)
            for fmt, templates in self.SUPPORTED_FORMATS.items()
        }


# 全局单例
document_generator = DocumentGenerator()
