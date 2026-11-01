"""
专家体系 API（PRD expert-team-orchestration v1.0 · FR-E2/E4）
=============================================================

- GET  /conversation/experts          专家目录发现（元数据视图）
- GET  /conversation/experts/{name}   专家详情（方法论全文 + KPI 聚合）

专家是 Agent 之上的角色层：绑定基座 Agent + 方法论 SOP + 技能集 +
工具白名单 + 日预算 + 检查清单。绑定 Agent 缺席时降级"建议模式"。
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.middleware.auth import get_current_user
from app.core.agent.experts import expert_registry

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/conversation/experts", tags=["专家体系"])


@router.get("", summary="专家目录发现（FR-E2，元数据视图）")
async def list_experts(
    domain: str = Query("", max_length=64, description="按域过滤"),
    current_user: dict = Depends(get_current_user),
):
    items = expert_registry.list_experts(domain=domain or "")
    return {"items": items, "total": len(items)}


@router.get("/{name}", summary="专家详情（方法论全文 + KPI 聚合，FR-E1/E4）")
async def get_expert(
    name: str,
    days: int = Query(30, ge=1, le=90, description="KPI 统计窗口（天）"),
    current_user: dict = Depends(get_current_user),
):
    expert = expert_registry.get(name)
    if expert is None:
        raise HTTPException(status_code=404, detail="专家不存在")
    detail = expert.to_detail()
    detail["kpi_window"] = await expert_registry.kpi(expert, days=days)
    # 建议模式标注：绑定 Agent 缺席时，专家仅提供方法论与检查清单
    detail["advisory_mode"] = True
    try:
        from app.core.agent.orchestrator import orchestrator

        detail["advisory_mode"] = orchestrator.get_agent_by_name(expert.agent) is None
    except Exception:  # noqa: BLE001
        pass
    return detail
