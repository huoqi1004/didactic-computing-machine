"""挂起诊断：25 秒后转储全部线程栈（sys._current_frames，纯 Python 可靠）。"""
import asyncio
import faulthandler
import pathlib
import sys
import tempfile
import threading
import traceback


def _dump_all_frames():
    with open(r"C:\Users\天机一号\AppData\Local\Temp\frames_dump.txt", "w",
              encoding="utf-8") as f:
        for tid, frame in sys._current_frames().items():
            f.write(f"\n=== THREAD {tid} ===\n")
            f.write("".join(traceback.format_stack(frame)))


async def main():
    t = threading.Timer(25.0, _dump_all_frames)
    t.daemon = True
    t.start()

    import app.core.config as cfg
    tmp = pathlib.Path(tempfile.mkdtemp()) / "diag.db"
    cfg.settings.mysql_url = f"sqlite+aiosqlite:///{tmp.as_posix()}"
    import app.core.database as database
    await database.close_db()
    engine = await database.init_db()
    await database.bootstrap_schema()

    from app.core.agent.orchestrator import orchestrator
    from app.core.security.did import did_registry
    from app.core.rbac import rbac_engine
    from app.agents.treasury.agent import TreasuryAgent
    import app.agents.treasury.tools
    rbac_engine.check_permission = lambda u, r, a: True
    agent = TreasuryAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name, domain=agent.domain,
        capabilities=agent.capabilities, security_clearance=agent.security_clearance)
    await orchestrator.register_agent(agent)

    from app.core.agent.workflows import start_workflow
    from app.core.agent.tools_v2 import ToolContext
    ctx = ToolContext(user_id="u1", username="u1", agent_key="procure_to_pay",
                      thread_id="diag-1")
    r = await start_workflow("procure_to_pay",
                             {"po_id": "PO-DIAG", "total_amount": 80000,
                              "supplier_id": "s"}, ctx)
    print("DBG result:", r["status"], flush=True)
    await database.close_db()


asyncio.run(main())
