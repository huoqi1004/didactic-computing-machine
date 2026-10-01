"""一次性夹具导出工具（非常规测试）
====================================
把 /agent-ops/workflow-definitions/* 的**真实响应**（同一 FastAPI 路由 + 真校验器 +
真版本表）写入 frontend/e2e/fixtures/workflow-designer/，供 Playwright 在浏览器里
回放，从而在没有可用后端进程时也能验证设计器 UI 的字段契约。

运行：DUMP_WF_FIXTURES=1 python -m pytest tests/test_dump_designer_fixtures.py -s
"""
from tests.test_workflow_dsl_f3 import _api_app, _reimb_v1, f3_env  # noqa: F401

import json
import os
import pathlib

import pytest
from httpx import ASGITransport, AsyncClient

OUT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures" / "workflow-designer"
BASE = "/api/v1/agent-ops/workflow-definitions"
NAME = "reimbursement_approval"


@pytest.mark.skipif(not os.getenv("DUMP_WF_FIXTURES"), reason="仅在显式请求时导出夹具")
async def test_dump_designer_fixtures(f3_env):
    from app.core.agent import workflow_versions as wfv

    await wfv.save_draft(NAME, "v1", _reimb_v1(), created_by="user_admin")
    await wfv.publish(NAME, "v1", 0, "user_admin")
    await wfv.save_draft(NAME, "v2", _reimb_v1(200), created_by="user_admin")
    await wfv.publish(NAME, "v2", 30, "user_admin")

    bad = {
        "name": "broken_flow", "entry": "a",
        "nodes": [
            {"id": "a", "type": "task", "handler": "shell.exec", "next": "g"},
            {"id": "g", "type": "gate",
             "threshold": {"key": "not_seeded_key", "default": 1},
             "next_approved": "END"},
            {"id": "orphan", "type": "task", "handler": "reimb.submit", "next": "END"},
        ],
    }

    transport = ASGITransport(app=_api_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dumps = {
            "meta.json": (await client.get(f"{BASE}/meta")).json(),
            "validate_ok.json": (await client.post(
                f"{BASE}/validate", json={"definition": _reimb_v1()})).json(),
            "validate_errors.json": (await client.post(
                f"{BASE}/validate", json={"definition": bad})).json(),
            "status.json": (await client.get(f"{BASE}/{NAME}")).json(),
            "definition.json": (await client.get(f"{BASE}/{NAME}/v1")).json(),
            "draft.json": (await client.put(
                f"{BASE}/{NAME}/v3/draft",
                json={"definition": _reimb_v1(300), "comment": "E2E 草稿"})).json(),
            "publish.json": (await client.post(
                f"{BASE}/{NAME}/v3/publish", json={"traffic": 0, "comment": "E2E 发布"})).json(),
            "rollback.json": (await client.post(
                f"{BASE}/{NAME}/rollback", json={"version": "v1"})).json(),
        }

    OUT.mkdir(parents=True, exist_ok=True)
    for fname, payload in dumps.items():
        (OUT / fname).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {fname}: {len(json.dumps(payload))} bytes")

    assert isinstance(dumps["status.json"]["canary"], dict)
    assert dumps["validate_errors.json"]["valid"] is False
    assert dumps["publish.json"]["status"] == "pending_approval"
