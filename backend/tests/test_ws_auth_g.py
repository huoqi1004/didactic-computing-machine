"""
P1-7 WS 凭证 + refresh 轮换整改测试（production-acceptance-review 整改回签）
============================================================================
"""
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient


def _make_token(user_id="user_admin"):
    from app.core.auth.jwt_manager import jwt_manager

    return jwt_manager.create_access_token({
        "user_id": user_id, "username": user_id,
        "role_codes": ["cio"], "full_name": "测试", "department": "信息部",
    })


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_ws_subprotocol_auth_accepted(client):
    """P1-7：凭证经 Sec-WebSocket-Protocol 子协议头 → 连接建立。"""
    token = _make_token()
    with client.websocket_connect(
        "/api/v1/ws/chat",
        subprotocols=["Authorization", token],
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert msg["user_id"] == "user_admin"


def test_ws_invalid_token_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/v1/ws/chat",
            subprotocols=["Authorization", "invalid.token.here"],
        ) as ws:
            ws.receive_json()


def test_ws_query_param_fallback_still_works(client):
    """兼容降级：旧客户端 query param 仍可用（服务端打废弃告警）。"""
    token = _make_token()
    with client.websocket_connect(f"/api/v1/ws/chat?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"


def test_ws_query_param_invalid_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/ws/chat?token=garbage") as ws:
            ws.receive_json()


# ---------------------------------------------------------------------------
# P1-6 配套：refresh 轮换（后端）
# ---------------------------------------------------------------------------

async def test_refresh_rotates_refresh_token(client):
    """每次 refresh 签发新 refresh_token（被盗凭证窗口收窄到一个刷新周期）。

    注意：本文件所有测试必须保持 **sync**——pytest-asyncio 的主循环与
    TestClient portal 循环争用单例（async 测试 + TestClient = lifespan
    启动死锁，见探针实验）。
    """
    login_resp = client.post("/api/v1/auth/login",
                             json={"username": "admin", "password": "admin123"})
    assert login_resp.status_code == 200
    old_refresh = login_resp.json()["refresh_token"]

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["refresh_token"] != old_refresh, "refresh_token 必须轮换"

    # 新 refresh_token 可再次换新
    resp2 = client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp2.status_code == 200
    assert resp2.json()["refresh_token"] != body["refresh_token"]
