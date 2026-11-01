"""IM 路由（全员群/部门群/数字分身私聊）。"""
from __future__ import annotations

import json

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/im", tags=["IM"])


@router.get("/channels")
async def list_channels(user: dict = Depends(auth_user)):
    session = get_session()
    from app.models import IMChannel

    async with session as s:
        rows = (await s.execute(select(IMChannel))).scalars().all()
    visible = []
    for ch in rows:
        members = json.loads(ch.member_ids or "[]")
        if ch.type == "company" or \
           (ch.type == "department" and ch.department == user["department"]) or \
           (ch.type == "avatar_private" and user["username"] in members) or \
           user["role"] in ("dept_admin", "sys_admin"):
            visible.append({"id": ch.id, "type": ch.type, "name": ch.name})
    return {"items": visible}


@router.get("/{channel_id}/messages")
async def get_messages(channel_id: int, user: dict = Depends(auth_user)):
    """获取消息——分身私聊仅部门管理员和当事人可查看。"""
    from app.models import IMChannel, IMMessage
    from sqlalchemy import text

    session = get_session()
    async with session as s:
        ch = (await s.execute(
            select(IMChannel).where(IMChannel.id == channel_id))).scalar_one_or_none()
        if ch is None:
            raise HTTPException(404, "频道不存在")
        members = json.loads(ch.member_ids or "[]")
        if ch.type == "avatar_private":
            if user["username"] not in members and user["role"] not in ("dept_admin", "sys_admin"):
                raise HTTPException(403, "无权查看该分身私聊")
        elif ch.type == "department" and user["department"] != ch.department \
                and user["role"] not in ("dept_admin", "sys_admin", "leader", "chairman"):
            raise HTTPException(403, "非本部门")
        msgs = (await s.execute(
            select(IMMessage).where(IMMessage.channel_id == channel_id)
            .order_by(IMMessage.created_at))).scalars().all()
    return {"items": [{"id": m.id, "sender_type": m.sender_type,
                       "sender_id": m.sender_id, "content": m.content} for m in msgs]}
