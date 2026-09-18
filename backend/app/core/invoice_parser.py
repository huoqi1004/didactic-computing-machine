"""
发票 PDF 识别解析（财务模块 · 报销管理发票上传识别）
====================================================

用途：报销表单上传发票 PDF 后，解析票面固定要素（发票种类、购买方抬头、
抬头类型、纳税人识别号、开票日期、发票号码/代码、价税合计、销售方、备注），
生成报销表单预填建议（类别/金额/事由/附票状态），减少手工填写。

技术说明：
- 解析基于 PDF **文本层**（数电票/电子普票/电子专票均为税务局开票服务
  生成的带文本层 PDF）。扫描件（纯图像 PDF）无文本层，返回低置信度结果
  并提示人工填写；外部 OCR 服务可后续接入 ``extract_pdf_text`` 的扩展点。
- ``parse_invoice_text`` 为纯函数（str -> dict），与 IO 解耦，便于单测；
- ``extract_pdf_text`` 为同步 CPU 密集操作，调用方（路由层）必须经
  ``run_in_threadpool`` 调度，避免阻塞事件循环（评估报告 A-03 教训）。

解析鲁棒性说明：发票版式多样（数电票/增值税电子普通票/专用票/铁路车票/
机票行程单），字段一律"尽力而为 + 置信度"，识别结果仅供预填，由用户核对。
"""
from __future__ import annotations

import io
import re
from typing import Any, Optional

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# PDF 文本抽取
# ---------------------------------------------------------------------------

def extract_pdf_text(content: bytes) -> str:
    """
    从 PDF 字节流抽取文本（同步，CPU 密集——路由层须用线程池调度）。

    优先 pdfplumber（布局感知），失败降级 PyPDF2，再失败返回空串。
    """
    if not content:
        return ""
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            parts = []
            for page in pdf.pages[:5]:  # 发票通常 1 页，上限保护
                page_text = page.extract_text() or ""
                parts.append(page_text)
            return "\n".join(parts)
    except Exception as e:  # noqa: BLE001
        logger.warning("invoice_parser.pdfplumber_failed", error=str(e))
    try:
        import PyPDF2  # type: ignore

        reader = PyPDF2.PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:5])
    except Exception as e:  # noqa: BLE001
        logger.warning("invoice_parser.pypdf_failed", error=str(e))
    return ""


# ---------------------------------------------------------------------------
# 字段解析（纯函数）
# ---------------------------------------------------------------------------

# 发票种类：标题关键词 -> 规范名称（按顺序匹配，精确种别在前）
_INVOICE_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"电子发票\s*[（(]\s*专用发票\s*[)）]", "电子发票（专用发票）"),
    (r"电子发票\s*[（(]\s*普通发票\s*[)）]", "电子发票（普通发票）"),
    (r"增值税电子专用发票", "增值税电子专用发票"),
    (r"增值税专用发票", "增值税专用发票"),
    (r"增值税电子普通发票", "增值税电子普通发票"),
    (r"增值税普通发票", "增值税普通发票"),
    (r"数电[^，。\n]*专用", "电子发票（专用发票）"),
    (r"数电[^，。\n]*普通", "电子发票（普通发票）"),
    (r"铁路电子客票|火车票|铁路车票", "铁路车票"),
    (r"航空运输电子客票行程单|航空客票|机票行程单", "机票行程单"),
    (r"出租车|客运|网约车", "交通票据"),
]

_RE_LABELED_VALUE = {
    "invoice_number": re.compile(r"发票号码\s*[:：]?\s*(\d{8,20})"),
    "invoice_code": re.compile(r"发票代码\s*[:：]?\s*(\d{10,12})"),
    "check_code": re.compile(r"校验[码碼]\s*[:：]?\s*([0-9 ]{12,24})"),
    "invoice_date": re.compile(
        r"开票日期\s*[:：]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    ),
}
# 纳税人识别号：15-20 位大写字母数字（数电票标签为"统一社会信用代码/纳税人识别号"）
_RE_TAXPAYER_ID = re.compile(
    r"(?:统一社会信用代码\s*[/／]\s*)?纳税人识别号\s*[:：]?\s*([0-9A-Z]{15,20})"
)
_RE_BUYER_NAME = re.compile(
    r"购(?:买方|买方信息)?[\s\S]{0,240}?名\s*称\s*[:：]\s*([^\n]+)"
)
_RE_SELLER_NAME = re.compile(
    r"销(?:售方|售方信息)?[\s\S]{0,240}?名\s*称\s*[:：]\s*([^\n]+)"
)
_RE_TOTAL = re.compile(
    r"价税合计[\s\S]{0,160}?(?:（小写）|\(小写\))\s*[¥￥]?\s*([0-9][0-9,]*\.\d{2})"
)
_RE_TOTAL_FALLBACK = re.compile(r"[¥￥]\s*([0-9][0-9,]*\.\d{2})")
_RE_HEJI = re.compile(
    r"合\s*计\s*[¥￥]?\s*([0-9][0-9,]*\.\d{2})\s*[¥￥]?\s*([0-9][0-9,]*\.\d{2})"
)
_RE_REMARK = re.compile(r"备\s*注\s*[:：]?\s*([^\n]{0,200})")

# 事业单位/机关特征词（优先级高于"企业"判定）
_INSTITUTION_KEYWORDS = ("医院", "学校", "大学", "学院", "研究院", "研究所", "中心", "博物馆")
_GOVERNMENT_KEYWORDS = ("人民政府", "委员会", "财政局", "税务局", "管理局", "局", "所")
_ORG_KEYWORDS = ("公司", "集团", "厂", "企业", "商行", "店", "银行", "行", "合作社", "工作室")


def _detect_invoice_type(text: str) -> Optional[str]:
    head = text[:600]  # 发票种类在票面标题区
    for pattern, label in _INVOICE_TYPE_PATTERNS:
        if re.search(pattern, head):
            return label
    return None


def _classify_buyer_type(name: str, has_tax_id: bool) -> str:
    """抬头类型判定：企业 / 个人 / 事业单位 / 机关 / 其他。"""
    name = (name or "").strip()
    if not name or name.startswith("个人") or re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name):
        return "个人"
    if any(k in name for k in _GOVERNMENT_KEYWORDS):
        return "机关"
    if any(k in name for k in _INSTITUTION_KEYWORDS):
        return "事业单位"
    if has_tax_id or any(k in name for k in _ORG_KEYWORDS):
        return "企业"
    return "其他"


def _normalize_amount(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        return round(float(raw.replace(",", "")), 2)
    except (ValueError, TypeError):
        return None


# 报销类别猜测：按 (关键词集, category) 顺序匹配（与前端 CATEGORY_LABELS 对齐）
_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("出差", "差旅", "酒店", "住宿", "旅馆", "宾馆"), "travel"),
    (("餐", "酒楼", "伙食", "餐饮"), "meal"),
    (("办公", "文具", "打印", "耗材", "纸张"), "office"),
    (("话费", "通信", "电信", "联通", "移动", "宽带"), "communication"),
    (("培训", "会务", "会议", "研讨会"), "training"),
    (("招待", "宴请", "礼品"), "entertainment"),
]
# 票种直接映射（交通票据类）
_TYPE_CATEGORY_MAP = {
    "铁路车票": "transport",
    "机票行程单": "transport",
    "交通票据": "transport",
}


def _guess_category(parsed: dict) -> str:
    itype = parsed.get("invoice_type") or ""
    if itype in _TYPE_CATEGORY_MAP:
        return _TYPE_CATEGORY_MAP[itype]
    haystack = " ".join(
        str(parsed.get(k) or "") for k in ("remark", "invoice_title", "seller_name")
    )
    for keywords, category in _CATEGORY_RULES:
        if any(k in haystack for k in keywords):
            return category
    return "other"


# 核心字段（用于置信度计算）
_CORE_FIELDS = ("invoice_type", "invoice_title", "taxpayer_id",
                "invoice_date", "invoice_number", "total_amount")

# 部分 PDF 字体 ToUnicode 映射缺陷：全角"¥"等字符会被抽取为 \x00 控制符，
# 解析前统一清除（货币符号在金额正则中本为可选）
_RE_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def parse_invoice_text(text: str) -> dict:
    """
    解析发票文本层，返回票面要素字典。

    所有字段尽力而为，未识别为 None；``confidence`` 为核心字段检出率
    （0.0~1.0），低于 0.3 时 ``reliable=False`` 提示前端要求人工填写。
    """
    result: dict[str, Any] = {
        "invoice_type": None,
        "invoice_title": None,      # 购买方名称（报销抬头）
        "buyer_type": None,         # 抬头类型：企业/个人/事业单位/机关/其他
        "taxpayer_id": None,        # 纳税人识别号
        "invoice_date": None,       # 开票日期（ISO YYYY-MM-DD）
        "invoice_number": None,
        "invoice_code": None,
        "check_code": None,
        "amount_without_tax": None,
        "tax_amount": None,
        "total_amount": None,       # 价税合计（小写）
        "seller_name": None,
        "remark": None,
        "confidence": 0.0,
        "reliable": False,
        "text_length": len(text or ""),
    }
    text = _RE_CONTROL_CHARS.sub("", text or "")
    if not text:
        return result

    result["invoice_type"] = _detect_invoice_type(text)

    for field, pattern in _RE_LABELED_VALUE.items():
        m = pattern.search(text)
        if not m:
            continue
        if field == "invoice_date":
            result[field] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        elif field == "check_code":
            result[field] = m.group(1).replace(" ", "")[:20]
        else:
            result[field] = m.group(1)

    # 纳税人识别号（取第一处，即购买方）
    m = _RE_TAXPAYER_ID.search(text)
    if m:
        result["taxpayer_id"] = m.group(1)

    # 购买方抬头：先截取"购买方…销售方"片段再匹配，避免跨段落错配
    buyer_zone = text
    seller_zone_end = text.find("销售方")
    if seller_zone_end > 0:
        buyer_zone = text[:seller_zone_end]
    m = _RE_BUYER_NAME.search(buyer_zone)
    if m:
        result["invoice_title"] = m.group(1).strip().rstrip("　")
    # 销售方
    seller_zone = text[seller_zone_end:] if seller_zone_end > 0 else text
    m = _RE_SELLER_NAME.search(seller_zone)
    if m:
        result["seller_name"] = m.group(1).strip().rstrip("　")

    result["buyer_type"] = _classify_buyer_type(
        result["invoice_title"] or "", bool(result["taxpayer_id"])
    )

    # 金额：价税合计优先，回退最后一个 ¥ 金额
    m = _RE_TOTAL.search(text)
    if m:
        result["total_amount"] = _normalize_amount(m.group(1))
    else:
        fallbacks = _RE_TOTAL_FALLBACK.findall(text)
        if fallbacks:
            result["total_amount"] = _normalize_amount(fallbacks[-1])

    # 不含税金额 + 税额（合计行"金额 税额"两个数值，尽力而为）
    m = _RE_HEJI.search(text)
    if m:
        result["amount_without_tax"] = _normalize_amount(m.group(1))
        result["tax_amount"] = _normalize_amount(m.group(2))

    m = _RE_REMARK.search(text)
    if m:
        result["remark"] = m.group(1).strip()

    detected = sum(1 for f in _CORE_FIELDS if result.get(f))
    result["confidence"] = round(detected / len(_CORE_FIELDS), 2)
    result["reliable"] = result["confidence"] >= 0.3
    return result


def build_prefill(parsed: dict, invoice_id: str = "") -> dict:
    """
    将识别结果映射为报销表单预填建议（与前端表单字段对齐）。

    - category：票种/备注关键词猜测；
    - description：备注优先，回退"票种-抬头"；
    - amount/receipt_amount：价税合计；
    - has_receipt：有识别结果即视为已附票。
    """
    total = parsed.get("total_amount")
    description = parsed.get("remark") or (
        f"{parsed.get('invoice_type') or '发票'}-{parsed.get('invoice_title') or ''}"
    )
    return {
        "invoice_id": invoice_id,
        "category": _guess_category(parsed),
        "amount": total if total is not None else None,
        "receipt_amount": total if total is not None else None,
        "description": description.strip(" -"),
        "has_receipt": True,
        "invoice_summary": {
            "invoice_type": parsed.get("invoice_type"),
            "invoice_title": parsed.get("invoice_title"),
            "buyer_type": parsed.get("buyer_type"),
            "taxpayer_id": parsed.get("taxpayer_id"),
            "invoice_date": parsed.get("invoice_date"),
            "invoice_number": parsed.get("invoice_number"),
            "total_amount": parsed.get("total_amount"),
            "tax_amount": parsed.get("tax_amount"),
        },
        "confidence": parsed.get("confidence", 0.0),
        "reliable": parsed.get("reliable", False),
    }
