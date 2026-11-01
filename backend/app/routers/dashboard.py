"""看板路由（分管领导 + 董事长全域驾驶舱）。"""
from __future__ import annotations

from fastapi import Depends, APIRouter, HTTPException

from app.core.deps import auth_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["看板"])


@router.get("/leader")
async def leader_dashboard(user: dict = Depends(auth_user)):
    """分管领导看板（M4 完整实现；M0 返回骨架）。"""
    if user["role"] not in ("leader", "chairman", "sys_admin"):
        raise HTTPException(403, "仅主管领导及以上可查看")
    return {"widgets": [], "message": "分管看板（M4 交付）"}


@router.get("/chairman")
async def chairman_dashboard(user: dict = Depends(auth_user)):
    """董事长全域驾驶舱（M4 完整实现：数字孪生大屏 + 战略建议）。"""
    if user["role"] not in ("chairman", "sys_admin"):
        raise HTTPException(403, "仅董事长及以上可查看")
    return {"widgets": [], "strategy_suggestions": [],
            "message": "全域驾驶舱（M4 交付：数字孪生大屏 + 战略决策建议）"}
