"""
创作类 golden 评测集运行器（工作包一 1.4 / 评审委员会裁决 D-E）
================================================================

裁决要求：创作能力上线前必须有 ≥10 例 golden 评测集，断言三个维度
——**结构完整性 / 项目数据感知性 / 阶段匹配度**，并把兜底触发率纳入指标。

打桩边界（为什么这样打）：
- 治理面只读工具打桩 → 实时项目快照由用例给定，断言"文档里的数字 = 给定快照里的数字"，
  杜绝模型臆造；治理面链路本身由 test_conversation_upgrade.py 覆盖；
- LLM 打桩 → 评测不联网、可重复；模型只负责章节文字，装配规则由被测代码
  （build_creative_doc_data）决定，正是 D-E 三个维度要评的对象；
- 断言读**真实落盘的 .docx**（python-docx 回读正文/表格/页眉），不读返回字典——
  交付物是什么就评什么。
"""
import json
import re
from pathlib import Path

import pytest
from docx import Document

from app.core.config import settings

GOLDEN_PATH = Path(__file__).parent / "evals" / "creative_golden.json"
_GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
FIXTURES = _GOLDEN["fixtures"]
CASES = _GOLDEN["cases"]

CURRENT_USER = {"user_id": "u-creative-eval", "username": "creative-eval",
                "role_codes": ["project_manager"]}


class _StubLLM:
    """按用例契约返回结构化创作内容（ok / error / garbage 三种行为）。"""

    def __init__(self, mode: str, outline: dict):
        self.mode = mode
        self.outline = outline

    async def chat_completion(self, messages, **kwargs):
        if self.mode == "error":
            raise RuntimeError("评测强制模型调用异常")
        if self.mode == "garbage":
            return "模型没有按 JSON 契约返回内容"
        return json.dumps(self.outline, ensure_ascii=False)


def _doc_body_text(path: str) -> str:
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def _doc_header_text(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for section in doc.sections
                     for p in section.header.paragraphs)


def _doc_section_text(path: str, heading_key: str) -> str:
    """取指定标题章节的正文（标题段落之后、下一个标题之前的所有段落）。"""
    doc = Document(path)
    collected, started = [], False
    for para in doc.paragraphs:
        is_heading = para.style.name.startswith("Heading")
        if started and not is_heading:
            collected.append(para.text)
        if is_heading and heading_key in para.text:
            started = True
            collected = []
    return "\n".join(collected)


def _counter_value(counter, **labels) -> float:
    child = counter.labels(**labels)
    return float(child._value.get())  # noqa: SLF001 — prometheus_client 无公开读值接口


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_creative_golden(case, tmp_path, monkeypatch):
    from app.api.routes import conversation as conv
    from app.core import metrics
    from app.core.document import document_generator

    expect = case["expect"]
    out_dir = tmp_path / "documents"
    monkeypatch.setattr(settings, "document_output_dir", str(out_dir))

    # ── 1. 意图分类：必须判为创作类，且不带查询类意图（规则收窄的正面验证）
    classified = await conv.classify_intent(case["message"])
    assert classified["intent"] == case["expect_intent"], (
        f"[{case['id']}] 意图判定 {classified['intent']} ≠ 期望 {case['expect_intent']}")
    if case.get("expect_project_id"):
        assert classified["project_id"] == case["expect_project_id"], (
            f"[{case['id']}] 项目编号抽取失败：{classified}")

    # ── 2. 治理面打桩：实时项目快照的唯一来源
    realtime = FIXTURES["projects"].get(case.get("realtime", ""), [])
    progress_mode = case.get("progress_mode", "ok")
    tool_calls: list[dict] = []

    async def fake_tool(tool_name, args, agent_key, current_user):
        tool_calls.append({"tool": tool_name, "args": dict(args or {}),
                           "agent_key": agent_key,
                           "user_id": (current_user or {}).get("user_id")})
        if tool_name == "supervisor_query_progress" and progress_mode != "unavailable":
            return {"total": len(realtime), "projects": list(realtime)}
        return None

    monkeypatch.setattr(conv, "_call_governance_tool", fake_tool)

    # ── 3. LLM 打桩
    llm_mode = case.get("llm_mode", "outline")
    monkeypatch.setattr(settings, "llm_enabled", llm_mode != "unavailable")
    outline = FIXTURES["outlines"].get(case.get("outline", ""), {})
    if llm_mode == "unavailable":
        monkeypatch.setattr(conv, "_get_llm_client", lambda: None)
    else:
        monkeypatch.setattr(conv, "_get_llm_client",
                            lambda: _StubLLM(llm_mode, outline))

    # ── 4. 审计与文档生成打桩
    audits: list[dict] = []

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(conv, "_audit_creative_generation", fake_audit)
    if case.get("doc_mode") == "fail":
        def boom(*args, **kwargs):
            raise RuntimeError("评测强制文档生成失败")
        monkeypatch.setattr(document_generator, "generate", boom)

    metric_reason = expect.get("metric_reason")
    before = _counter_value(
        metrics.CONVERSATION_FALLBACK,
        agent_id=case["agent_id"], intent="creative_document",
        reason=metric_reason) if metric_reason else 0.0

    # 管理员待办落库由 test_conversation_upgrade.py 用真库验证；
    # golden 集只评"降级是否转人工队列 + 回复口径"，此处打桩避免建库。
    todos: list[dict] = []

    async def fake_todo(**kwargs):
        todos.append(kwargs)
        return "todo-eval-0001"

    from app.core.agent import orchestration_service as orchestration

    monkeypatch.setattr(orchestration, "create_admin_todo", fake_todo)

    agent_config = next(a for a in conv.CHAT_AGENTS if a["id"] == case["agent_id"])

    # ── 5. 走被测链路
    result = await conv._handle_creative(
        case["agent_id"], case["message"], agent_config, classified, CURRENT_USER)

    # ── 6. 通用契约：degraded 必须显性（裁决 D-A）
    assert "degraded" in result, f"[{case['id']}] 缺少 degraded 标记"
    assert result["degraded"] is expect.get("degraded", False), (
        f"[{case['id']}] degraded={result['degraded']}，期望 {expect.get('degraded', False)}")
    assert result["intent"] == "creative_document"
    if expect.get("degraded_reason"):
        assert result["degradedReason"] == expect["degraded_reason"], (
            f"[{case['id']}] 降级原因 {result['degradedReason']} ≠ {expect['degraded_reason']}")

    # ── 7. 降级分支：转人工队列 + 管理员待办 + 兜底计数，且不得产出附件
    if expect.get("manual_queue"):
        assert result.get("manualQueue") is True, f"[{case['id']}] 未转人工队列"
        assert result.get("todoId"), f"[{case['id']}] 管理员待办未生成"
        assert len(todos) == 1, f"[{case['id']}] 待办工单应恰好 1 张，实际 {len(todos)}"
        todo = todos[0]
        assert todo["agent_key"] == case["agent_id"]
        assert todo["tool"] == "creative_document"
        assert todo["requested_by"] == CURRENT_USER["user_id"]
        assert case["message"][:20] in todo["preview"], (
            f"[{case['id']}] 待办预览未带上原始请求：{todo['preview']}")
        assert expect["degraded_reason"] in todo["preview"]
        assert "attachments" not in result, f"[{case['id']}] 降级仍返回了附件"
        for needle in expect.get("content_must_contain", []):
            assert needle in result["content"], (
                f"[{case['id']}] 回复缺少「{needle}」：{result['content']}")
        if metric_reason:
            after = _counter_value(
                metrics.CONVERSATION_FALLBACK,
                agent_id=case["agent_id"], intent="creative_document",
                reason=metric_reason)
            assert after == before + 1, (
                f"[{case['id']}] 兜底指标未计数：{before} → {after}")
        assert not audits, f"[{case['id']}] 未生成文档却落了生成审计"
        return

    # ── 8. 成功分支：附件 + 受控目录 + 下载链接走既有鉴权端点
    attachments = result.get("attachments") or []
    assert attachments, f"[{case['id']}] 创作成功但没有附件"
    att = attachments[0]
    assert att["format"] == expect.get("attachment_format", "docx")
    filename = att["filename"]
    if "filename_must_not_contain" in expect:
        for bad in expect["filename_must_not_contain"]:
            assert bad not in filename, (
                f"[{case['id']}] 文件名含非法成分「{bad}」：{filename}")
    assert att["url"] == f"/api/v1/document/download/{filename}"
    assert filename in result["content"], f"[{case['id']}] 正文未给出下载链接"

    produced = out_dir / filename
    assert produced.is_file(), f"[{case['id']}] 产物未落受控目录：{produced}"
    if expect.get("controlled_dir"):
        assert str(produced.resolve()).startswith(
            str(out_dir.resolve())), f"[{case['id']}] 产物越出受控目录"
    path = att.get("path") or str(produced)

    # 页眉标注（裁决 D-C）
    header = _doc_header_text(path)
    for needle in expect.get("header_must_contain", []):
        assert needle in header, (
            f"[{case['id']}] 页眉缺少「{needle}」：{header!r}")

    body = _doc_body_text(path)

    # 结构完整性
    headings = [item["label"] for item in
                (result.get("dataCard") or {}).get("items", [])]
    if expect.get("min_sections"):
        assert len(headings) >= expect["min_sections"], (
            f"[{case['id']}] 章节数 {len(headings)} < {expect['min_sections']}：{headings}")
    for required in expect.get("required_headings", []):
        assert any(required in h for h in headings), (
            f"[{case['id']}] 缺少章节「{required}」：{headings}")
    for absent in expect.get("no_sections_headed", []):
        assert not any(absent in h for h in headings), (
            f"[{case['id']}] 不该出现的章节「{absent}」：{headings}")

    # 编号叠加防线（真机实测：模型自带"一、"与模板自动编号拼成"二、一、xxx"）
    if expect.get("headings_no_double_number"):
        doc = Document(path)
        titles = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
        doubled = [t for t in titles
                   if re.search(r"[一二三四五六七八九十]、\s*"
                                r"(?:[一二三四五六七八九十][、.．]|\d+[.．、]|第[一二三四五六七八九十]+[章节])", t)]
        assert not doubled, f"[{case['id']}] 文档标题出现重复编号：{doubled}"
    if expect.get("card_headings_unnumbered"):
        leaked = [h for h in headings
                  if re.match(r"^(?:[一二三四五六七八九十][、.．]|\d+[.．、]|第[一二三四五六七八九十]+[章节])", h)]
        assert not leaked, f"[{case['id']}] dataCard 章节名残留自带编号：{leaked}"

    # 项目数据感知性（断言读真实产物文本）
    for needle in expect.get("doc_must_contain", []):
        assert needle in body, f"[{case['id']}] 文档正文缺少实时数据「{needle}」"
    for forbidden in expect.get("doc_must_not_contain", []):
        assert forbidden not in body, (
            f"[{case['id']}] 文档正文出现了不该有的内容「{forbidden}」")

    # 阶段匹配度（只看"本阶段监理重点"章节，防止全文断言误判）
    focus = _doc_section_text(path, "本阶段监理重点")
    for needle in expect.get("stage_focus_must_contain", []):
        assert needle in focus, (
            f"[{case['id']}] 阶段重点缺少「{needle}」：{focus}")
    for forbidden in expect.get("stage_focus_must_not_contain", []):
        assert forbidden not in focus, (
            f"[{case['id']}] 阶段重点写入了非本阶段内容「{forbidden}」：{focus}")

    # 草稿属性不得缺席（摘要 + 结论口径）
    for needle in expect.get("summary_must_contain", []):
        assert needle in body, f"[{case['id']}] 摘要缺少「{needle}」"

    # ── 9. 治理面调用口径：走 project_supervisor 域、带真实用户身份
    progress_call = next((c for c in tool_calls
                          if c["tool"] == "supervisor_query_progress"), None)
    assert progress_call, f"[{case['id']}] 未通过治理面取实时数据"
    assert progress_call["agent_key"] == "project_supervisor"
    assert progress_call["user_id"] == CURRENT_USER["user_id"], (
        f"[{case['id']}] 治理面调用丢失了真实用户身份（RBAC/审计会失真）")
    if expect.get("tool_args_project_id"):
        assert progress_call["args"].get("project_id") == expect["tool_args_project_id"], (
            f"[{case['id']}] 未按项目编号取数：{progress_call['args']}")

    # ── 10. 生成行为审计（裁决 D-C）
    assert len(audits) == 1, f"[{case['id']}] 生成审计应恰好 1 条，实际 {len(audits)}"
    record = audits[0]
    assert record["user_id"] == CURRENT_USER["user_id"]
    assert record["agent_id"] == case["agent_id"]
    assert record["message"] == case["message"]
    assert record["doc_data"]["header_note"]
