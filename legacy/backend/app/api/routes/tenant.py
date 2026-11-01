"""
多租户管理 API 路由（P3-1 企业就绪）
====================================

提供租户管理 HTTP API：
- ``GET/POST /api/v1/tenants``: 租户列表 / 注册
- ``GET/PUT/DELETE /api/v1/tenants/{tenant_id}``: 租户详情 / 更新 / 注销
- ``POST /api/v1/tenants/{tenant_id}/suspend|activate``: 暂停 / 恢复
- ``GET /api/v1/tenants/{tenant_id}/quota``: 配额用量
"""
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.tenant import (
    tenant_registry,
    quota_manager,
    TenantPlan,
    TenantStatus,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/tenants", tags=["多租户管理"])


class RegisterTenantRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="租户名称")
    plan: TenantPlan = Field(TenantPlan.PROFESSIONAL, description="套餐")
    tenant_id: Optional[str] = Field(None, description="自定义租户ID")


class UpdateTenantRequest(BaseModel):
    name: Optional[str] = Field(None, description="租户名称")
    plan: Optional[TenantPlan] = Field(None, description="套餐")


@router.get("")
async def list_tenants(
    status: Optional[TenantStatus] = Query(None, description="按状态过滤"),
):
    """获取租户列表"""
    return {"tenants": tenant_registry.list(status)}


@router.post("")
async def register_tenant(req: RegisterTenantRequest):
    """注册新租户"""
    tenant = tenant_registry.register(
        name=req.name,
        plan=req.plan,
        tenant_id=req.tenant_id,
    )
    logger.info("tenant.api_registered", tenant_id=tenant.tenant_id, name=req.name)
    return {"status": "success", "tenant": tenant.to_dict()}


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: str):
    """获取租户详情"""
    tenant = tenant_registry.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    return tenant.to_dict()


@router.put("/{tenant_id}")
async def update_tenant(tenant_id: str, req: UpdateTenantRequest):
    """更新租户信息"""
    tenant = tenant_registry.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    if req.name:
        tenant.name = req.name
    if req.plan:
        tenant.plan = req.plan
        # 切换套餐时更新配额
        from app.core.tenant.tenant import TenantQuota
        tenant.quota = TenantQuota.for_plan(req.plan)
    logger.info("tenant.api_updated", tenant_id=tenant_id)
    return {"status": "success", "tenant": tenant.to_dict()}


@router.delete("/{tenant_id}")
async def delete_tenant(tenant_id: str):
    """注销租户（标记删除，保留审计）"""
    if tenant_id == "default":
        raise HTTPException(status_code=400, detail="cannot_delete_default_tenant")
    result = tenant_registry.unregister(tenant_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    logger.warning("tenant.api_unregistered", tenant_id=tenant_id)
    return result


@router.post("/{tenant_id}/suspend")
async def suspend_tenant(tenant_id: str):
    """暂停租户服务"""
    if tenant_id == "default":
        raise HTTPException(status_code=400, detail="cannot_suspend_default_tenant")
    result = tenant_registry.suspend(tenant_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/{tenant_id}/activate")
async def activate_tenant(tenant_id: str):
    """恢复租户服务"""
    result = tenant_registry.activate(tenant_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{tenant_id}/quota")
async def get_tenant_quota(tenant_id: str):
    """获取租户配额与当前用量"""
    tenant = tenant_registry.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    usage = quota_manager.get_usage(tenant_id)
    return {
        "tenant_id": tenant_id,
        "quota": tenant.quota.to_dict(),
        "usage": usage,
    }
