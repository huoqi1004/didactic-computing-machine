"""Office 文档生成器（M8 · docx / xlsx / pptx / md）。

由 LLM 产出结构化 JSON，本模块渲染为真实文件（商务风配色）：
- ``build_docx(path, spec)``：spec = {title, subtitle?, sections: [{heading, body}]}
- ``build_xlsx(path, spec)``：spec = {title?, sheets: [{name, headers: [], rows: [[]]}]}
- ``build_pptx(path, spec)``：spec = {title, subtitle?, slides: [{title, bullets: []}]}
- ``build_md(path, spec)``：spec = {title, body}（body 为 Markdown 文本）
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger()

NAVY = (0x1D, 0x4E, 0xD8)
SKY = (0x0E, 0xA5, 0xE9)


def build_docx(path: str, spec: dict) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc = Document()
    title = doc.add_heading(spec.get("title", "文档"), level=0)
    for run in title.runs:
        run.font.color.rgb = RGBColor(*NAVY)
    if spec.get("subtitle"):
        p = doc.add_paragraph(spec["subtitle"])
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            run.font.size = Pt(12)
            run.font.color.rgb = RGBColor(*SKY)
    for sec in spec.get("sections", []):
        doc.add_heading(sec.get("heading", ""), level=1)
        for para in str(sec.get("body", "")).split("\n"):
            para = para.strip()
            if not para:
                continue
            if para.startswith(("- ", "• ")):
                doc.add_paragraph(para[2:], style="List Bullet")
            elif para[:2] in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."):
                doc.add_paragraph(para[3:], style="List Number")
            else:
                doc.add_paragraph(para)
    doc.add_paragraph("—— 本文档由企业数字化大脑智能体生成")
    doc.save(path)


def build_xlsx(path: str, spec: dict) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    header_fill = PatternFill("solid", fgColor="1D4ED8")
    header_font = Font(color="FFFFFF", bold=True)
    for sheet_spec in spec.get("sheets", []):
        ws = wb.create_sheet(str(sheet_spec.get("name", "Sheet"))[:31])
        headers = sheet_spec.get("headers", [])
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=str(h))
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for r, row in enumerate(sheet_spec.get("rows", []), 2):
            for c, v in enumerate(row, 1):
                if isinstance(v, str) and v.replace(".", "", 1).replace(",", "").isdigit():
                    v = float(v.replace(",", "")) if "." in v else int(v.replace(",", ""))
                ws.cell(row=r, column=c, value=v)
        # 列宽自适应（采样前 30 行）
        for c in range(1, len(headers) + 1):
            width = max([len(str(ws.cell(row=r, column=c).value or ""))
                         for r in range(1, min(ws.max_row, 30) + 1)] + [8]) + 4
            ws.column_dimensions[get_column_letter(c)].width = min(width, 50)
        ws.freeze_panes = "A2"
    wb.save(path)


def build_pptx(path: str, spec: dict) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    prs = Presentation()
    navy = RGBColor(*NAVY)
    sky = RGBColor(*SKY)
    white = RGBColor(0xFF, 0xFF, 0xFF)
    dark = RGBColor(0x0F, 0x1F, 0x3D)

    # 封面
    cover = prs.slides.add_slide(prs.slide_layouts[6])
    bar = cover.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width, Inches(0.25))
    bar.fill.solid(); bar.fill.fore_color.rgb = sky; bar.line.fill.background()
    box = cover.shapes.add_textbox(Inches(0.8), Inches(2.2), Inches(8.4), Inches(1.4))
    tf = box.text_frame; tf.text = str(spec.get("title", "演示文稿"))
    p = tf.paragraphs[0]
    p.font.size = Pt(40); p.font.bold = True; p.font.color.rgb = navy
    if spec.get("subtitle"):
        sub = cover.shapes.add_textbox(Inches(0.85), Inches(3.6), Inches(8.4), Inches(0.6))
        sp = sub.text_frame.paragraphs[0]
        sp.text = str(spec["subtitle"])
        sp.font.size = Pt(16); sp.font.color.rgb = sky

    for s in spec.get("slides", []):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        accent = slide.shapes.add_shape(1, Inches(0.5), Inches(0.55), Inches(0.12), Inches(0.5))
        accent.fill.solid(); accent.fill.fore_color.rgb = sky; accent.line.fill.background()
        head = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(8.4), Inches(0.7))
        hp = head.text_frame.paragraphs[0]
        hp.text = str(s.get("title", ""))
        hp.font.size = Pt(26); hp.font.bold = True; hp.font.color.rgb = dark
        body = slide.shapes.add_textbox(Inches(0.85), Inches(1.45), Inches(8.3), Inches(4.8))
        btf = body.text_frame
        btf.word_wrap = True
        for i, bullet in enumerate(s.get("bullets", [])[:10]):
            bp = btf.paragraphs[0] if i == 0 else btf.add_paragraph()
            bp.text = f"▪ {str(bullet)}"
            bp.font.size = Pt(15); bp.font.color.rgb = dark
            bp.space_after = Pt(8)
    prs.save(path)


def build_md(path: str, spec: dict) -> None:
    body = str(spec.get("body", ""))
    if not body.lstrip().startswith("#"):
        body = f"# {spec.get('title', '文档')}\n\n{body}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def build_csv(path: str, spec: dict) -> None:
    """CSV 生成（utf-8-sig BOM，Excel 直接打开不乱码）。"""
    import csv as _csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.writer(f)
        w.writerow(spec.get("headers", []))
        for row in spec.get("rows", []):
            w.writerow(row)


_CJK_FONT_CACHE: str | None = None


def _cjk_font() -> str | None:
    """注册系统中文字体（微软雅黑→黑体→宋体），返回字体名或 None。"""
    global _CJK_FONT_CACHE
    if _CJK_FONT_CACHE is not None:
        return _CJK_FONT_CACHE
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, f, idx in [("MSYaHei", "C:/Windows/Fonts/msyh.ttc", 0),
                         ("SimHei", "C:/Windows/Fonts/simhei.ttf", 0),
                         ("SimSun", "C:/Windows/Fonts/simsun.ttc", 0)]:
        try:
            if not __import__("os").path.exists(f):
                continue
            pdfmetrics.registerFont(TTFont(name, f, subfontIndex=idx))
            _CJK_FONT_CACHE = name
            return name
        except Exception:
            continue
    _CJK_FONT_CACHE = ""
    return None


def build_pdf(path: str, spec: dict) -> None:
    """PDF 生成（platypus 流式排版，中文字体自动注册）。"""
    path = str(path)  # reportlab 不接受 Path 对象
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    font = _cjk_font() or "Helvetica"
    navy = colors.HexColor("#1D4ED8")
    sky = colors.HexColor("#0EA5E9")
    ink = colors.HexColor("#0F1F3D")

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=str(spec.get("title", "文档")))
    title_style = ParagraphStyle("T", fontName=font, fontSize=20,
                                 textColor=navy, leading=28, spaceAfter=6)
    sub_style = ParagraphStyle("S", fontName=font, fontSize=11,
                               textColor=sky, leading=16, spaceAfter=14)
    head_style = ParagraphStyle("H", fontName=font, fontSize=14,
                                textColor=ink, leading=20,
                                spaceBefore=12, spaceAfter=6)
    body_style = ParagraphStyle("B", fontName=font, fontSize=10.5,
                                textColor=ink, leading=17, wordWrap="CJK")
    story = [Paragraph(str(spec.get("title", "文档")), title_style)]
    if spec.get("subtitle"):
        story.append(Paragraph(str(spec["subtitle"]), sub_style))
    for sec in spec.get("sections", []):
        if sec.get("heading"):
            story.append(Paragraph(str(sec["heading"]), head_style))
        for para in str(sec.get("body", "")).split("\n"):
            para = para.strip()
            if not para:
                continue
            story.append(Paragraph(para, body_style))
        story.append(Spacer(1, 4))
    story.append(Spacer(1, 16))
    story.append(Paragraph("—— 本文档由企业数字化大脑智能体生成", sub_style))
    doc.build(story)


BUILDERS = {"docx": build_docx, "xlsx": build_xlsx, "pptx": build_pptx,
            "md": build_md, "csv": build_csv, "pdf": build_pdf}
SUFFIX = {"docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "md": ".md",
          "csv": ".csv", "pdf": ".pdf"}

# 各格式 → LLM 结构化产出要求
FORMAT_PROMPTS = {
    "docx": ('输出 JSON：{"title": "...", "subtitle": "...", '
             '"sections": [{"heading": "章节标题", "body": "正文段落，用\\n分段，可用 - 表示要点"}]}，'
             '章节 3-6 个，正文专业完整。'),
    "xlsx": ('输出 JSON：{"sheets": [{"name": "工作表名", "headers": ["列1","列2"], '
             '"rows": [["值", ...], ...]}]}，至少 1 个工作表，'
             '行数 8-25 行，数据要贴合主题且数值合理。'),
    "pptx": ('输出 JSON：{"title": "演示标题", "subtitle": "副标题", '
             '"slides": [{"title": "页标题", "bullets": ["要点1", "要点2", "要点3"]}]},'
             '共 5-8 页（不含封面），每页 3-6 个要点。'),
    "md": ('输出 JSON：{"title": "标题", "body": "完整 Markdown 正文，可用标题/列表/表格"}。'),
    "csv": ('输出 JSON：{"headers": ["列1","列2","列3"], "rows": [["值", ...], ...]}，'
            '行数 10-30 行，数据贴合主题且数值合理。'),
    "pdf": ('输出 JSON：{"title": "...", "subtitle": "...", '
            '"sections": [{"heading": "章节标题", "body": "正文段落，用\\n分段，可用 - 表示要点"}]}，'
            '章节 3-6 个，正文专业完整。'),
}
