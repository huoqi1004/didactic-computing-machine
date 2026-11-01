"""
多租户隔离测试（P3-1 企业就绪）

覆盖：
1. TenantRegistry：注册/查询/列表/暂停/恢复/注销/状态校验
2. TenantQuota：按套餐生成配额
3. TenantContext：上下文设置/获取/清除/租户前缀/跨租户访问拦截
4. QuotaManager：配额检查/消耗/释放/用量查询/API限流
5. API 路由：注册/查询/暂停/配额用量
"""
import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes.tenant import router as tenant_router
from app.core.tenant import (
    tenant_registry,
    tenant_context,
    quota_manager,
    with_tenant_id,
    parse_tenant_id,
    assert_tenant_access,
    TenantPlan,
    TenantStatus,
    RESOURCE_AGENTS,
    RESOURCE_API_CALLS,
    RESOURCE_CONCURRENT_SAGAS,
)
from app.core.tenant.tenant import TenantQuota


# ============================================================
# 夹具：清理全局状态
# ============================================================

@pytest.fixture(autouse=True)
def reset_tenant_state():
    """测试前后重置租户注册表到初始状态（仅保留 default）"""
    for tid in list(tenant_registry._tenants.keys()):
        if tid != "default":
            del tenant_registry._tenants[tid]
    tenant_context.clear()
    quota_manager._api_windows.clear()
    quota_manager._concurrent.clear()
    yield
    for tid in list(tenant_registry._tenants.keys()):
        if tid != "default":
            del tenant_registry._tenants[tid]
    tenant_context.clear()
    quota_manager._api_windows.clear()
    quota_manager._concurrent.clear()


@pytest_asyncio.fixture
async def api_client():
    """创建仅含租户路由的测试客户端（ASGITransport，不触发 lifespan/认证）"""
    app = FastAPI()
    app.include_router(tenant_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ============================================================
# 1. TenantRegistry 测试
# ============================================================

def test_register_tenant():
    """注册租户返回带配额和状态的租户"""
    tenant = tenant_registry.register(name="Acme 制造集团")
    assert tenant.tenant_id.startswith("tnt-")
    assert tenant.name == "Acme 制造集团"
    assert tenant.status == TenantStatus.ACTIVE
    assert tenant.plan == TenantPlan.PROFESSIONAL
    assert tenant.quota.max_agents == 33


def test_register_tenant_with_custom_id_and_plan():
    """自定义租户ID和套餐"""
    tenant = tenant_registry.register(name="Acme", plan=TenantPlan.FREE, tenant_id="acme")
    assert tenant.tenant_id == "acme"
    assert tenant.quota.max_agents == 5  # FREE 套餐配额


def test_register_tenant_string_plan():
    """字符串套餐兼容"""
    tenant = tenant_registry.register(name="Beta", plan="free")
    assert tenant.plan == TenantPlan.FREE
    assert tenant.quota.max_agents == 5


def test_get_tenant():
    """按 ID 查询租户"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant = tenant_registry.get("acme")
    assert tenant is not None
    assert tenant.name == "Acme"


def test_get_tenant_not_found():
    """查询不存在的租户返回 None"""
    assert tenant_registry.get("nonexistent") is None


def test_list_tenants():
    """列出租户（含默认租户）"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_registry.register(name="Beta", tenant_id="beta")
    tenants = tenant_registry.list()
    ids = {t["tenant_id"] for t in tenants}
    assert "default" in ids
    assert "acme" in ids
    assert "beta" in ids


def test_list_tenants_by_status():
    """按状态过滤租户"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_registry.suspend("acme")
    active = tenant_registry.list(status=TenantStatus.ACTIVE)
    suspended = tenant_registry.list(status=TenantStatus.SUSPENDED)
    active_ids = {t["tenant_id"] for t in active}
    suspended_ids = {t["tenant_id"] for t in suspended}
    assert "default" in active_ids
    assert "acme" in suspended_ids
    assert "acme" not in active_ids


def test_suspend_and_activate():
    """暂停与恢复租户"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    result = tenant_registry.suspend("acme")
    assert result["status"] == "success"
    assert tenant_registry.is_active("acme") is False

    result = tenant_registry.activate("acme")
    assert result["status"] == "success"
    assert tenant_registry.is_active("acme") is True


def test_unregister_marks_deleted():
    """注销租户标记为已删除"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    result = tenant_registry.unregister("acme")
    assert result["status"] == "success"
    assert tenant_registry.get("acme").status == TenantStatus.DELETED
    assert tenant_registry.is_active("acme") is False


def test_is_active_default_tenant():
    """默认租户始终活跃"""
    assert tenant_registry.is_active("default") is True


# ============================================================
# 2. TenantQuota 测试
# ============================================================

def test_quota_for_plan_free():
    """免费版配额限制严格"""
    quota = TenantQuota.for_plan(TenantPlan.FREE)
    assert quota.max_agents == 5
    assert quota.max_users == 10
    assert quota.max_api_calls_per_min == 60


def test_quota_for_plan_professional():
    """专业版配额"""
    quota = TenantQuota.for_plan(TenantPlan.PROFESSIONAL)
    assert quota.max_agents == 33
    assert quota.max_api_calls_per_min == 600


def test_quota_for_plan_enterprise():
    """企业版配额"""
    quota = TenantQuota.for_plan(TenantPlan.ENTERPRISE)
    assert quota.max_agents == 100
    assert quota.max_storage_mb == 51200


def test_quota_to_dict():
    """配额序列化"""
    quota = TenantQuota()
    d = quota.to_dict()
    assert d["max_agents"] == 33
    assert d["max_users"] == 50


# ============================================================
# 3. TenantContext 测试
# ============================================================

def test_set_and_get_tenant():
    """设置并获取当前租户"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_context.set_tenant("acme")
    assert tenant_context.get_tenant() == "acme"
    assert tenant_context.get_tenant_name() == "Acme"


def test_set_unknown_tenant_falls_back():
    """设置不存在的租户回退到默认"""
    tenant_context.set_tenant("ghost")
    assert tenant_context.get_tenant() == "default"


def test_set_suspended_tenant_falls_back():
    """设置已暂停租户回退到默认"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_registry.suspend("acme")
    tenant_context.set_tenant("acme")
    assert tenant_context.get_tenant() == "default"


def test_clear_tenant():
    """清除租户回退到默认"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_context.set_tenant("acme")
    tenant_context.clear()
    assert tenant_context.get_tenant() == "default"


def test_with_tenant_id():
    """实体ID添加租户前缀"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_context.set_tenant("acme")
    assert with_tenant_id("prod-001") == "acme:prod-001"


def test_parse_tenant_id():
    """解析租户前缀"""
    assert parse_tenant_id("acme:prod-001") == ("acme", "prod-001")
    assert parse_tenant_id("prod-001") == ("default", "prod-001")


def test_assert_tenant_access_allowed():
    """同租户访问允许"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_context.set_tenant("acme")
    assert assert_tenant_access("acme:prod-001") is True


def test_assert_tenant_access_blocked():
    """跨租户访问被拦截"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_registry.register(name="Beta", tenant_id="beta")
    tenant_context.set_tenant("acme")
    assert assert_tenant_access("beta:prod-001") is False


def test_contextvar_isolation():
    """不同任务的租户上下文相互隔离"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    tenant_registry.register(name="Beta", tenant_id="beta")
    results = {}

    async def task(tenant_id, key):
        tenant_context.set_tenant(tenant_id)
        await asyncio.sleep(0.05)
        results[key] = tenant_context.get_tenant()

    async def run():
        t1 = asyncio.create_task(task("acme", "r1"))
        t2 = asyncio.create_task(task("beta", "r2"))
        await asyncio.gather(t1, t2)

    asyncio.run(run())
    assert results["r1"] == "acme"
    assert results["r2"] == "beta"


# ============================================================
# 4. QuotaManager 测试
# ============================================================

def test_quota_check_within_limit():
    """配额内检查允许"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    result = quota_manager.check("acme", RESOURCE_AGENTS)
    assert result["allowed"] is True
    assert result["limit"] == 33


def test_quota_check_exceeded():
    """超过并发配额检查拒绝"""
    tenant_registry.register(name="Acme", tenant_id="acme", plan=TenantPlan.FREE)
    for _ in range(5):
        assert quota_manager.consume("acme", RESOURCE_AGENTS) is True
    result = quota_manager.check("acme", RESOURCE_AGENTS)
    assert result["allowed"] is False
    assert result["current"] == 5
    assert result["limit"] == 5


def test_quota_consume_and_release():
    """消耗与释放配额"""
    tenant_registry.register(name="Acme", tenant_id="acme", plan=TenantPlan.FREE)
    assert quota_manager.consume("acme", RESOURCE_AGENTS) is True
    assert quota_manager.consume("acme", RESOURCE_AGENTS) is True
    quota_manager.release("acme", RESOURCE_AGENTS)
    assert quota_manager._concurrent["acme"][RESOURCE_AGENTS] == 1


def test_quota_unknown_tenant_allows():
    """未知租户默认允许（不阻塞）"""
    result = quota_manager.check("ghost", RESOURCE_AGENTS)
    assert result["allowed"] is True


def test_quota_api_calls_limited():
    """API 调用频率限流"""
    tenant_registry.register(name="Acme", tenant_id="acme", plan=TenantPlan.FREE)
    for _ in range(60):
        assert quota_manager.consume("acme", RESOURCE_API_CALLS) is True
    assert quota_manager.consume("acme", RESOURCE_API_CALLS) is False


def test_quota_get_usage():
    """用量查询"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    quota_manager.consume("acme", RESOURCE_CONCURRENT_SAGAS)
    usage = quota_manager.get_usage("acme")
    assert usage[RESOURCE_CONCURRENT_SAGAS]["current"] == 1
    assert usage[RESOURCE_CONCURRENT_SAGAS]["limit"] == 20


def test_quota_reset():
    """重置租户配额用量"""
    tenant_registry.register(name="Acme", tenant_id="acme")
    quota_manager.consume("acme", RESOURCE_AGENTS)
    quota_manager.reset("acme")
    assert quota_manager._concurrent.get("acme") is None


# ============================================================
# 5. API 路由测试
# ============================================================

async def test_tenant_api_register_and_get(api_client):
    """API：注册租户并查询"""
    resp = await api_client.post("/api/v1/tenants", json={"name": "API租户", "plan": "professional"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    tenant_id = body["tenant"]["tenant_id"]

    resp = await api_client.get(f"/api/v1/tenants/{tenant_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "API租户"


async def test_tenant_api_list(api_client):
    """API：租户列表"""
    await api_client.post("/api/v1/tenants", json={"name": "A公司", "plan": "free"})
    await api_client.post("/api/v1/tenants", json={"name": "B公司", "plan": "enterprise"})
    resp = await api_client.get("/api/v1/tenants")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tenants"]}
    assert "A公司" in names
    assert "B公司" in names


async def test_tenant_api_suspend_activate(api_client):
    """API：暂停与恢复租户"""
    resp = await api_client.post("/api/v1/tenants", json={"name": "C公司"})
    tenant_id = resp.json()["tenant"]["tenant_id"]

    resp = await api_client.post(f"/api/v1/tenants/{tenant_id}/suspend")
    assert resp.status_code == 200
    assert tenant_registry.is_active(tenant_id) is False

    resp = await api_client.post(f"/api/v1/tenants/{tenant_id}/activate")
    assert resp.status_code == 200
    assert tenant_registry.is_active(tenant_id) is True


async def test_tenant_api_quota_usage(api_client):
    """API：查询租户配额用量"""
    resp = await api_client.post("/api/v1/tenants", json={"name": "D公司", "plan": "free"})
    tenant_id = resp.json()["tenant"]["tenant_id"]

    resp = await api_client.get(f"/api/v1/tenants/{tenant_id}/quota")
    assert resp.status_code == 200
    body = resp.json()
    assert body["quota"]["max_agents"] == 5
    assert body["usage"]["agents"]["limit"] == 5


async def test_tenant_api_default_protected(api_client):
    """API：默认租户不可暂停/删除"""
    resp = await api_client.post("/api/v1/tenants/default/suspend")
    assert resp.status_code == 400

    resp = await api_client.delete("/api/v1/tenants/default")
    assert resp.status_code == 400


async def test_tenant_api_delete(api_client):
    """API：注销租户"""
    resp = await api_client.post("/api/v1/tenants", json={"name": "E公司"})
    tenant_id = resp.json()["tenant"]["tenant_id"]

    resp = await api_client.delete(f"/api/v1/tenants/{tenant_id}")
    assert resp.status_code == 200
    assert tenant_registry.is_active(tenant_id) is False


async def test_tenant_api_update_plan(api_client):
    """API：更新租户套餐与配额"""
    resp = await api_client.post("/api/v1/tenants", json={"name": "F公司", "plan": "free"})
    tenant_id = resp.json()["tenant"]["tenant_id"]

    resp = await api_client.put(f"/api/v1/tenants/{tenant_id}", json={"plan": "enterprise"})
    assert resp.status_code == 200
    assert resp.json()["tenant"]["quota"]["max_agents"] == 100


async def test_tenant_api_not_found(api_client):
    """API：查询不存在的租户返回 404"""
    resp = await api_client.get("/api/v1/tenants/nonexistent")
    assert resp.status_code == 404
