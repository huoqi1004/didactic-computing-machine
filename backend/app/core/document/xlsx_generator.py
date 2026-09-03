"""
Excel 表格生成器 (Excel Spreadsheet Generator)
===============================================

基于 openpyxl 生成 .xlsx 标准办公表格，供各业务 Agent 调用。

支持的模板类型（template_type）：
1. basic      - 基础表格（数据行 + 表头）
2. voucher    - 会计凭证表
3. inventory  - 库存盘点表
4. payroll    - 工资条表
5. tax        - 税务申报表
6. budget     - 预算执行表（预算/实际/差异）
7. project    - 项目进度表（任务/负责人/进度/状态）
8. asset      - 资产清单表（资产编号/名称/类别/原值/折旧/净值）
9. customer   - 客户清单表（客户编号/名称/联系人/电话/邮箱/等级）
10. employee  - 员工花名册（工号/姓名/部门/职位/入职日期/状态）
11. supplier  - 供应商清单（编号/名称/联系人/电话/评级）
12. quality   - 质量检测表（批次/检测项/标准值/实测值/结果）
13. production - 生产计划表（产品/计划量/实际量/完成率/状态）

表格数据统一格式：
    {"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}

使用方式::

    from app.core.document.xlsx_generator import xlsx_generator

    path = xlsx_generator.generate(
        template_type="basic",
        data={"title": "示例", "headers": ["A", "B"], "rows": [["1", "2"]]},
        filename="example.xlsx",
    )
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

import structlog
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

logger = structlog.get_logger()

# 样式常量
_TITLE_FONT = Font(name="宋体", size=14, bold=True, color="FFFFFF")
_TITLE_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
_HEADER_FONT = Font(name="宋体", size=11, bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_SUBTOTAL_FONT = Font(name="宋体", size=11, bold=True)
_SUBTOTAL_FILL = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
_BODY_FONT = Font(name="宋体", size=11)
_NOTE_FONT = Font(name="宋体", size=10, italic=True, color="666666")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
_RIGHT = Alignment(horizontal="right", vertical="center")
_THIN_BORDER = Border(
    left=Side(style="thin", color="B0B0B0"),
    right=Side(style="thin", color="B0B0B0"),
    top=Side(style="thin", color="B0B0B0"),
    bottom=Side(style="thin", color="B0B0B0"),
)


class XlsxGenerator:
    """
    Excel 表格生成器

    通过 ``generate(template_type, data, filename)`` 统一入口，
    根据模板类型分发到对应的私有生成方法。
    """

    SUPPORTED_TEMPLATES = {
        "basic": "_generate_basic",
        "voucher": "_generate_voucher",
        "inventory": "_generate_inventory",
        "payroll": "_generate_payroll",
        "tax": "_generate_tax",
        "budget": "_generate_budget",
        "project": "_generate_project",
        "asset": "_generate_asset",
        "customer": "_generate_customer",
        "employee": "_generate_employee",
        "supplier": "_generate_supplier",
        "quality": "_generate_quality",
        "production": "_generate_production",
    }

    def generate(self, template_type: str, data: dict, filename: str) -> str:
        """
        生成 Excel 表格。

        Args:
            template_type: 模板类型（basic/voucher/inventory/payroll/tax/
                            budget/project/asset/customer/employee/supplier/
                            quality/production）
            data:           表格所需结构化数据
            filename:       输出文件名（不带路径）

        Returns:
            生成的文件绝对路径

        Raises:
            ValueError: 模板类型不支持或生成失败
        """
        method_name = self.SUPPORTED_TEMPLATES.get(template_type)
        if method_name is None:
            raise ValueError(
                f"不支持的 Excel 模板类型: {template_type}，"
                f"支持: {list(self.SUPPORTED_TEMPLATES.keys())}"
            )

        if not filename.endswith(".xlsx"):
            filename = f"{filename}.xlsx"
        output_path = os.path.join(tempfile.gettempdir(), filename)

        try:
            wb = Workbook()
            # 分发到具体模板生成方法
            method = getattr(self, method_name)
            method(wb, data or {})
            wb.save(output_path)
            logger.info(
                "xlsx.generated",
                template_type=template_type,
                filename=filename,
                path=output_path,
            )
            return output_path
        except ValueError:
            raise
        except Exception as e:
            logger.error(
                "xlsx.generate_failed",
                template_type=template_type,
                filename=filename,
                error=str(e),
            )
            raise ValueError(f"Excel 表格生成失败（{template_type}）: {e}") from e

    # ===================================================================
    # 模板：基础表格
    # ===================================================================

    def _generate_basic(self, wb: Workbook, data: dict) -> None:
        """
        基础表格：标题 + 表头 + 数据行

        data 字段：
        - sheet_name: 工作表名（默认 Sheet）
        - title:      表格标题（可选，写在首行合并单元格）
        - headers:    表头列表
        - rows:       数据行列表
        - notes:      备注列表（可选，追加在表格末尾）
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "Sheet")[:31] or "Sheet"

        title = data.get("title", "")
        headers = data.get("headers", [])
        rows = data.get("rows", [])

        col_count = len(headers) if headers else (len(rows[0]) if rows else 1)
        row_cursor = 1

        # 标题行
        if title:
            ws.merge_cells(
                start_row=row_cursor, start_column=1,
                end_row=row_cursor, end_column=col_count,
            )
            cell = ws.cell(row=row_cursor, column=1, value=str(title))
            cell.font = _TITLE_FONT
            cell.fill = _TITLE_FILL
            cell.alignment = _CENTER
            ws.row_dimensions[row_cursor].height = 28
            row_cursor += 1

        # 表头
        if headers:
            for j, header in enumerate(headers, 1):
                cell = ws.cell(row=row_cursor, column=j, value=str(header))
                cell.font = _HEADER_FONT
                cell.fill = _HEADER_FILL
                cell.alignment = _CENTER
                cell.border = _THIN_BORDER
            ws.row_dimensions[row_cursor].height = 22
            row_cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=row_cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.alignment = _CENTER
                cell.border = _THIN_BORDER
            row_cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(
                start_row=row_cursor, start_column=1,
                end_row=row_cursor, end_column=col_count,
            )
            cell = ws.cell(row=row_cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            row_cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：会计凭证表
    # ===================================================================

    def _generate_voucher(self, wb: Workbook, data: dict) -> None:
        """
        会计凭证表

        data 字段：
        - sheet_name:   工作表名
        - voucher_no:   凭证编号
        - date:         凭证日期
        - abstract:     凭证摘要
        - entries:      分录表 {"headers": [...], "rows": [[摘要, 科目, 借方, 贷方]]}
        - total_debit:  借方合计
        - total_credit: 贷方合计
        - attachments:  附件张数
        - preparer / reviewer / approver: 制单 / 审核 / 记账
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "会计凭证")[:31]

        title = data.get("title", "记账凭证")
        voucher_no = data.get("voucher_no", "")
        date = data.get("date", "")

        # 标题
        ws.merge_cells("A1:F1")
        cell = ws.cell(row=1, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[1].height = 28

        # 凭证编号与日期
        ws.cell(row=2, column=1, value=f"凭证编号：{voucher_no}").font = _BODY_FONT
        ws.cell(row=2, column=4, value=f"日期：{date}").font = _BODY_FONT
        ws.cell(row=2, column=1).alignment = _LEFT
        ws.cell(row=2, column=4).alignment = _LEFT

        # 分录表
        entries = data.get("entries", {}) or {}
        headers = entries.get("headers", ["摘要", "科目名称", "借方金额", "贷方金额"])
        rows = entries.get("rows", [])

        header_row = 3
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[header_row].height = 22

        # 数据行
        cursor = header_row + 1
        for row in rows:
            for j in range(len(headers)):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                # 金额列右对齐
                if j >= 2:
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
                else:
                    cell.alignment = _LEFT
            cursor += 1

        # 合计行
        total_debit = data.get("total_debit", "")
        total_credit = data.get("total_credit", "")
        ws.cell(row=cursor, column=1, value="合计").font = _SUBTOTAL_FONT
        ws.cell(row=cursor, column=1).fill = _SUBTOTAL_FILL
        ws.cell(row=cursor, column=1).alignment = _CENTER
        ws.cell(row=cursor, column=1).border = _THIN_BORDER
        # 合并摘要与科目列
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=2)
        ws.cell(row=cursor, column=2).fill = _SUBTOTAL_FILL
        ws.cell(row=cursor, column=2).border = _THIN_BORDER
        cell_debit = ws.cell(row=cursor, column=3, value=self._normalize(total_debit))
        cell_credit = ws.cell(row=cursor, column=4, value=self._normalize(total_credit))
        for c in (cell_debit, cell_credit):
            c.font = _SUBTOTAL_FONT
            c.fill = _SUBTOTAL_FILL
            c.alignment = _RIGHT
            c.border = _THIN_BORDER
            if isinstance(c.value, (int, float)):
                c.number_format = "#,##0.00"
        # 补齐其余列边框
        for j in range(5, len(headers) + 1):
            ws.cell(row=cursor, column=j).fill = _SUBTOTAL_FILL
            ws.cell(row=cursor, column=j).border = _THIN_BORDER
        cursor += 2

        # 制单信息
        info_parts = []
        if data.get("attachments"):
            info_parts.append(f"附件：{data['attachments']} 张")
        if data.get("preparer"):
            info_parts.append(f"制单：{data['preparer']}")
        if data.get("reviewer"):
            info_parts.append(f"审核：{data['reviewer']}")
        if data.get("approver"):
            info_parts.append(f"记账：{data['approver']}")
        if info_parts:
            ws.cell(row=cursor, column=1, value="    ".join(info_parts)).font = _BODY_FONT

        self._auto_width(ws, max(len(headers), 4))

    # ===================================================================
    # 模板：库存盘点表
    # ===================================================================

    def _generate_inventory(self, wb: Workbook, data: dict) -> None:
        """
        库存盘点表

        data 字段：
        - sheet_name:   工作表名
        - title:        表标题
        - warehouse:    仓库
        - count_date:   盘点日期
        - items:        物料表 {"headers": [...], "rows": [[...]]}
        - summary:      盘点结论（可选）
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "库存盘点表")[:31]

        title = data.get("title", "库存盘点表")
        warehouse = data.get("warehouse", "")
        count_date = data.get("count_date", "")

        items = data.get("items", {}) or {}
        headers = items.get("headers", [
            "序号", "物料编码", "物料名称", "规格型号", "单位",
            "账面数量", "实盘数量", "差异", "差异原因",
        ])
        rows = items.get("rows", [])
        col_count = len(headers)

        # 标题
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=col_count)
        cell = ws.cell(row=1, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[1].height = 28

        # 仓库与日期
        ws.cell(row=2, column=1, value=f"仓库：{warehouse}").font = _BODY_FONT
        ws.cell(row=2, column=1).alignment = _LEFT
        ws.cell(row=2, column=col_count - 1, value=f"盘点日期：{count_date}").font = _BODY_FONT
        ws.cell(row=2, column=col_count - 1).alignment = _LEFT

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[3].height = 22

        # 数据行
        cursor = 4
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                cell.alignment = _CENTER
                if j >= 5 and isinstance(value, (int, float)):
                    cell.number_format = "#,##0.00"
            cursor += 1

        # 盘点结论
        summary = data.get("summary")
        if summary:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=f"盘点结论：{summary}")
            cell.font = _SUBTOTAL_FONT
            cell.fill = _SUBTOTAL_FILL
            cell.alignment = _LEFT
            cursor += 1

        # 签字栏
        ws.cell(row=cursor + 1, column=1, value="盘点人：__________").font = _BODY_FONT
        ws.cell(row=cursor + 1, column=col_count - 1, value="复核人：__________").font = _BODY_FONT

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：工资条表
    # ===================================================================

    def _generate_payroll(self, wb: Workbook, data: dict) -> None:
        """
        工资条表

        data 字段：
        - sheet_name: 工作表名
        - title:      表标题
        - month:      所属月份
        - headers:    表头（如：姓名/部门/基本工资/绩效/津贴/社保/公积金/个税/实发）
        - rows:       员工薪资行
        - total_row:  合计行（可选）
        - notes:      备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "工资条")[:31]

        title = data.get("title", "员工工资表")
        month = data.get("month", "")

        headers = data.get("headers", [])
        rows = data.get("rows", [])
        col_count = len(headers) if headers else 1
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 月份
        if month:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=f"所属月份：{month}")
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                # 姓名与部门列左对齐，金额列右对齐
                if j < 2:
                    cell.alignment = _LEFT
                else:
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
            cursor += 1

        # 合计行
        total_row = data.get("total_row")
        if total_row:
            for j in range(col_count):
                value = total_row[j] if j < len(total_row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _SUBTOTAL_FONT
                cell.fill = _SUBTOTAL_FILL
                cell.border = _THIN_BORDER
                if j < 2:
                    cell.alignment = _CENTER
                else:
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：税务申报表
    # ===================================================================

    def _generate_tax(self, wb: Workbook, data: dict) -> None:
        """
        税务申报表

        data 字段：
        - sheet_name:  工作表名
        - title:       表标题
        - tax_type:    税种（如 增值税 / 企业所得税）
        - period:      所属税期
        - taxpayer:    纳税人名称
        - tax_id:      纳税人识别号
        - headers:     表头
        - rows:        申报项目行
        - total_tax:   应纳税额合计
        - notes:       填表说明
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "税务申报表")[:31]

        title = data.get("title", "税务申报表")
        tax_type = data.get("tax_type", "")
        period = data.get("period", "")
        taxpayer = data.get("taxpayer", "")
        tax_id = data.get("tax_id", "")

        headers = data.get("headers", [])
        rows = data.get("rows", [])
        col_count = len(headers) if headers else 1
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 申报信息
        info_lines = []
        if tax_type:
            info_lines.append(f"税种：{tax_type}")
        if period:
            info_lines.append(f"税期：{period}")
        if taxpayer:
            info_lines.append(f"纳税人：{taxpayer}")
        if tax_id:
            info_lines.append(f"识别号：{tax_id}")
        if info_lines:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_lines))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                if j == 0:
                    cell.alignment = _LEFT
                else:
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
            cursor += 1

        # 应纳税额合计
        total_tax = data.get("total_tax")
        if total_tax is not None:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count - 1)
            cell = ws.cell(row=cursor, column=1, value="应纳税额合计")
            cell.font = _SUBTOTAL_FONT
            cell.fill = _SUBTOTAL_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
            for j in range(2, col_count):
                ws.cell(row=cursor, column=j).fill = _SUBTOTAL_FILL
                ws.cell(row=cursor, column=j).border = _THIN_BORDER
            cell = ws.cell(row=cursor, column=col_count, value=self._normalize(total_tax))
            cell.font = _SUBTOTAL_FONT
            cell.fill = _SUBTOTAL_FILL
            cell.alignment = _RIGHT
            cell.border = _THIN_BORDER
            if isinstance(total_tax, (int, float)):
                cell.number_format = "#,##0.00"
            cursor += 1

        # 填表说明
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：预算执行表
    # ===================================================================

    def _generate_budget(self, wb: Workbook, data: dict) -> None:
        """
        预算执行表（含预算/实际/差异列）

        data 字段：
        - sheet_name:   工作表名
        - title:        表标题
        - period:       预算周期（如 2026年Q1）
        - department:   部门
        - headers:      表头（默认 序号/预算项目/预算金额/实际金额/差异/差异率/备注）
        - rows:         数据行
        - total_row:    合计行（可选）
        - notes:        备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "预算执行表")[:31]

        title = data.get("title", "预算执行表")
        period = data.get("period", "")
        department = data.get("department", "")

        headers = data.get("headers", [
            "序号", "预算项目", "预算金额", "实际金额", "差异", "差异率", "备注",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        info_parts = []
        if department:
            info_parts.append(f"部门：{department}")
        if period:
            info_parts.append(f"预算周期：{period}")
        if info_parts:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_parts))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行（金额列右对齐）
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                if j in (2, 3, 4):  # 预算/实际/差异金额列
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
                elif j == 5:  # 差异率
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "0.00%"
                else:
                    cell.alignment = _CENTER
            cursor += 1

        # 合计行
        total_row = data.get("total_row")
        if total_row:
            for j in range(col_count):
                value = total_row[j] if j < len(total_row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _SUBTOTAL_FONT
                cell.fill = _SUBTOTAL_FILL
                cell.border = _THIN_BORDER
                if j in (2, 3, 4):
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
                elif j == 5:
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "0.00%"
                else:
                    cell.alignment = _CENTER
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：项目进度表
    # ===================================================================

    def _generate_project(self, wb: Workbook, data: dict) -> None:
        """
        项目进度表（含任务/负责人/进度/状态列）

        data 字段：
        - sheet_name:  工作表名
        - title:       表标题
        - project_name: 项目名称
        - manager:     项目经理
        - headers:     表头（默认 序号/任务名称/负责人/开始日期/结束日期/进度(%)/状态/备注）
        - rows:        数据行
        - notes:       备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "项目进度表")[:31]

        title = data.get("title", "项目进度表")
        project_name = data.get("project_name", "")
        manager = data.get("manager", "")

        headers = data.get("headers", [
            "序号", "任务名称", "负责人", "开始日期", "结束日期", "进度(%)", "状态", "备注",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        info_parts = []
        if project_name:
            info_parts.append(f"项目：{project_name}")
        if manager:
            info_parts.append(f"项目经理：{manager}")
        if info_parts:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_parts))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                if j == 5:  # 进度列右对齐
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "0"
                else:
                    cell.alignment = _CENTER
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：资产清单表
    # ===================================================================

    def _generate_asset(self, wb: Workbook, data: dict) -> None:
        """
        资产清单表（含资产编号/名称/类别/原值/折旧/净值列）

        data 字段：
        - sheet_name:  工作表名
        - title:       表标题
        - count_date:  盘点日期
        - department:  使用部门
        - headers:     表头（默认 序号/资产编号/资产名称/类别/原值/累计折旧/净值/使用部门/状态）
        - rows:        数据行
        - total_row:   合计行（可选）
        - notes:       备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "资产清单表")[:31]

        title = data.get("title", "固定资产清单表")
        count_date = data.get("count_date", "")
        department = data.get("department", "")

        headers = data.get("headers", [
            "序号", "资产编号", "资产名称", "类别", "原值", "累计折旧", "净值", "使用部门", "状态",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        info_parts = []
        if department:
            info_parts.append(f"部门：{department}")
        if count_date:
            info_parts.append(f"盘点日期：{count_date}")
        if info_parts:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_parts))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行（原值/累计折旧/净值为金额列）
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                if j in (4, 5, 6):  # 原值/累计折旧/净值
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
                else:
                    cell.alignment = _CENTER
            cursor += 1

        # 合计行
        total_row = data.get("total_row")
        if total_row:
            for j in range(col_count):
                value = total_row[j] if j < len(total_row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _SUBTOTAL_FONT
                cell.fill = _SUBTOTAL_FILL
                cell.border = _THIN_BORDER
                if j in (4, 5, 6):
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0.00"
                else:
                    cell.alignment = _CENTER
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：客户清单表
    # ===================================================================

    def _generate_customer(self, wb: Workbook, data: dict) -> None:
        """
        客户清单表（含客户编号/名称/联系人/电话/邮箱/等级列）

        data 字段：
        - sheet_name: 工作表名
        - title:      表标题
        - region:     区域
        - salesperson: 负责销售
        - headers:    表头（默认 客户编号/客户名称/联系人/电话/邮箱/等级/地址）
        - rows:       数据行
        - notes:      备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "客户清单")[:31]

        title = data.get("title", "客户清单")
        region = data.get("region", "")
        salesperson = data.get("salesperson", "")

        headers = data.get("headers", [
            "客户编号", "客户名称", "联系人", "电话", "邮箱", "等级", "地址",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        info_parts = []
        if region:
            info_parts.append(f"区域：{region}")
        if salesperson:
            info_parts.append(f"负责销售：{salesperson}")
        if info_parts:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_parts))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                cell.alignment = _CENTER
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：员工花名册
    # ===================================================================

    def _generate_employee(self, wb: Workbook, data: dict) -> None:
        """
        员工花名册（含工号/姓名/部门/职位/入职日期/状态列）

        data 字段：
        - sheet_name: 工作表名
        - title:      表标题
        - department: 部门
        - count_date: 统计日期
        - headers:    表头（默认 工号/姓名/部门/职位/入职日期/状态/联系方式）
        - rows:       数据行
        - notes:      备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "员工花名册")[:31]

        title = data.get("title", "员工花名册")
        department = data.get("department", "")
        count_date = data.get("count_date", "")

        headers = data.get("headers", [
            "工号", "姓名", "部门", "职位", "入职日期", "状态", "联系方式",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        info_parts = []
        if department:
            info_parts.append(f"部门：{department}")
        if count_date:
            info_parts.append(f"统计日期：{count_date}")
        if info_parts:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_parts))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                cell.alignment = _CENTER
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：供应商清单
    # ===================================================================

    def _generate_supplier(self, wb: Workbook, data: dict) -> None:
        """
        供应商清单（含编号/名称/联系人/电话/评级列）

        data 字段：
        - sheet_name: 工作表名
        - title:      表标题
        - category:   供应类别
        - headers:    表头（默认 供应商编号/供应商名称/联系人/电话/评级/合作状态/主营产品）
        - rows:       数据行
        - notes:      备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "供应商清单")[:31]

        title = data.get("title", "供应商清单")
        category = data.get("category", "")

        headers = data.get("headers", [
            "供应商编号", "供应商名称", "联系人", "电话", "评级", "合作状态", "主营产品",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        if category:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=f"供应类别：{category}")
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                cell.alignment = _CENTER
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：质量检测表
    # ===================================================================

    def _generate_quality(self, wb: Workbook, data: dict) -> None:
        """
        质量检测表（含批次/检测项/标准值/实测值/结果列）

        data 字段：
        - sheet_name:   工作表名
        - title:        表标题
        - product:      产品名称
        - test_date:    检测日期
        - headers:      表头（默认 批次号/检测项目/标准值/实测值/判定结果/检测人/检测日期）
        - rows:         数据行
        - pass_count:   合格数（可选）
        - fail_count:   不合格数（可选）
        - notes:        备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "质量检测表")[:31]

        title = data.get("title", "质量检测表")
        product = data.get("product", "")
        test_date = data.get("test_date", "")

        headers = data.get("headers", [
            "批次号", "检测项目", "标准值", "实测值", "判定结果", "检测人", "检测日期",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        info_parts = []
        if product:
            info_parts.append(f"产品：{product}")
        if test_date:
            info_parts.append(f"检测日期：{test_date}")
        if info_parts:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_parts))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                cell.alignment = _CENTER
            cursor += 1

        # 检测结论
        pass_count = data.get("pass_count")
        fail_count = data.get("fail_count")
        if pass_count is not None or fail_count is not None:
            summary_parts = []
            if pass_count is not None:
                summary_parts.append(f"合格：{pass_count}")
            if fail_count is not None:
                summary_parts.append(f"不合格：{fail_count}")
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="检测结论：" + "，".join(summary_parts))
            cell.font = _SUBTOTAL_FONT
            cell.fill = _SUBTOTAL_FILL
            cell.alignment = _LEFT
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 模板：生产计划表
    # ===================================================================

    def _generate_production(self, wb: Workbook, data: dict) -> None:
        """
        生产计划表（含产品/计划量/实际量/完成率/状态列）

        data 字段：
        - sheet_name:  工作表名
        - title:       表标题
        - workshop:    车间
        - period:      生产周期
        - headers:     表头（默认 序号/产品名称/规格/计划产量/实际产量/完成率(%)/状态/备注）
        - rows:        数据行
        - total_row:   合计行（可选）
        - notes:       备注列表
        """
        ws = wb.active
        ws.title = data.get("sheet_name", "生产计划表")[:31]

        title = data.get("title", "生产计划表")
        workshop = data.get("workshop", "")
        period = data.get("period", "")

        headers = data.get("headers", [
            "序号", "产品名称", "规格", "计划产量", "实际产量", "完成率(%)", "状态", "备注",
        ])
        rows = data.get("rows", [])
        col_count = len(headers)
        cursor = 1

        # 标题
        ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
        cell = ws.cell(row=cursor, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.fill = _TITLE_FILL
        cell.alignment = _CENTER
        ws.row_dimensions[cursor].height = 28
        cursor += 1

        # 信息行
        info_parts = []
        if workshop:
            info_parts.append(f"车间：{workshop}")
        if period:
            info_parts.append(f"周期：{period}")
        if info_parts:
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value="    ".join(info_parts))
            cell.font = _BODY_FONT
            cell.alignment = _LEFT
            cursor += 1

        # 表头
        for j, header in enumerate(headers, 1):
            cell = ws.cell(row=cursor, column=j, value=str(header))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _CENTER
            cell.border = _THIN_BORDER
        ws.row_dimensions[cursor].height = 22
        cursor += 1

        # 数据行（产量列右对齐）
        for row in rows:
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                if j in (3, 4):  # 计划产量/实际产量
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0"
                elif j == 5:  # 完成率
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "0.00"
                else:
                    cell.alignment = _CENTER
            cursor += 1

        # 合计行
        total_row = data.get("total_row")
        if total_row:
            for j in range(col_count):
                value = total_row[j] if j < len(total_row) else ""
                cell = ws.cell(row=cursor, column=j + 1, value=self._normalize(value))
                cell.font = _SUBTOTAL_FONT
                cell.fill = _SUBTOTAL_FILL
                cell.border = _THIN_BORDER
                if j in (3, 4):
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "#,##0"
                elif j == 5:
                    cell.alignment = _RIGHT
                    if isinstance(value, (int, float)):
                        cell.number_format = "0.00"
                else:
                    cell.alignment = _CENTER
            cursor += 1

        # 备注
        for note in data.get("notes", []):
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=col_count)
            cell = ws.cell(row=cursor, column=1, value=str(note))
            cell.font = _NOTE_FONT
            cell.alignment = _LEFT
            cursor += 1

        self._auto_width(ws, col_count)

    # ===================================================================
    # 辅助方法
    # ===================================================================

    @staticmethod
    def _normalize(value: Any) -> Any:
        """规范化单元格值：None → 空字符串，其余原样返回"""
        if value is None:
            return ""
        return value

    @staticmethod
    def _auto_width(ws, col_count: int, min_width: int = 10, max_width: int = 40) -> None:
        """自动调整列宽（基于表头与内容估算）"""
        for j in range(1, col_count + 1):
            max_len = 0
            for row in ws.iter_rows(min_col=j, max_col=j, values_only=True):
                for cell_value in row:
                    if cell_value is None:
                        continue
                    # 中文按 2 个字符宽度估算
                    text = str(cell_value)
                    length = sum(2 if ord(c) > 127 else 1 for c in text)
                    if length > max_len:
                        max_len = length
            width = min(max(max_len + 4, min_width), max_width)
            ws.column_dimensions[get_column_letter(j)].width = width


# 全局单例
xlsx_generator = XlsxGenerator()
