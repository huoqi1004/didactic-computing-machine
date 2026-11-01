"""
G1 追踪联调测试（roadmap-logging-sandbox-2026-10-01 G1.5）
==========================================================

覆盖：
1. trace 模块单元：32hex 格式（W3C 兼容）/ ensure 幂等复用 / business_key 登记；
2. 跨异步任务边界显式注入（trace_context，P2-2 验收点）；
3. HTTP 中间件：X-Trace-Id 生成与续传（响应头回写）；
4. 治理面：审计条目携带 trace_id；工具结果提取 business_key 并登记链路；
5. 红线 2（G1.4）：写工具补偿未声明幂等 → 拒绝注册；
6. 全链一致断言：报销提交（对话→治理面→执行→审计）同链 trace_id +
   business_key 贯穿（M-G1 出口标准）；
7. audit_logs 落库行携带追踪键列（G1.2）。
"""
import asyncio

import pytest
import pytest_asyncio

from app.core.observability.trace import (
    bind_trace,
    ensure_trace_id,
    extract_business_key,
    get_business_key,
    get_trace_id,
    is_valid_trace_id,
    new_trace_id,
    set_business_key,
    trace_context,
)


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + bootstrap 建表（含 operation_ledger）+ 全局引擎重置。"""
    import app.core.database as database

    db_file = tmp_path / "trace_g1.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    from app.core.config import settings

    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


@pytest.fixture(autouse=True)
def _clean_trace_context():
    """用例前后清空追踪上下文，保证隔离。"""
    from app.core.observability import trace as trace_mod

    trace_mod._trace_id_var.set("")
    trace_mod._business_key_var.set("")
    yield
    trace_mod._trace_id_var.set("")
    trace_mod._business_key_var.set("")


# ===========================================================================
# 1. trace 模块单元
# ===========================================================================

def test_trace_id_format_w3c_compatible():
    """32 位十六进制（W3C traceparent trace-id 段兼容，裁决 D2 附加条件）。"""
    for _ in range(20):
        assert is_valid_trace_id(new_trace_id())
    assert not is_valid_trace_id("")
    assert not is_valid_trace_id("xyz")
    assert not is_valid_trace_id("ABCDEF01" * 4)  # 大写非法（W3C 规定小写）


def test_ensure_trace_id_reuses_within_context():
    """ensure 幂等：同链路多次调用返回同一 trace_id（不产生链路分裂）。"""
    first = ensure_trace_id()
    assert first == ensure_trace_id() == ensure_trace_id()
    assert get_trace_id() == first


def test_business_key_binding():
    set_business_key("RE-2026-0001")
    assert get_business_key() == "RE-2026-0001"
    bind_trace(trace_id="a" * 32)
    assert get_trace_id() == "a" * 32
    assert get_business_key() == "RE-2026-0001"  # 空值不覆盖


def test_extract_business_key_from_tool_result():
    """显式字段优先；启发式覆盖常见单据字段；嵌套一层 dict 可提取。"""
    assert extract_business_key({"reimbursement_id": "RE-1"}) == "RE-1"
    assert extract_business_key({"order_id": 42}) == "42"
    # 显式声明优先于启发式默认顺序
    assert extract_business_key(
        {"order_id": "O-1", "record_id": "R-1"}, explicit_field="record_id"
    ) == "R-1"
    assert extract_business_key({"nested": {"transfer_id": "TF-9"}}) == "TF-9"
    # 提取不到 = 空串（可接受降级，不阻断业务）
    assert extract_business_key({"foo": "bar"}) == ""
    assert extract_business_key("not-a-dict") == ""


async def test_trace_context_cross_task_boundary():
    """跨异步任务边界：trace_context 显式注入（P2-2），退出后上下文复位。"""
    seen = {}

    async def body():
        seen["tid"] = get_trace_id()
        seen["bk"] = get_business_key()
        await asyncio.sleep(0)
        seen["tid_after_yield"] = get_trace_id()

    ensure_trace_id()
    injected = new_trace_id()
    async with trace_context(trace_id=injected, business_key="PO-7"):
        await asyncio.create_task(body())
        assert get_trace_id() == injected
        assert get_business_key() == "PO-7"
    # 退出复位：回到注入前的链路
    assert get_trace_id() != injected


# ===========================================================================
# 2. HTTP 中间件（G1.1）
# ===========================================================================

async def test_middleware_generates_and_echoes_trace_header():
    """无上游头 → 生成 32hex trace_id 并回写响应头。"""
    import httpx
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/health")
    assert resp.status_code in (200, 404)  # /health 具体实现不挑剔，中间件已跑通
    assert is_valid_trace_id(resp.headers.get("x-trace-id", ""))


async def test_middleware_continues_upstream_trace_id():
    """上游续传：携带合法 X-Trace-Id 的请求原样沿用（跨服务链路串联）。"""
    import httpx
    from app.main import app

    upstream = new_trace_id()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/health", headers={"X-Trace-Id": upstream})
    assert resp.headers.get("x-trace-id") == upstream


# ===========================================================================
# 3. 红线 2（G1.4）：写工具补偿未声明幂等 → 拒绝注册
# ===========================================================================

async def test_write_tool_without_idempotent_rejected():
    from pydantic import BaseModel

    from app.core.agent.tools_v2 import ToolRegistryV2, ToolRisk

    class Args(BaseModel):
        value: int = 1

    async def handler(ctx, value=1):
        return {}

    async def comp(ctx, args, result):
        return None

    reg = ToolRegistryV2()
    spec_cls = __import__("app.core.agent.tools_v2", fromlist=["ToolSpecV2"]).ToolSpecV2
    with pytest.raises(ValueError, match="幂等"):
        reg.register(spec_cls(
            name="non_idem_tool", description="t", handler=handler,
            args_model=Args, risk_level=ToolRisk.WRITE_LOW,
            resource="t", action="create",
            compensation=comp,
            effects_fn=lambda a: "x",
            agent_domain="t",
            idempotent=False,
        ))
    # 声明幂等后注册通过
    reg.register(spec_cls(
        name="idem_tool", description="t", handler=handler,
        args_model=Args, risk_level=ToolRisk.WRITE_LOW,
        resource="t", action="create",
        compensation=comp, effects_fn=lambda a: "x",
        agent_domain="t", idempotent=True,
    ))
    assert reg.get("idem_tool") is not None


# ===========================================================================
# 4. 治理面：审计携带追踪键 + business_key 提取（G1.3）
# ===========================================================================

async def test_governor_audit_entry_carries_trace_and_business_key(sqlite_env, monkeypatch):
    """工具执行 → 审计条目带 trace_id；结果提取 business_key 并登记上下文。"""
    from pydantic import BaseModel

    from app.core.agent.governor import AgentGovernor
    from app.core.agent.tools_v2 import ToolContext, ToolRegistryV2, ToolRisk
    from app.core.rbac import rbac_engine

    class SubmitArgs(BaseModel):
        amount: float = 0

    async def fake_submit(ctx, amount=0):
        return {"reimbursement_id": "RE-TRACE-1", "status": "pending"}

    async def comp(ctx, args, result):
        return None

    reg = ToolRegistryV2()
    reg.register(__import__("app.core.agent.tools_v2", fromlist=["ToolSpecV2"]).ToolSpecV2(
        name="trace_submit_tool", description="t", handler=fake_submit,
        args_model=SubmitArgs, risk_level=ToolRisk.WRITE_LOW,
        resource="reimbursement", action="create",
        compensation=comp, effects_fn=lambda a: "创建报销单",
        agent_domain="test", idempotent=True,
    ))
    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    my_trace = new_trace_id()
    bind_trace(trace_id=my_trace)
    gov = AgentGovernor(registry=reg)
    result = await gov.execute(
        "trace_submit_tool", {"amount": 100},
        ToolContext(user_id="user_admin", agent_key="t"),
    )
    assert result.ok
    assert result.meta["trace_id"] == my_trace
    assert result.meta["business_key"] == "RE-TRACE-1"
    # business_key 已登记进上下文（后续审计/日志自动继承）
    assert get_business_key() == "RE-TRACE-1"

    # 审计链：executed 条目携带同链 trace_id + business_key
    from app.core.security.audit import audit_chain

    executed = [e for e in audit_chain._entries if e.action.endswith("executed")]
    assert executed, "应有 executed 审计条目"
    assert executed[-1].trace_id == my_trace
    assert executed[-1].business_key == "RE-TRACE-1"


# ===========================================================================
# 5. audit_logs 落库行携带追踪键列（G1.2）
# ===========================================================================

async def test_audit_db_row_persists_trace_columns(sqlite_env, monkeypatch):
    """审计落库：trace_id / business_key 列写入（哈希计算不含追踪键）。"""
    from sqlalchemy import text

    from app.core.security.audit import audit_chain

    engine = sqlite_env
    # sqlite 无 NOW(6) 默认值兼容——手工建同构表验证写入路径（MySQL 环境
    # 由 migrations.run_migrations 自动建表带列，见 test_bootstrap_smoke）
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                entry_id VARCHAR(64) PRIMARY KEY,
                actor_did VARCHAR(128) NOT NULL,
                action VARCHAR(128) NOT NULL,
                resource VARCHAR(256) NOT NULL,
                result VARCHAR(32) NOT NULL,
                details TEXT NOT NULL,
                risk_level VARCHAR(16) NOT NULL DEFAULT 'low',
                prev_hash VARCHAR(128),
                entry_hash VARCHAR(128),
                trace_id VARCHAR(64),
                business_key VARCHAR(128),
                timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))

    monkeypatch.setattr(audit_chain, "_db_available", True, raising=False)
    monkeypatch.setattr(audit_chain, "_db_initialized", True, raising=False)
    try:
        entry_id = await audit_chain.log(
            actor_did="user_admin", action="tool.trace.db_persist",
            resource="t:t", result="success",
            details={"k": "v"}, risk_level="low",
            trace_id="b" * 32, business_key="RE-DB-1",
        )
    finally:
        monkeypatch.setattr(audit_chain, "_db_available", False, raising=False)

    async with engine.connect() as conn:
        row = (
            await conn.execute(text(
                "SELECT trace_id, business_key FROM audit_logs WHERE entry_id = :e"
            ), {"e": entry_id})
        ).first()
    assert row is not None
    assert row[0] == "b" * 32 and row[1] == "RE-DB-1"


# ===========================================================================
# 6. 全链一致（M-G1 出口）：对话→治理面→执行→审计 同链贯穿
# ===========================================================================


@pytest_asyncio.fixture
async def registered_agents(sqlite_env):
    """注册报销 Agent（工具 handler 运行时解析）。"""
    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.agents.reimbursement.agent import ReimbursementAgent
    import app.agents.reimbursement.tools  # noqa: F401

    agent = ReimbursementAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await orchestrator.register_agent(agent)
    yield
    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain == agent.domain:
            del orchestrator._agents[key]


async def test_full_chain_trace_consistency_reimbursement_submit(
    sqlite_env, registered_agents, monkeypatch
):
    """报销提交全链：绑定 trace 后，治理面全部审计条目同链；
    提交产生报销单号 → business_key 登记并贯穿后续审计。"""
    from langchain_core.messages import AIMessage

    from app.core.agent import runtime as runtime_mod
    from app.core.agent.tools_v2 import ToolContext
    from app.core.security.audit import audit_chain

    class _ScriptedLLM:
        def __init__(self, script):
            self.script = list(script)

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return self.script.pop(0)

    script_llm = _ScriptedLLM([
        AIMessage(content="", tool_calls=[{
            "name": "reimbursement_submit",
            "args": {"employee_name": "追踪用户", "amount": 66, "description": "追踪联调",
                     "category": "office"},
            "id": "c1",
        }]),
        AIMessage(content="报销已提交"),
    ])
    monkeypatch.setattr(runtime_mod.agent_runtime, "_get_llm", lambda: script_llm)
    runtime_mod.agent_runtime._graphs.clear()

    my_trace = new_trace_id()
    bind_trace(trace_id=my_trace)

    result = await runtime_mod.agent_runtime.run(
        "reimbursement", "帮我提交一笔66元的报销",
        ToolContext(user_id="user_admin", username="admin",
                    agent_key="reimbursement", thread_id="trace-e2e-1"),
    )
    assert result["status"] == "done"

    # 全链断言：本轮所有审计条目同链 trace_id
    entries = [e for e in audit_chain._entries if e.trace_id]
    assert entries, "审计条目应携带 trace_id"
    assert all(e.trace_id == my_trace for e in entries), \
        f"链路分裂: {sorted({e.trace_id for e in entries})}"

    # business_key：提交工具产生报销单号 → 登记进审计（凭单据号可反查）
    executed = [e for e in entries if e.action == "tool.reimbursement_submit.executed"]
    assert executed and executed[-1].business_key, "提交单据号应登记为 business_key"
    reimb_id = executed[-1].business_key

    # 反查演练（P0-1）：凭单据号从审计链拉出同业务全部条目
    related = [e for e in audit_chain._entries if e.business_key == reimb_id]
    assert len(related) >= 1
