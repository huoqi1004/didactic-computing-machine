"""
发票上传识别测试（财务模块 · 报销管理）
========================================

覆盖：
1. parse_invoice_text 纯函数：数电票/增值税电子普通发票文本 → 票面要素
   （发票种类/抬头/抬头类型/税号/开票日期/号码/价税合计/销售方/备注）；
2. 抬头类型判定（企业/个人/事业单位/机关）；
3. 报销类别预填猜测（差旅/餐饮/办公/交通等）；
4. reportlab 生成带文本层的 PDF → extract_pdf_text 端到端；
5. repository 落库/关联（sqlite）；上传路由（依赖覆盖后走 ASGI）。
"""
import asyncio
import io

import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.invoice_parser import (
    build_prefill,
    extract_pdf_text,
    parse_invoice_text,
)
# 注意：路由模块必须在 fixture 初始化 DB 会话工厂之前导入——
# 首次导入会构建 RBAC 引擎并播种种子数据，若此时事件循环已存在，
# fire-and-forget 的 DB 写入会与测试用 sqlite 争写锁（database is locked）
from app.api.routes import reimbursement as reimbursement_routes


# ===========================================================================
# 样本文本
# ===========================================================================

E_INVOICE_TEXT = """电子发票（普通发票）
发票号码: 24312000000123456789
开票日期: 2026年08月15日
购
购买方信息
名    称: 天津恒泰制造集团有限公司
统一社会信用代码/纳税人识别号: 91120116MA05JKLM9K
地址/电话: 天津市滨海新区XX路1号 022-88888888
项目名称                    金额        税率      税额
*信息技术服务*系统运维服务    9433.96     6%       566.04
价税合计(大写) 壹万元整      （小写）¥10000.00
销
销售方信息
名    称: 北京云服务股份有限公司
统一社会信用代码/纳税人识别号: 91110108MA7XYZAB12
备    注: 出差北京运维现场支持费用
"""


def _vat_e_invoice_text(buyer: str = "北京星辰科技有限公司",
                        tax_id: str = "91110108MA01ABC23X",
                        remark: str = "办公用品采购") -> str:
    return f"""增值税电子普通发票
发票代码: 044032000110
发票号码: 09876543
开票日期: 2026年09月01日
校验码: 12345 67890 12345 67890
购买方
名      称: {buyer}
统一社会信用代码/纳税人识别号: {tax_id}
项目名称        金额        税率      税额
*商品*打印纸    88.68      13%      11.53
合    计 ¥88.68 ¥11.53
价税合计(大写) 壹佰元整  （小写）¥100.21
销售方
名      称: 上海办公用品商贸有限公司
统一社会信用代码/纳税人识别号: 91310115MA1SELLER1
备    注: {remark}
"""


# ===========================================================================
# 解析器单测
# ===========================================================================

async def test_parse_dianpiao_full_fields():
    """数电票：核心要素全部识别。"""
    parsed = parse_invoice_text(E_INVOICE_TEXT)
    assert parsed["invoice_type"] == "电子发票（普通发票）"
    assert parsed["invoice_number"] == "24312000000123456789"
    assert parsed["invoice_date"] == "2026-08-15"
    assert parsed["invoice_title"] == "天津恒泰制造集团有限公司"
    assert parsed["taxpayer_id"] == "91120116MA05JKLM9K"
    assert parsed["buyer_type"] == "企业"
    assert parsed["total_amount"] == 10000.00
    assert parsed["seller_name"] == "北京云服务股份有限公司"
    assert parsed["remark"] == "出差北京运维现场支持费用"
    assert parsed["confidence"] == 1.0
    assert parsed["reliable"] is True


async def test_parse_vat_einvoice_with_amounts():
    """增值税电子普通发票：号码/代码/校验码/不含税金额/税额。"""
    parsed = parse_invoice_text(_vat_e_invoice_text())
    assert parsed["invoice_type"] == "增值税电子普通发票"
    assert parsed["invoice_code"] == "044032000110"
    assert parsed["invoice_number"] == "09876543"
    assert parsed["check_code"] == "12345678901234567890"
    assert parsed["amount_without_tax"] == 88.68
    assert parsed["tax_amount"] == 11.53
    assert parsed["total_amount"] == 100.21
    assert parsed["invoice_title"] == "北京星辰科技有限公司"


async def test_classify_buyer_types():
    """抬头类型：企业/个人/事业单位/机关。"""
    assert parse_invoice_text(_vat_e_invoice_text())["buyer_type"] == "企业"
    # 个人：无税号 + 2-4 字自然人姓名
    parsed = parse_invoice_text(
        _vat_e_invoice_text(buyer="张伟", tax_id="-")
    )
    assert parsed["buyer_type"] == "个人"
    # 事业单位
    parsed = parse_invoice_text(
        _vat_e_invoice_text(buyer="天津市第一中心医院", tax_id="12100000471883991H")
    )
    assert parsed["buyer_type"] == "事业单位"
    # 机关
    parsed = parse_invoice_text(
        _vat_e_invoice_text(buyer="天津市滨海新区管理委员会", tax_id="11120000MB2P12345X")
    )
    assert parsed["buyer_type"] == "机关"


async def test_prefill_category_and_fields():
    """预填建议：类别猜测/金额/事由/附票状态。"""
    parsed = parse_invoice_text(E_INVOICE_TEXT)
    prefill = build_prefill(parsed, invoice_id="RINV-test")
    assert prefill["category"] == "travel"  # 备注"出差" → 差旅
    assert prefill["amount"] == 10000.00
    assert prefill["receipt_amount"] == 10000.00
    assert prefill["description"] == "出差北京运维现场支持费用"
    assert prefill["has_receipt"] is True
    assert prefill["invoice_id"] == "RINV-test"
    assert prefill["reliable"] is True

    # 办公用品 → office；车票类型 → transport
    office = parse_invoice_text(_vat_e_invoice_text(remark="办公用品采购"))
    assert build_prefill(office)["category"] == "office"
    train = parse_invoice_text("铁路电子客票\n发票号码: 12345678\n开票日期: 2026年09月01日\n价税合计(大写) 伍拾元整 （小写）¥50.00")
    assert build_prefill(train)["category"] == "transport"


async def test_parse_garbage_low_confidence():
    """乱码/空文本：低置信度，reliable=False。"""
    garbage = parse_invoice_text("这是一段与发票无关的文本 hello world 123")
    assert garbage["reliable"] is False
    assert garbage["confidence"] < 0.3
    empty = parse_invoice_text("")
    assert empty["reliable"] is False
    assert empty["total_amount"] is None


# ===========================================================================
# PDF 抽取端到端（reportlab 生成带文本层样例）
# ===========================================================================

def _make_invoice_pdf_bytes() -> bytes:
    """用 reportlab + 系统中文 TTF 生成带文本层的发票样例 PDF。

    TTFont 嵌入字体会带 ToUnicode CMap，pdfplumber 可正确抽取中文；
    找不到可用字体时跳过（CI 环境无 Windows 字体）。
    """
    import os

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_candidates = [
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    font_path = next((p for p in font_candidates if os.path.exists(p)), None)
    if not font_path:
        pytest.skip("环境中无可用的中文 TTF 字体，跳过 PDF 端到端")
    if font_path.lower().endswith(".ttc"):
        pdfmetrics.registerFont(TTFont("CNInvoice", font_path, subfontIndex=0))
    else:
        pdfmetrics.registerFont(TTFont("CNInvoice", font_path))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("CNInvoice", 14)
    lines = [
        "增值税电子普通发票",
        "发票代码: 044032000110    发票号码: 09876543",
        "开票日期: 2026年09月01日",
        "购买方",
        "名      称: 北京星辰科技有限公司",
        "统一社会信用代码/纳税人识别号: 91110108MA01ABC23X",
        "项目名称        金额        税率      税额",
        "*商品*打印纸    88.68      13%      11.53",
        "价税合计(大写) 壹佰元整  （小写）¥100.21",
        "销售方",
        "名      称: 上海办公用品商贸有限公司",
        "备    注: 办公用品采购",
    ]
    y = 780
    for line in lines:
        c.drawString(60, y, line)
        y -= 24
    c.showPage()
    c.save()
    return buf.getvalue()


async def test_extract_pdf_text_end_to_end():
    """PDF 字节流 → 文本层抽取 → 要素解析 全链路。"""
    pdf_bytes = _make_invoice_pdf_bytes()
    text = await asyncio.to_thread(extract_pdf_text, pdf_bytes)
    assert text, "reportlab 生成的 PDF 应能抽取到文本"
    parsed = parse_invoice_text(text)
    # 数字类字段对字体映射鲁棒，作为端到端断言锚点
    assert parsed["invoice_number"] == "09876543"
    assert parsed["invoice_code"] == "044032000110"
    assert parsed["invoice_date"] == "2026-09-01"
    assert parsed["total_amount"] == 100.21


# ===========================================================================
# Repository / 路由（sqlite）
# ===========================================================================

@pytest_asyncio.fixture
async def sqlite_db(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "invoice_test.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    monkeypatch.setattr(settings, "invoice_upload_dir", str(tmp_path / "uploads"))
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


def _sample_record(invoice_id: str) -> dict:
    parsed = parse_invoice_text(_vat_e_invoice_text())
    return {
        "invoice_id": invoice_id,
        "file_name": "invoice.pdf",
        "file_size": 1024,
        "file_path": "/tmp/x.pdf",
        "uploaded_by": "user_staff",
        "uploaded_by_name": "staff",
        "raw_text": _vat_e_invoice_text(),
        "parsed_json": parsed,
        "status": "parsed",
        **{k: parsed.get(k) for k in (
            "invoice_type", "invoice_title", "buyer_type", "taxpayer_id",
            "invoice_date", "invoice_number", "invoice_code",
            "total_amount", "tax_amount", "amount_without_tax",
            "seller_name", "remark", "confidence",
        )},
    }


async def test_invoice_repository_upsert_and_link(sqlite_db):
    from app.db.repositories.reimbursement_repository import (
        reimbursement_invoice_repository as repo,
    )

    invoice_id = "RINV-repo1"
    assert await repo.upsert(_sample_record(invoice_id), key_field="invoice_id") is True
    record = await repo.get_by_id(invoice_id)
    assert record["invoice_number"] == "09876543"
    assert record["total_amount"] == 100.21
    assert record["status"] == "parsed"

    # 详情含大字段
    full = await repo.get_full_by_id(invoice_id)
    assert full["parsed_json"]["invoice_title"] == "北京星辰科技有限公司"
    assert "增值税电子普通发票" in full["raw_text"]

    # 幂等重复写入不报错
    assert await repo.upsert(_sample_record(invoice_id), key_field="invoice_id") is True

    # 关联
    assert await repo.mark_linked(invoice_id, "REIMB-001") is True
    linked = await repo.get_by_id(invoice_id)
    assert linked["status"] == "linked"
    assert linked["reimbursement_id"] == "REIMB-001"
    # 重复关联幂等（已是 linked 不再改写）
    assert await repo.mark_linked(invoice_id, "REIMB-002") is False

    # 按上传人过滤
    mine = await repo.list_mine(uploaded_by="user_staff")
    assert any(r["invoice_id"] == invoice_id for r in mine)
    others = await repo.list_mine(uploaded_by="someone_else")
    assert all(r["invoice_id"] != invoice_id for r in others)


async def test_upload_route_end_to_end(sqlite_db, monkeypatch):
    """上传路由：ASGI 客户端 + 假认证中间件 → 解析 + 落盘 + 落库 + 预填。"""
    from pathlib import Path

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.core.rbac import rbac_engine

    fake_user = {"user_id": "user_staff", "username": "staff", "department": "业务部"}

    class FakeAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = fake_user
            return await call_next(request)

    # 权限判定放行（require_permission 内部走 rbac_engine.check_permission）
    monkeypatch.setattr(
        rbac_engine, "check_permission", lambda uid, r, a: True, raising=True
    )
    monkeypatch.setattr(
        rbac_engine, "get_user_permissions", lambda uid: set(), raising=True
    )

    app = FastAPI()
    app.include_router(reimbursement_routes.router)
    app.add_middleware(FakeAuthMiddleware)

    pdf_bytes = _make_invoice_pdf_bytes()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1) 正常上传
        resp = await client.post(
            "/api/v1/reimbursement/invoices/upload",
            files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["invoice_id"].startswith("RINV-")
        assert data["parsed"]["invoice_number"] == "09876543"
        assert data["prefill"]["amount"] == 100.21
        assert data["prefill"]["category"] == "office"
        # 文件已落盘
        assert (Path(settings.invoice_upload_dir) / f"{data['invoice_id']}.pdf").exists()

        # 2) 落库可查
        resp2 = await client.get(f"/api/v1/reimbursement/invoices/{data['invoice_id']}")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "parsed"

        # 3) 列表
        resp3 = await client.get("/api/v1/reimbursement/invoices")
        assert resp3.status_code == 200
        assert resp3.json()["total"] >= 1

        # 4) 关联
        resp4 = await client.post(
            f"/api/v1/reimbursement/invoices/{data['invoice_id']}/link",
            json={"reimbursement_id": "REIMB-E2E-1"},
        )
        assert resp4.status_code == 200

        # 5) 非 PDF 拒绝
        resp5 = await client.post(
            "/api/v1/reimbursement/invoices/upload",
            files={"file": ("invoice.txt", b"not a pdf", "text/plain")},
        )
        assert resp5.status_code == 400

        # 6) 伪 PDF（魔数对但内容损坏）→ 解析得低置信度而非 500
        resp6 = await client.post(
            "/api/v1/reimbursement/invoices/upload",
            files={"file": ("broken.pdf", b"%PDF-1.4 broken", "application/pdf")},
        )
        assert resp6.status_code in (200, 400)
        if resp6.status_code == 200:
            assert resp6.json()["parsed"]["reliable"] is False
