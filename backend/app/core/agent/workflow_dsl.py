"""
工作流 DSL：schema / 校验器 / 编译器（路线图 Phase F / F3.1-F3.2）
====================================================================

把跨域业务流程从硬编码 builder 升级为**声明式 DSL**，管理端可改造业务链路。

DSL 结构（JSON/YAML 同构）::

    {
      "name": "procure_to_pay",
      "entry": "validate",
      "nodes": [
        {"id": "validate", "type": "task", "handler": "p2p.validate", "next": "schedule"},
        {"id": "route", "type": "condition", "field": "input.category",
         "branches": [{"equals": "entertainment", "goto": "compliance_gate"},
                      {"gte": 500000, "goto": "cfo_gate"}],
         "default": "gate"},
        {"id": "gate", "type": "gate",
         "threshold": {"key": "payment_human_gate_limit", "default": 100000},
         "amount_from": "result.amount",
         "document_type": "payment", "approve_roles": ["cfo"], "timeout_hours": 24,
         "preview": "大额付款确认：PO {po_id} 应付 ¥{amount}",
         "next_approved": "pay", "next_rejected": "reject"},
        {"id": "pay", "type": "task", "handler": "p2p.pay", "next": "END"},
        {"id": "reject", "type": "task", "handler": "p2p.reject_mark", "next": "END"}
      ]
    }

节点类型（白名单，仅三种——D-2 修订：condition 承载条件分支多路路由）：
- ``task``：执行业务动作（handler 白名单引用）；handler 返回 ``{"_next": id}``
  可覆盖静态 ``next``（动态路由）；
- ``condition``：声明式多路分支——按 ``field``（状态路径）匹配 ``branches``
  有序条件（equals/not_equals/in/gt/gte/lt/lte/exists），首个命中即路由其
  ``goto``；全不命中走 ``default``（**必须声明**，失败安全）；
- ``gate``：人审门——金额与阈值比较：低于阈值走 ``next_approved``（自动通过），
  达到阈值 ``interrupt()`` 人审：批准走 ``next_approved``、驳回走
  ``next_rejected``。per-node 可配 ``approve_roles`` / ``timeout_hours``（F3.4）。

``next``/``goto`` 取值：节点 id 或 ``"END"``（终止）。

**安全模型**：
- handler 只能引用 :data:`HANDLERS` 注册表中的具名函数（确定性业务逻辑，
  非任意代码）；未注册 handler 校验拒绝；
- 治理不可禁用：task 节点逐节点审计内建于编译器，DSL 无法移除；
- **治理节点不可缺失（红线 1）**：定义必须含 ≥1 个 gate 节点；
- gate 的 rejected 分支必须存在（治理完整性校验）；
- 阈值键必须在阈值白名单（customization.THRESHOLD_DEFAULTS）内；
- 所有路由目标必须是已声明节点或 END；不可达（孤立）节点拒绝发布；环检测；
- ``__wf_finish__`` 为保留节点 id（隐式终止哨兵，DSL 不可占用）。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import structlog
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

logger = structlog.get_logger()

NODE_TYPES = ("task", "condition", "gate")
END_SENTINEL = "__wf_finish__"  # 保留节点 id：error/完成的路由目标
END_ALIAS = "END"               # DSL 中书写 ``END``，编译期归一化为 END 哨兵
CONDITION_OPS = ("equals", "not_equals", "in", "gt", "gte", "lt", "lte", "exists")
STATE_KEYS = ("input", "result", "error", "steps", "thread_id")


class WorkflowState(TypedDict, total=False):
    input: dict
    steps: list
    result: dict
    error: Optional[str]
    thread_id: str  # G5.1 Saga 步骤登记关联键


HANDLERS: dict[str, Callable[..., Any]] = {}


def register_handler(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        logger.info("workflow_dsl.handler_registered", handler=name)
        return fn
    return deco


def get_handler(name: str) -> Optional[Callable[..., Any]]:
    return HANDLERS.get(name)


def _route_targets(node: dict) -> list[str]:
    """节点的全部出边引用（供校验与环检测）。"""
    out: list[str] = []
    if node.get("type") == "condition":
        for br in node.get("branches") or []:
            if isinstance(br, dict) and br.get("goto"):
                out.append(str(br["goto"]))
        if node.get("default"):
            out.append(str(node["default"]))
        return out
    for field in ("next", "next_approved", "next_rejected"):
        ref = node.get(field)
        if ref:
            out.append(str(ref))
    return out


def validate_dsl(defn: dict, valid_threshold_keys: set,
                 handler_names: Optional[set] = None) -> list:
    """校验 DSL 定义，返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    if not isinstance(defn, dict):
        return ["DSL 必须为对象"]
    nodes = defn.get("nodes") or []
    if not defn.get("name"):
        errors.append("缺少 name")
    if not nodes or not isinstance(nodes, list):
        errors.append("nodes 不能为空")
        return errors

    handler_names = handler_names if handler_names is not None else set(HANDLERS.keys())
    by_id: dict[str, dict] = {}
    has_gate = False
    for n in nodes:
        if not isinstance(n, dict):
            errors.append("节点必须为对象")
            continue
        nid = n.get("id")
        if not nid or not isinstance(nid, str):
            errors.append(f"节点缺少 id: {n}")
            continue
        if nid in by_id:
            errors.append(f"节点 id 重复: {nid}")
        if nid in (END_SENTINEL, END_ALIAS, "__end__"):
            errors.append(f"节点 id {nid} 为保留字（终止哨兵）")
        by_id[nid] = n
        ntype = n.get("type")
        if ntype not in NODE_TYPES:
            errors.append(f"节点 {nid} 类型非法: {ntype}（仅 {'/'.join(NODE_TYPES)}）")
            continue
        if ntype == "task":
            h = n.get("handler")
            if not h:
                errors.append(f"task 节点 {nid} 缺少 handler")
            elif h not in handler_names:
                errors.append(f"task 节点 {nid} 引用未注册 handler: {h}")
            if not n.get("next") and not n.get("handler"):
                errors.append(f"task 节点 {nid} 缺少 next")
        if ntype == "condition":
            if not n.get("field"):
                errors.append(f"condition 节点 {nid} 缺少 field（状态路径）")
            branches = n.get("branches") or []
            if not branches:
                errors.append(f"condition 节点 {nid} branches 不能为空")
            for i, br in enumerate(branches):
                if not isinstance(br, dict):
                    errors.append(f"condition 节点 {nid} 分支 {i} 必须为对象")
                    continue
                ops = [k for k in br if k in CONDITION_OPS]
                if not ops:
                    errors.append(
                        f"condition 节点 {nid} 分支 {i} 缺少条件算子（{'/'.join(CONDITION_OPS)}）")
                if len(ops) > 1:
                    errors.append(f"condition 节点 {nid} 分支 {i} 只能一个条件算子: {ops}")
                if not br.get("goto"):
                    errors.append(f"condition 节点 {nid} 分支 {i} 缺少 goto")
            if not n.get("default"):
                errors.append(f"condition 节点 {nid} 缺少 default（失败安全）")
        if ntype == "gate":
            has_gate = True
            for field in ("next_approved", "next_rejected"):
                if not n.get(field):
                    errors.append(f"gate 节点 {nid} 缺少 {field}（治理完整性）")
            th = n.get("threshold") or {}
            if th.get("key") and valid_threshold_keys and th["key"] not in valid_threshold_keys:
                errors.append(f"gate 节点 {nid} 阈值键不在白名单: {th['key']}")
            roles = n.get("approve_roles")
            if roles is not None and (not isinstance(roles, list) or not all(
                    isinstance(r, str) and r for r in roles)):
                errors.append(f"gate 节点 {nid} approve_roles 必须为非空字符串数组")
            to = n.get("timeout_hours")
            if to is not None:
                try:
                    if float(to) <= 0:
                        errors.append(f"gate 节点 {nid} timeout_hours 必须为正数")
                except (TypeError, ValueError):
                    errors.append(f"gate 节点 {nid} timeout_hours 非法: {to}")

    # 路由目标必须已声明（或 END）
    for nid, n in by_id.items():
        for ref in _route_targets(n):
            if ref != END_ALIAS and ref not in by_id:
                errors.append(f"节点 {nid} 路由到未声明目标: {ref}")

    entry = defn.get("entry") or (nodes[0].get("id") if nodes else None)
    if entry not in by_id:
        errors.append(f"entry 节点不存在: {entry}")

    if not has_gate and nodes:
        errors.append("缺少 gate 节点（治理节点不可缺失——红线 1）")

    if errors:
        return errors

    # 可达性 + 环检测
    reachable: set[str] = set()
    stack: list[str] = []

    def _dfs(nid):
        if nid in stack:
            errors.append(f"检测到环: {' -> '.join(stack + [nid])}")
            return
        if nid in reachable or nid == END_ALIAS:
            return
        reachable.add(nid)
        stack.append(nid)
        n = by_id.get(nid)
        if n:
            for t in _route_targets(n):
                _dfs(t)
        stack.pop()

    _dfs(entry)
    for nid in by_id:
        if nid not in reachable:
            errors.append(f"节点不可达（孤立节点）: {nid}")
    return errors


def _resolve_ref(state: dict, path: str, default=None):
    if not path:
        return default
    cur: Any = state
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return default
    return cur if cur is not None else default


def _flat_view(state: dict) -> dict:
    view = dict(state.get("input") or {})
    view.update(state.get("result") or {})
    return view


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _branch_matches(value: Any, branch: dict) -> bool:
    for op in CONDITION_OPS:
        if op not in branch:
            continue
        expected = branch[op]
        if op == "equals":
            return value == expected
        if op == "not_equals":
            return value != expected
        if op == "in":
            return isinstance(expected, (list, tuple, set)) and value in expected
        if op == "exists":
            return (value is not None) == bool(expected)
        if value is None:
            return False
        try:
            if op == "gt":
                return float(value) > float(expected)
            if op == "gte":
                return float(value) >= float(expected)
            if op == "lt":
                return float(value) < float(expected)
            if op == "lte":
                return float(value) <= float(expected)
        except (TypeError, ValueError):
            return False
    return False


def compile_dsl(defn: dict, name: str, checkpointer, threshold_reader: Callable):
    """从 DSL 定义动态编译 StateGraph（Command(goto) 全量接管路由）。"""
    nodes = {n["id"]: n for n in defn["nodes"]}
    entry = defn.get("entry") or defn["nodes"][0]["id"]
    goto_map = set(nodes)

    class DSLState(WorkflowState):
        pass

    async def _audit_node(node_id, state, out, risk_level="low"):
        try:
            from app.core.security.audit import audit_chain

            await audit_chain.log(
                actor_did="workflow",
                action=f"workflow.{name}.{node_id}",
                resource=f"workflow:{name}",
                result="success",
                details={"steps": list(state.get("steps", []))[-5:],
                         "out": {k: str(v)[:100] for k, v in out.items()
                                 if k in ("error", "_next", "auto", "approved",
                                          "matched", "comment")}},
                risk_level=risk_level)
        except Exception as e:  # noqa: BLE001
            logger.warning("workflow_dsl.node_audit_failed", error=str(e))

    def _normalize_goto(target) -> str:
        """路由目标归一化：END 别名 / 未声明目标 → END 哨兵。"""
        if target is None or target == END_ALIAS or target == END:
            return END
        if target not in goto_map:
            logger.warning("workflow_dsl.unknown_goto_target",
                           workflow=name, target=target)
            return END
        return target

    async def _run_task(node, state):
        handler = get_handler(node["handler"])
        if handler is None:
            return ({"error": f"handler 未注册: {node['handler']}",
                     "steps": list(state.get("steps", [])) + [node["id"]]}, END)
        out = await handler(state, node.get("params") or {})
        if not isinstance(out, dict):
            out = {}
        patch = {k: v for k, v in out.items() if k in STATE_KEYS}
        patch["steps"] = list(state.get("steps", [])) + [node["id"]]
        await _audit_node(node["id"], state, out)
        if not out.get("error"):
            # G5.1 Saga：创建类步骤成功即登记（失败终止时逆序补偿）
            try:
                from app.core.agent.workflow_saga import saga_record

                await saga_record(str(state.get("thread_id") or ""),
                                  str(node.get("id")), str(node.get("handler")),
                                  out.get("result"))
            except Exception:  # noqa: BLE001
                pass
        if out.get("error"):
            return patch, END
        dyn = out.get("_next")
        if dyn is not None and dyn != END_ALIAS and dyn not in goto_map:
            patch["error"] = f"handler 返回非法路由目标: {dyn}"
            return patch, END
        return patch, _normalize_goto(dyn if dyn is not None else node.get("next"))

    async def _run_condition(node, state):
        value = _resolve_ref(state, node.get("field", ""), None)
        matched = None
        for br in node.get("branches") or []:
            if _branch_matches(value, br):
                matched = br
                break
        target = (matched or {}).get("goto") or node.get("default")
        steps = list(state.get("steps", [])) + [
            node["id"] + (f"(={target})" if matched else "(default)")]
        await _audit_node(node["id"], state, {"matched": bool(matched),
                                              "value": str(value)[:80]})
        return {"steps": steps}, _normalize_goto(target)

    async def _run_gate(node, state):
        from langgraph.types import interrupt

        amount = _resolve_ref(state, node.get("amount_from", "result.amount"), 0.0)
        try:
            amount = float(amount or 0)
        except (TypeError, ValueError):
            amount = 0.0
        th = node.get("threshold") or {}
        threshold = float(th.get("default", 0) or 0)
        if th.get("key"):
            overridden = threshold_reader(th.get("key"), threshold)
            threshold = float(overridden)

        view = _flat_view(state)
        try:
            preview = str(node.get("preview", "")).format_map(_SafeDict(view))
        except Exception:  # noqa: BLE001
            preview = f"金额 ¥{amount}"

        steps_base = list(state.get("steps", []))

        if amount < threshold:
            await _audit_node(node["id"], state, {"auto": "below_threshold"})
            return ({"steps": steps_base + [node["id"] + "(auto)"]},
                    _normalize_goto(node.get("next_approved")))

        payload = {
            "tool": "workflow:" + name,
            "risk": "write_high",
            "args": {"node": node["id"], "amount": amount},
            "preview": preview,
        }
        if node.get("approve_roles"):
            payload["approve_roles"] = list(node["approve_roles"])
        if node.get("document_type"):
            payload["document_type"] = node["document_type"]
        if node.get("timeout_hours") is not None:
            payload["args"]["timeout_hours"] = node["timeout_hours"]

        decision = interrupt(payload) or {}
        approved = isinstance(decision, dict) and bool(decision.get("approved"))
        await _audit_node(node["id"], state,
                          {"approved": approved,
                           "comment": str((decision or {}).get("comment", ""))[:100]},
                          risk_level="high" if approved else "medium")
        target = node.get("next_approved") if approved else node.get("next_rejected")
        return ({"steps": steps_base + [
            node["id"] + ("(approved)" if approved else "(rejected)")]},
            _normalize_goto(target))

    def _make_fn(node):
        async def _fn(state):
            from langgraph.types import Command

            if node["type"] == "gate":
                patch, goto = await _run_gate(node, state)
            elif node["type"] == "condition":
                patch, goto = await _run_condition(node, state)
            else:
                patch, goto = await _run_task(node, state)

            update = {k: v for k, v in patch.items() if k in STATE_KEYS}
            return Command(goto=goto, update=update)

        return _fn

    builder = StateGraph(DSLState)
    for node in defn["nodes"]:
        builder.add_node(node["id"], _make_fn(node))
    builder.add_edge(START, entry)

    return builder.compile(checkpointer=checkpointer)
