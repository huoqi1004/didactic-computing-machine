"""
平台级文档生成工具（工作包二：五件套 Skill 的工具落点）
======================================================

出厂 Skill `docx_report` / `xlsx_report` / `meeting_notes` 声明的工具名在这里注册。
生成能力复用工作包一的 `document_generator`（四种格式的子生成器与受控产物目录口径
都在那里），本模块只负责治理面该管的部分：

- **D-C 标注不可绕过**：调用方传入的 `header_note` 一律丢弃，由治理面按真实
  trace_id/时间重新注入；xlsx 没有页眉机制，落到 `basic` 模板的 `notes` 行；
- **产物只进受控目录**：文件名走 `safe_artifact_name` 白名单净化（标题可能来自模型
  输出，控制字符进了 Content-Disposition 会打挂下载），路径成分被剥掉；
- **写工具三件套齐备**：补偿=删除产物（文件已不存在则 no-op，重放幂等），
  未改动任何业务数据表，故风险定级 write_low 而非 write_high。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import structlog
from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool
from app.core.document.output import output_dir, safe_artifact_name

logger = structlog.get_logger()

_DOCX_TEMPLATES = ("basic", "report", "proposal", "meeting", "notice",
                   "personnel", "contract")
_MAX_SECTIONS = 40
_MAX_SECTION_CHARS = 8000
_MAX_XLSX_ROWS = 5000

_DRAFT_NOTICE = ("AI 生成草稿 · 生成时间 {ts} · trace_id {trace} · "
                 "未经审批，不得作为正式交付物")


def _trace_and_ts(ctx: ToolContext) -> tuple[str, str]:
    from app.core.observability.trace import get_trace_id

    # 以 ctx 为准、contextvar 兜底：D-C 标注里的 trace 必须指向发起本次生成的请求
    return (getattr(ctx, "trace_id", "") or get_trace_id() or "",
            datetime.now().astimezone().isoformat(timespec="seconds"))


def _artifact_result(fmt: str, template_type: str, path: str, notice: str) -> dict:
    filename = os.path.basename(path)
    return {
        "format": fmt,
        "template_type": template_type,
        "filename": filename,
        "path": path,
        "size": os.path.getsize(path),
        "download_url": f"/api/v1/document/download/{filename}",
        "draft_notice": notice,
    }


def _remove_artifact_factory(fmt: str):
    """补偿：删掉本次生成的产物文件（幂等——已不存在即 no-op，不报错）。"""

    async def _remove(ctx: ToolContext, args: dict, result: Optional[dict]) -> dict:
        filename = os.path.basename(str((result or {}).get("filename") or ""))
        if not filename:
            return {"removed": False, "reason": "结果中无产物文件名，无需回滚"}
        path = os.path.join(output_dir(), filename)
        if not os.path.exists(path):
            return {"removed": False, "reason": "产物已不存在（补偿幂等：no-op）"}
        os.remove(path)
        logger.info("platform.document_artifact_removed", filename=filename,
                    format=fmt, user=ctx.user_id)
        return {"removed": True, "filename": filename}

    return _remove


class DocxGenerateArgs(BaseModel):
    """Word 文档生成参数（data 形状按模板而定，见 DocxGenerator 各模板文档）。"""
    template_type: str = Field("report", description=f"模板：{list(_DOCX_TEMPLATES)}")
    data: dict[str, Any] = Field(default_factory=dict,
                                 description="模板结构化数据（title/summary/sections/…）")
    filename: str = Field("", max_length=80,
                          description="可选产物文件名；留空则按标题+时间戳生成")


class XlsxGenerateArgs(BaseModel):
    """Excel 报表生成参数（basic 模板：sheet_name/title/headers/rows/notes）。"""
    data: dict[str, Any] = Field(default_factory=dict,
                                 description="表格数据：headers + rows（+ title/notes）")
    filename: str = Field("", max_length=80,
                          description="可选产物文件名；留空则按标题+时间戳生成")


@erp_tool(
    name="document_generate_docx",
    description=(
        "生成 Word 文档（.docx）并返回鉴权下载链接。用于大纲/方案/报告/纪要等"
        "结构化内容落盘；产物进受控目录，页眉强制标注 AI 草稿与 trace_id"
    ),
    args_model=DocxGenerateArgs,
    risk_level=ToolRisk.WRITE_LOW,
    resource="document", action="create",
    agent_domain="platform",
    idempotent=True,
    business_key_field="filename",
    compensation=_remove_artifact_factory("docx"),
    effects_fn=lambda args: (
        f"在受控产物目录新增 1 个 .docx（模板 {args.get('template_type', 'report')}，"
        f"章节上限 {_MAX_SECTIONS}），不改动任何业务数据表；补偿=删除该文件"),
)
async def document_generate_docx(ctx: ToolContext, template_type: str = "report",
                                 data: Optional[dict] = None,
                                 filename: str = "") -> dict:
    if template_type not in _DOCX_TEMPLATES:
        raise ValueError(
            f"不支持的 Word 模板: {template_type}，支持: {list(_DOCX_TEMPLATES)}")

    from app.core.document import document_generator  # noqa: PLC0415

    payload = dict(data or {})
    trace_id, ts = _trace_and_ts(ctx)
    # 裁决 D-C：标注文案由治理面出，调用方（含模型）自带的一律丢弃
    payload.pop("header_note", None)
    payload["header_note"] = _DRAFT_NOTICE.format(ts=ts, trace=trace_id or "NA")
    if isinstance(payload.get("sections"), list):
        payload["sections"] = payload["sections"][:_MAX_SECTIONS]

    seed = filename.strip() or str(payload.get("title") or template_type)
    name = safe_artifact_name(seed, trace_id, "docx")
    path = document_generator.generate(format="docx", template_type=template_type,
                                       data=payload, filename=name)
    logger.info("platform.document_generated", format="docx",
                template_type=template_type, filename=os.path.basename(path),
                user=ctx.user_id, trace_id=trace_id)
    return _artifact_result("docx", template_type, path, payload["header_note"])


@erp_tool(
    name="document_generate_xlsx",
    description=(
        "生成 Excel 报表（.xlsx，basic 模板：表头+数据行）并返回鉴权下载链接。"
        "用于对账单/清单/汇总；数值来自真实业务数据，禁止编造"
    ),
    args_model=XlsxGenerateArgs,
    risk_level=ToolRisk.WRITE_LOW,
    resource="document", action="create",
    agent_domain="platform",
    idempotent=True,
    business_key_field="filename",
    compensation=_remove_artifact_factory("xlsx"),
    effects_fn=lambda args: (
        f"在受控产物目录新增 1 个 .xlsx（数据行上限 {_MAX_XLSX_ROWS}），"
        "不改动任何业务数据表；补偿=删除该文件"),
)
async def document_generate_xlsx(ctx: ToolContext, data: Optional[dict] = None,
                                 filename: str = "") -> dict:
    from app.core.document import document_generator  # noqa: PLC0415

    payload = dict(data or {})
    trace_id, ts = _trace_and_ts(ctx)
    if isinstance(payload.get("rows"), list):
        payload["rows"] = payload["rows"][:_MAX_XLSX_ROWS]
    # xlsx 无页眉机制，D-C 标注落进 basic 模板的备注行
    notes = [str(n) for n in (payload.get("notes") or [])][:_MAX_XLSX_ROWS]
    notes.append(_DRAFT_NOTICE.format(ts=ts, trace=trace_id or "NA"))
    payload["notes"] = notes

    headers = payload.get("headers")
    rows = payload.get("rows") or []
    if not headers and isinstance(rows, list) and rows and isinstance(rows[0], list):
        payload["headers"] = [f"列{i + 1}" for i in range(len(rows[0]))]

    seed = filename.strip() or str(payload.get("title") or "报表")
    name = safe_artifact_name(seed, trace_id, "xlsx")
    path = document_generator.generate(format="xlsx", template_type="basic",
                                       data=payload, filename=name)
    logger.info("platform.document_generated", format="xlsx",
                template_type="basic", filename=os.path.basename(path),
                rows=len(rows), user=ctx.user_id, trace_id=trace_id)
    return _artifact_result("xlsx", "basic", path, payload["notes"][-1])
