"""IM 路由（M11：公司全员群 / 部门全员群 / 员工单聊 / 数字分身私聊 / 安全告警）。

- 频道可见性：company 全员；department 限本部门（领导层可见）；dm 与
  avatar_private 仅成员可见（avatar_private 另对 dept_admin/sys_admin 开放
  治理视角，D-R6）；admin_alert 仅对应管理员
- 发言权限：dm/avatar_private 仅成员；company 全员；department 本部门；
  admin_alert 仅管理员（系统告警为主）
"""
from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user

logger = structlog.get_logger()
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/im", tags=["IM"])

LEADERSHIP = ("dept_admin", "sys_admin", "leader", "chairman")


def _members(ch) -> list[str]:
    try:
        return json.loads(ch.member_ids or "[]")
    except Exception:
        return []


def _can_read(ch, user: dict) -> bool:
    t, m = ch.type, _members(ch)
    if t == "company":
        return True
    if t == "department":
        return ch.department == user.get("department", "") or user["role"] in LEADERSHIP
    if t in ("dm", "avatar_private"):
        if user["username"] in m:
            return True
        return t == "avatar_private" and user["role"] in ("dept_admin", "sys_admin")
    if t == "admin_alert":
        return user["role"] in ("dept_admin", "sys_admin") or user["username"] in m
    return False


def _can_speak(ch, user: dict) -> bool:
    t, m = ch.type, _members(ch)
    if t == "company":
        return True
    if t == "department":
        return ch.department == user.get("department", "")
    if t in ("dm", "avatar_private"):
        return user["username"] in m
    if t == "admin_alert":
        return user["role"] in ("dept_admin", "sys_admin")
    return False


@router.get("/channels")
async def list_channels(user: dict = Depends(auth_user)):
    """当前用户可见的全部频道（按类别聚合给前端分组展示）。"""
    from app.models import IMChannel

    session = get_session()
    async with session as s:
        rows = (await s.execute(select(IMChannel))).scalars().all()
    items = []
    for ch in rows:
        if not _can_read(ch, user):
            continue
        category = {"company": "群聊", "department": "群聊", "dm": "单聊",
                    "avatar_private": "数字分身", "admin_alert": "安全告警"}.get(ch.type, "群聊")
        name = ch.name
        if ch.type == "dm":  # 单聊展示对方
            others = [m for m in _members(ch) if m != user["username"]]
            name = f"单聊 · {others[0] if others else ch.name}"
        items.append({"id": ch.id, "type": ch.type, "name": name,
                      "category": category, "department": ch.department})
    return {"items": items}


@router.get("/users")
async def list_users(user: dict = Depends(auth_user)):
    """可发起单聊的同事列表（active，除自己）。"""
    from app.models import User

    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(User).where(User.status == "active").order_by(User.department))).scalars().all()
    return {"items": [{"username": u.username, "display_name": u.display_name,
                       "department": u.department, "role": u.role}
                      for u in rows if u.username != user["username"]]}


@router.post("/dm/{other_username}")
async def find_or_create_dm(other_username: str, user: dict = Depends(auth_user)):
    """与同事建立单聊（幂等：已存在则直接返回）。"""
    from app.models import IMChannel, User

    session = get_session()
    async with session as s:
        other = (await s.execute(
            select(User).where(User.username == other_username,
                               User.status == "active"))).scalar_one_or_none()
        if other is None:
            raise HTTPException(404, "对方不存在或未激活")
        if other.username == user["username"]:
            raise HTTPException(400, "不能与自己单聊")
        pair = sorted([user["username"], other.username])
        marker = f"dm:{pair[0]}:{pair[1]}"
        ch = (await s.execute(
            select(IMChannel).where(IMChannel.name == marker))).scalar_one_or_none()
        if ch is None:
            ch = IMChannel(type="dm", name=marker, department="",
                           member_ids=json.dumps(pair))
            s.add(ch)
            await s.flush()
        await s.commit()
        return {"id": ch.id, "type": "dm", "name": f"单聊 · {other.username}",
                "category": "单聊"}


@router.get("/{channel_id}/messages")
async def get_messages(channel_id: int, user: dict = Depends(auth_user)):
    """获取消息——单聊/分身私聊仅成员可见（分身私聊管理员有治理视角）。"""
    from app.models import IMChannel, IMMessage

    session = get_session()
    async with session as s:
        ch = (await s.execute(
            select(IMChannel).where(IMChannel.id == channel_id))).scalar_one_or_none()
        if ch is None:
            raise HTTPException(404, "频道不存在")
        if not _can_read(ch, user):
            raise HTTPException(403, "无权查看该频道")
        msgs = (await s.execute(
            select(IMMessage).where(IMMessage.channel_id == channel_id)
            .order_by(IMMessage.created_at))).scalars().all()
    return {"items": [{"id": m.id, "sender_type": m.sender_type,
                       "sender_id": m.sender_id, "content": m.content} for m in msgs]}


class ChannelCreate(BaseModel):
    type: str  # company / department / avatar_private
    name: str = ""
    target_user: str = ""  # avatar_private 对方
    member_ids: list[str] = []


class MessageSend(BaseModel):
    content: str


@router.post("/channels")
async def create_channel(req: ChannelCreate, user: dict = Depends(auth_user)):
    from app.models import IMChannel

    if req.type not in ("company", "department", "avatar_private"):
        raise HTTPException(400, "非法频道类型（单聊请用 /im/dm/{user}）")
    if req.type == "company" and user["role"] != "sys_admin":
        raise HTTPException(403, "全员群仅系统管理员可创建")
    if req.type == "department" and user["role"] != "dept_admin":
        raise HTTPException(403, "部门群仅部门管理员可创建")
    members = [user["username"]]
    if req.type == "avatar_private":
        if not req.target_user:
            raise HTTPException(400, "数字分身私聊需指定对方")
        members.append(req.target_user)
    elif req.type == "department":
        members.append(f"dept:{user['department']}")
    session = get_session()
    async with session as s:
        ch = IMChannel(type=req.type,
                       name=req.name or f"对话:{user['username']}↔{req.target_user}",
                       department=user["department"] if req.type == "department" else "",
                       member_ids=json.dumps(members))
        s.add(ch)
        await s.commit()
        return {"id": ch.id, "type": ch.type, "name": ch.name}


@router.post("/{channel_id}/messages")
async def send_message(channel_id: int, req: MessageSend,
                       user: dict = Depends(auth_user)):
    """发送消息（按频道类型校验发言权限）。"""
    from app.models import IMChannel, IMMessage

    session = get_session()
    async with session as s:
        ch = (await s.execute(
            select(IMChannel).where(IMChannel.id == channel_id))).scalar_one_or_none()
        if ch is None:
            raise HTTPException(404, "频道不存在")
        if not _can_speak(ch, user):
            raise HTTPException(403, "无权在该频道发言")
        msg = IMMessage(channel_id=channel_id, sender_type="user",
                        sender_id=user["username"], content=req.content)
        s.add(msg)
        await s.commit()

    # 数字分身自动应答（M13）：分身私聊消息触发后台 LLM 回复
    if ch.type == "avatar_private":
        avatar_name = next((m for m in _members(ch) if m != user["username"]),
                           "数字分身")
        task = asyncio.create_task(
            _avatar_reply(channel_id, user["username"], req.content,
                          avatar_name))
        _avatar_tasks.add(task)
        task.add_done_callback(_avatar_tasks.discard)
    return {"id": msg.id, "status": "sent"}


# 后台任务句柄集（防 GC）
_avatar_tasks: set = set()


async def _avatar_reply(channel_id: int, username: str, message: str,
                        avatar_name: str) -> None:
    """数字分身后台自动应答：D-R6 研判 → 记忆召回 → 专家 persona → LLM。"""
    from app.core.audit_chain import append as audit_append
    from app.core.llm_gateway import (QuotaError, get_default_provider,
                                      provider_chat)
    from app.core.memory import memory_context, retrieve_memories
    from app.models import IMMessage

    await asyncio.sleep(0.2)
    session = get_session()
    try:
        async with session as s:
            history = (await s.execute(
                select(IMMessage).where(IMMessage.channel_id == channel_id)
                .order_by(IMMessage.created_at.desc()).limit(8))).scalars().all()
            # persona：优先能力市场同名专家
            from app.models import MarketItem

            expert = (await s.execute(
                select(MarketItem).where(MarketItem.name == avatar_name,
                                         MarketItem.status == "approved"))).scalars().first()
            persona = (expert.description if expert
                       else f"你是数字分身「{avatar_name}」，"
                            "以专业、严谨、友好的方式协助用户处理工作事务。")
            system_parts = [persona,
                            f"当前对话用户：{username}。请用简洁专业的中文回复 IM 消息。"]

            # D-R6 恶意研判：命中则不调用模型，安全回复 + 留痕
            from app.core.intent_guard import detect_threats

            threats = detect_threats(message)
            reply_text = None
            sender_id = avatar_name
            sender_type = "avatar"
            if threats:
                kinds = [t["kind"] for t in threats]
                await audit_append(s, actor=username,
                                   action="avatar.im_threat_blocked",
                                   resource=f"im_channel:{channel_id}",
                                   result="blocked",
                                   details={"threats": kinds,
                                            "preview": message[:150]})
                reply_text = "您的消息包含疑似越权或恶意内容，已按安全策略中断本次应答并记录。"
                sender_id = "security"
                sender_type = "system"
            else:
                memories = await retrieve_memories(s, username, message)
                if memories:
                    system_parts.append(memory_context(memories))
                msgs = [{"role": "user" if m.sender_type == "user" else "assistant",
                         "content": m.content}
                        for m in reversed(history)]
                provider = await get_default_provider(s)
                if provider is None:
                    return
                try:
                    reply_text, _tokens = await provider_chat(
                        s, provider, msgs,
                        system="\n".join(system_parts), username=username)
                except (QuotaError, RuntimeError):
                    return  # 静默：额度/服务异常不打扰用户
                await s.flush()

            if reply_text:
                s.add(IMMessage(channel_id=channel_id,
                                sender_type=sender_type,
                                sender_id=sender_id, content=reply_text))
                await s.commit()
    except Exception as e:
        logger.warning("avatar.reply_failed", channel=channel_id, error=str(e))
