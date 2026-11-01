"""模型接入与 token 额度管理（sys_admin 管理；用户自助查询）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import auth_user
from app.core.database import get_session
from app.models import ModelProvider, User

router = APIRouter(prefix="/api/v1", tags=["模型管理"])


def _require_admin(user: dict) -> None:
    if user["role"] != "sys_admin":
        raise HTTPException(403, "仅系统管理员可管理模型接入")


def _mask(key: str) -> str:
    return (key[:6] + "****" + key[-4:]) if len(key) > 12 else ("****" if key else "")


# ============ 模型接入（OpenAI / Claude 双协议） ============

class ModelCreate(BaseModel):
    name: str
    protocol: str          # openai / claude
    base_url: str
    api_key: str = ""
    model_name: str
    enabled: bool = True
    is_default: bool = False


class ModelUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None


@router.get("/admin/models")
async def list_models(user: dict = Depends(auth_user)):
    _require_admin(user)
    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(ModelProvider).order_by(ModelProvider.id))).scalars().all()
    return {"items": [{"id": m.id, "name": m.name, "protocol": m.protocol,
                       "base_url": m.base_url, "api_key_masked": _mask(m.api_key),
                       "model_name": m.model_name, "enabled": m.enabled,
                       "is_default": m.is_default} for m in rows]}


@router.get("/models/available")
async def available_models(user: dict = Depends(auth_user)):
    """对话模型选择器数据（所有登录用户可见）。"""
    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(ModelProvider).where(ModelProvider.enabled == True)  # noqa: E712
            .order_by(ModelProvider.id))).scalars().all()
    return {"items": [{"id": m.id, "name": m.name, "protocol": m.protocol,
                       "is_default": m.is_default} for m in rows]}


@router.post("/admin/models")
async def create_model(req: ModelCreate, user: dict = Depends(auth_user)):
    _require_admin(user)
    if req.protocol not in ("openai", "claude"):
        raise HTTPException(400, "protocol 仅支持 openai 或 claude")
    session = get_session()
    async with session as s:
        dup = (await s.execute(select(ModelProvider.id).where(
            ModelProvider.name == req.name))).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(409, "同名模型已存在")
        if req.is_default:
            for m in (await s.execute(select(ModelProvider))).scalars().all():
                m.is_default = False
        row = ModelProvider(name=req.name, protocol=req.protocol,
                            base_url=req.base_url, api_key=req.api_key,
                            model_name=req.model_name, enabled=req.enabled,
                            is_default=req.is_default)
        s.add(row)
        await s.commit()
        return {"id": row.id}


@router.patch("/admin/models/{model_id}")
async def update_model(model_id: int, req: ModelUpdate,
                       user: dict = Depends(auth_user)):
    _require_admin(user)
    session = get_session()
    async with session as s:
        row = (await s.execute(select(ModelProvider).where(
            ModelProvider.id == model_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "模型不存在")
        if req.is_default:
            for m in (await s.execute(select(ModelProvider))).scalars().all():
                m.is_default = False
        for field in ("name", "base_url", "api_key", "model_name",
                      "enabled", "is_default"):
            val = getattr(req, field)
            if val is not None:
                setattr(row, field, val)
        await s.commit()
    return {"id": row.id, "status": "ok"}


@router.delete("/admin/models/{model_id}")
async def delete_model(model_id: int, user: dict = Depends(auth_user)):
    _require_admin(user)
    session = get_session()
    async with session as s:
        row = (await s.execute(select(ModelProvider).where(
            ModelProvider.id == model_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "模型不存在")
        await s.delete(row)
        await s.commit()
    return {"status": "deleted"}


# ============ token 日额度管理 ============

class QuotaSet(BaseModel):
    username: str
    daily_limit: int
    enabled: bool = True


@router.get("/admin/quotas")
async def list_quotas(user: dict = Depends(auth_user)):
    """全员额度与当日用量（sys_admin）。"""
    _require_admin(user)
    from datetime import datetime

    from app.core.llm_gateway import get_quota
    from app.models import TokenQuota

    session = get_session()
    async with session as s:
        users = (await s.execute(
            select(User).where(User.status == "active").order_by(User.id))).scalars().all()
        quotas = {q.username: q for q in (await s.execute(
            select(TokenQuota))).scalars().all()}
        items = []
        for u in users:
            info = await get_quota(s, u.username)
            items.append({"username": u.username, "display_name": u.display_name,
                          "department": u.department,
                          "daily_limit": quotas[u.username].daily_limit if u.username in quotas else 100000,
                          "enabled": info["enabled"], "used_today": info["used"],
                          "remaining": info["remaining"]})
    return {"items": items}


@router.post("/admin/quotas")
async def set_quota(req: QuotaSet, user: dict = Depends(auth_user)):
    _require_admin(user)
    if req.daily_limit < 0:
        raise HTTPException(400, "额度不可为负")
    session = get_session()
    async with session as s:
        exists = (await s.execute(select(User.id).where(
            User.username == req.username))).scalar_one_or_none()
        if exists is None:
            raise HTTPException(404, "用户不存在")
        from app.models import TokenQuota

        row = (await s.execute(select(TokenQuota).where(
            TokenQuota.username == req.username))).scalar_one_or_none()
        if row is None:
            row = TokenQuota(username=req.username)
            s.add(row)
        row.daily_limit = req.daily_limit
        row.enabled = req.enabled
        await s.commit()
    return {"username": req.username, "daily_limit": row.daily_limit,
            "enabled": row.enabled}


@router.get("/me/quota")
async def my_quota(user: dict = Depends(auth_user)):
    """用户自助查看自己的 token 使用量与剩余量。"""
    from app.core.llm_gateway import get_quota

    session = get_session()
    async with session as s:
        info = await get_quota(s, user["username"])
    return info


# ============ 市场上架审核（M10 安全审查闭环） ============

@router.get("/admin/market-pending")
async def market_pending(user: dict = Depends(auth_user)):
    """待审核条目（含安全扫描结论）：sys_admin 全量，dept_admin 限本部门上传。"""
    if user["role"] not in ("sys_admin", "dept_admin"):
        raise HTTPException(403, "仅管理员可审核")
    import json as _json

    from app.models import MarketItem, User

    session = get_session()
    async with session as s:
        rows = (await s.execute(
            select(MarketItem).where(MarketItem.status == "pending")
            .order_by(MarketItem.id.desc()))).scalars().all()
        users = {u.username: u for u in (await s.execute(
            select(User))).scalars().all()}
        items = []
        for r in rows:
            owner = users.get(r.uploader)
            if (user["role"] == "dept_admin"
                    and (owner is None
                         or owner.department != user.get("department", ""))):
                continue
            try:
                payload = _json.loads(r.payload or "{}")
            except Exception:
                payload = {}
            scan = payload.get("scan") or {}
            if isinstance(scan, str):  # 爬虫种子条目存的是结论字符串
                scan = {"verdict": scan}
            items.append({"id": r.id, "kind": r.kind, "name": r.name,
                          "description": r.description[:200],
                          "uploader": r.uploader,
                          "department": owner.department if owner else "",
                          "scan_verdict": scan.get("verdict", "unknown"),
                          "risk_types": scan.get("risk_types", []),
                          "created": str(r.created_at)[:19]})
    return {"items": items}
