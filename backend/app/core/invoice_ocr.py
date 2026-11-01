"""PDF 发票识别（M13 · 文本层提取 + LLM 结构化）。

策略：国内数电票/电子发票 PDF 绝大多数携带文本层（非扫描件）——
pdfplumber 提取全文 → LLM 结构化为发票要素 → 自动创建资料库发票记录。
无文本层的扫描件返回明确提示（OCR 服务选型另立项）。
"""
from __future__ import annotations

import json

import structlog

logger = structlog.get_logger()

INVOICE_FIELDS = ("发票号码", "开票日期", "购买方名称", "销售方名称",
                  "价税合计", "金额", "税额", "发票类型")


def extract_pdf_text(data: bytes) -> str:
    """提取 PDF 文本层（前 3 页足够覆盖票面）。"""
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages[:3]]
        text = "\n".join(t for t in pages if t.strip())
        return text[:4000]
    except Exception as e:
        logger.warning("invoice.extract_failed", error=str(e))
        return ""


async def structure_invoice(text: str, llm_call) -> dict | None:
    """LLM 结构化发票要素。llm_call(messages, system) -> (text, tokens)。"""
    if not text or len(text) < 30:
        return None
    prompt = ("从以下发票 PDF 文本中提取要素，输出 JSON（缺失字段填空字符串）：\n"
              '{"发票号码": "...", "开票日期": "YYYY-MM-DD", "发票类型": "增值税专用发票/普通发票/数电票",'
              ' "购买方名称": "...", "销售方名称": "...", "金额": "不含税金额", "税额": "...",'
              ' "价税合计": "...", "备注": "..."}\n\n发票文本：\n' + text[:3000])
    try:
        reply, _ = await llm_call(
            [{"role": "user", "content": prompt}],
            system="你是发票要素结构化引擎，只输出合法 JSON。金额/税额/价税合计保留数字。")
        start, end = reply.find("{"), reply.rfind("}")
        if start == -1 or end <= start:
            return None
        data = json.loads(reply[start:end + 1])
        if not any(data.get(f) for f in INVOICE_FIELDS):
            return None
        return data
    except Exception as e:
        logger.warning("invoice.structure_failed", error=str(e))
        return None


def is_scanned_only(data: bytes) -> bool:
    """整份 PDF 均无文本层 → 判定扫描件。"""
    return not extract_pdf_text(data).strip()
