"""一次性夹具导出工具（非常规测试）
====================================
把 Agent 定制中心用到的**真实响应**（同一 FastAPI 路由 + 真定制服务 + 真版本表/审批表）
写入 frontend/e2e/fixtures/agent-customization/，供 Playwright 在浏览器里回放，
从而在没有可用后端进程时也能验证 F2.7 二期 UI 的字段契约与权限门。

刻意保留一条待批的放宽变更（工具重新启用），让"待审批队列 + 矩阵计数"在真机里可见。

运行：DUMP_CUST_FIXTURES=1 python -m pytest tests/test_dump_customization_fixtures.py -s
"""
from tests.test_customization import cust_env  # noqa: F401

import json
import os
import pathlib

import pytest
from httpx import ASGITransport, AsyncClient

OUT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures" / "agent-customization"
BASE = "/api/v1/agent-ops/customizations"


def _api_app():
    """挂定制 + 工具目录 + 人审队列路由，带假登录态。"""
    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.api.routes import agent_ops, customizations

    class FakeAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = {"user_id": "user_admin", "username": "admin"}
            return await call_next(request)

    app = FastAPI()
    app.include_router(agent_ops.router)
    app.include_router(customizations.router)
    app.add_middleware(FakeAuth)
    return app


@pytest.mark.skipif(not os.getenv("DUMP_CUST_FIXTURES"), reason="仅在显式请求时导出夹具")
async def test_dump_customization_fixtures(cust_env):
    from app.core.agent.tools_v2 import tool_registry_v2

    write_low = next(t.name for t in tool_registry_v2.list_specs("reimbursement")
                    if t.risk_level.value == "write_low")
    offline = next(t.name for t in tool_registry_v2.list_specs("reimbursement")
                   if t.name != write_low)

    transport = ASGITransport(app=_api_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # ① 收紧：风险上调 + 停用（即时生效）
        await client.put(f"{BASE}/tool/{write_low}", json={
            "overrides": {"risk_override": "write_high", "reason": "大额单据收紧"},
            "enabled": True, "comment": "E2E：风险上调"})
        await client.put(f"{BASE}/tool/{offline}", json={
            "overrides": {"reason": "临时下线"}, "enabled": False, "comment": "E2E：停用"})

        # ② 放宽：重新启用 → 转人审（这条**留在待批**，用于验证队列）
        relax = (await client.put(f"{BASE}/tool/{offline}", json={
            "overrides": {}, "enabled": True, "comment": "E2E：重新启用（放宽）"})).json()

        # ③ Agent 级定制：人设 + 温度 + 域业务参数（即时生效）
        agent_applied = (await client.put(f"{BASE}/agent/reimbursement", json={
            "overrides": {
                "prompt_override": "你是报销领域的严谨审核助手，只依据单据事实作答，金额存疑必须转人审。",
                "temperature": 0.2,
                "daily_token_budget": 60000,
                "tool_whitelist": [write_low],
                "params": {"require_receipt": True},
            },
            "enabled": True, "comment": "E2E：Agent 卡片编辑"})).json()

        # ④ 审批路由矩阵：任何方向都转人审（红线 2）
        matrix_rules = {"rules": [
            {"document_type": "treasury_fund_transfer_request", "amount_min": 100000,
             "approve_roles": ["cfo", "chairman"], "mode": "all"},
            {"document_type": "reimbursement_submit", "amount_min": 0, "amount_max": 5000,
             "approve_roles": ["fin_manager"], "mode": "any"},
        ], "default": "legacy"}
        matrix_pending = (await client.put(f"{BASE}/gate/approval_route_matrix", json={
            "overrides": matrix_rules, "enabled": True,
            "comment": "E2E：矩阵首次下发"})).json()

        approvals = (await client.get("/api/v1/agent-ops/approvals",
                                      params={"status": "pending"})).json()
        matrix_thread = matrix_pending["thread_id"]
        decided = (await client.post(
            f"/api/v1/agent-ops/approvals/{matrix_thread}/decide",
            json={"approved": True, "comment": "E2E：矩阵批准"})).json()

        # ⑤ 撤销一次变更（回到该版本变更前）
        versions_all = (await client.get(f"{BASE}/versions",
                                        params={"limit": 100})).json()
        target_ver = next(v for v in versions_all["versions"]
                          if v["type"] == "tool" and v["target"] == write_low)
        rollback = (await client.post(f"{BASE}/tool/{write_low}/rollback",
                                      json={"version_id": target_ver["id"]})).json()

        dumps = {
            "tools.json": (await client.get("/api/v1/agent-ops/tools")).json(),
            "meta.json": (await client.get(f"{BASE}/meta")).json(),
            "customizations.json": (await client.get(BASE)).json(),
            "versions.json": (await client.get(f"{BASE}/versions")).json(),
            "approvals_pending.json": (await client.get(
                "/api/v1/agent-ops/approvals", params={"status": "pending"})).json(),
            "apply_relax.json": relax,
            "apply_agent.json": agent_applied,
            "matrix_pending.json": matrix_pending,
            "matrix_decided.json": decided,
            "rollback.json": rollback,
        }

    OUT.mkdir(parents=True, exist_ok=True)
    for fname, payload in dumps.items():
        (OUT / fname).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {fname}: {len(json.dumps(payload, ensure_ascii=False))} bytes")

    assert relax["status"] == "pending_approval"
    assert agent_applied["status"] == "applied"
    assert matrix_pending["status"] == "pending_approval"
    assert decided["status"] == "executed", decided
    # 矩阵批准后进入生效表；工具那条放宽仍在待批
    active = {f"{c['type']}:{c['target']}": c
              for c in dumps["customizations.json"]["customizations"]}
    assert "gate:approval_route_matrix" in active, active
    assert active[f"tool:{offline}"]["enabled"] is False, (
        "待批的启用变更不得顶掉生效中的停用（红线 2）")
    assert len(dumps["approvals_pending.json"]["approvals"]) == 1, "队列应只剩工具那条待批"
    assert rollback["status"] == "applied"
