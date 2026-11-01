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

from app.core.deps import auth_user
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

        # 2. 思考流：意图 → 能力装配
        steps: list[dict[str, Any]] = [
            {"kind": "intent", "status": "done",
             "detail": f"解析请求（模式：{_MODE_LABELS[conv.agent_mode]}）"}]
        system_parts = [f"你是企业数字化大脑的智能体，当前用户 {user['username']}"
                        f"（{user.get('display_name', '')}，角色 {user.get('role', '')}，"
                        f"部门 {user.get('department', '')}）。"
                        "请用简洁专业的中文回答企业办公相关问题。"]

        caps = req.capabilities or {}
        caps_used = await _assemble_capabilities(s, user, caps, system_parts, steps)
        if req.attachments:
            names = [a.get("filename", "?") for a in req.attachments]
            steps.append({"kind": "attachments", "status": "done",
                          "detail": f"已挂载附件：{', '.join(names)}"})
            system_parts.append(f"用户随消息附带了文件：{', '.join(names)}；"
                                "如需分析请说明你将如何处理这些文件。")

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

        # 4. 工作区产物（按模式）
        file_ops: list[dict] = []
        if reply and (req.generate_file or _wants_file(req.message)):
            ws = _ensure_ws(conv, user["username"])
            fname = f"产物_{datetime.now().strftime('%m%d_%H%M')}.md"
            if conv.agent_mode == "sandbox":
                staged_rel = f"_sandbox/{fname}"
                (ws / staged_rel).write_text(reply, encoding="utf-8")
                file_ops.append({"kind": "file_write", "status": "pending",
                                 "staged": staged_rel, "path": staged_rel,
                                 "detail": f"沙箱已生成 {fname}，等待人工审批后写入工作区"})
                steps.append(file_ops[-1])
            elif conv.agent_mode == "approve_risky":
                (ws / fname).write_text(reply, encoding="utf-8")
                file_ops.append({"kind": "file_write", "status": "auto",
                                 "path": fname,
                                 "detail": f"普通写入已自动完成：{fname}（工作区内自主权限）"})
                steps.append(file_ops[-1])
            else:  # full_auto
                (ws / fname).write_text(reply, encoding="utf-8")
                file_ops.append({"kind": "file_write", "status": "auto",
                                 "path": fname,
                                 "detail": f"完全权限模式：已直接写入工作区 {fname}"})
                steps.append(file_ops[-1])

        # 5. 助手消息落库
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
    return {"reply": bot.content, "blocked": False, "message_id": bot.id,
            "thinking": steps, "file_ops": file_ops}


_MODE_LABELS = {"full_auto": "完全权限", "approve_risky": "审批+自主", "sandbox": "沙箱"}


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
    return bool(re.search(r"(起草|撰写|写一份|生成一份|写个|生成.*文档|输出.*报告|导出)", message))


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
        rows = (await s.execute(select(KnowledgeEntry).where(
            KnowledgeEntry.id.in_(kb_ids)))).scalars().all()
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
        kinds_cn = {"expert": "专家", "team": "专家团", "skill": "Skill", "mcp": "MCP"}
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
