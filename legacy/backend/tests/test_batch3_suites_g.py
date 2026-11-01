"""
批次三新增套件测试：意图接线 / code-exec 工具 / 监理文档工作流 / Skill 矩阵
==============================================================================
"""
import json

import pytest
import pytest_asyncio

from app.core.config import settings


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    import app.core.database as database

    db_file = tmp_path / "batch_g.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info
    yield engine
    await database.close_db()


@pytest.fixture(autouse=True)
def _pin_rules(monkeypatch):
    """CI 规则模式（conftest 已钉，双保险）；沙箱开关每用例自控。"""
    monkeypatch.setattr(settings, "llm_enabled", False)
    yield


# ===========================================================================
# 1. runtime 意图接线（计划 3.1）
# ===========================================================================

async def test_runtime_run_carries_intent(sqlite_env, monkeypatch):
    """runtime.run 经意图层分类，intent 进操作日志 details（3.1 接线验证）。"""
    from langchain_core.messages import AIMessage

    from app.core.agent import runtime as runtime_mod
    from app.core.agent.tools_v2 import ToolContext
    from app.core.observability.operation_log import operation_log_buffer
    import app.agents.reimbursement.tools  # noqa: F401  （域内需已注册工具）

    class _LLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="已为您查询")

    monkeypatch.setattr(runtime_mod.agent_runtime, "_get_llm", lambda: _LLM())
    runtime_mod.agent_runtime._graphs.clear()

    r = await runtime_mod.agent_runtime.run(
        "reimbursement", "查询最近的报销单",
        ToolContext(user_id="user_admin", agent_key="reimbursement",
                    thread_id="intent-e2e"))
    assert r["status"] == "done"

    buffered = [x for x in operation_log_buffer._buf
                if x["action"] == "llm.interaction"]
    assert buffered and buffered[-1]["details"].get("intent") == "query", \
        f"意图应落操作日志 details: {buffered[-1:]}"


# ===========================================================================
# 2. code-exec 工具（D-H3）
# ===========================================================================

from app.core.agent.tools_v2 import ToolRegistryV2, tool_registry_v2


def _make_registry_with_analysis(sandbox_enabled: bool):
    """独立注册表 + 沙箱开关（零 reload、零全局污染，D-H3 测试口径）。"""
    from app.core.config import settings as _settings

    _settings.sandbox_enabled = sandbox_enabled
    reg = ToolRegistryV2()
    import app.agents.platform.tools as _pt

    ok = _pt._register_analysis_tool(registry=reg)
    return reg, ok


def test_code_exec_registration_gated_by_sandbox(sqlite_env):
    from app.core.agent.tools_v2 import ToolRisk
    from app.agents.platform import tools as _pt

    # 关：注册被闸门拒绝
    settings.sandbox_enabled = False
    reg_off = ToolRegistryV2()
    assert _pt._register_analysis_tool(registry=reg_off) is False
    assert reg_off.get("python_data_analysis") is None

    # 开：注册成功 + write_high + code-exec 标签
    settings.sandbox_enabled = True
    reg_on = ToolRegistryV2()
    assert _pt._register_analysis_tool(registry=reg_on) is True
    spec = reg_on.get("python_data_analysis")
    assert spec.risk_level.value == "write_high" and "code-exec" in spec.tags
    settings.sandbox_enabled = False


async def test_code_exec_runs_analysis_in_sandbox(sqlite_env, monkeypatch):
    import asyncio
    import json as _json

    from app.agents.platform.tools import (_build_analysis_harness,
                                           _parse_analysis_result,
                                           DataAnalysisArgs)
    from app.core.agent.tools_v2 import ToolRegistryV2, ToolContext
    from app.core.sandbox.executor import sandbox_executor

    settings.sandbox_enabled = True
    reg = ToolRegistryV2()

    class _FakeProc:
        returncode = 0

        async def communicate(self, stdin_data):
            ns = {"__name__": "__sandbox__"}
            exec(stdin_data.decode(), ns)  # 受控执行（测试环境）
            out = "__ANALYSIS_RESULT__" + _json.dumps(ns["result"])
            return (out.encode(), b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        lambda *a, **k: _AsyncCtx(_FakeProc()))

    spec = DataAnalysisArgs(
        dataset_json=_json.dumps([{"amount": 100}, {"amount": 300}]),
        code="result = {'total': sum(d['amount'] for d in data)}")
    harness = _build_analysis_harness(spec.dataset_json, spec.code)
    r = await sandbox_executor.run(harness, timeout_s=5)
    assert r.ok and _parse_analysis_result(r.stdout) == {"total": 400}


class _AsyncCtx:
    def __init__(self, proc):
        self.proc = proc

    def __await__(self):
        if False:
            yield
        return self.proc


async def test_code_exec_runs_analysis_in_sandbox(tmp_path, monkeypatch):
    from app.core.agent.tools_v2 import ToolContext
    from app.core.config import settings as _s

    # 直接构造执行环境：启用沙箱 + mock 容器通道
    monkeypatch.setattr(_s := __import__("app.core.config", fromlist=["settings"]).settings,
                        "sandbox_enabled", True)
    import asyncio

    from app.agents.platform.tools import (_build_analysis_harness,
                                           _parse_analysis_result)
    from app.core.sandbox.executor import sandbox_executor

    class _FakeProc:
        returncode = 0

        async def communicate(self, stdin_data):
            # 模拟容器：data 可用、result 被回传
            harness = stdin_data.decode()
            assert "data = json.loads" in harness
            ns = {"__name__": "__sandbox__"}
            exec(harness, ns)  # 受控执行（测试环境）
            out = "__ANALYSIS_RESULT__" + json.dumps(ns["result"])
            return (out.encode(), b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        lambda *a, **k: _async_return(_FakeProc()))

    harness = _build_analysis_harness(
        json.dumps([{"amount": 100}, {"amount": 300}]),
        "result = {'total': sum(d['amount'] for d in data)}")
    r = await sandbox_executor.run(harness, timeout_s=5)
    assert r.ok and _parse_analysis_result(r.stdout) == {"total": 400}


class _async_return:
    def __init__(self, value):
        self._v = value

    def __await__(self):
        if False:
            yield
        return self._v


# ===========================================================================
# 3. 监理文档工作流（D-H5：含人审节点）
# ===========================================================================

async def test_supervisor_doc_workflow_full_loop(sqlite_env, monkeypatch):
    """起草→人审 gate→定稿 docx：全链 + 驳回路径。"""
    from app.core.agent.orchestrator import orchestrator
    from app.core.agent.workflows import start_workflow
    from app.core.agent.tools_v2 import ToolContext
    from app.core.security.did import did_registry
    from app.agents.project_supervisor.agent import ProjectSupervisorAgent

    sup = ProjectSupervisorAgent()
    sup.identity = did_registry.register_agent(
        name=sup.name, domain=sup.domain,
        capabilities=sup.capabilities, security_clearance=sup.security_clearance)
    await orchestrator.register_agent(sup)
    # proj-003 已由 _seed_supervision 注入（工程样机阶段）

    r = await start_workflow(
        "supervisor_doc_workflow",
        {"project_id": "proj-003", "doc_type": "监理大纲"},
        ToolContext(user_id="user_admin", username="admin",
                    agent_key="workflow:supervisor_doc_workflow"),
        document_key="proj-003",
    )
    assert r["status"] == "awaiting_approval", r  # gate 必经人审（D-H5）
    thread_id = r["thread_id"]

    # 批准 → finalize 生成 docx
    from app.core.agent.workflows import resume_workflow
    r2 = await resume_workflow(
        "supervisor_doc_workflow", thread_id,
        {"approved": True, "comment": "大纲确认"}, None)
    assert r2["status"] == "done", r2
    assert "download_url" in r2["result"]

    # 驳回路径：再来一轮 → 驳回 → status rejected
    r3 = await start_workflow(
        "supervisor_doc_workflow",
        {"project_id": "proj-003", "doc_type": "实施方案"},
        ToolContext(user_id="user_admin", username="admin",
                    agent_key="workflow:supervisor_doc_workflow"),
    )
    assert r3["status"] == "awaiting_approval"
    r4 = await resume_workflow(
        "supervisor_doc_workflow", r3["thread_id"],
        {"approved": False, "comment": "大纲需重写"}, None)
    assert r4["status"] == "done" and r4["result"]["status"] == "rejected"

    for k in list(orchestrator._agents.keys()):
        if orchestrator._agents[k].domain == sup.domain:
            del orchestrator._agents[k]
