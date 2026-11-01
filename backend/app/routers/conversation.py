"""对话路由（含 D-R6 数字分身恶意意图研判 + 双管理员告警）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.deps import auth_user
from app.core.intent_guard import detect_threats

router = APIRouter(prefix="/api/v1/conversation", tags=["对话"])


class ChatRequest(BaseModel):
    message: str
    agent_name: str = ""
    channel_id: int | None = None


class ChatResponse(BaseModel):
    reply: str
    intent: str
    blocked: bool = False
    thinking: list[str] = []


@router.post("/chat")
async def chat(req: ChatRequest, user: dict = Depends(auth_user)):
    """统一对话入口：意图分类→恶意研判→（正常）回复 / （恶意）拦截+双管理员告警。"""
    from app.core.audit_chain import append as audit_append
    from app.core.database import get_session

    session = get_session()
    if session is None:
        raise HTTPException(503, "数据库不可用")

    # 1. 意图分类
    from app.core.intent_guard import classify

    intent = classify(req.message)

    # 2. D-R6 恶意意图研判：越权/诱导/套取/冒充/注入 → 中断 + 双管理员告警
    threats = detect_threats(req.message)
    malicious = bool(threats)
    if malicious:
        kinds = [t["kind"] for t in threats]
        severity = max((t["severity"] for t in threats),
                       key=lambda s: ["critical", "high", "medium"].index(s)
                       if s in ["critical", "high", "medium"] else 0)

        # 审计留痕（溯源审计入口）
        async with session as s:
            await audit_append(
                s, actor=user["username"], action="avatar.threat_detected",
                resource=f"conversation:{req.agent_name or 'general'}",
                result="blocked",
                details={"threats": kinds, "severity": severity,
                         "message_preview": req.message[:200],
                         "department": user.get("department", "")},
            )
            # 双管理员告警（同一事务提交）
            await _send_dual_admin_alert(s, user, kinds, severity, req.message[:200])
            await s.commit()

        return ChatResponse(
            reply="您的请求包含疑似越权或恶意内容，已被系统拦截并记录。"
                  "如有疑问请联系部门管理员或系统管理员。",
            intent="blocked", blocked=True,
            thinking=[f"恶意意图研判：命中 {kinds}", "已中断对话",
                      f"已通知部门管理员与系统管理员（{severity}）"],
        )

    # 3. 正常回复（M0 骨架：回显意图 + 消息；LLM 接入后续批次）
    return ChatResponse(
        reply=f"已收到您的消息（意图: {intent['intent']}）。",
        intent=intent["intent"],
        thinking=["意图分类", f"结果: {intent['intent']}"],
    )


async def _send_dual_admin_alert(session, user: dict, kinds: list,
                                 severity: str, message_preview: str) -> None:
    """向部门管理员 + 系统管理员发送系统内部告警消息（D-R6）。"""
    from sqlalchemy import select

    from app.models import IMChannel, IMMessage, User

    dept = user.get("department", "")
    # 找部门管理员和系统管理员
    admins = (await session.execute(
        select(User).where(User.role.in_(["dept_admin", "sys_admin"]),
                           User.status == "active"))).scalars().all()
    # 筛选同部门管理员 + 全部系统管理员
    targets = [a for a in admins
               if a.role == "sys_admin" or a.department == dept]

    for admin in targets:
        # 找或建管理员告警通道
        ch = (await session.execute(
            select(IMChannel).where(
                IMChannel.type == "admin_alert",
                IMChannel.name == f"告警:{admin.username}"))).scalar_one_or_none()
        if ch is None:
            ch = IMChannel(type="admin_alert", name=f"告警:{admin.username}",
                           department="", member_ids="[]")
            session.add(ch)
            await session.flush()
        msg = IMMessage(
            channel_id=ch.id, sender_type="system", sender_id="security",
            content=f"[{severity.upper()}] 用户 {user['username']} 触发安全规则: {kinds} | 消息摘要: {message_preview[:100]}")
        session.add(msg)
    await session.flush()
