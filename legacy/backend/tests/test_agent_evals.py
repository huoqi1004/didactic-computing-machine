"""
Agent 编排评测集运行器（路线图 Phase B / B6）
==============================================

**CI 模式（默认执行）**：脚本 LLM 按用例的 expected_tool_calls 回放，
断言编排管线端到端正确——治理面校验、工具真实执行、落库断言、
write_high 正确路由到人审（awaiting_approval）。
这保护的是"管线不回归"：任何治理面/工具/持久化改动破坏闭环即刻发现。

**在线模式（能力评测）**：设置 ERP_EVAL_LLM_ENABLED=true 后，
test_online_eval 用真模型执行同批用例，断言 LLM 实际选择的工具
与期望序列一致（首工具匹配即可，防止提示词/模型变更悄悄翻车）。
"""
import json
from pathlib import Path

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

from app.core.config import settings

EVALS_PATH = Path(__file__).parent / "evals" / "agent_evals.json"


def _load_cases() -> list[dict]:
    data = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
    return data["cases"]


@pytest_asyncio.fixture
async def eval_env(tmp_path, monkeypatch):
    """临时 sqlite 库 + bootstrap + 7 域 Agent 注册 + 全部工具导入。"""
    import app.core.database as database

    db_file = tmp_path / "agent_evals.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setattr(settings, "mysql_url", url)
    await database.close_db()
    engine = await database.init_db()
    assert engine is not None
    info = await database.bootstrap_schema()
    assert "error" not in info

    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.agents.reimbursement.agent import ReimbursementAgent
    from app.agents.treasury.agent import TreasuryAgent
    from app.agents.payroll.agent import PayrollAgent
    from app.agents.hr.agent import HRAgent
    from app.agents.crm.agent import CRMAgent
    from app.agents.sales.agent import SalesAgent
    from app.agents.contract.agent import ContractAgent

    import app.agents.reimbursement.tools  # noqa: F401
    import app.agents.treasury.tools  # noqa: F401
    import app.agents.payroll.tools  # noqa: F401
    import app.agents.hr.tools  # noqa: F401
    import app.agents.crm.tools  # noqa: F401
    import app.agents.sales.tools  # noqa: F401
    import app.agents.contract.tools  # noqa: F401

    from app.core.rbac import rbac_engine

    monkeypatch.setattr(rbac_engine, "check_permission", lambda u, r, a: True, raising=True)

    agent_classes = (ReimbursementAgent, TreasuryAgent, PayrollAgent,
                     HRAgent, CRMAgent, SalesAgent, ContractAgent)
    created = []
    for cls in agent_classes:
        agent = cls()
        agent.identity = did_registry.register_agent(
            name=agent.name, domain=agent.domain,
            capabilities=agent.capabilities,
            security_clearance=agent.security_clearance,
        )
        await orchestrator.register_agent(agent)
        created.append(agent)
    yield engine
    target_domains = {a.domain for a in created}
    for key in list(orchestrator._agents.keys()):
        if orchestrator._agents[key].domain in target_domains:
            del orchestrator._agents[key]
    await database.close_db()


def _scripted_llm_for_case(monkeypatch, case: dict):
    """按用例期望的工具序列生成脚本 LLM：逐个发起 tool_call，最后一轮总结。"""
    from app.core.agent import runtime as runtime_mod

    script = []
    for i, tc in enumerate(case["expected_tool_calls"]):
        script.append(AIMessage(
            content="",
            tool_calls=[{"name": tc["name"], "args": tc["args"], "id": f"eval-{i}"}],
        ))
    script.append(AIMessage(content="已完成。"))
    monkeypatch.setattr(runtime_mod.agent_runtime, "_get_llm",
                        lambda: ScriptedEvalLLM(script))
    runtime_mod.agent_runtime._graphs.clear()


class ScriptedEvalLLM:
    def __init__(self, script):
        self.script = script

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return self.script.pop(0)


def _resolve_prev(args: dict, prev_result: dict | None) -> dict:
    """解析 $prev 占位符："$prev.order_id" → 上一步结果字段（序列用例）。"""
    if prev_result is None:
        return args
    resolved = {}
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("$prev."):
            resolved[k] = prev_result.get(v.split(".", 1)[1])
        else:
            resolved[k] = v
    return resolved


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
async def test_eval_case_pipeline(case, eval_env, monkeypatch):
    """CI 模式：每个评测用例走真实图 + 治理面 + DB，断言状态与落库。

    序列用例（多工具且参数含 $prev）走治理面序列执行模式：
    前一步结果经 $prev 解析注入后步参数；write_high 在序列中照常
    被人审门拦截（状态 awaiting）。
    """
    from app.core.agent import runtime as runtime_mod

    seq = case["expected_tool_calls"]
    is_sequence = len(seq) > 1 or any(
        isinstance(a.get("args"), dict) and any(
            isinstance(v, str) and v.startswith("$prev.")
            for v in a.get("args", {}).values())
        for a in seq
    ) and len(seq) > 1

    if is_sequence:
        from app.core.agent.governor import AgentGovernor
        from app.core.rbac import rbac_engine

        monkeypatch.setattr(rbac_engine, "check_permission",
                            lambda u, r, a: True, raising=True)
        gov = AgentGovernor()
        ctx = runtime_mod.ToolContext(
            user_id="user_admin", username="admin",
            agent_key=case["domain"], thread_id=f"eval:{case['id']}",
        )
        status = "done"
        prev_result = None
        for i, tc in enumerate(seq):
            args = _resolve_prev(tc.get("args", {}), prev_result)
            result = await gov.execute(tc["name"], args, ctx)
            if not result.ok and result.approval is not None:
                status = "awaiting_approval"
                break
            assert result.ok, f"[{case['id']}][{tc['name']}] {result.error}"
            prev_result = result.data
        assert status == case["expect_status"], (
            f"[{case['id']}] 期望 {case['expect_status']}，实际 {status}"
        )
        # DB 断言（可选）
        expect_db = case.get("expect_db")
        if expect_db:
            from sqlalchemy import text

            async with eval_env.connect() as conn:
                row = (await conn.execute(
                    text(f"SELECT COUNT(*) FROM {expect_db['table']} WHERE {expect_db['where']}")
                )).scalar()
            assert row == expect_db["count"], (
                f"[{case['id']}] DB 断言失败：{expect_db}，实际 count={row}"
            )
        return

    _scripted_llm_for_case(monkeypatch, case)
    ctx = runtime_mod.ToolContext(
        user_id="user_admin", username="admin",
        agent_key=case["domain"],
        thread_id=f"eval:{case['id']}",
    )
    result = await runtime_mod.agent_runtime.run(case["domain"], case["message"], ctx)

    assert result["status"] == case["expect_status"], (
        f"[{case['id']}] 期望 {case['expect_status']}，实际 {result['status']}，"
        f"reply={result.get('reply', '')[:150]}"
    )

    # DB 断言（可选）
    expect_db = case.get("expect_db")
    if expect_db:
        from sqlalchemy import text

        engine = eval_env
        async with engine.connect() as conn:
            row = (await conn.execute(
                text(f"SELECT COUNT(*) FROM {expect_db['table']} WHERE {expect_db['where']}")
            )).scalar()
        assert row == expect_db["count"], (
            f"[{case['id']}] DB 断言失败：{expect_db}，实际 count={row}"
        )


@pytest.mark.skipif(
    not settings.__dict__.get("eval_llm_enabled", False)
    and not __import__("os").environ.get("ERP_EVAL_LLM_ENABLED"),
    reason="在线能力评测需设置 ERP_EVAL_LLM_ENABLED=true（消耗真实 LLM tokens）",
)
async def test_online_eval(eval_env, monkeypatch):
    """
    在线模式：真模型执行评测用例，断言 LLM 实际选择的**首个工具**与期望一致。

    用例失败不代表管线坏——是模型行为漂移信号，需评审提示词或更新期望。
    """
    import httpx

    cases = _load_cases()
    base_url = settings.llm_base_url
    api_key = settings.llm_api_key
    if not api_key or api_key.startswith("sk-placeholder"):
        pytest.skip("未配置有效 LLM API Key")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    tool_schemas = []
    from app.core.agent.tools_v2 import tool_registry_v2

    for spec in tool_registry_v2.list_specs():
        tool_schemas.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.args_model.model_json_schema(),
            },
        })

    mismatches = []
    for case in cases:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content":
                    "你是企业ERP助手。根据用户指令选择最合适的一个工具调用。"},
                {"role": "user", "content": case["message"]},
            ],
            tools=tool_schemas,
            tool_choice="auto",
        )
        actual = resp.choices[0].message.tool_calls
        actual_name = actual[0].function.name if actual else "(no tool)"
        expected_name = case["expected_tool_calls"][0]["name"]
        if actual_name != expected_name:
            mismatches.append({"case": case["id"], "expected": expected_name,
                               "actual": actual_name})

    assert not mismatches, (
        f"在线评测 {len(mismatches)}/{len(cases)} 用例工具选择漂移：{mismatches}"
    )
