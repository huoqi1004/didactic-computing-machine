"""
Word 文档生成器 (Word Document Generator)
=========================================

基于 python-docx 生成 .docx 标准办公文档，供各业务 Agent 调用。

支持的模板类型（template_type）：
1. basic      - 基础文档（标题 + 段落 + 表格）
2. contract   - 合同文档（采购合同模板）
3. report     - 报告文档（审计报告 / 风控报告模板）
4. personnel  - 人事文档（入职通知 / 薪资单模板）
5. proposal   - 方案文档（含背景/目标/方案/预算/时间表章节）
6. meeting    - 会议纪要（含时间/地点/参会人/议题/决议/待办事项）
7. notice     - 通知公告（含标题/接收人/正文/落款）

表格数据统一格式：
    {"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}

使用方式::

    from app.core.document.docx_generator import docx_generator

    path = docx_generator.generate(
        template_type="basic",
        data={"title": "示例", "paragraphs": ["段落1"], "table": {...}},
        filename="example.docx",
    )
"""
from __future__ import annotations

import re
from typing import Any

import structlog
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.core.document.output import resolve_output_path

logger = structlog.get_logger()

# 中文默认字体
_DEFAULT_FONT = "宋体"
_DEFAULT_HEADING_FONT = "黑体"

# 模板会按序自动编号（一、二、…），而模型产出的章节标题常自带"一、/1./第X章"，
# 两者叠加会渲染成"二、一、监理范围"（2026-10-04 真机实测）——先剥离自带编号。
_LEADING_SECTION_NUM_RE = re.compile(
    r"^\s*(?:"
    r"[（(]?[一二三四五六七八九十百零〇]{1,4}[)）]?[、.．:：]"
    r"|[（(][一二三四五六七八九十百零〇\d]{1,4}[)）]"
    r"|[（(]?\d{1,2}[)）]?[、.．:：](?!\d)"
    r"|第[一二三四五六七八九十百零〇\d]{1,4}[章节部篇条]"
    r")\s*"
)


def strip_section_leading_number(heading: str) -> str:
    """剥离章节标题自带的序号（一、/（一）/1./第X章），交由模板统一编号。"""
    return _LEADING_SECTION_NUM_RE.sub("", heading, count=1).strip()


class DocxGenerator:
    """
    Word 文档生成器

    通过 ``generate(template_type, data, filename)`` 统一入口，
    根据模板类型分发到对应的私有生成方法。
    """

    # 支持的模板类型 → 生成方法名
    SUPPORTED_TEMPLATES = {
        "basic": "_generate_basic",
        "contract": "_generate_contract",
        "report": "_generate_report",
        "personnel": "_generate_personnel",
        "proposal": "_generate_proposal",
        "meeting": "_generate_meeting",
        "notice": "_generate_notice",
    }

    def generate(self, template_type: str, data: dict, filename: str) -> str:
        """
        生成 Word 文档。

        Args:
            template_type: 模板类型（basic/contract/report/personnel/
                            proposal/meeting/notice）
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
                f"不支持的 Word 模板类型: {template_type}，"
                f"支持: {list(self.SUPPORTED_TEMPLATES.keys())}"
            )

        # 规范化文件名
        if not filename.endswith(".docx"):
            filename = f"{filename}.docx"
        output_path = resolve_output_path(filename)

        try:
            doc = Document()
            # 设置默认正文字体
            self._set_default_font(doc)

            # 分发到具体模板生成方法
            method = getattr(self, method_name)
            method(doc, data or {})

            # 页眉标注（裁决 D-C：AI 生成文档强制标注，可溯源、防冒充正式文件）
            header_note = (data or {}).get("header_note")
            if header_note:
                self._add_header_note(doc, str(header_note))

            doc.save(output_path)
            logger.info(
                "docx.generated",
                template_type=template_type,
                filename=filename,
                path=output_path,
            )
            return output_path
        except ValueError:
            raise
        except Exception as e:
            logger.error(
                "docx.generate_failed",
                template_type=template_type,
                filename=filename,
                error=str(e),
            )
            raise ValueError(f"Word 文档生成失败（{template_type}）: {e}") from e

    # ===================================================================
    # 页眉标注（跨模板通用）
    # ===================================================================

    @staticmethod
    def _add_header_note(doc: Document, note: str) -> None:
        """把标注写入各节页眉（首个空段落复用，居中、灰色小字号）。"""
        for section in doc.sections:
            header = section.header
            header.is_linked_to_previous = False
            para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
            para.text = note[:300]
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            DocxGenerator._format_runs(para.runs, _DEFAULT_FONT, Pt(9))
            for run in para.runs:
                run.font.color.rgb = RGBColor(0x8A, 0x8A, 0x8A)

    # ===================================================================
    # 模板：基础文档
    # ===================================================================

    def _generate_basic(self, doc: Document, data: dict) -> None:
        """
        基础文档：标题 + 段落 + 表格

        data 字段：
        - title:      文档标题
        - paragraphs: 段落文本列表
        - table:      表格数据 {"headers": [...], "rows": [[...]]}
        """
        title = data.get("title", "")
        if title:
            self._add_heading(doc, str(title), level=0, center=True)

        # 段落
        for para in data.get("paragraphs", []):
            self._add_paragraph(doc, str(para))

        # 表格
        table_data = data.get("table")
        if table_data:
            self._add_table(doc, table_data)

    # ===================================================================
    # 模板：采购合同
    # ===================================================================

    def _generate_contract(self, doc: Document, data: dict) -> None:
        """
        采购合同模板

        data 字段：
        - title:              合同标题（默认"采购合同"）
        - contract_no:        合同编号
        - sign_location:      签订地点
        - sign_date:          签订日期
        - party_a / party_b:  甲乙双方信息 dict（name/address/contact/legal_person）
        - items:              标的物表格 {"headers": [...], "rows": [[...]]}
        - total_amount:       总金额（大写）
        - total_amount_number: 总金额（数字）
        - terms:              合同条款文本列表
        """
        title = data.get("title", "采购合同")
        self._add_heading(doc, title, level=0, center=True)

        # 合同编号与签订信息
        meta_lines = []
        if data.get("contract_no"):
            meta_lines.append(f"合同编号：{data['contract_no']}")
        if data.get("sign_location"):
            meta_lines.append(f"签订地点：{data['sign_location']}")
        if data.get("sign_date"):
            meta_lines.append(f"签订日期：{data['sign_date']}")
        for line in meta_lines:
            self._add_paragraph(doc, line)

        # 甲乙双方
        party_a = data.get("party_a", {}) or {}
        party_b = data.get("party_b", {}) or {}
        self._add_heading(doc, "一、合同双方", level=1)
        self._add_paragraph(doc, self._format_party("甲方", party_a))
        self._add_paragraph(doc, self._format_party("乙方", party_b))

        # 采购标的
        items = data.get("items")
        if items:
            self._add_heading(doc, "二、采购标的", level=1)
            self._add_table(doc, items)

        # 金额
        if data.get("total_amount") or data.get("total_amount_number"):
            self._add_heading(doc, "三、合同金额", level=1)
            amount_parts = []
            if data.get("total_amount_number"):
                amount_parts.append(f"总金额（小写）：¥{data['total_amount_number']}")
            if data.get("total_amount"):
                amount_parts.append(f"总金额（大写）：{data['total_amount']}")
            for part in amount_parts:
                self._add_paragraph(doc, part)

        # 条款
        terms = data.get("terms", [])
        if terms:
            self._add_heading(doc, "四、合同条款", level=1)
            for idx, term in enumerate(terms, 1):
                self._add_paragraph(doc, f"{idx}. {term}")

        # 签字栏
        self._add_heading(doc, "五、签字盖章", level=1)
        self._add_paragraph(doc, "甲方（盖章）：________________________")
        self._add_paragraph(doc, "乙方（盖章）：________________________")
        self._add_paragraph(doc, "签订日期：________年______月______日")

    # ===================================================================
    # 模板：报告文档（审计 / 风控）
    # ===================================================================

    def _generate_report(self, doc: Document, data: dict) -> None:
        """
        报告文档模板（审计报告 / 风控报告通用）

        data 字段：
        - title:     报告标题
        - report_no: 报告编号
        - date:      报告日期
        - author:    编制部门 / 编制人
        - summary:   摘要
        - sections:  主体章节列表 [{"heading": "...", "content": "...", "table": {...}}]
        - table:     汇总表格（可选）
        - conclusion: 结论
        """
        title = data.get("title", "报告")
        self._add_heading(doc, title, level=0, center=True)

        # 报告元信息
        meta_lines = []
        if data.get("report_no"):
            meta_lines.append(f"报告编号：{data['report_no']}")
        if data.get("author"):
            meta_lines.append(f"编制：{data['author']}")
        if data.get("date"):
            meta_lines.append(f"日期：{data['date']}")
        for line in meta_lines:
            self._add_paragraph(doc, line, align=WD_ALIGN_PARAGRAPH.CENTER)

        # 摘要
        if data.get("summary"):
            self._add_heading(doc, "一、摘要", level=1)
            self._add_paragraph(doc, str(data["summary"]))

        # 主体章节
        sections = data.get("sections", [])
        start_idx = 2 if data.get("summary") else 1
        for i, section in enumerate(sections):
            idx = start_idx + i
            heading = strip_section_leading_number(str(section.get("heading", ""))) or f"第{idx}部分"
            self._add_heading(doc, f"{'一二三四五六七八九十'[idx-1] if idx <= 10 else idx}、{heading}", level=1)
            if section.get("content"):
                self._add_paragraph(doc, str(section["content"]))
            if section.get("table"):
                self._add_table(doc, section["table"])

        # 汇总表格
        if data.get("table"):
            self._add_heading(doc, "附表", level=1)
            self._add_table(doc, data["table"])

        # 结论
        if data.get("conclusion"):
            self._add_heading(doc, "结论", level=1)
            self._add_paragraph(doc, str(data["conclusion"]))

    # ===================================================================
    # 模板：人事文档（入职通知 / 薪资单）
    # ===================================================================

    def _generate_personnel(self, doc: Document, data: dict) -> None:
        """
        人事文档模板

        通过 data["subtype"] 区分：
        - onboarding: 入职通知书
        - salary:     薪资单

        onboarding data 字段：
            title, employee_name, department, position, start_date,
            salary, probation, content

        salary data 字段：
            title, employee_name, department, month,
            items {"headers": [...], "rows": [[...]]}, net_pay
        """
        subtype = data.get("subtype", "onboarding")
        if subtype == "salary":
            self._generate_salary_slip(doc, data)
        else:
            self._generate_onboarding_notice(doc, data)

    def _generate_onboarding_notice(self, doc: Document, data: dict) -> None:
        """生成入职通知书"""
        title = data.get("title", "入职通知书")
        self._add_heading(doc, title, level=0, center=True)

        employee_name = data.get("employee_name", "________")
        self._add_paragraph(doc, f"{employee_name} 先生/女士：")

        content = data.get("content")
        if content:
            self._add_paragraph(doc, str(content))
        else:
            # 默认正文
            lines = []
            if data.get("department"):
                lines.append(f"  很高兴通知您，您已被录用为 {data['department']} 部门员工。")
            if data.get("position"):
                lines.append(f"  职位：{data['position']}。")
            if data.get("start_date"):
                lines.append(f"  报到日期：{data['start_date']}。")
            if data.get("probation"):
                lines.append(f"  试用期：{data['probation']}。")
            if data.get("salary"):
                lines.append(f"  薪资待遇：{data['salary']}。")
            for line in lines:
                self._add_paragraph(doc, line)

        self._add_paragraph(doc, "")
        self._add_paragraph(doc, "  请您于报到日携带相关证件到人力资源部办理入职手续。")
        self._add_paragraph(doc, "  特此通知。")
        self._add_paragraph(doc, "")
        self._add_paragraph(doc, "                                        人力资源部")
        if data.get("date"):
            self._add_paragraph(doc, f"                                        {data['date']}")

    def _generate_salary_slip(self, doc: Document, data: dict) -> None:
        """生成薪资单"""
        title = data.get("title", "薪资单")
        self._add_heading(doc, title, level=0, center=True)

        # 员工信息
        info_lines = []
        if data.get("employee_name"):
            info_lines.append(f"姓名：{data['employee_name']}")
        if data.get("department"):
            info_lines.append(f"部门：{data['department']}")
        if data.get("month"):
            info_lines.append(f"所属月份：{data['month']}")
        for line in info_lines:
            self._add_paragraph(doc, line)

        # 薪资明细表
        items = data.get("items")
        if items:
            self._add_table(doc, items)

        # 实发金额
        if data.get("net_pay"):
            self._add_paragraph(doc, f"实发金额：¥{data['net_pay']}", bold=True)

        self._add_paragraph(doc, "")
        self._add_paragraph(doc, "备注：本薪资单仅供员工本人核对，请妥善保管。")

    # ===================================================================
    # 模板：方案文档
    # ===================================================================

    def _generate_proposal(self, doc: Document, data: dict) -> None:
        """
        方案文档模板（含背景/目标/方案/预算/时间表章节）

        data 字段：
        - title:       方案标题
        - proposal_no: 方案编号
        - author:      编制人 / 编制部门
        - date:        编制日期
        - background:  背景说明
        - objective:   目标说明
        - solution:    方案正文（文本列表或单段文本）
        - budget:      预算明细 {"headers": [...], "rows": [[...]]}
        - budget_total: 预算总额
        - schedule:    时间表 {"headers": ["阶段","时间","内容"], "rows": [[...]]}
        - conclusion:  结论与建议
        """
        title = data.get("title", "方案文档")
        self._add_heading(doc, title, level=0, center=True)

        # 方案元信息
        meta_lines = []
        if data.get("proposal_no"):
            meta_lines.append(f"方案编号：{data['proposal_no']}")
        if data.get("author"):
            meta_lines.append(f"编制：{data['author']}")
        if data.get("date"):
            meta_lines.append(f"日期：{data['date']}")
        for line in meta_lines:
            self._add_paragraph(doc, line, align=WD_ALIGN_PARAGRAPH.CENTER)

        # 背景说明
        if data.get("background"):
            self._add_heading(doc, "一、项目背景", level=1)
            self._add_paragraph(doc, str(data["background"]))

        # 目标说明
        if data.get("objective"):
            self._add_heading(doc, "二、方案目标", level=1)
            self._add_paragraph(doc, str(data["objective"]))

        # 方案正文
        solution = data.get("solution")
        if solution:
            self._add_heading(doc, "三、方案内容", level=1)
            if isinstance(solution, list):
                for idx, item in enumerate(solution, 1):
                    self._add_paragraph(doc, f"{idx}. {item}")
            else:
                self._add_paragraph(doc, str(solution))

        # 预算明细
        budget = data.get("budget")
        if budget or data.get("budget_total"):
            self._add_heading(doc, "四、预算明细", level=1)
            if budget:
                self._add_table(doc, budget)
            if data.get("budget_total"):
                self._add_paragraph(doc, f"预算总额：¥{data['budget_total']}", bold=True)

        # 时间表
        schedule = data.get("schedule")
        if schedule:
            self._add_heading(doc, "五、实施时间表", level=1)
            self._add_table(doc, schedule)

        # 结论
        if data.get("conclusion"):
            self._add_heading(doc, "六、结论与建议", level=1)
            self._add_paragraph(doc, str(data["conclusion"]))

    # ===================================================================
    # 模板：会议纪要
    # ===================================================================

    def _generate_meeting(self, doc: Document, data: dict) -> None:
        """
        会议纪要模板（含时间/地点/参会人/议题/决议/待办事项）

        data 字段：
        - title:       纪要标题（默认"会议纪要"）
        - meeting_no:  会议编号
        - date:        会议日期
        - time:        会议时间
        - location:    会议地点
        - host:        主持人
        - recorder:    记录人
        - attendees:   参会人员列表
        - agenda:      议题列表 [{"topic": "...", "discussion": "...", "decision": "..."}]
        - decisions:   决议列表（可选，若 agenda 已含 decision 可省略）
        - action_items: 待办事项 {"headers": ["序号","事项","负责人","截止日期"], "rows": [[...]]}
        - notes:       其他说明
        """
        title = data.get("title", "会议纪要")
        self._add_heading(doc, title, level=0, center=True)

        # 会议编号
        if data.get("meeting_no"):
            self._add_paragraph(doc, f"会议编号：{data['meeting_no']}", align=WD_ALIGN_PARAGRAPH.CENTER)

        # 会议基本信息表
        info_rows = []
        if data.get("date"):
            info_rows.append(["日期", str(data["date"])])
        if data.get("time"):
            info_rows.append(["时间", str(data["time"])])
        if data.get("location"):
            info_rows.append(["地点", str(data["location"])])
        if data.get("host"):
            info_rows.append(["主持人", str(data["host"])])
        if data.get("recorder"):
            info_rows.append(["记录人", str(data["recorder"])])
        if info_rows:
            self._add_table(doc, {"headers": ["项目", "内容"], "rows": info_rows})

        # 参会人员
        attendees = data.get("attendees", [])
        if attendees:
            self._add_heading(doc, "一、参会人员", level=1)
            if isinstance(attendees, list):
                self._add_paragraph(doc, "、".join(str(a) for a in attendees))
            else:
                self._add_paragraph(doc, str(attendees))

        # 议题与讨论
        agenda = data.get("agenda", [])
        if agenda:
            self._add_heading(doc, "二、会议议题", level=1)
            for idx, item in enumerate(agenda, 1):
                topic = item.get("topic", f"议题{idx}")
                self._add_paragraph(doc, f"{idx}. {topic}", bold=True)
                if item.get("discussion"):
                    self._add_paragraph(doc, f"讨论：{item['discussion']}")
                if item.get("decision"):
                    self._add_paragraph(doc, f"决议：{item['decision']}")

        # 决议汇总
        decisions = data.get("decisions", [])
        if decisions:
            self._add_heading(doc, "三、会议决议", level=1)
            for idx, decision in enumerate(decisions, 1):
                self._add_paragraph(doc, f"{idx}. {decision}")

        # 待办事项
        action_items = data.get("action_items")
        if action_items:
            self._add_heading(doc, "四、待办事项", level=1)
            self._add_table(doc, action_items)

        # 其他说明
        if data.get("notes"):
            self._add_heading(doc, "五、其他说明", level=1)
            self._add_paragraph(doc, str(data["notes"]))

    # ===================================================================
    # 模板：通知公告
    # ===================================================================

    def _generate_notice(self, doc: Document, data: dict) -> None:
        """
        通知公告模板（含标题/接收人/正文/落款）

        data 字段：
        - title:     通知标题
        - notice_no: 通知编号
        - recipients: 接收人 / 接收部门
        - date:      发布日期
        - body:      正文内容（文本列表或单段文本）
        - attachment: 附件说明
        - issuer:    发布单位 / 落款
        """
        title = data.get("title", "通知")
        self._add_heading(doc, title, level=0, center=True)

        # 通知编号
        if data.get("notice_no"):
            self._add_paragraph(doc, data["notice_no"], align=WD_ALIGN_PARAGRAPH.CENTER)

        # 接收人
        recipients = data.get("recipients", "")
        if recipients:
            self._add_paragraph(doc, f"致：{recipients}")

        # 正文
        body = data.get("body")
        if body:
            self._add_paragraph(doc, "")
            if isinstance(body, list):
                for para in body:
                    self._add_paragraph(doc, str(para))
            else:
                self._add_paragraph(doc, str(body))

        # 附件
        if data.get("attachment"):
            self._add_paragraph(doc, "")
            self._add_paragraph(doc, f"附件：{data['attachment']}")

        # 落款
        self._add_paragraph(doc, "")
        self._add_paragraph(doc, "")
        issuer = data.get("issuer", "")
        if issuer:
            self._add_paragraph(doc, f"                                        {issuer}", align=WD_ALIGN_PARAGRAPH.RIGHT)
        if data.get("date"):
            self._add_paragraph(doc, f"                                        {data['date']}", align=WD_ALIGN_PARAGRAPH.RIGHT)

    # ===================================================================
    # 辅助方法
    # ===================================================================

    def _set_default_font(self, doc: Document) -> None:
        """设置文档默认正文字体（含东亚字体）"""
        style = doc.styles["Normal"]
        style.font.name = _DEFAULT_FONT
        style.font.size = Pt(11)
        # 设置东亚字体
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            from docx.oxml import OxmlElement
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), _DEFAULT_FONT)

    def _add_heading(
        self,
        doc: Document,
        text: str,
        level: int = 1,
        center: bool = False,
    ) -> None:
        """添加标题，并设置中文字体"""
        if level == 0:
            heading = doc.add_heading(text, level=0)
            if center:
                heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._format_runs(heading.runs, font_name=_DEFAULT_HEADING_FONT, size=Pt(22))
        else:
            heading = doc.add_heading(text, level=level)
            self._format_runs(
                heading.runs,
                font_name=_DEFAULT_HEADING_FONT,
                size=Pt(16 - (level - 1) * 2),
            )

    def _add_paragraph(
        self,
        doc: Document,
        text: str,
        align: WD_ALIGN_PARAGRAPH | None = None,
        bold: bool = False,
    ) -> None:
        """添加段落，并设置中文字体"""
        para = doc.add_paragraph()
        if align is not None:
            para.alignment = align
        run = para.add_run(text)
        run.font.name = _DEFAULT_FONT
        run.font.size = Pt(11)
        run.bold = bold
        # 东亚字体
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            from docx.oxml import OxmlElement
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), _DEFAULT_FONT)

    def _add_table(self, doc: Document, table_data: dict) -> None:
        """添加表格

        table_data 格式：{"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}
        """
        headers = table_data.get("headers", [])
        rows = table_data.get("rows", [])
        if not headers and not rows:
            return

        col_count = len(headers) if headers else (len(rows[0]) if rows else 0)
        if col_count == 0:
            return

        table = doc.add_table(rows=1 + len(rows), cols=col_count)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        # 表头
        for j, header in enumerate(headers):
            cell = table.rows[0].cells[j]
            cell.text = ""
            para = cell.paragraphs[0]
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run(str(header))
            run.bold = True
            run.font.name = _DEFAULT_FONT
            run.font.size = Pt(11)
            self._set_cell_east_asia_font(run)
            # 表头底色
            self._shade_cell(cell, "D9E2F3")

        # 数据行
        for i, row in enumerate(rows, 1):
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = table.rows[i].cells[j]
                cell.text = ""
                para = cell.paragraphs[0]
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = para.add_run(str(value))
                run.font.name = _DEFAULT_FONT
                run.font.size = Pt(10.5)
                self._set_cell_east_asia_font(run)

    def _set_cell_east_asia_font(self, run) -> None:
        """为 run 设置东亚字体"""
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            from docx.oxml import OxmlElement
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), _DEFAULT_FONT)

    @staticmethod
    def _shade_cell(cell, color_hex: str) -> None:
        """为单元格设置背景色"""
        from docx.oxml import OxmlElement
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), color_hex)
        tc_pr.append(shd)

    @staticmethod
    def _format_runs(runs, font_name: str, size: Pt) -> None:
        """批量格式化 run 的字体"""
        for run in runs:
            run.font.name = font_name
            run.font.size = size
            rpr = run._element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                from docx.oxml import OxmlElement
                rfonts = OxmlElement("w:rFonts")
                rpr.append(rfonts)
            rfonts.set(qn("w:eastAsia"), font_name)

    @staticmethod
    def _format_party(role: str, party: dict) -> str:
        """格式化合同方信息"""
        parts = [role]
        if party.get("name"):
            parts.append(party["name"])
        detail = []
        if party.get("legal_person"):
            detail.append(f"法定代表人：{party['legal_person']}")
        if party.get("address"):
            detail.append(f"地址：{party['address']}")
        if party.get("contact"):
            detail.append(f"联系方式：{party['contact']}")
        if detail:
            parts.append("（" + "，".join(detail) + "）")
        return "".join(parts)


# 全局单例
docx_generator = DocxGenerator()
