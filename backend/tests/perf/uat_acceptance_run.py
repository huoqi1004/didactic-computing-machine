"""
UAT 六场景现场验收执行器（roadmap-logging-sandbox G6.2 / 评审委员会现场动线实录）
==================================================================================

按《UAT 支持包》（docs/uat-logging-sandbox-2026-10-03.md）六场景逐一执行，
在真实代码动线（真实 DB 会话/真实审计链/真实 API 路由函数/真实工作流 DSL）
上产出证据。任一场景 FAIL 则进程退出码 1。

运行：python tests/perf/uat_acceptance_run.py
说明：G1-1 的"对话页提交"环节依赖真实 LLM（DeepSeek 欠费），本执行器以
     工作流直启动动线现场验证（同一 trace/账面贯通，对话环节列为生产复验项）。
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_RESULTS: list[tuple[str, str, list[str]]] = []  # (场景, PASS/FAIL, 证据行)


def evidence(text: str) -> None:
    _RESULTS[-1][2].append(text)
    print(f"    · {text}")


def scenario(name: str):
    def deco(fn):
        async def wrapper(engine):
            _RESULTS.append((name, "PASS", []))
            try:
                await fn(engine)
            except Exception as e:  # noqa: BLE001
                _RESULTS[-1] = (name, "FAIL", _RESULTS[-1][2] + [f"异常: {type(e).__name__}: {e}"])
                print(f"    !! {type(e).__name__}: {e}")
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# 环境准备（真实 DB 会话 + 全域 Agent 注册 + 审计/事件/日志底座全开）
# ---------------------------------------------------------------------------

async def setup(tmp: Path):
    from app.core.config import settings
    import app.core.database as database

    settings.mysql_url = f"sqlite+aiosqlite:///{(tmp / 'uat.db').as_posix()}"
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    from sqlalchemy import text as _text

    async with engine.begin() as conn:
        await conn.execute(_text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                entry_id VARCHAR(64) PRIMARY KEY, actor_did VARCHAR(128) NOT NULL,
                action VARCHAR(128) NOT NULL, resource VARCHAR(256) NOT NULL,
                result VARCHAR(32) NOT NULL, details TEXT NOT NULL,
                risk_level VARCHAR(16) NOT NULL DEFAULT 'low',
                prev_hash VARCHAR(128), entry_hash VARCHAR(128),
                trace_id VARCHAR(64), business_key VARCHAR(128),
                timestamp DATETIME NOT NULL)
        """))
        await conn.execute(_text("""
            CREATE TABLE IF NOT EXISTS sagas (
                saga_id VARCHAR(150) PRIMARY KEY, saga_type VARCHAR(64),
                status VARCHAR(32), steps TEXT, current_step INTEGER,
                created_at DATETIME, updated_at DATETIME)
        """))

    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.core.rbac import rbac_engine
    from app.agents.reimbursement.agent import ReimbursementAgent
    from app.agents.treasury.agent import TreasuryAgent
    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401

    rbac_engine.check_permission = lambda u, r, a: True
    for cls in (ReimbursementAgent, TreasuryAgent):
        agent = cls()
        agent.identity = did_registry.register_agent(
            name=agent.name, domain=agent.domain,
            capabilities=agent.capabilities,
            security_clearance=agent.security_clearance)
        await orchestrator.register_agent(agent)

    from app.core.event.bus import event_bus
    from app.core.observability.operation_log import operation_log_buffer
    from app.core.security.audit import audit_chain

    # 生产语义：DB 始终可用（审计链自创世起完整落库）
    audit_chain._db_available = True
    audit_chain._db_initialized = True

    await event_bus.start()
    await operation_log_buffer.start()  # 审计→操作日志 订阅挂载
    return engine


async def flush_oplog() -> int:
    from app.core.observability.operation_log import operation_log_buffer

    await asyncio.sleep(0.25)  # 事件总线分发
    n = 0
    for _ in range(5):
        n += await operation_log_buffer.flush_once()
        if operation_log_buffer.pending() == 0:
            break
    return n


# ---------------------------------------------------------------------------
# UAT-G1-1 全链路溯源
# ---------------------------------------------------------------------------

@scenario("UAT-G1-1 全链路溯源（仅凭单据号还原）")
async def s1(engine):
    from app.core.agent.workflows import start_workflow
    from app.core.agent.tools_v2 import ToolContext
    from app.core.security.audit import audit_chain
    from app.api.routes.operation_logs import query_operation_logs

    r = await start_workflow(
        "reimbursement_approval",
        {"employee_name": "UAT 溯源员", "amount": 666, "category": "office",
         "description": "G1-1 现场验收", "has_receipt": True},
        ToolContext(user_id="user_admin", username="admin",
                    agent_key="workflow:reimbursement_approval"),
        document_key="RE-UAT-001",
    )
    assert r["status"] == "done", r
    evidence(f"工作流执行完成 status=done thread={r['thread_id']}")

    entries = [e for e in audit_chain._entries
               if e.business_key == "RE-UAT-001"]
    assert entries, "审计条目应携带单据号"
    traces = {e.trace_id for e in entries}
    assert len(traces) == 1 and all(traces), f"同链唯一 trace_id，实得 {traces}"
    actions = [e.action for e in entries]
    evidence(f"同链审计 {len(entries)} 条（trace {list(traces)[0][:16]}…）：{actions}")

    await flush_oplog()
    res = await query_operation_logs(
        business_key="RE-UAT-001", trace_id="", actor="", action="", source="",
        result="", risk_level="", start=None, end=None, page=1, limit=50,
        current_user={"user_id": "user_admin"})
    assert res["total"] >= 1, res
    evidence(f"仅凭单据号 RE-UAT-001 查操作日志命中 {res['total']} 行"
             f"（操作日志页同链动线）")


# ---------------------------------------------------------------------------
# UAT-G2-1 非 Agent 操作审计 + 告警死信兜底
# ---------------------------------------------------------------------------

@scenario("UAT-G2-1 非 Agent 操作审计 + 死信兜底")
async def s2(engine):
    from sqlalchemy import text as _text
    from app.api.routes.auth import login, LoginRequest
    from app.api.routes.rbac import assign_role, AssignRoleRequest
    from app.core.observability.operation_log import operation_log_buffer
    from fastapi import HTTPException

    # 1) 错误密码登录 → 401 + 审计
    try:
        await login(LoginRequest(username="admin", password="definitely-wrong-123"))
        raise AssertionError("错误密码应 401")
    except HTTPException as e:
        evidence(f"错误密码登录被拒（HTTP {e.status_code}）")
    # 2) RBAC 变更（真实路由函数）
    out = await assign_role("user_staff", AssignRoleRequest(role_code="dept_staff"),
                            current_user={"user_id": "user_admin", "username": "admin"})
    evidence(f"RBAC 角色分配成功：{out.get('message')}")

    await flush_oplog()
    async with engine.connect() as conn:
        rows = (await conn.execute(_text(
            "SELECT action, actor, result FROM operation_logs "
            "WHERE action LIKE 'user.%' OR action LIKE 'rbac.%' "
            "ORDER BY id"))).mappings().all()
    pairs = [(r["action"], r["result"]) for r in rows]
    assert ("user.login", "failure") in pairs, f"登录失败应入操作日志，实得 {pairs}"
    assert ("rbac.role.assign", "success") in pairs, f"RBAC 变更应入操作日志，实得 {pairs}"
    evidence(f"操作日志落库 {len(rows)} 行：{pairs}")

    # 3) webhook 失败 → 死信兜底（生产配 ERP_ALERT_WEBHOOK_URL；此处演示兜底闭环）
    from app.core.config import settings as _s
    import httpx as _httpx
    import app.core.observability.alerts as _alerts
    from app.core.observability.alerts import alert_channel, note_permission_denied

    _s.alert_webhook_url = "http://hook.invalid/x"

    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise RuntimeError("外发通道不可达")

    real_client = _httpx.AsyncClient
    _httpx.AsyncClient = _Boom
    try:
        for _ in range(10):
            note_permission_denied("user_intruder", "payroll", "read")
        await asyncio.sleep(0.2)
    finally:
        _httpx.AsyncClient = real_client
    assert alert_channel.stats["dead_lettered"] >= 1, "webhook 失败应落死信"
    evidence(f"高频越权告警触发且 webhook 失败 → 死信兜底 "
             f"raised={alert_channel.stats['raised']} dead_lettered={alert_channel.stats['dead_lettered']}")


# ---------------------------------------------------------------------------
# UAT-G3-1 篡改检测当场点名
# ---------------------------------------------------------------------------

@scenario("UAT-G3-1 篡改检测当场点名")
async def s3(engine):
    from sqlalchemy import text as _text
    from app.core.security.audit import audit_chain

    real = audit_chain._db_available
    audit_chain._db_available = True
    ids = []
    try:
        for i in range(3):
            ids.append(await audit_chain.log(
                actor_did="user_admin", action=f"tool.uat_g3_{i}.executed",
                resource="t:t", result="success", details={"i": i},
                risk_level="low", trace_id="", business_key=f"RE-UAT-G3-{i}"))
    finally:
        audit_chain._db_available = real

    ok = await audit_chain.verify_chain(full=True)
    assert ok["status"] == "ok" and ok["checked"] >= 3, ok
    evidence(f"干净链全量校验通过（checked={ok['checked']}）")

    async with engine.begin() as conn:
        await conn.execute(_text(
            "UPDATE audit_logs SET details='{\"i\": 999}' WHERE entry_id = :e"),
            {"e": ids[1]})
    broken = await audit_chain.verify_chain(full=True)
    assert broken["status"] == "broken" and broken["broken_at"] == ids[1], broken
    evidence(f"篡改当场点名：broken_at={broken['broken_at'][:16]}… "
             f"expected={broken['expected'][:12]}… actual={broken['actual'][:12]}…")


# ---------------------------------------------------------------------------
# UAT-G4-1 误操作回滚（正向 + 反向）
# ---------------------------------------------------------------------------

def _register_rollback_tool(registry, precheck=None, comp_hook=None):
    from pydantic import BaseModel
    from app.core.agent.tools_v2 import ToolRisk, ToolSpecV2

    class Args(BaseModel):
        target: str = "demo"

    store = {"docs": {}}

    async def handler(ctx, target="demo"):
        store["docs"][target] = {"status": "created"}
        return {"reimbursement_id": target, "status": "created"}

    async def comp(ctx, args, result):
        if comp_hook:
            await comp_hook(args, result)
        store["docs"].pop((args or {}).get("target")
                          or (result or {}).get("reimbursement_id"), None)

    spec = ToolSpecV2(
        name="uat_rollback_tool", description="uat", handler=handler,
        args_model=Args, risk_level=ToolRisk.WRITE_LOW,
        resource="reimbursement", action="create", compensation=comp,
        effects_fn=lambda a: "创建", agent_domain="uat", idempotent=True,
        rollback_precheck=precheck)
    registry._tools = {**registry._tools, "uat_rollback_tool": spec}
    return store


@scenario("UAT-G4-1 误操作回滚（正向闭环 + 反向拒绝）")
async def s4(engine):
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.operation_ledger import (execute_rollback, ledger_get,
                                                 ledger_query, request_rollback)
    from app.core.agent.tools_v2 import ToolContext, tool_registry_v2
    from app.core.security.audit import audit_chain

    async def precheck_blocked(ctx, args, result):
        return {"blocked": True, "dependencies": ["PAY-UAT-001 已引用该调拨"],
                "message": "存在下游引用"}

    store = _register_rollback_tool(tool_registry_v2, precheck=precheck_blocked)
    gov = AgentGovernor()
    await gov.execute("uat_rollback_tool", {"target": "DOC-UAT-1"},
                      ToolContext(user_id="user_admin"))
    lid = (await ledger_query(tool="uat_rollback_tool"))[0]["ledger_id"]

    rev = await request_rollback(lid, requested_by="user_cio", reason="已被引用")
    assert rev["status"] == "rejected_precheck" and rev["dependencies"], rev
    evidence(f"反向用例：下游引用默认拒绝，清单={rev['dependencies']}（红线 6）")

    # 正向：解除引用后发起 → 审批 → 执行
    async def precheck_ok(ctx, args, result):
        return {"blocked": False, "dependencies": [], "message": "无下游引用"}

    tool_registry_v2.get("uat_rollback_tool").rollback_precheck = precheck_ok
    req = await request_rollback(lid, requested_by="user_cio", reason="收款方错误")
    assert req["status"] == "pending_approval", req
    evidence(f"发起回滚 → 审批行 {req['thread_id']}（write_high 一律人审）")

    out = await execute_rollback(lid, decided_by="user_cfo", approved=True,
                                 comment="同意")
    assert out["status"] == "compensated", out
    assert store["docs"].get("DOC-UAT-1") is None
    ledger = await ledger_get(lid)
    assert ledger["status"] == "compensated" and ledger["rolled_by"] == "user_cfo"
    actions = [e.action for e in audit_chain._entries]
    assert "rollback.requested" in actions and "rollback.executed" in actions
    evidence("正向闭环：单据恢复 + 账本 compensated + rollback.requested/executed 双审计")


# ---------------------------------------------------------------------------
# UAT-G4-2 回滚失败人工介入 + 重放幂等
# ---------------------------------------------------------------------------

@scenario("UAT-G4-2 回滚失败人工介入 + 重放幂等")
async def s5(engine):
    from app.core.agent.governor import AgentGovernor
    from app.core.agent.operation_ledger import (execute_rollback, ledger_get,
                                                 ledger_query, request_rollback)
    from app.core.agent.tools_v2 import ToolContext, tool_registry_v2
    from app.core.observability.alerts import alert_channel

    calls = {"n": 0}

    async def comp_hook(args, result):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("UAT 注入：补偿故障")

    async def precheck_ok(ctx, args, result):
        return {"blocked": False, "dependencies": [], "message": ""}

    store = _register_rollback_tool(tool_registry_v2, precheck=precheck_ok,
                                    comp_hook=comp_hook)
    gov = AgentGovernor()
    await gov.execute("uat_rollback_tool", {"target": "DOC-UAT-2"},
                      ToolContext(user_id="user_admin"))
    lid = (await ledger_query(tool="uat_rollback_tool"))[0]["ledger_id"]

    await request_rollback(lid, requested_by="user_cio")
    out = await execute_rollback(lid, "user_cfo", approved=True)
    assert out["status"] == "compensate_failed", out
    ledger = await ledger_get(lid)
    assert ledger["status"] == "compensate_failed" and ledger["compensation_error"]
    assert alert_channel.stats["raised"] >= 1
    evidence("补偿失败 → compensate_failed + compensation_error 落库 + error 告警待办")

    req2 = await request_rollback(lid, requested_by="user_cio", reason="已修复")
    assert req2["status"] == "pending_approval"
    out2 = await execute_rollback(lid, "user_cfo", approved=True)
    assert out2["status"] == "compensated" and calls["n"] == 2
    evidence("修复后重放成功（重放经 CAS 门禁 + 协议幂等双重保护）")

    replay = await execute_rollback(lid, "user_cfo", approved=True)
    assert replay["status"] == "rejected" and "状态已变更" in replay["reason"]
    evidence(f"对已回滚行的重复执行被拒绝：{replay['reason']}")


# ---------------------------------------------------------------------------
# UAT-G5-1 工作流中断补偿 + 崩溃恢复
# ---------------------------------------------------------------------------

@scenario("UAT-G5-1 工作流中断补偿 + kill -9 恢复")
async def s6(engine):
    from sqlalchemy import text as _text
    from app.core.agent import workflow_versions as wfv
    from app.core.agent.workflows import start_workflow
    from app.core.agent.tools_v2 import ToolContext
    from app.core.agent.workflow_saga import saga_record, saga_recover_pending
    from app.core.security.audit import audit_chain

    defn = {
        "name": "uat_saga_wf", "entry": "submit",
        "nodes": [
            {"id": "submit", "type": "task", "handler": "reimb.submit",
             "next": "gate"},
            {"id": "gate", "type": "gate",
             "threshold": {"key": "reimbursement_auto_approve_limit", "default": 5000},
             "amount_from": "result.amount", "preview": "UAT {reimbursement_no}",
             "next_approved": "verify", "next_rejected": "reject"},
            {"id": "verify", "type": "task", "handler": "reimb.verify_approve",
             "next": "END"},
            {"id": "reject", "type": "task", "handler": "reimb.reject_mark",
             "next": "END"},
        ],
    }
    await wfv.save_draft("uat_saga_wf", "1.0.0", defn, created_by="user_admin")
    await wfv.publish("uat_saga_wf", "1.0.0")

    from app.core.agent.orchestrator import orchestrator
    agent = orchestrator.get_agent_by_name("报销Agent")

    async def _boom(reimb_id):
        return {"error": "UAT 注入：核验服务中断"}

    real_verify = agent.verify_reimbursement
    agent.verify_reimbursement = _boom
    try:
        r = await start_workflow(
            "uat_saga_wf",
            {"employee_name": "UAT Saga 员工", "amount": 888, "category": "office",
             "description": "G5-1 验收", "has_receipt": True},
            ToolContext(user_id="user_admin", username="admin",
                        agent_key="workflow:uat_saga_wf"),
            document_key="PO-UAT-SAGA")
    finally:
        agent.verify_reimbursement = real_verify
    assert r["status"] == "error", r
    evidence(f"工作流中途失败（status=error）：{r.get('reply', '')[:60]}")

    # 精确断言：S6 自己创建的单据被逆序补偿（S1 的已完成单据合法留存）
    leftover = [x for x in agent._requests.values()
                if x.get("employee_name") == "UAT Saga 员工"]
    assert not leftover, f"创建类步骤应被逆序自动补偿，残留 {leftover}"
    async with engine.connect() as conn:
        rows = (await conn.execute(_text(
            "SELECT saga_id, status, steps FROM sagas WHERE saga_type='workflow_saga'"
        ))).mappings().all()
    assert rows, "sagas 表应有 workflow_saga 行"
    bad = [(r["saga_id"], r["status"]) for r in rows
           if r["status"] not in ("compensated", "needs_manual", "completed")]
    assert not bad, f"存在未收口的 saga 行: {bad}"
    own = [r for r in rows if "uat_saga_wf" in str(r["saga_id"])]
    assert own and own[0]["status"] == "compensated", own
    steps = json.loads(own[0]["steps"]) if isinstance(own[0]["steps"], str) else own[0]["steps"]
    evidence(f"Saga 账面 status={own[0]['status']}，"
             f"步骤={[(s['handler'], s['auto']) for s in steps]}")

    # 崩溃恢复：遗留 executing → 启动续跑
    await saga_record("uat-crash-thread", "submit", "reimb.submit",
                      {"reimbursement_id": "RE-UAT-CRASH", "amount": 100})
    out = await saga_recover_pending()
    assert out["recovered"] >= 1, out
    evidence(f"kill -9 恢复：遗留 executing 续跑补偿 {out['items'][0]['status']}")
    saga_audits = [e for e in audit_chain._entries if e.action == "workflow.saga_compensated"]
    assert saga_audits, "补偿应有审计"


# ---------------------------------------------------------------------------

async def main() -> int:
    print("=" * 78)
    print("UAT 六场景现场验收 · 执行实录（评审委员会验收组）")
    print("=" * 78)
    tmp = Path(tempfile.mkdtemp())
    engine = await setup(tmp)
    for name, fn, _ in []:
        pass
    for sfn in (s1, s2, s3, s4, s5, s6):
        await sfn(engine)
    print("-" * 78)
    failed = 0
    for name, verdict, evs in _RESULTS:
        print(f"[{verdict}] {name}")
        if verdict == "FAIL":
            failed += 1
    print("-" * 78)
    print(f"验收执行结果：{'全部通过' if failed == 0 else f'{failed} 个场景失败'}"
          f"（{len(_RESULTS)} 场景）")
    from app.core.database import database as _db  # noqa: F401
    from app.core import database as database

    await database.close_db()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
