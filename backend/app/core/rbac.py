"""五层角色权限矩阵（服务端强制，PRD §4）。

角色层级：sys_admin > chairman > leader > dept_admin > employee
权限判定：权限矩阵逐条匹配（资源:动作），高级角色含低级角色能力。
"""
from __future__ import annotations

# 角色 → 资源:动作 全集
# 资源：users/roles/market/knowledge/records/im/todos/conversation/logs/dashboard
# 动作：read/create/update/delete/approve/manage

_ROLE_MATRIX: dict[str, dict[str, set[str]]] = {
    "sys_admin": {
        "users": {"read", "create", "update", "delete"},
        "roles": {"read", "create", "update", "delete", "manage"},
        "market": {"read", "create", "update", "delete", "approve", "manage"},
        "logs": {"read", "manage"},
        "system": {"read", "manage"},
        "knowledge": {"read", "create", "update", "delete", "manage"},
        "records": {"read", "create", "update", "delete", "approve", "manage"},
        "im": {"read", "manage"},
        "conversation": {"read", "create"},
        "todos": {"read", "update"},
        "dashboard": {"read"},
    },
    "chairman": {
        "dashboard": {"read", "manage"},
        "records": {"read", "approve"},
        "knowledge": {"read"},
        "im": {"read"},
        "conversation": {"read", "create"},
        "users": {"read"},
        "market": {"read"},
        "todos": {"read", "update"},
        "strategy": {"read", "create"},
    },
    "leader": {
        "dashboard": {"read"},
        "records": {"read", "approve"},
        "knowledge": {"read"},
        "im": {"read", "create"},
        "conversation": {"read", "create"},
        "users": {"read"},
        "market": {"read"},
        "todos": {"read", "update"},
    },
    "dept_admin": {
        "knowledge": {"read", "create", "update", "delete", "manage"},
        "records": {"read", "create", "update", "delete", "approve"},
        "im": {"read", "create", "manage"},
        "conversation": {"read", "create"},
        "users": {"read", "create", "update", "delete"},
        "market": {"read", "create", "approve"},
        "todos": {"read", "update"},
    },
    "employee": {
        "knowledge": {"read", "create", "update", "delete"},
        "records": {"read", "create"},
        "im": {"read", "create"},
        "conversation": {"read", "create"},
        "market": {"read", "create"},
        "todos": {"read", "update"},
    },
}

# 部门管理员对本部门保密资料拥有审批权（D-R4：删改须部门管理员审批）
ROLE_LABELS = {
    "sys_admin": "系统管理员",
    "chairman": "董事长",
    "leader": "主管领导",
    "dept_admin": "部门管理员",
    "employee": "普通员工",
}


def has_permission(role: str, resource: str, action: str) -> bool:
    """权限判定（服务端强制）。"""
    matrix = _ROLE_MATRIX.get(role)
    if matrix is None:
        return False
    actions = matrix.get(resource, set())
    if action in actions:
        return True
    # manage 通配：拥有 resource:manage 则隐含全部动作
    return "manage" in actions


def get_permissions(role: str) -> dict[str, list[str]]:
    matrix = _ROLE_MATRIX.get(role, {})
    return {res: sorted(acts) for res, acts in matrix.items()}
