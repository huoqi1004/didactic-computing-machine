"""智能体会话路由（WorkBuddy 式对话 · M6）。

- 会话 CRUD + 工作区（每个会话一个目录，存会话产物与附件）
- 附件上传（多格式）→ 工作区 attachments/
- 能力引用：知识库条目 / 资料库定稿 / 专家·专家团·Skill·MCP（能力市场）/
  数字分身 persona → 装配进系统提示词
- 三种智能体模式：
  full_auto      完全权限：产物直接写工作区
  approve_risky  审批+自主：普通写入直接落工作区，仅标记风险动作待审
  sandbox        沙箱：产物先写 _sandbox/，人工审批后提交到工作区根
- 思考/执行步骤逐步记录进消息 thinking（前端上方展示）
- 模型路由：按会话 model_id 走网关（OpenAI/Claude 双协议）+ token 额度
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
import structlog

from app.core.deps import auth_user

logger = structlog.get_logger()
from app.core.intent_guard import detect_threats
from app.models import (BusinessDocument, Conversation, ConversationMessage,
                        IMChannel, IMMessage, KnowledgeEntry, MarketItem, User)

router = APIRouter(prefix="/api/v1/conversation", tags=["智能体会话"])

WORKSPACE_ROOT = Path(__file__).resolve().parents[2] / "workspaces"

AGENT_MODES = ("full_auto", "approve_risky", "sandbox")


def get_session():
    from app.core.database import get_session as _g
    return _g()


def _workspace_dir(conv: Conversation, user_id: str) -> Path:
    """会话工作区目录：workspaces/{user}/{cid}_{workspace}/"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", conv.workspace or "default")
    return WORKSPACE_ROOT / user_id / f"{conv.id:04d}_{safe}"


def _ensure_ws(conv: Conversation, user_id: str) -> Path:
    ws = _workspace_dir(conv, user_id)
    (ws / "attachments").mkdir(parents=True, exist_ok=True)
    if conv.agent_mode == "sandbox":
        (ws / "_sandbox").mkdir(parents=True, exist_ok=True)
    return ws


# ============ 会话 CRUD ============

class ConversationCreate(BaseModel):
    title: str = "新对话"
    workspace: str = "default"
    agent_mode: str = "approve_risky"
    model_id: int = 0


@router.post("")
async def create_conversation(req: ConversationCreate,
                              user: dict = Depends(auth_user)):
    if req.agent_mode not in AGENT_MODES:
        raise HTTPException(400, "非法智能体模式")
    session = get_session()
    async with session as s:
        conv = Conversation(user_id=user["username"], title=req.title[:120],
                            workspace=req.workspace[:80] or "default",
                            agent_mode=req.agent_mode, model_id=req.model_id)
        s.add(conv)
        await s.flush()
        _ensure_ws(conv, user["username"])
        await s.commit()
        return {"id": conv.id, "title": conv.title, "workspace": conv.workspace,
                "agent_mode": conv.agent_mode, "model_id": conv.model_id}


# ============ 兼容端点：无会话轻量对话（旧测试/老首页） ============

class LegacyChatRequest(BaseModel):
    message: str
    agent_name: str = ""


@router.post("/chat")
async def legacy_chat(req: LegacyChatRequest, user: dict = Depends(auth_user)):
    """无会话轻量对话：恶意研判 → 默认模型直答（不落库、不建工作区）。"""
    from app.core.audit_chain import append as audit_append
    from app.core.llm_gateway import (QuotaError, get_default_provider,
                                      provider_chat)

    session = get_session()
    async with session as s:
        threats = detect_threats(req.message)
        if threats:
            kinds = [t["kind"] for t in threats]
            await audit_append(
                s, actor=user["username"], action="avatar.threat_detected",
                resource=f"conversation:{req.agent_name or 'general'}",
                result="blocked", details={"threats": kinds,
                                           "message_preview": req.message[:200]})
            await _send_dual_admin_alert(s, user, kinds)
            await s.commit()
            return {"reply": "您的请求包含疑似越权或恶意内容，已被系统拦截并记录。",
                    "intent": "blocked", "blocked": True,
                    "thinking": [f"恶意意图研判：命中 {kinds}", "已中断对话"]}
        intent = "chitchat"
        provider = await get_default_provider(s)
        reply = None
        if provider is not None:
            try:
                reply, _tokens = await provider_chat(
                    s, provider, [{"role": "user", "content": req.message}],
                    system=f"你是企业数字化大脑的智能助手，当前用户 {user['username']}"
                           f"（角色 {user.get('role', '')}，部门 {user.get('department', '')}）。"
                           "请用简洁专业的中文回答。",
                    username=user["username"])
            except (QuotaError, RuntimeError):
                reply = None
            await s.flush()
        return {"reply": reply or f"已收到您的消息（意图: {intent}）。",
                "intent": intent, "blocked": False,
                "thinking": ["意图分类", f"结果: {intent}"]}


@router.get("")
async def list_conversations(user: dict = Depends(auth_user)):
    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(Conversation).where(Conversation.user_id == user["username"])
            .order_by(Conversation.updated_at.desc()))).scalars().all()
    return {"items": [{"id": c.id, "title": c.title, "workspace": c.workspace,
                       "agent_mode": c.agent_mode, "model_id": c.model_id,
                       "updated_at": str(c.updated_at)} for c in rows]}


@router.patch("/{conv_id}")
async def update_conversation(conv_id: int, req: dict,
                              user: dict = Depends(auth_user)):
    session = get_session()
    async with session as s:
        conv = await _owned_conv(s, conv_id, user)
        if "title" in req:
            conv.title = str(req["title"])[:120]
        if "agent_mode" in req:
            if req["agent_mode"] not in AGENT_MODES:
                raise HTTPException(400, "非法智能体模式")
            conv.agent_mode = req["agent_mode"]
            _ensure_ws(conv, user["username"])
        if "model_id" in req:
            conv.model_id = int(req["model_id"])
        conv.updated_at = datetime.now(timezone.utc)
        await s.commit()
        return {"id": conv.id, "title": conv.title,
                "agent_mode": conv.agent_mode, "model_id": conv.model_id}


@router.delete("/{conv_id}")
async def delete_conversation(conv_id: int, user: dict = Depends(auth_user)):
    """删除会话（右键菜单）：删除消息记录与工作区目录。"""
    session = get_session()
    async with session as s:
        conv = await _owned_conv(s, conv_id, user)
        msgs = (await s.execute(select(ConversationMessage).where(
            ConversationMessage.conversation_id == conv_id))).scalars().all()
        for m in msgs:
            await s.delete(m)
        ws = _workspace_dir(conv, user["username"])
        await s.delete(conv)
        await s.commit()
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    return {"status": "deleted"}


async def _owned_conv(s, conv_id: int, user: dict) -> Conversation:
    conv = (await s.execute(select(Conversation).where(
        Conversation.id == conv_id,
        Conversation.user_id == user["username"]))).scalar_one_or_none()
    if conv is None:
        raise HTTPException(404, "会话不存在")
    return conv


# ============ 消息与工作区文件 ============

@router.get("/{conv_id}/messages")
async def get_messages(conv_id: int, user: dict = Depends(auth_user)):
    session = get_session()
    async with session as s:
        conv = await _owned_conv(s, conv_id, user)
        rows = (await s.execute(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conv_id)
            .order_by(ConversationMessage.id))).scalars().all()
        files: list[dict] = []
        ws = _ensure_ws(conv, user["username"])
        for p in sorted(ws.rglob("*")):
            if p.is_file() and "_sandbox" not in p.parts:
                rel = p.relative_to(ws).as_posix()
                if rel.startswith("attachments/"):
                    continue
                files.append({"path": rel, "size": p.stat().st_size})
        pending = await _list_pending(s, conv, user)
    return {"items": [{"id": m.id, "role": m.role, "content": m.content,
                       "thinking": json.loads(m.thinking or "[]"),
                       "attachments": json.loads(m.attachments or "[]"),
                       "created_at": str(m.created_at)} for m in rows],
            "workspace": conv.workspace, "agent_mode": conv.agent_mode,
            "model_id": conv.model_id, "files": files, "pending_ops": pending}


@router.post("/{conv_id}/upload")
async def upload_attachment(conv_id: int, file: UploadFile = File(...),
                            user: dict = Depends(auth_user)):
    """上传附件到会话工作区 attachments/。"""
    session = get_session()
    async with session as s:
        conv = await _owned_conv(s, conv_id, user)
        ws = _ensure_ws(conv, user["username"])
        data = await file.read()
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(400, "附件不能超过 20MB")
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", file.filename or "file.bin")
        dest = ws / "attachments" / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        dest.write_bytes(data)
        await s.commit()
        return {"path": dest.name, "filename": safe_name,
                "size": len(data), "content_type": file.content_type or ""}


@router.get("/{conv_id}/files/{path:path}")
async def download_workspace_file(conv_id: int, path: str,
                                  user: dict = Depends(auth_user)):
    session = get_session()
    async with session as s:
        conv = await _owned_conv(s, conv_id, user)
        ws = _ensure_ws(conv, user["username"])
    target = (ws / path).resolve()
    if not str(target).startswith(str(ws.resolve())) or not target.is_file():
        raise HTTPException(404, "文件不存在")
    from fastapi.responses import FileResponse

    return FileResponse(target, filename=target.name)


# ============ 审批决策（沙箱/高风险动作） ============

@router.post("/{conv_id}/messages/{msg_id}/ops/{op_idx}/decision")
async def decide_op(conv_id: int, msg_id: int, op_idx: int, req: dict,
                    user: dict = Depends(auth_user)):
    """审批：approve=true 把沙箱产物提交到工作区根；false 丢弃。"""
    approve = bool(req.get("approve"))
    session = get_session()
    async with session as s:
        conv = await _owned_conv(s, conv_id, user)
        msg = (await s.execute(select(ConversationMessage).where(
            ConversationMessage.id == msg_id,
            ConversationMessage.conversation_id == conv_id))).scalar_one_or_none()
        if msg is None:
            raise HTTPException(404, "消息不存在")
        steps = json.loads(msg.thinking or "[]")
        if op_idx < 0 or op_idx >= len(steps):
            raise HTTPException(404, "操作不存在")
        step = steps[op_idx]
        if step.get("status") != "pending":
            raise HTTPException(400, "该操作不在待审批状态")
        ws = _ensure_ws(conv, user["username"])
        staged = step.get("staged", "")
        if approve and staged:
            src = ws / staged
            dest = ws / Path(staged).name
            if src.exists():
                shutil.move(str(src), str(dest))
            step["path"] = dest.name
            step["status"] = "approved"
            step["detail"] = f"已由 {user['username']} 审批通过并写入工作区"
        else:
            if staged:
                (ws / staged).unlink(missing_ok=True)
            step["status"] = "rejected"
            step["detail"] = f"已由 {user['username']} 拒绝，产物已丢弃"
        msg.thinking = json.dumps(steps, ensure_ascii=False)
        await s.commit()
    return {"status": step["status"]}


async def _list_pending(s, conv: Conversation, user: str) -> list[dict]:
    """收集会话内所有待审批操作。"""
    pending = []
    rows = (await s.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conv.id))).scalars().all()
    for m in rows:
        for i, step in enumerate(json.loads(m.thinking or "[]")):
            if step.get("status") == "pending":
                pending.append({"message_id": m.id, "op_idx": i,
                                "kind": step.get("kind", ""),
                                "detail": step.get("detail", ""),
                                "staged": step.get("staged", "")})
    return pending


# ============ 对话主链路 ============

class ChatRequest(BaseModel):
    message: str
    attachments: list[dict] = []
    capabilities: dict = {}
    generate_file: bool = False


@router.post("/{conv_id}/chat")
async def chat(conv_id: int, req: ChatRequest, user: dict = Depends(auth_user)):
    """统一对话：威胁研判 → 能力装配 → 网关模型调用 → 工作区产物（按模式） → 思考流。"""
    from app.core.audit_chain import append as audit_append
    from app.core.llm_gateway import (QuotaError, get_provider, provider_chat)

    session = get_session()
    async with session as s:
        conv = await _owned_conv(s, conv_id, user)

        # 0. 恶意意图研判（D-R6）
        threats = detect_threats(req.message)
        if threats:
            kinds = [t["kind"] for t in threats]
            await audit_append(
                s, actor=user["username"], action="avatar.threat_detected",
                resource=f"conversation:{conv_id}", result="blocked",
                details={"threats": kinds, "message_preview": req.message[:200]})
            await _send_dual_admin_alert(s, user, kinds)
            await s.commit()
            user_msg = ConversationMessage(conversation_id=conv_id, role="user",
                                           content=req.message,
                                           attachments=json.dumps(req.attachments, ensure_ascii=False),
                                           thinking=json.dumps(
                                               [{"kind": "guard", "detail": f"恶意意图研判命中 {kinds}，已拦截"},
                                                {"kind": "blocked", "status": "done",
                                                 "detail": "已通知管理员并留痕审计链"}],
                                               ensure_ascii=False))
            s.add(user_msg)
            bot = ConversationMessage(
                conversation_id=conv_id, role="assistant",
                content="您的请求包含疑似越权或恶意内容，已被系统拦截并记录。",
                thinking="[]")
            s.add(bot)
            conv.updated_at = datetime.now(timezone.utc)
            await s.commit()
            return {"reply": bot.content, "blocked": True, "message_id": bot.id,
                    "thinking": json.loads(user_msg.thinking)}

        # 1. 用户消息落库
        user_msg = ConversationMessage(
            conversation_id=conv_id, role="user", content=req.message,
            attachments=json.dumps(req.attachments, ensure_ascii=False))
        s.add(user_msg)
        await s.flush()

        # 2. 思考流：意图 → 能力装配 → 记忆召回
        steps: list[dict[str, Any]] = [
            {"kind": "intent", "status": "done",
             "detail": f"解析请求（模式：{_MODE_LABELS[conv.agent_mode]}）"}]
        system_parts = [f"你是企业数字化大脑的智能体，当前用户 {user['username']}"
                        f"（{user.get('display_name', '')}，角色 {user.get('role', '')}，"
                        f"部门 {user.get('department', '')}）。"
                        "请用简洁专业的中文回答企业办公相关问题。"]

        caps = req.capabilities or {}
        caps_used = await _assemble_capabilities(s, user, caps, system_parts, steps)

        # 记忆召回（AgentScope 式长期记忆注入）
        from app.core.memory import memory_context, retrieve_memories, touch_memory
        memories = await retrieve_memories(s, user["username"], req.message)
        if memories:
            system_parts.append(memory_context(memories))
            for m in memories:
                await touch_memory(s, m.id)
            steps.append({"kind": "memory", "status": "done",
                          "detail": f"召回 {len(memories)} 条长期记忆："
                                    + "；".join(m.content[:30] for m in memories[:3])})
        if req.attachments:
            names = [a.get("filename", "?") for a in req.attachments]
            steps.append({"kind": "attachments", "status": "done",
                          "detail": f"已挂载附件：{', '.join(names)}"})
            system_parts.append(f"用户随消息附带了文件：{', '.join(names)}；"
                                "如需分析请说明你将如何处理这些文件。")

        # 2.5 Web 插件（M11）：消息含 URL 自动浏览 / 搜索意图联网检索
        try:
            from app.core.web_plugin import gather_web_context
            web_ctx, web_steps = await gather_web_context(req.message)
            if web_ctx:
                system_parts.append(web_ctx)
                steps.extend(web_steps)
        except Exception as e:
            logger.warning("chat.web_plugin_failed", error=str(e))

        # 3. 模型调用（额度校验在网关内）
        provider = await get_provider(s, conv.model_id)
        if provider is None:
            steps.append({"kind": "model", "status": "error",
                          "detail": "系统未接入任何可用模型，请联系系统管理员在管理台接入"})
            reply = None
        else:
            steps.append({"kind": "model", "status": "running",
                          "detail": f"调用模型 {provider.name}（{provider.protocol} 协议 · {provider.model_name}）"})
            try:
                reply, tokens = await provider_chat(
                    s, provider, [{"role": "user", "content": req.message}],
                    system="\n".join(system_parts), username=user["username"])
                steps[-1] = {"kind": "model", "status": "done",
                             "detail": f"{provider.name} 返回 {tokens} tokens"}
            except QuotaError as e:
                steps.append({"kind": "quota", "status": "error",
                              "detail": str(e)})
                reply = None
            except RuntimeError as e:
                steps.append({"kind": "model", "status": "error",
                              "detail": str(e)})
                reply = None
            await s.flush()

        # 4. 工作区产物（M12：AI 意图自动判断是否生成文件与格式）
        #    - 用户显式开启"生成文件"开关 → 直接按启发式格式生成（用户意图优先）
        #    - 否则由 AI 单次决策：是否需要产物 + 格式（docx/pdf/pptx/xlsx/csv/md）+ 内容 spec
        #    - 纯寒暄短消息短路跳过，避免无谓延迟
        file_ops: list[dict] = []
        manual_request = bool(req.generate_file)
        ai_decides = bool(reply and not manual_request
                          and not _is_pure_chitchat(req.message))
        if reply and (manual_request or ai_decides):
            ws = _ensure_ws(conv, user["username"])
            office_fmt: Optional[str] = None
            spec: Optional[dict] = None
            decision_reason = ""
            if manual_request:
                office_fmt = _detect_office_format(req.message) or "md"
            else:
                # AI 意图决策（一次调用同时给出 是否生成/格式/内容spec）
                from app.core import office_gen
                decision, office_fmt, spec, decision_reason = await _ai_decide_artifact(
                    s, provider, user, req.message, reply)
                steps.append({"kind": "artifact_decision", "status": "done",
                              "detail": f"AI 意图判断：{'需要生成 ' + str(office_fmt).upper() + ' 文件' if decision else '无需生成文件'}"
                                        + (f"（{decision_reason[:40]}）" if decision_reason else "")})
            if office_fmt:
                from app.core import office_gen
                try:
                    if spec is None:
                        spec_text, _spec_tokens = await provider_chat(
                            s, provider,
                            [{"role": "user",
                              "content": f"请根据以下要求生成{office_fmt.upper()}文档内容。\n"
                                         f"用户要求：{req.message}\n"
                                         f"你的初步回复（供参考提取内容）：{reply[:2000]}\n"
                                         f"{office_gen.FORMAT_PROMPTS[office_fmt]}\n只输出 JSON。"}],
                            system="你是文档结构化引擎，只输出合法 JSON，不要任何解释或代码块标记。",
                            username=user["username"])
                        spec = _parse_json_safe(spec_text)
                    if spec is None:
                        raise ValueError("模型未返回合法 JSON")
                    base_title = str(spec.get("title") or "产物")[:30]
                    fname = re.sub(r'[\\/:*?"<>|]', "_",
                                   f"{base_title}_{datetime.now().strftime('%m%d_%H%M')}"
                                   f"{office_gen.SUFFIX[office_fmt]}")
                    op = _persist_artifact(conv, ws, fname,
                                           lambda path: office_gen.BUILDERS[office_fmt](path, spec))
                    if op["status"] == "auto":
                        reply = reply + f"\n\n📎 已生成文件：{op['path']}"
                    file_ops.append(op); steps.append(op)
                except Exception as e:
                    logger.warning("chat.office_gen_failed", error=str(e))
                    err_step = {"kind": "file_write", "status": "error", "path": "",
                                "detail": f"Office 生成失败（{e}），已回退为 Markdown 文本"}
                    md_name = f"产物_{datetime.now().strftime('%m%d_%H%M')}.md"
                    md_op = _persist_artifact(
                        conv, ws, md_name,
                        lambda path: path.write_text(reply, encoding="utf-8"))
                    file_ops += [err_step, md_op]; steps += [err_step, md_op]

        # 4.5 首轮对话自动生成会话标题（AI 命名，失败回退消息前缀）
        if provider is not None and conv.title in ("新对话", ""):
            first_user = (await s.execute(
                select(ConversationMessage.id).where(
                    ConversationMessage.conversation_id == conv_id,
                    ConversationMessage.role == "user"))).scalars().all()
            if len(first_user) <= 1:
                try:
                    title_text, _tt = await provider_chat(
                        s, provider,
                        [{"role": "user",
                          "content": "为以下对话生成一个不超过12字的中文会话标题，"
                                     "直接输出标题本身，不要引号和标点：\n"
                                     f"用户：{req.message[:150]}\n"
                                     f"助手：{str(reply)[:150]}"}],
                        system="你是标题生成器，只输出标题文本。",
                        username=user["username"])
                    new_title = title_text.strip().strip('"“” ').split("\n")[0][:24]
                except Exception:
                    new_title = ""
                if not new_title:
                    new_title = req.message.strip().replace("\n", " ")[:12]
                conv.title = new_title
                steps.append({"kind": "title", "status": "done",
                              "detail": f"已自动生成会话标题：{new_title}"})

        # 5. 助手消息落库 + 记忆沉淀
        bot = ConversationMessage(
            conversation_id=conv_id, role="assistant",
            content=reply or "本次请求未能完成：请查看上方执行步骤中的错误提示。",
            thinking=json.dumps(steps, ensure_ascii=False),
            attachments="[]")
        s.add(bot)
        conv.updated_at = datetime.now(timezone.utc)
        await audit_append(
            s, actor=user["username"], action="conversation.chat",
            resource=f"conversation:{conv_id}",
            result="ok" if reply else "failed",
            details={"mode": conv.agent_mode,
                     "model": provider.name if provider else "none",
                     "capabilities": caps_used})
        await s.commit()
    # 记忆抽取（回复后沉淀，独立事务）
    mined = []
    if reply:
        from app.core.memory import extract_memories
        try:
            session2 = get_session()
            async with session2 as s2:
                mined = await extract_memories(s2, user["username"],
                                               req.message, conv_id)
                await s2.commit()
        except Exception as e:
            logger.warning("memory.extract_failed", error=str(e))
    if mined:
        steps.append({"kind": "memory", "status": "done",
                      "detail": f"沉淀 {len(mined)} 条长期记忆（{mined[0]['kind']}）"})
        try:
            session3 = get_session()
            async with session3 as s3:
                row = (await s3.execute(select(ConversationMessage).where(
                    ConversationMessage.id == bot.id))).scalar_one_or_none()
                if row is not None:
                    row.thinking = json.dumps(steps, ensure_ascii=False)
                    await s3.commit()
        except Exception as e:
            logger.warning("memory.thinking_update_failed", error=str(e))
    return {"reply": bot.content, "blocked": False, "message_id": bot.id,
            "thinking": steps, "file_ops": file_ops}


_MODE_LABELS = {"full_auto": "完全权限", "approve_risky": "审批+自主", "sandbox": "沙箱"}


_CHITCHAT_PAT = re.compile(
    r"^(你好|您好|hi|hello|嗨|哈喽|谢谢|感谢|辛苦|再见|拜拜|晚安|早安|ok|好的|收到|嗯)[！!。.~\s]*$",
    re.IGNORECASE)


def _is_pure_chitchat(message: str) -> bool:
    """纯寒暄短路（<20字且命中寒暄词），避免无谓的意图决策调用。"""
    return len(message.strip()) < 20 and bool(_CHITCHAT_PAT.match(message.strip()))


async def _ai_decide_artifact(s, provider, user: dict, message: str,
                              reply: str) -> tuple[bool, Optional[str], Optional[dict], str]:
    """AI 意图决策：根据会话内容判断是否需要生成文件及最佳格式。

    单次调用同时返回 是否生成 / 格式 / 内容 spec / 理由。
    决策失败或格式非法时按 (False, None, None, '') 处理（不生成）。
    """
    from app.core import office_gen
    from app.core.llm_gateway import provider_chat

    prompt = (
        "请根据下面的用户请求与助手回复，判断本次对话是否应该把成果落成文件，"
        "以及最合适的文件格式。\n"
        "判断标准：内容是一份可交付的文档/表格/演示/数据/通知/报告/清单/纪要等"
        "（哪怕用户没有明说'生成文件'），就应该生成；"
        "纯咨询、解释、闲聊、单句问答则不需要。\n"
        "格式选择：结构化数据/表格→xlsx 或 csv（纯数据交换用 csv）；"
        "正式文档/通知/制度/报告→docx；演示汇报→pptx；"
        "需要固定排版分发→pdf；轻量笔记/技术文档→md。\n"
        "用户请求：" + message[:400] + "\n\n助手回复：" + reply[:1200] + "\n\n"
        "只输出一个 JSON 对象：\n"
        '{"generate": true/false, "format": "docx|pdf|pptx|xlsx|csv|md 或 null", '
        '"reason": "简短理由", "spec": {…按格式要求…}}\n'
        "各格式 spec 要求：\n"
        + "\n".join(f"{k}: {v}" for k, v in office_gen.FORMAT_PROMPTS.items()))
    try:
        text, _tokens = await provider_chat(
            s, provider, [{"role": "user", "content": prompt}],
            system="你是文件产物决策引擎，只输出合法 JSON，不要任何解释或代码块标记。",
            username=user["username"])
        data = _parse_json_safe(text)
        if data is None:
            return False, None, None, ""
        if not data.get("generate"):
            return False, None, None, str(data.get("reason", ""))[:60]
        fmt = str(data.get("format") or "").lower().strip()
        if fmt == "ppt":
            fmt = "pptx"
        if fmt not in office_gen.BUILDERS:
            return False, None, None, ""
        spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
        if fmt in ("csv",) and spec is not None:
            spec = {"headers": spec.get("headers", []),
                    "rows": spec.get("rows", [])}
        return True, fmt, spec, str(data.get("reason", ""))[:60]
    except Exception as e:
        logger.warning("chat.artifact_decision_failed", error=str(e))
        return False, None, None, ""


def _persist_artifact(conv: Conversation, ws: Path, fname: str,
                      writer) -> dict:
    """按会话模式把产物写入工作区：沙箱先落 _sandbox/ 待审批，其余直接落盘。"""
    if conv.agent_mode == "sandbox":
        staged_rel = f"_sandbox/{fname}"
        writer(ws / staged_rel)
        return {"kind": "file_write", "status": "pending",
                "staged": staged_rel, "path": staged_rel,
                "detail": f"沙箱已生成 {fname}，等待人工审批后写入工作区"}
    writer(ws / fname)
    if conv.agent_mode == "approve_risky":
        return {"kind": "file_write", "status": "auto", "path": fname,
                "detail": f"普通写入已自动完成：{fname}（工作区内自主权限）"}
    return {"kind": "file_write", "status": "auto", "path": fname,
            "detail": f"完全权限模式：已直接写入工作区 {fname}"}


_OFFICE_PAT = re.compile(r"(word|docx|excel|xlsx|表格|ppt|幻灯片|演示文稿)", re.IGNORECASE)


def _detect_office_format(message: str) -> Optional[str]:
    m = message.lower()
    if re.search(r"(word|docx|公文|红头)", m):
        return "docx"
    if re.search(r"(excel|xlsx|表格)", m):
        return "xlsx"
    if re.search(r"(ppt|幻灯片|演示文稿)", m):
        return "pptx"
    if _OFFICE_PAT.search(message):
        return "docx"  # 泛指 office 文档时默认 Word
    return None


def _parse_json_safe(text: str) -> Optional[dict]:
    """从模型输出中稳健解析 JSON（剥代码围栏、截取首尾花括号）。"""
    text = re.sub(r"```(?:json)?|```", "", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


async def _send_dual_admin_alert(session, user: dict, kinds: list) -> None:
    """向部门管理员 + 系统管理员发送系统内部告警消息（D-R6）。"""
    from app.models import IMChannel, IMMessage

    dept = user.get("department", "")
    admins = (await session.execute(
        select(User).where(User.role.in_(["dept_admin", "sys_admin"]),
                           User.status == "active"))).scalars().all()
    targets = [a for a in admins
               if a.role == "sys_admin" or a.department == dept]
    for admin in targets:
        ch = (await session.execute(
            select(IMChannel).where(
                IMChannel.type == "admin_alert",
                IMChannel.name == f"告警:{admin.username}"))).scalar_one_or_none()
        if ch is None:
            ch = IMChannel(type="admin_alert", name=f"告警:{admin.username}",
                           department="", member_ids="[]")
            session.add(ch)
            await session.flush()
        session.add(IMMessage(
            channel_id=ch.id, sender_type="system", sender_id="security",
            content=f"[安全告警] 用户 {user['username']} 触发安全规则: {kinds}"
                    f" | 时间: {datetime.now(timezone.utc).isoformat()}"))
    await session.flush()


def _wants_file(message: str) -> bool:
    return bool(re.search(r"(起草|撰写|写一份|做一份|来一份|制作|生成一份|写个|生成.{0,12}(文档|表格|演示|报告|文件)|输出.{0,12}(报告|文档)|导出)", message))


def _team_members(payload: str) -> list[str]:
    """解析专家团 payload 中的成员列表。"""
    try:
        return json.loads(payload or "{}").get("members", [])
    except Exception:
        return []


async def _assemble_capabilities(s, user: dict, caps: dict,
                                 system_parts: list, steps: list) -> dict:
    """把知识库/资料库/市场能力/分身 persona 装配进系统提示词。"""
    used: dict[str, int] = {}
    kb_ids = [int(i) for i in (caps.get("knowledge") or [])][:8]
    if kb_ids:
        # 知识权限过滤（M9）：个人条目仅所有者可引用，部门条目限同部门，公司全员
        dept = user.get("department", "")
        uname = user["username"]
        rows = (await s.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.id.in_(kb_ids),
                (KnowledgeEntry.scope == "company")
                | ((KnowledgeEntry.scope == "department")
                   & (KnowledgeEntry.department == dept))
                | ((KnowledgeEntry.scope == "personal")
                   & (KnowledgeEntry.owner_id == uname))))).scalars().all()
        if rows:
            snippets = "\n".join(f"【{r.title}】{r.content[:300]}" for r in rows)
            system_parts.append(f"以下是与本请求相关的知识库条目（仅限当前用户可见范围）：\n{snippets}")
            steps.append({"kind": "capability", "status": "done",
                          "detail": f"已注入知识库条目 {len(rows)} 篇"})
            used["knowledge"] = len(rows)
    rec_ids = [int(i) for i in (caps.get("records") or [])][:8]
    if rec_ids:
        rows = (await s.execute(select(BusinessDocument).where(
            BusinessDocument.id.in_(rec_ids)))).scalars().all()
        if rows:
            meta = "\n".join(f"【{r.title}】类型 {r.doc_type} · 状态 {r.status} · 定稿哈希 {r.final_hash[:12] if r.final_hash else '-'}"
                             for r in rows)
            system_parts.append(f"用户引用了资料库定稿文件（防篡改元数据）：\n{meta}")
            steps.append({"kind": "capability", "status": "done",
                          "detail": f"已挂接资料库定稿 {len(rows)} 份"})
            used["records"] = len(rows)
    market_ids = [int(i) for i in (caps.get("market") or [])][:8]
    if market_ids:
        rows = (await s.execute(select(MarketItem).where(
            MarketItem.id.in_(market_ids)))).scalars().all()
        kinds_cn = {"expert": "专家", "team": "专家团", "skill": "Skill", "mcp": "MCP",
                    "plugin": "浏览器插件"}
        for r in rows:
            if r.kind == "team":
                members = _team_members(r.payload)
                system_parts.append(
                    f"请以专家团「{r.name}」的组织方式协同处理本请求：{r.description}"
                    f"（成员专家：{'、'.join(members) or '见描述'}）。"
                    "请先给出成员分工，再以各成员专业视角依次分析，最后汇总形成综合结论。")
            else:
                desc = r.description[:200]
                system_parts.append(
                    f"请以{kinds_cn.get(r.kind, r.kind)}「{r.name}」的身份与能力执行本请求：{desc}")
        if rows:
            steps.append({"kind": "capability", "status": "done",
                          "detail": "已装配市场能力：" + "、".join(
                              f"{kinds_cn.get(r.kind, r.kind)}·{r.name}" for r in rows)})
            used["market"] = len(rows)
    if caps.get("avatar"):
        system_parts.append(f"当前以数字分身「{caps['avatar']}」的人设与岗位职能作答。")
        steps.append({"kind": "capability", "status": "done",
                      "detail": f"数字分身 {caps['avatar']} 已上线"})
        used["avatar"] = 1
    return used
