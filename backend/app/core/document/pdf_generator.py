"""
PDF 文档生成器 (PDF Document Generator)
=========================================

基于 reportlab 生成 .pdf 标准办公文档，供各业务 Agent 调用。

支持的模板类型（template_type）：
1. basic              - 基础 PDF（标题 + 段落 + 表格）
2. invoice            - 发票 PDF（增值税发票格式）
3. reimbursement      - 报销单 PDF
4. audit_report       - 审计报告 PDF
5. risk_report        - 风控报告 PDF
6. compliance_report  - 合规报告 PDF
7. performance_report - 绩效报告 PDF
8. project_report     - 项目报告 PDF

表格数据统一格式：
    {"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}

使用方式::

    from app.core.document.pdf_generator import pdf_generator

    path = pdf_generator.generate(
        template_type="basic",
        data={"title": "示例", "paragraphs": ["段落1"], "table": {...}},
        filename="example.pdf",
    )
"""
from __future__ import annotations

from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Paragraph,
)

from app.core.document.output import resolve_output_path

logger = structlog.get_logger()

# 注册中文字体（Adobe 亚洲字体包，无需外部字体文件）
_CN_FONT = "STSong-Light"
_CN_FONT_REGISTERED = False


def _ensure_cn_font() -> None:
    """确保中文字体已注册（仅注册一次）"""
    global _CN_FONT_REGISTERED
    if not _CN_FONT_REGISTERED:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(_CN_FONT))
        except Exception:
            # 注册失败时回退到内置字体名（仍可能无法显示中文，但保证不抛错）
            pass
        _CN_FONT_REGISTERED = True


# 颜色常量
_COLOR_PRIMARY = colors.HexColor("#1F4E78")
_COLOR_HEADER_BG = colors.HexColor("#4472C4")
_COLOR_SUBTOTAL_BG = colors.HexColor("#D9E2F3")
_COLOR_BORDER = colors.HexColor("#B0B0B0")
_COLOR_LIGHT = colors.HexColor("#F2F2F2")


class PdfGenerator:
    """
    PDF 文档生成器

    通过 ``generate(template_type, data, filename)`` 统一入口，
    根据模板类型分发到对应的私有生成方法。
    """

    SUPPORTED_TEMPLATES = {
        "basic": "_generate_basic",
        "invoice": "_generate_invoice",
        "reimbursement": "_generate_reimbursement",
        "audit_report": "_generate_audit_report",
        "risk_report": "_generate_risk_report",
        "compliance_report": "_generate_compliance_report",
        "performance_report": "_generate_performance_report",
        "project_report": "_generate_project_report",
    }

    def generate(self, template_type: str, data: dict, filename: str) -> str:
        """
        生成 PDF 文档。

        Args:
            template_type: 模板类型（basic/invoice/reimbursement/audit_report/
                            risk_report/compliance_report/performance_report/
                            project_report）
            data:           文档所需结构化数据
            filename:       输出文件名（不带路径）

        Returns:
            生成的文件绝对路径

        Raises:
            ValueError: 模板类型不支持或生成失败
        """
        method_name = self.SUPPORTED_TEMPLATES.get(template_type)
        if method_name is None:
            raise ValueError(
                f"不支持的 PDF 模板类型: {template_type}，"
                f"支持: {list(self.SUPPORTED_TEMPLATES.keys())}"
            )

        if not filename.endswith(".pdf"):
            filename = f"{filename}.pdf"
        output_path = resolve_output_path(filename)

        try:
            _ensure_cn_font()
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                leftMargin=2 * cm,
                rightMargin=2 * cm,
                topMargin=2 * cm,
                bottomMargin=2 * cm,
                title=data.get("title", filename),
            )
            styles = self._build_styles()

            method = getattr(self, method_name)
            story = method(data or {}, styles)

            doc.build(story)
            logger.info(
                "pdf.generated",
                template_type=template_type,
                filename=filename,
                path=output_path,
            )
            return output_path
        except ValueError:
            raise
        except Exception as e:
            logger.error(
                "pdf.generate_failed",
                template_type=template_type,
                filename=filename,
                error=str(e),
            )
            raise ValueError(f"PDF 文档生成失败（{template_type}）: {e}") from e

    # ===================================================================
    # 模板：基础 PDF
    # ===================================================================

    def _generate_basic(self, data: dict, styles: dict) -> list:
        """
        基础 PDF：标题 + 段落 + 表格

        data 字段：
        - title:      文档标题
        - paragraphs: 段落文本列表
        - table:      表格数据 {"headers": [...], "rows": [[...]]}
        """
        story: list = []

        title = data.get("title", "")
        if title:
            story.append(Paragraph(str(title), styles["title"]))
            story.append(Spacer(1, 12))

        for para in data.get("paragraphs", []):
            story.append(Paragraph(str(para), styles["body"]))
            story.append(Spacer(1, 6))

        table_data = data.get("table")
        if table_data:
            story.append(Spacer(1, 6))
            story.append(self._build_table(table_data, styles))

        return story

    # ===================================================================
    # 模板：增值税发票
    # ===================================================================

    def _generate_invoice(self, data: dict, styles: dict) -> list:
        """
        增值税发票 PDF

        data 字段：
        - invoice_no:       发票号码
        - invoice_code:     发票代码
        - date:             开票日期
        - machine_no:       机器编号
        - seller:           销售方信息 dict（name/tax_id/address_bank）
        - buyer:            购买方信息 dict（name/tax_id/address_bank）
        - items:            货物明细 {"headers": [...], "rows": [...]}
        - total_amount:     合计金额
        - total_tax:        合计税额
        - total:            价税合计（数字）
        - total_in_words:   价税合计（大写）
        - payee / reviewer / drawer: 收款人 / 复核 / 开票人
        """
        story: list = []
        story.append(Paragraph("增值税专用发票", styles["invoice_title"]))
        story.append(Spacer(1, 10))

        # 发票头部信息
        head_rows = [
            ["发票代码：", data.get("invoice_code", ""), "发票号码：", data.get("invoice_no", "")],
            ["开票日期：", data.get("date", ""), "机器编号：", data.get("machine_no", "")],
        ]
        head_table = Table(head_rows, colWidths=[2.5 * cm, 5 * cm, 2.5 * cm, 5 * cm])
        head_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
            ("TEXTCOLOR", (2, 0), (2, -1), colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(head_table)
        story.append(Spacer(1, 10))

        # 购销双方信息
        buyer = data.get("buyer", {}) or {}
        seller = data.get("seller", {}) or {}
        party_rows = [
            ["购买方信息：", self._format_party_text(buyer)],
            ["销售方信息：", self._format_party_text(seller)],
        ]
        party_table = Table(party_rows, colWidths=[3 * cm, 12 * cm])
        party_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (0, -1), _COLOR_LIGHT),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, _COLOR_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        # 将文本转为 Paragraph 以支持换行
        party_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
        ]))
        party_rows_para = [
            ["购买方信息：", Paragraph(self._format_party_text(buyer).replace("\n", "<br/>"), styles["cell_small"])],
            ["销售方信息：", Paragraph(self._format_party_text(seller).replace("\n", "<br/>"), styles["cell_small"])],
        ]
        party_table = Table(party_rows_para, colWidths=[3 * cm, 12 * cm])
        party_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (0, -1), 9),
            ("BACKGROUND", (0, 0), (0, -1), _COLOR_LIGHT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, _COLOR_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(party_table)
        story.append(Spacer(1, 8))

        # 货物明细
        items = data.get("items", {}) or {}
        headers = items.get("headers", [
            "货物名称", "规格型号", "单位", "数量", "单价", "金额", "税率",
        ])
        rows = items.get("rows", [])
        story.append(self._build_table({"headers": headers, "rows": rows}, styles))
        story.append(Spacer(1, 8))

        # 金额合计
        total_amount = data.get("total_amount", "")
        total_tax = data.get("total_tax", "")
        total = data.get("total", "")
        total_in_words = data.get("total_in_words", "")
        sum_rows = [
            ["合计", "", "", "", "", str(total_amount), str(total_tax)],
        ]
        sum_table = Table(sum_rows, colWidths=[2 * cm, 1.8 * cm, 1.2 * cm, 1.5 * cm, 2 * cm, 2.5 * cm, 2.5 * cm])
        sum_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, -1), _COLOR_SUBTOTAL_BG),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, _COLOR_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(sum_table)
        story.append(Spacer(1, 8))

        # 价税合计
        if total or total_in_words:
            total_text = f"价税合计（大写）：{total_in_words}　　（小写）：¥{total}"
            story.append(Paragraph(total_text, styles["body_bold"]))
            story.append(Spacer(1, 12))

        # 签字栏
        sign_rows = [[
            f"收款人：{data.get('payee', '')}",
            f"复核：{data.get('reviewer', '')}",
            f"开票人：{data.get('drawer', '')}",
        ]]
        sign_table = Table(sign_rows, colWidths=[5 * cm, 5 * cm, 5 * cm])
        sign_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(sign_table)

        return story

    # ===================================================================
    # 模板：报销单
    # ===================================================================

    def _generate_reimbursement(self, data: dict, styles: dict) -> list:
        """
        报销单 PDF

        data 字段：
        - reimbursement_no: 报销单号
        - applicant:        报销人
        - department:       部门
        - date:             报销日期
        - reason:           报销事由
        - items:            费用明细 {"headers": [...], "rows": [[日期, 摘要, 科目, 金额, 附件]]}
        - total_amount:     报销金额合计
        - amount_in_words:  合计大写
        - approver / reviewer / applicant_sign: 审批 / 审核 / 报销人签字
        """
        story: list = []
        story.append(Paragraph("费用报销单", styles["title"]))
        story.append(Spacer(1, 10))

        # 报销人信息
        info_rows = [
            [
                f"报销单号：{data.get('reimbursement_no', '')}",
                f"报销人：{data.get('applicant', '')}",
                f"部门：{data.get('department', '')}",
                f"日期：{data.get('date', '')}",
            ]
        ]
        info_table = Table(info_rows, colWidths=[4 * cm, 3.5 * cm, 3.5 * cm, 4 * cm])
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(info_table)

        # 报销事由
        reason = data.get("reason", "")
        if reason:
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>报销事由：</b>{reason}", styles["body"]))
        story.append(Spacer(1, 8))

        # 费用明细
        items = data.get("items", {}) or {}
        headers = items.get("headers", ["日期", "摘要", "科目", "金额", "附件张数"])
        rows = items.get("rows", [])
        story.append(self._build_table({"headers": headers, "rows": rows}, styles))
        story.append(Spacer(1, 8))

        # 合计金额
        total_amount = data.get("total_amount", "")
        amount_in_words = data.get("amount_in_words", "")
        sum_text = f"合计金额（小写）：¥{total_amount}"
        if amount_in_words:
            sum_text += f"　　（大写）：{amount_in_words}"
        story.append(Paragraph(sum_text, styles["body_bold"]))
        story.append(Spacer(1, 20))

        # 签字栏
        sign_rows = [[
            f"报销人签字：{data.get('applicant_sign', '__________')}",
            f"审核：{data.get('reviewer', '__________')}",
            f"审批：{data.get('approver', '__________')}",
        ]]
        sign_table = Table(sign_rows, colWidths=[5 * cm, 5 * cm, 5 * cm])
        sign_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(sign_table)

        return story

    # ===================================================================
    # 模板：审计报告
    # ===================================================================

    def _generate_audit_report(self, data: dict, styles: dict) -> list:
        """
        审计报告 PDF

        data 字段：
        - title:          报告标题
        - report_no:      报告编号
        - date:           报告日期
        - audited_entity: 被审计单位
        - period:         审计期间
        - summary:        审计概况
        - sections:       章节列表 [{"heading": "...", "content": "...", "table": {...}}]
        - findings:       审计发现表 {"headers": [...], "rows": [[...]]}
        - opinion:        审计意见
        """
        story: list = []
        title = data.get("title", "审计报告")
        story.append(Paragraph(title, styles["title"]))
        story.append(Spacer(1, 8))

        # 报告元信息
        meta_parts = []
        if data.get("report_no"):
            meta_parts.append(f"报告编号：{data['report_no']}")
        if data.get("audited_entity"):
            meta_parts.append(f"被审计单位：{data['audited_entity']}")
        if data.get("period"):
            meta_parts.append(f"审计期间：{data['period']}")
        if data.get("date"):
            meta_parts.append(f"报告日期：{data['date']}")
        if meta_parts:
            story.append(Paragraph("　　".join(meta_parts), styles["meta"]))
            story.append(Spacer(1, 12))

        # 审计概况
        if data.get("summary"):
            story.append(Paragraph("一、审计概况", styles["h1"]))
            story.append(Paragraph(str(data["summary"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 主体章节
        sections = data.get("sections", [])
        start_idx = 2 if data.get("summary") else 1
        for i, section in enumerate(sections):
            idx = start_idx + i
            cn_idx = self._cn_ordinal(idx)
            heading = section.get("heading", f"第{cn_idx}部分")
            story.append(Paragraph(f"{cn_idx}、{heading}", styles["h1"]))
            if section.get("content"):
                story.append(Paragraph(str(section["content"]), styles["body"]))
                story.append(Spacer(1, 4))
            if section.get("table"):
                story.append(self._build_table(section["table"], styles))
            story.append(Spacer(1, 8))

        # 审计发现
        findings = data.get("findings")
        if findings:
            story.append(Paragraph("审计发现", styles["h1"]))
            story.append(self._build_table(findings, styles))
            story.append(Spacer(1, 10))

        # 审计意见
        if data.get("opinion"):
            story.append(Paragraph("审计意见", styles["h1"]))
            story.append(Paragraph(str(data["opinion"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 签发栏
        sign_rows = [[
            "审计机构（盖章）：________________",
            "",
            f"日期：{data.get('date', '________年____月____日')}",
        ]]
        sign_table = Table(sign_rows, colWidths=[7 * cm, 2 * cm, 6 * cm])
        sign_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 20),
        ]))
        story.append(sign_table)

        return story

    # ===================================================================
    # 模板：风控报告
    # ===================================================================

    def _generate_risk_report(self, data: dict, styles: dict) -> list:
        """
        风控报告 PDF

        data 字段：
        - title:       报告标题
        - report_no:   报告编号
        - date:        报告日期
        - risk_level:  整体风险等级（低/中/高）
        - summary:     风险概况
        - sections:    章节列表 [{"heading": "...", "content": "...", "table": {...}}]
        - risks:       风险清单 {"headers": ["风险点","风险等级","影响程度","应对措施"], "rows": [[...]]}
        - conclusion:  风控结论与建议
        """
        story: list = []
        title = data.get("title", "风险控制报告")
        story.append(Paragraph(title, styles["title"]))
        story.append(Spacer(1, 8))

        # 报告元信息
        meta_parts = []
        if data.get("report_no"):
            meta_parts.append(f"报告编号：{data['report_no']}")
        if data.get("risk_level"):
            meta_parts.append(f"整体风险等级：{data['risk_level']}")
        if data.get("date"):
            meta_parts.append(f"报告日期：{data['date']}")
        if meta_parts:
            story.append(Paragraph("　　".join(meta_parts), styles["meta"]))
            story.append(Spacer(1, 12))

        # 风险概况
        if data.get("summary"):
            story.append(Paragraph("一、风险概况", styles["h1"]))
            story.append(Paragraph(str(data["summary"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 主体章节
        sections = data.get("sections", [])
        start_idx = 2 if data.get("summary") else 1
        for i, section in enumerate(sections):
            idx = start_idx + i
            cn_idx = self._cn_ordinal(idx)
            heading = section.get("heading", f"第{cn_idx}部分")
            story.append(Paragraph(f"{cn_idx}、{heading}", styles["h1"]))
            if section.get("content"):
                story.append(Paragraph(str(section["content"]), styles["body"]))
                story.append(Spacer(1, 4))
            if section.get("table"):
                story.append(self._build_table(section["table"], styles))
            story.append(Spacer(1, 8))

        # 风险清单
        risks = data.get("risks")
        if risks:
            story.append(Paragraph("风险清单", styles["h1"]))
            story.append(self._build_table(risks, styles))
            story.append(Spacer(1, 10))

        # 风控结论
        if data.get("conclusion"):
            story.append(Paragraph("风控结论与建议", styles["h1"]))
            story.append(Paragraph(str(data["conclusion"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 签发栏
        sign_rows = [[
            "风控部门（盖章）：________________",
            "",
            f"日期：{data.get('date', '________年____月____日')}",
        ]]
        sign_table = Table(sign_rows, colWidths=[7 * cm, 2 * cm, 6 * cm])
        sign_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 20),
        ]))
        story.append(sign_table)

        return story

    # ===================================================================
    # 模板：合规报告
    # ===================================================================

    def _generate_compliance_report(self, data: dict, styles: dict) -> list:
        """
        合规报告 PDF

        data 字段：
        - title:          报告标题
        - report_no:      报告编号
        - date:           报告日期
        - scope:          合规检查范围
        - compliance_level: 整体合规等级（合规/基本合规/不合规）
        - summary:        合规概况
        - sections:       章节列表 [{"heading": "...", "content": "...", "table": {...}}]
        - findings:       合规检查发现表 {"headers": ["检查项","合规状态","问题描述","整改建议"], "rows": [[...]]}
        - conclusion:     合规结论与建议
        """
        story: list = []
        title = data.get("title", "合规检查报告")
        story.append(Paragraph(title, styles["title"]))
        story.append(Spacer(1, 8))

        # 报告元信息
        meta_parts = []
        if data.get("report_no"):
            meta_parts.append(f"报告编号：{data['report_no']}")
        if data.get("scope"):
            meta_parts.append(f"检查范围：{data['scope']}")
        if data.get("compliance_level"):
            meta_parts.append(f"整体合规等级：{data['compliance_level']}")
        if data.get("date"):
            meta_parts.append(f"报告日期：{data['date']}")
        if meta_parts:
            story.append(Paragraph("　　".join(meta_parts), styles["meta"]))
            story.append(Spacer(1, 12))

        # 合规概况
        if data.get("summary"):
            story.append(Paragraph("一、合规概况", styles["h1"]))
            story.append(Paragraph(str(data["summary"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 主体章节
        sections = data.get("sections", [])
        start_idx = 2 if data.get("summary") else 1
        for i, section in enumerate(sections):
            idx = start_idx + i
            cn_idx = self._cn_ordinal(idx)
            heading = section.get("heading", f"第{cn_idx}部分")
            story.append(Paragraph(f"{cn_idx}、{heading}", styles["h1"]))
            if section.get("content"):
                story.append(Paragraph(str(section["content"]), styles["body"]))
                story.append(Spacer(1, 4))
            if section.get("table"):
                story.append(self._build_table(section["table"], styles))
            story.append(Spacer(1, 8))

        # 检查发现
        findings = data.get("findings")
        if findings:
            story.append(Paragraph("合规检查发现", styles["h1"]))
            story.append(self._build_table(findings, styles))
            story.append(Spacer(1, 10))

        # 合规结论
        if data.get("conclusion"):
            story.append(Paragraph("合规结论与建议", styles["h1"]))
            story.append(Paragraph(str(data["conclusion"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 签发栏
        sign_rows = [[
            "合规管理部门（盖章）：________________",
            "",
            f"日期：{data.get('date', '________年____月____日')}",
        ]]
        sign_table = Table(sign_rows, colWidths=[7 * cm, 2 * cm, 6 * cm])
        sign_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 20),
        ]))
        story.append(sign_table)

        return story

    # ===================================================================
    # 模板：绩效报告
    # ===================================================================

    def _generate_performance_report(self, data: dict, styles: dict) -> list:
        """
        绩效报告 PDF

        data 字段：
        - title:       报告标题
        - report_no:   报告编号
        - date:        报告日期
        - period:      考核周期（如 2026年上半年）
        - overall_grade: 整体绩效等级（如 A/B/C/D）
        - summary:     绩效概况
        - sections:    章节列表 [{"heading": "...", "content": "...", "table": {...}}]
        - scores:      绩效评分表 {"headers": ["部门/人员","考核指标","权重","得分","评级"], "rows": [[...]]}
        - conclusion:  绩效结论与改进建议
        """
        story: list = []
        title = data.get("title", "绩效评估报告")
        story.append(Paragraph(title, styles["title"]))
        story.append(Spacer(1, 8))

        # 报告元信息
        meta_parts = []
        if data.get("report_no"):
            meta_parts.append(f"报告编号：{data['report_no']}")
        if data.get("period"):
            meta_parts.append(f"考核周期：{data['period']}")
        if data.get("overall_grade"):
            meta_parts.append(f"整体绩效等级：{data['overall_grade']}")
        if data.get("date"):
            meta_parts.append(f"报告日期：{data['date']}")
        if meta_parts:
            story.append(Paragraph("　　".join(meta_parts), styles["meta"]))
            story.append(Spacer(1, 12))

        # 绩效概况
        if data.get("summary"):
            story.append(Paragraph("一、绩效概况", styles["h1"]))
            story.append(Paragraph(str(data["summary"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 主体章节
        sections = data.get("sections", [])
        start_idx = 2 if data.get("summary") else 1
        for i, section in enumerate(sections):
            idx = start_idx + i
            cn_idx = self._cn_ordinal(idx)
            heading = section.get("heading", f"第{cn_idx}部分")
            story.append(Paragraph(f"{cn_idx}、{heading}", styles["h1"]))
            if section.get("content"):
                story.append(Paragraph(str(section["content"]), styles["body"]))
                story.append(Spacer(1, 4))
            if section.get("table"):
                story.append(self._build_table(section["table"], styles))
            story.append(Spacer(1, 8))

        # 绩效评分表
        scores = data.get("scores")
        if scores:
            story.append(Paragraph("绩效评分明细", styles["h1"]))
            story.append(self._build_table(scores, styles))
            story.append(Spacer(1, 10))

        # 绩效结论
        if data.get("conclusion"):
            story.append(Paragraph("绩效结论与改进建议", styles["h1"]))
            story.append(Paragraph(str(data["conclusion"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 签发栏
        sign_rows = [[
            "人力资源部（盖章）：________________",
            "",
            f"日期：{data.get('date', '________年____月____日')}",
        ]]
        sign_table = Table(sign_rows, colWidths=[7 * cm, 2 * cm, 6 * cm])
        sign_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 20),
        ]))
        story.append(sign_table)

        return story

    # ===================================================================
    # 模板：项目报告
    # ===================================================================

    def _generate_project_report(self, data: dict, styles: dict) -> list:
        """
        项目报告 PDF

        data 字段：
        - title:        报告标题
        - report_no:    报告编号
        - date:         报告日期
        - project_name: 项目名称
        - manager:      项目经理
        - status:       项目状态（如 进行中/已完成/延期）
        - progress:     整体进度（如 75%）
        - summary:      项目概况
        - sections:     章节列表 [{"heading": "...", "content": "...", "table": {...}}]
        - milestones:   里程碑表 {"headers": ["阶段","计划完成","实际完成","状态"], "rows": [[...]]}
        - conclusion:   项目总结与建议
        """
        story: list = []
        title = data.get("title", "项目总结报告")
        story.append(Paragraph(title, styles["title"]))
        story.append(Spacer(1, 8))

        # 报告元信息
        meta_parts = []
        if data.get("report_no"):
            meta_parts.append(f"报告编号：{data['report_no']}")
        if data.get("project_name"):
            meta_parts.append(f"项目名称：{data['project_name']}")
        if data.get("manager"):
            meta_parts.append(f"项目经理：{data['manager']}")
        if data.get("status"):
            meta_parts.append(f"项目状态：{data['status']}")
        if data.get("progress"):
            meta_parts.append(f"整体进度：{data['progress']}")
        if data.get("date"):
            meta_parts.append(f"报告日期：{data['date']}")
        if meta_parts:
            story.append(Paragraph("　　".join(meta_parts), styles["meta"]))
            story.append(Spacer(1, 12))

        # 项目概况
        if data.get("summary"):
            story.append(Paragraph("一、项目概况", styles["h1"]))
            story.append(Paragraph(str(data["summary"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 主体章节
        sections = data.get("sections", [])
        start_idx = 2 if data.get("summary") else 1
        for i, section in enumerate(sections):
            idx = start_idx + i
            cn_idx = self._cn_ordinal(idx)
            heading = section.get("heading", f"第{cn_idx}部分")
            story.append(Paragraph(f"{cn_idx}、{heading}", styles["h1"]))
            if section.get("content"):
                story.append(Paragraph(str(section["content"]), styles["body"]))
                story.append(Spacer(1, 4))
            if section.get("table"):
                story.append(self._build_table(section["table"], styles))
            story.append(Spacer(1, 8))

        # 里程碑
        milestones = data.get("milestones")
        if milestones:
            story.append(Paragraph("项目里程碑", styles["h1"]))
            story.append(self._build_table(milestones, styles))
            story.append(Spacer(1, 10))

        # 项目总结
        if data.get("conclusion"):
            story.append(Paragraph("项目总结与建议", styles["h1"]))
            story.append(Paragraph(str(data["conclusion"]), styles["body"]))
            story.append(Spacer(1, 8))

        # 签发栏
        sign_rows = [[
            "项目管理办公室（盖章）：________________",
            "",
            f"日期：{data.get('date', '________年____月____日')}",
        ]]
        sign_table = Table(sign_rows, colWidths=[7 * cm, 2 * cm, 6 * cm])
        sign_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 20),
        ]))
        story.append(sign_table)

        return story

    # ===================================================================
    # 辅助方法
    # ===================================================================

    def _build_styles(self) -> dict:
        """构建段落样式集合"""
        _ensure_cn_font()
        title = ParagraphStyle(
            "CnTitle", fontName=_CN_FONT, fontSize=20, leading=28,
            alignment=TA_CENTER, textColor=_COLOR_PRIMARY, spaceAfter=6,
        )
        invoice_title = ParagraphStyle(
            "CnInvoiceTitle", fontName=_CN_FONT, fontSize=22, leading=30,
            alignment=TA_CENTER, textColor=colors.black, spaceAfter=6,
        )
        h1 = ParagraphStyle(
            "CnH1", fontName=_CN_FONT, fontSize=14, leading=20,
            alignment=TA_LEFT, textColor=_COLOR_PRIMARY,
            spaceBefore=8, spaceAfter=4,
        )
        body = ParagraphStyle(
            "CnBody", fontName=_CN_FONT, fontSize=11, leading=18,
            alignment=TA_JUSTIFY, textColor=colors.black,
            firstLineIndent=22, spaceAfter=2,
        )
        body_bold = ParagraphStyle(
            "CnBodyBold", fontName=_CN_FONT, fontSize=11, leading=18,
            alignment=TA_LEFT, textColor=colors.black, spaceAfter=2,
        )
        meta = ParagraphStyle(
            "CnMeta", fontName=_CN_FONT, fontSize=10, leading=16,
            alignment=TA_CENTER, textColor=colors.HexColor("#555555"),
        )
        cell = ParagraphStyle(
            "CnCell", fontName=_CN_FONT, fontSize=10, leading=14,
            alignment=TA_CENTER, textColor=colors.black,
        )
        cell_small = ParagraphStyle(
            "CnCellSmall", fontName=_CN_FONT, fontSize=9, leading=13,
            alignment=TA_LEFT, textColor=colors.black,
        )
        cell_left = ParagraphStyle(
            "CnCellLeft", fontName=_CN_FONT, fontSize=10, leading=14,
            alignment=TA_LEFT, textColor=colors.black,
        )
        return {
            "title": title,
            "invoice_title": invoice_title,
            "h1": h1,
            "body": body,
            "body_bold": body_bold,
            "meta": meta,
            "cell": cell,
            "cell_small": cell_small,
            "cell_left": cell_left,
        }

    def _build_table(self, table_data: dict, styles: dict, col_widths: list | None = None) -> Table:
        """根据统一表格格式构建 reportlab Table

        table_data 格式：{"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}
        """
        headers = table_data.get("headers", [])
        rows = table_data.get("rows", [])
        if not headers and not rows:
            return Table([[""]])

        # 表格内容（全部转为字符串并包装为 Paragraph 以支持换行与中文字体）
        data_rows: list[list] = []
        if headers:
            header_row = [Paragraph(str(h), styles["cell"]) for h in headers]
            data_rows.append(header_row)

        for row in rows:
            data_rows.append([
                Paragraph(str(row[j] if j < len(row) else ""), styles["cell"])
                for j in range(len(headers) if headers else len(row))
            ])

        # 计算列宽
        col_count = len(headers) if headers else (len(rows[0]) if rows else 1)
        if col_widths is None:
            total_width = 16.5 * cm  # A4 可用宽度约 17cm
            col_widths = [total_width / col_count] * col_count

        table = Table(data_rows, colWidths=col_widths, repeatRows=1 if headers else 0)
        style_cmds = [
            ("FONTNAME", (0, 0), (-1, -1), _CN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, _COLOR_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        # 表头样式
        if headers:
            style_cmds.extend([
                ("BACKGROUND", (0, 0), (-1, 0), _COLOR_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ])
        table.setStyle(TableStyle(style_cmds))
        return table

    @staticmethod
    def _format_party_text(party: dict) -> str:
        """格式化购销方信息文本"""
        lines = []
        if party.get("name"):
            lines.append(f"名称：{party['name']}")
        if party.get("tax_id"):
            lines.append(f"纳税人识别号：{party['tax_id']}")
        if party.get("address_bank"):
            lines.append(f"地址、电话、开户行及账号：{party['address_bank']}")
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _cn_ordinal(n: int) -> str:
        """将数字转为中文序号（1→一，2→二，…，10→十，11→十一…）"""
        cn_digits = "零一二三四五六七八九"
        if n <= 0:
            return str(n)
        if n < 10:
            return cn_digits[n]
        if n == 10:
            return "十"
        if n < 20:
            return f"十{cn_digits[n - 10]}"
        if n < 100:
            tens, units = divmod(n, 10)
            return f"{cn_digits[tens]}十{cn_digits[units] if units else ''}"
        return str(n)


# 全局单例
pdf_generator = PdfGenerator()
