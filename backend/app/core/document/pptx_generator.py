"""
PowerPoint 演示文稿生成器 (PowerPoint Generator)
=================================================

基于 python-pptx 生成 .pptx 标准办公演示文稿，供各业务 Agent 调用。

支持的模板类型（template_type）：
1. basic              - 基础演示文稿（标题页 + 内容页）
2. business_analysis  - 经营分析 PPT（图表 + 数据表格）
3. status_report      - 状态汇报 PPT（概览页/关键指标页/风险页/计划页）

表格数据统一格式：
    {"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}

图表数据格式：
    {"type": "bar"|"line"|"pie", "title": "...",
     "categories": ["Q1", "Q2"],
     "series": [{"name": "...", "values": [1, 2]}]}

使用方式::

    from app.core.document.pptx_generator import pptx_generator

    path = pptx_generator.generate(
        template_type="basic",
        data={"title": "示例", "slides": [{"title": "页1", "bullets": ["a", "b"]}]},
        filename="example.pptx",
    )
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

import structlog
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

logger = structlog.get_logger()

# 中文默认字体
_DEFAULT_FONT = "微软雅黑"
_TITLE_FONT = "微软雅黑"

# 图表类型映射
_CHART_TYPE_MAP = {
    "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,    # 柱状图
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED, # 柱状图
    "line": XL_CHART_TYPE.LINE_MARKERS,       # 折线图
    "pie": XL_CHART_TYPE.PIE,                 # 饼图
    "area": XL_CHART_TYPE.AREA,               # 面积图
}


class PptxGenerator:
    """
    PowerPoint 演示文稿生成器

    通过 ``generate(template_type, data, filename)`` 统一入口，
    根据模板类型分发到对应的私有生成方法。
    """

    SUPPORTED_TEMPLATES = {
        "basic": "_generate_basic",
        "business_analysis": "_generate_business_analysis",
        "status_report": "_generate_status_report",
    }

    def generate(self, template_type: str, data: dict, filename: str) -> str:
        """
        生成 PowerPoint 演示文稿。

        Args:
            template_type: 模板类型（basic/business_analysis/status_report）
            data:           文稿所需结构化数据
            filename:       输出文件名（不带路径）

        Returns:
            生成的文件绝对路径

        Raises:
            ValueError: 模板类型不支持或生成失败
        """
        method_name = self.SUPPORTED_TEMPLATES.get(template_type)
        if method_name is None:
            raise ValueError(
                f"不支持的 PowerPoint 模板类型: {template_type}，"
                f"支持: {list(self.SUPPORTED_TEMPLATES.keys())}"
            )

        if not filename.endswith(".pptx"):
            filename = f"{filename}.pptx"
        output_path = os.path.join(tempfile.gettempdir(), filename)

        try:
            prs = Presentation()
            # 使用 16:9 宽屏布局
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

            method = getattr(self, method_name)
            method(prs, data or {})

            prs.save(output_path)
            logger.info(
                "pptx.generated",
                template_type=template_type,
                filename=filename,
                path=output_path,
                slides=len(prs.slides),
            )
            return output_path
        except ValueError:
            raise
        except Exception as e:
            logger.error(
                "pptx.generate_failed",
                template_type=template_type,
                filename=filename,
                error=str(e),
            )
            raise ValueError(f"PowerPoint 文稿生成失败（{template_type}）: {e}") from e

    # ===================================================================
    # 模板：基础演示文稿
    # ===================================================================

    def _generate_basic(self, prs: Presentation, data: dict) -> None:
        """
        基础演示文稿：标题页 + 内容页

        data 字段：
        - title:    封面标题
        - subtitle: 封面副标题
        - author:   作者 / 汇报人
        - date:     日期
        - slides:   内容页列表，每页支持：
            {"title": "...", "content": "...", "bullets": ["...", "..."],
             "table": {"headers": [...], "rows": [[...]]}}
        """
        # 封面
        self._add_title_slide(
            prs,
            title=data.get("title", "演示文稿"),
            subtitle=data.get("subtitle", ""),
            author=data.get("author", ""),
            date=data.get("date", ""),
        )

        # 内容页
        for slide_data in data.get("slides", []):
            self._add_content_slide(prs, slide_data)

    # ===================================================================
    # 模板：经营分析 PPT
    # ===================================================================

    def _generate_business_analysis(self, prs: Presentation, data: dict) -> None:
        """
        经营分析 PPT：图表 + 数据表格

        data 字段：
        - title:    封面标题
        - subtitle: 封面副标题
        - period:   分析周期
        - author:   编制人
        - slides:   内容页列表，每页支持：
            {"title": "...", "content": "...", "bullets": [...],
             "table": {...}, "chart": {...}, "layout": "content"|"table"|"chart"|"split"}
        - summary:  总结页文本
        """
        # 封面
        subtitle = data.get("subtitle", "")
        if data.get("period"):
            subtitle = f"{subtitle}（{data['period']}）" if subtitle else data["period"]
        self._add_title_slide(
            prs,
            title=data.get("title", "经营分析报告"),
            subtitle=subtitle,
            author=data.get("author", ""),
            date=data.get("date", ""),
        )

        # 内容页
        for slide_data in data.get("slides", []):
            layout = slide_data.get("layout", "auto")
            if layout == "chart" or (layout == "auto" and slide_data.get("chart") and not slide_data.get("table")):
                self._add_chart_slide(prs, slide_data)
            elif layout == "split" or (layout == "auto" and slide_data.get("chart") and slide_data.get("table")):
                self._add_split_slide(prs, slide_data)
            else:
                self._add_content_slide(prs, slide_data)

        # 总结页
        if data.get("summary"):
            self._add_content_slide(prs, {
                "title": "总结与建议",
                "content": data["summary"],
            })

        # 致谢页
        self._add_title_slide(
            prs,
            title="谢谢",
            subtitle="Thank You",
        )

    # ===================================================================
    # 模板：状态汇报 PPT
    # ===================================================================

    def _generate_status_report(self, prs: Presentation, data: dict) -> None:
        """
        状态汇报 PPT：概览页/关键指标页/风险页/计划页

        data 字段：
        - title:     封面标题
        - subtitle:  封面副标题
        - period:    汇报周期
        - author:    汇报人
        - date:      汇报日期
        - overview:  概览页内容
            {"content": "...", "bullets": [...], "status": "进行中"}
        - metrics:   关键指标页内容
            {"content": "...", "bullets": [...],
             "table": {"headers": [...], "rows": [[...]]},
             "chart": {"type": "bar", "title": "...", "categories": [...], "series": [...]}}
        - risks:     风险页内容
            {"content": "...", "bullets": [...],
             "table": {"headers": ["风险项","等级","影响","应对措施"], "rows": [[...]]}}
        - plan:      计划页内容
            {"content": "...", "bullets": [...],
             "table": {"headers": ["事项","负责人","截止日期"], "rows": [[...]]}}
        - summary:   总结页文本（可选）
        """
        # 封面
        subtitle = data.get("subtitle", "")
        if data.get("period"):
            subtitle = f"{subtitle}（{data['period']}）" if subtitle else data["period"]
        self._add_title_slide(
            prs,
            title=data.get("title", "状态汇报"),
            subtitle=subtitle,
            author=data.get("author", ""),
            date=data.get("date", ""),
        )

        # 概览页
        overview = data.get("overview", {})
        if overview:
            self._add_content_slide(prs, {
                "title": "一、项目概览",
                "content": overview.get("content", ""),
                "bullets": overview.get("bullets", []),
                "table": overview.get("table"),
            })

        # 关键指标页
        metrics = data.get("metrics", {})
        if metrics:
            slide_data = {
                "title": "二、关键指标",
                "content": metrics.get("content", ""),
                "bullets": metrics.get("bullets", []),
            }
            # 若同时有图表和表格，使用分栏布局；否则用普通内容页
            if metrics.get("chart") and metrics.get("table"):
                slide_data["chart"] = metrics["chart"]
                slide_data["table"] = metrics["table"]
                self._add_split_slide(prs, slide_data)
            elif metrics.get("chart"):
                slide_data["chart"] = metrics["chart"]
                self._add_chart_slide(prs, slide_data)
            else:
                if metrics.get("table"):
                    slide_data["table"] = metrics["table"]
                self._add_content_slide(prs, slide_data)

        # 风险页
        risks = data.get("risks", {})
        if risks:
            self._add_content_slide(prs, {
                "title": "三、风险与应对",
                "content": risks.get("content", ""),
                "bullets": risks.get("bullets", []),
                "table": risks.get("table"),
            })

        # 计划页
        plan = data.get("plan", {})
        if plan:
            self._add_content_slide(prs, {
                "title": "四、下阶段计划",
                "content": plan.get("content", ""),
                "bullets": plan.get("bullets", []),
                "table": plan.get("table"),
            })

        # 总结页
        if data.get("summary"):
            self._add_content_slide(prs, {
                "title": "总结",
                "content": data["summary"],
            })

        # 致谢页
        self._add_title_slide(
            prs,
            title="谢谢",
            subtitle="Thank You",
        )

    # ===================================================================
    # 幻灯片生成方法
    # ===================================================================

    def _add_title_slide(
        self,
        prs: Presentation,
        title: str,
        subtitle: str = "",
        author: str = "",
        date: str = "",
    ) -> None:
        """添加标题（封面）页"""
        slide_layout = prs.slide_layouts[6]  # 空白布局
        slide = prs.slides.add_slide(slide_layout)

        # 主标题
        title_box = slide.shapes.add_textbox(
            Inches(1), Inches(2.2), Inches(11.333), Inches(1.8)
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = str(title)
        run.font.name = _TITLE_FONT
        run.font.size = Pt(44)
        run.font.bold = True

        # 副标题
        if subtitle:
            sub_box = slide.shapes.add_textbox(
                Inches(1), Inches(4.0), Inches(11.333), Inches(1.0)
            )
            tf = sub_box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            run = p.add_run()
            run.text = str(subtitle)
            run.font.name = _DEFAULT_FONT
            run.font.size = Pt(24)

        # 作者与日期
        footer_parts = [p for p in (author, date) if p]
        if footer_parts:
            foot_box = slide.shapes.add_textbox(
                Inches(1), Inches(5.8), Inches(11.333), Inches(0.8)
            )
            tf = foot_box.text_frame
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            run = p.add_run()
            run.text = "    ".join(footer_parts)
            run.font.name = _DEFAULT_FONT
            run.font.size = Pt(18)

    def _add_content_slide(self, prs: Presentation, slide_data: dict) -> None:
        """添加内容页（标题 + 文本 + 可选表格）"""
        slide_layout = prs.slide_layouts[6]  # 空白布局
        slide = prs.slides.add_slide(slide_layout)

        # 标题
        title = slide_data.get("title", "")
        self._add_slide_title(slide, title)

        # 内容区域
        bullets = slide_data.get("bullets", [])
        content = slide_data.get("content", "")
        table_data = slide_data.get("table")

        if table_data:
            # 表格 + 文本上下分布
            text_top = Inches(1.5)
            text_height = Inches(1.2)
            if content or bullets:
                self._add_textbox(
                    slide, Inches(0.6), text_top, Inches(12.1), text_height,
                    content=content, bullets=bullets,
                )
                table_top = Inches(2.9)
            else:
                table_top = Inches(1.6)
            self._add_table(
                slide, Inches(0.6), table_top, Inches(12.1), Inches(5.0),
                table_data,
            )
        else:
            # 纯文本
            self._add_textbox(
                slide, Inches(0.8), Inches(1.7), Inches(11.7), Inches(5.3),
                content=content, bullets=bullets,
            )

    def _add_chart_slide(self, prs: Presentation, slide_data: dict) -> None:
        """添加图表页"""
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        self._add_slide_title(slide, slide_data.get("title", ""))

        chart_data_def = slide_data.get("chart")
        if chart_data_def:
            self._add_chart(
                slide,
                Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.5),
                chart_data_def,
            )

        # 图表下方的说明文字
        content = slide_data.get("content", "")
        bullets = slide_data.get("bullets", [])
        if content or bullets:
            self._add_textbox(
                slide, Inches(0.8), Inches(6.6), Inches(11.7), Inches(0.7),
                content=content, bullets=bullets, font_size=14,
            )

    def _add_split_slide(self, prs: Presentation, slide_data: dict) -> None:
        """添加图表 + 表格左右分布页"""
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        self._add_slide_title(slide, slide_data.get("title", ""))

        chart_data_def = slide_data.get("chart")
        table_data = slide_data.get("table")

        if chart_data_def:
            self._add_chart(
                slide,
                Inches(0.4), Inches(1.6), Inches(6.8), Inches(5.5),
                chart_data_def,
            )
        if table_data:
            self._add_table(
                slide, Inches(7.4), Inches(1.6), Inches(5.6), Inches(5.0),
                table_data,
            )

    # ===================================================================
    # 辅助方法
    # ===================================================================

    def _add_slide_title(self, slide, title: str) -> None:
        """为幻灯片添加顶部标题"""
        if not title:
            return
        box = slide.shapes.add_textbox(
            Inches(0.4), Inches(0.3), Inches(12.5), Inches(1.0)
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = str(title)
        run.font.name = _TITLE_FONT
        run.font.size = Pt(32)
        run.font.bold = True

    def _add_textbox(
        self,
        slide,
        left: Inches,
        top: Inches,
        width: Inches,
        height: Inches,
        content: str = "",
        bullets: list | None = None,
        font_size: int = 20,
    ) -> None:
        """添加文本框，支持正文与项目符号列表"""
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.TOP

        first = True
        # 正文段落
        if content:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            p.alignment = PP_ALIGN.LEFT
            run = p.add_run()
            run.text = str(content)
            run.font.name = _DEFAULT_FONT
            run.font.size = Pt(font_size)

        # 项目符号
        for bullet in bullets or []:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            p.alignment = PP_ALIGN.LEFT
            p.level = 0
            run = p.add_run()
            run.text = f"• {bullet}"
            run.font.name = _DEFAULT_FONT
            run.font.size = Pt(font_size)
            p.space_after = Pt(8)

    def _add_table(
        self,
        slide,
        left: Inches,
        top: Inches,
        width: Inches,
        height: Inches,
        table_data: dict,
    ) -> None:
        """添加表格

        table_data 格式：{"headers": ["列1", "列2"], "rows": [["值1", "值2"]]}
        """
        headers = table_data.get("headers", [])
        rows = table_data.get("rows", [])
        if not headers and not rows:
            return

        col_count = len(headers) if headers else (len(rows[0]) if rows else 1)
        row_count = len(rows) + (1 if headers else 0)
        if row_count == 0:
            return

        graphic = slide.shapes.add_table(
            row_count, col_count, left, top, width, height
        )
        table = graphic.table

        # 设置首行为表头行高
        if headers:
            table.rows[0].height = Inches(0.45)

        # 表头
        if headers:
            for j, header in enumerate(headers):
                cell = table.cell(0, j)
                cell.text = ""
                p = cell.text_frame.paragraphs[0]
                p.alignment = PP_ALIGN.CENTER
                run = p.add_run()
                run.text = str(header)
                run.font.name = _DEFAULT_FONT
                run.font.size = Pt(14)
                run.font.bold = True

        # 数据行
        for i, row in enumerate(rows):
            table.rows[i + (1 if headers else 0)].height = Inches(0.4)
            for j in range(col_count):
                value = row[j] if j < len(row) else ""
                cell = table.cell(i + (1 if headers else 0), j)
                cell.text = ""
                p = cell.text_frame.paragraphs[0]
                p.alignment = PP_ALIGN.CENTER
                run = p.add_run()
                run.text = str(value)
                run.font.name = _DEFAULT_FONT
                run.font.size = Pt(13)

    def _add_chart(
        self,
        slide,
        left: Inches,
        top: Inches,
        width: Inches,
        height: Inches,
        chart_data_def: dict,
    ) -> None:
        """添加图表

        chart_data_def 格式：
            {"type": "bar"|"line"|"pie", "title": "...",
             "categories": ["Q1", "Q2"],
             "series": [{"name": "...", "values": [1, 2]}]}
        """
        chart_type_key = chart_data_def.get("type", "bar")
        chart_type = _CHART_TYPE_MAP.get(chart_type_key, XL_CHART_TYPE.COLUMN_CLUSTERED)

        # 构建图表数据
        chart_data = CategoryChartData()
        chart_data.categories = chart_data_def.get("categories", [])
        for series in chart_data_def.get("series", []):
            chart_data.add_series(
                series.get("name", "系列"),
                series.get("values", []),
            )

        graphic = slide.shapes.add_chart(
            chart_type, left, top, width, height, chart_data
        )
        chart = graphic.chart
        chart.has_title = True
        chart.chart_title.text_frame.text = chart_data_def.get("title", "")
        # 设置标题字体
        for run in chart.chart_title.text_frame.paragraphs[0].runs:
            run.font.name = _DEFAULT_FONT
            run.font.size = Pt(18)
            run.font.bold = True

        # 图例
        if chart_type_key != "pie":
            chart.has_legend = True
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False
        else:
            chart.has_legend = True
            chart.legend.position = XL_LEGEND_POSITION.RIGHT
            chart.legend.include_in_layout = False


# 全局单例
pptx_generator = PptxGenerator()
