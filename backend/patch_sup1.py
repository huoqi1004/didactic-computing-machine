# -*- coding: utf-8 -*-
"""Patch: supervisor delegate 重放安全 + runtime/workflows 挂起探测。"""
import io

# ---- 1) runtime.sub_pending ----
path = "app/core/agent/runtime.py"
with io.open(path, "r", encoding="utf-8", newline="") as f:
    src = f.read()
if "async def sub_pending" not in src:
    old = "    def has_domain(self, domain: str) -> bool:"
    new = '''    async def sub_pending(self, domain: str, thread_id: str) -> bool:
        """该域场景图在指定线程上是否有挂起的人审中断（supervisor 重放探测）。"""
        try:
            graph = self.get_graph(domain)
            state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            return any(getattr(t, "interrupts", None) for t in state.tasks)
        except Exception:  # noqa: BLE001
            return False

    def has_domain(self, domain: str) -> bool:'''
    assert old in src, "runtime anchor"
    src = src.replace(old, new, 1)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
print("runtime.sub_pending ok")

# ---- 2) workflows._graph_pending ----
path = "app/core/agent/workflows.py"
with io.open(path, "r", encoding="utf-8", newline="") as f:
    src = f.read()
if "_graph_pending" not in src:
    anchor = "def list_workflows()"
    addition = (
        "async def _graph_pending(name: str, thread_id: str) -> bool:\n"
        '    """工作流在指定线程上是否有挂起的人审中断（supervisor 重放探测）。"""\n'
        "    try:\n"
        "        graph = _get_graph(name)\n"
        '        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})\n'
        '        return any(getattr(t, "interrupts", None) for t in state.tasks)\n'
        "    except Exception:  # noqa: BLE001\n"
        "        return False\n"
        "\n\n"
        "def list_workflows()"
    )
    assert anchor in src, "workflows anchor"
    src = src.replace(anchor, addition, 1)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
print("workflows._graph_pending ok")
print("done part 1")
