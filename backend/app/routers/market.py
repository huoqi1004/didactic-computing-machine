"""能力市场路由（专家/专家团/Skill/MCP 四市场）。"""
from __future__ import annotations

import json

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session

router = APIRouter(prefix="/api/v1/market", tags=["能力市场"])

KINDS = {"expert", "team", "skill", "mcp"}


class MarketUpload(BaseModel):
    kind: str
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
    """员工上传能力到市场（待管理员审核上架）。FR-MKT3"""
    if kind not in KINDS:
        raise HTTPException(400, f"不支持的能力类型: {kind}")
    from app.models import MarketItem

    session = get_session()
    async with session as s:
        item = MarketItem(kind=kind, name=req.name, description=req.description,
                          payload=json.dumps(req.payload, ensure_ascii=False),
                          status="pending", uploader=user["username"])
        s.add(item)
        await s.commit()
        return {"id": item.id, "status": "pending",
                "message": "已提交，等待系统管理员审核上架"}


@router.post("/{item_id}/approve")
async def approve_market_item(item_id: int, user: dict = Depends(auth_user)):
    """管理员审核上架（仅 sys_admin）。"""
    if user["role"] != "sys_admin":
        raise HTTPException(403, "仅系统管理员可审核上架")
    from app.models import MarketItem

    session = get_session()
    async with session as s:
        item = (await s.execute(
            select(MarketItem).where(MarketItem.id == item_id))).scalar_one_or_none()
        if item is None:
            raise HTTPException(404, "条目不存在")
        item.status = "approved"
        item.reviewer = user["username"]
        await s.commit()
        return {"id": item.id, "status": "approved"}
