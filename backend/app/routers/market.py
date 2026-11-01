"""能力市场路由（专家/专家团/Skill/MCP 四市场）。"""
from __future__ import annotations

import json

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/market", tags=["能力市场"])

KINDS = {"expert", "team", "skill", "mcp", "plugin"}


class MarketUpload(BaseModel):
    kind: str = ""   # 兼容旧契约；实际以路径 {kind} 为准
    name: str
    description: str = ""
    payload: dict = {}


@router.get("/{kind}")
async def list_market(kind: str, user: dict = Depends(auth_user)):
    """浏览市场（已审核通过的条目全公司可见；专家/专家团附带领域与技能元数据）。"""
    if kind not in KINDS:
        raise HTTPException(400, f"不支持的能力类型: {kind}")
    from app.models import MarketItem

    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(MarketItem).where(MarketItem.kind == kind,
                                     MarketItem.status == "approved")
            .order_by(MarketItem.id))).scalars().all()
    items = []
    for r in rows:
        try:
            payload = json.loads(r.payload or "{}")
        except Exception:
            payload = {}
        items.append({"id": r.id, "name": r.name, "description": r.description,
                      "uploader": r.uploader, "downloads": r.downloads,
                      "domain_label": payload.get("domain_label", ""),
                      "department": payload.get("department", ""),
                      "skills": payload.get("skills", []),
                      "members": payload.get("members", [])})
    return {"items": items}


@router.post("/{kind}/upload")
async def upload_to_market(kind: str, req: MarketUpload,
                           user: dict = Depends(auth_user)):
    """员工上传能力到市场（待管理员审核上架）。

    M10 安全审查：上传即扫描病毒/后门/钓鱼等恶意内容——
    - 命中风险 → 向本部门管理员与全体系统管理员的待办事项推送告警，
      并在返回中向上传用户提示风险类型与内容；
    - 全部条目仍进入待审核队列，风险条目需管理员人工裁决后才可能上架。
    """
    if kind not in KINDS:
        raise HTTPException(400, f"不支持的能力类型: {kind}")
    from app.core.security_scan import scan_upload
    from app.models import MarketItem, Todo, User

    payload_text = json.dumps(req.payload, ensure_ascii=False) if req.payload else ""
    scan = scan_upload(req.name, req.description, payload_text)

    session = get_session()
    async with session as s:
        item = MarketItem(kind=kind, name=req.name, description=req.description,
                          payload=json.dumps({**req.payload, "scan": scan},
                                             ensure_ascii=False, default=str),
                          status="pending", uploader=user["username"])
        s.add(item)
        await s.flush()

        admin_todos = 0
        if scan["verdict"] != "clean":
            risk_cn = "、".join(scan["risk_types"]) or "未知风险"
            title = (f"能力上传安全告警[{scan['verdict']}]：{req.name[:40]}"
                     f"（{risk_cn}）")
            # 本部门管理员 + 全体系统管理员
            admins = (await s.execute(
                select(User).where(User.role.in_(["dept_admin", "sys_admin"]),
                                   User.status == "active"))).scalars().all()
            targets = [a for a in admins
                       if a.role == "sys_admin"
                       or (a.role == "dept_admin"
                           and a.department == user.get("department", ""))]
            seen: set[str] = set()
            for a in targets:
                if a.username in seen:
                    continue
                seen.add(a.username)
                s.add(Todo(user_id=a.username, source="安全告警", title=title,
                           link=f"/admin?tab=market",
                           status="open"))
                admin_todos += 1
        await s.commit()

    resp = {"id": item.id, "status": "pending",
            "scan": {"verdict": scan["verdict"],
                     "risk_types": scan["risk_types"]},
            "message": "已提交审核" if scan["verdict"] == "clean"
            else f"已提交审核；扫描发现疑似风险（{('、'.join(scan['risk_types'])) or '未知'}），"
                 f"已通知 {admin_todos} 位管理员处理，请关注审核结果"}
    if scan["verdict"] != "clean":
        resp["risk_notice"] = {
            "verdict": scan["verdict"],
            "risk_types": scan["risk_types"],
            "findings": scan["findings"],
            "advice": "请核对上传内容：若为误报可在审核时向管理员说明；"
                      "若确含上述模式请立即撤回修改后再提交"}
    return resp


@router.post("/{item_id}/approve")
async def approve_market_item(item_id: int, user: dict = Depends(auth_user)):
    """管理员审核上架：sys_admin 全局；dept_admin 可审本部门成员上传的条目。"""
    from app.models import MarketItem, User

    session = get_session()
    async with session as s:
        item = (await s.execute(
            select(MarketItem).where(MarketItem.id == item_id))).scalar_one_or_none()
        if item is None:
            raise HTTPException(404, "条目不存在")
        if user["role"] != "sys_admin":
            if user["role"] != "dept_admin":
                raise HTTPException(403, "仅管理员可审核上架")
            owner = (await s.execute(
                select(User).where(User.username == item.uploader))).scalar_one_or_none()
            if owner is None or owner.department != user.get("department", ""):
                raise HTTPException(403, "部门管理员仅可审核本部门成员上传的条目")
        item.status = "approved"
        item.reviewer = user["username"]
        await s.commit()
        return {"id": item.id, "status": "approved"}


@router.post("/{item_id}/reject")
async def reject_market_item(item_id: int, user: dict = Depends(auth_user)):
    """驳回（下架风险条目，可说明原因）：权限同批准。"""
    from app.models import MarketItem, User

    session = get_session()
    async with session as s:
        item = (await s.execute(
            select(MarketItem).where(MarketItem.id == item_id))).scalar_one_or_none()
        if item is None:
            raise HTTPException(404, "条目不存在")
        if user["role"] != "sys_admin":
            if user["role"] != "dept_admin":
                raise HTTPException(403, "仅管理员可驳回")
            owner = (await s.execute(
                select(User).where(User.username == item.uploader))).scalar_one_or_none()
            if owner is None or owner.department != user.get("department", ""):
                raise HTTPException(403, "部门管理员仅可驳回本部门成员上传的条目")
        item.status = "rejected"
        item.reviewer = user["username"]
        item.review_comment = "安全审查驳回"
        await s.commit()
        return {"id": item.id, "status": "rejected"}
