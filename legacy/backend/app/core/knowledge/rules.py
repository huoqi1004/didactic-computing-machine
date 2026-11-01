"""
业务规则引擎 - 安全沙箱评估
============================

设计目标
--------
- Rule 模型封装条件表达式、动作、优先级
- RuleEngine 支持规则的注册、删除、评估、执行
- 条件表达式使用 Python eval 在受限沙箱中评估：
  - 仅允许比较运算符 (==, !=, <, >, <=, >=)
  - 仅允许逻辑运算符 (and, or, not)
  - 仅允许算术运算符 (+, -, *, /) 与括号
  - 禁止函数调用、属性访问、import、builtins
- 支持预置业务规则，启动时自动加载

预置业务规则
------------
1. 库存低于安全线 → 触发补货
2. 金额超过 10 万 → 需要多 Agent 共识
3. 非工作时间大额转账 → 拒绝
4. 供应商信用分低于 60 → 警告

条件表达式示例
--------------
- ``quantity < safety_stock``
- ``amount > 100000``
- ``amount > 50000 and (hour < 9 or hour > 18)``
- ``supplier_credit_score < 60``
"""
import ast
import operator
import structlog
from typing import Optional
from uuid import uuid4

logger = structlog.get_logger()


# =============================================================================
# 安全沙箱：AST 节点白名单评估器
# =============================================================================

# 允许的二元运算符
_BIN_OPS: dict[type, callable] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# 允许的二元比较运算符
_COMPARE_OPS: dict[type, callable] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

# 允许的一元运算符
_UNARY_OPS: dict[type, callable] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}

# 允许的布尔运算符
_BOOL_OPS: dict[type, callable] = {
    ast.And: all,
    ast.Or: any,
}

# 允许的 AST 节点类型白名单
_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Constant,
    ast.Load,
    ast.Tuple,
    ast.List,
    ast.IfExp,  # 三元表达式：x if cond else y
)


class SafeEvalError(Exception):
    """安全评估错误"""


def safe_eval(expr: str, context: dict) -> object:
    """安全评估表达式

    使用 ast 模块解析表达式，仅允许白名单节点类型和运算符。
    任何不支持的语法都会抛出 SafeEvalError。

    Args:
        expr: 表达式字符串（如 ``"amount > 100000"``）
        context: 变量上下文（如 ``{"amount": 150000}``）

    Returns:
        表达式的求值结果（通常是 bool）

    Raises:
        SafeEvalError: 表达式包含禁用语法或引用了未定义变量
    """
    if not expr or not expr.strip():
        # 空表达式视为始终匹配
        return True

    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise SafeEvalError(f"Syntax error: {e}") from e

    return _eval_node(tree.body, context)


def _eval_node(node: ast.AST, context: dict) -> object:
    """递归评估 AST 节点"""
    # BoolOp: and / or
    if isinstance(node, ast.BoolOp):
        op_func = _BOOL_OPS.get(type(node.op))
        if op_func is None:
            raise SafeEvalError(f"BoolOp not allowed: {type(node.op).__name__}")
        values = [_eval_node(v, context) for v in node.values]
        return op_func(values) if op_func is all else any(values)

    # BinOp: +, -, *, /, %, **
    if isinstance(node, ast.BinOp):
        op_func = _BIN_OPS.get(type(node.op))
        if op_func is None:
            raise SafeEvalError(f"BinOp not allowed: {type(node.op).__name__}")
        left = _eval_node(node.left, context)
        right = _eval_node(node.right, context)
        try:
            return op_func(left, right)
        except ZeroDivisionError as e:
            raise SafeEvalError(f"Division by zero: {e}") from e

    # UnaryOp: -, +, not
    if isinstance(node, ast.UnaryOp):
        op_func = _UNARY_OPS.get(type(node.op))
        if op_func is None:
            raise SafeEvalError(f"UnaryOp not allowed: {type(node.op).__name__}")
        operand = _eval_node(node.operand, context)
        return op_func(operand)

    # Compare: ==, !=, <, >, <=, >= (支持链式比较 a < b < c)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            op_func = _COMPARE_OPS.get(type(op))
            if op_func is None:
                raise SafeEvalError(f"Compare op not allowed: {type(op).__name__}")
            right = _eval_node(comparator, context)
            try:
                if not op_func(left, right):
                    return False
            except TypeError as e:
                raise SafeEvalError(f"Compare error: {e}") from e
            left = right
        return True

    # IfExp: x if cond else y
    if isinstance(node, ast.IfExp):
        if _eval_node(node.test, context):
            return _eval_node(node.body, context)
        return _eval_node(node.orelse, context)

    # Name: 变量引用
    if isinstance(node, ast.Name):
        name = node.id
        if name in context:
            return context[name]
        # 常量别名（不区分大小写）
        if name == "True":
            return True
        if name == "False":
            return False
        if name == "None":
            return None
        raise SafeEvalError(f"Undefined variable: {name}")

    # Constant: 字面量（数字、字符串、布尔）
    if isinstance(node, ast.Constant):
        return node.value

    # Tuple / List: 字面量集合
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval_node(e, context) for e in node.elts]

    raise SafeEvalError(
        f"Disallowed AST node: {type(node).__name__}. "
        "Only comparison, logical, arithmetic operators are allowed."
    )


# =============================================================================
# Rule 模型
# =============================================================================

class Rule:
    """业务规则

    Attributes:
        rule_id: 规则唯一标识（不指定时自动生成 UUID）
        name: 规则名称（人类可读）
        condition: 条件表达式字符串（安全沙箱评估）
            支持的运算符：==, !=, <, >, <=, >=, and, or, not, +, -, *, /, %
        action: 触发动作的描述（如 "trigger_replenishment"）
        priority: 优先级（数值越大优先级越高，默认 0）
        enabled: 是否启用
        description: 规则描述（可选）
        metadata: 元数据（如 risk_level, category 等）
    """

    __slots__ = (
        "rule_id", "name", "condition", "action",
        "priority", "enabled", "description", "metadata",
    )

    def __init__(
        self,
        name: str,
        condition: str,
        action: str,
        rule_id: Optional[str] = None,
        priority: int = 0,
        enabled: bool = True,
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        self.rule_id = rule_id or str(uuid4())
        self.name = name
        self.condition = condition
        self.action = action
        self.priority = priority
        self.enabled = enabled
        self.description = description or ""
        self.metadata = dict(metadata) if metadata else {}

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "condition": self.condition,
            "action": self.action,
            "priority": self.priority,
            "enabled": self.enabled,
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Rule":
        return cls(
            name=data["name"],
            condition=data["condition"],
            action=data["action"],
            rule_id=data.get("rule_id"),
            priority=data.get("priority", 0),
            enabled=data.get("enabled", True),
            description=data.get("description"),
            metadata=data.get("metadata"),
        )

    def __repr__(self) -> str:
        return f"Rule(id={self.rule_id[:8]}, name={self.name}, priority={self.priority})"


# =============================================================================
# RuleEngine
# =============================================================================

class RuleEngine:
    """
    业务规则引擎

    特性：
    - 规则注册、删除、查询
    - 安全沙箱评估条件表达式（仅允许比较和逻辑运算符）
    - 按优先级排序匹配的规则
    - 执行规则动作（返回结构化结果）

    使用方式：
        engine = RuleEngine()
        engine.add_rule(Rule(name="库存不足", condition="qty < 10", action="replenish"))
        matched = await engine.evaluate({"qty": 5})
        results = await engine.execute({"qty": 5})
    """

    def __init__(self):
        # rule_id -> Rule
        self._rules: dict[str, Rule] = {}

    def add_rule(self, rule: Rule) -> str:
        """添加规则

        Returns:
            rule_id
        """
        self._rules[rule.rule_id] = rule
        logger.debug(
            "rule_engine.rule_added",
            rule_id=rule.rule_id,
            name=rule.name,
            priority=rule.priority,
        )
        return rule.rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """删除规则

        Returns:
            True 表示删除成功，False 表示规则不存在
        """
        if rule_id not in self._rules:
            return False
        rule = self._rules.pop(rule_id)
        logger.debug("rule_engine.rule_removed", rule_id=rule_id, name=rule.name)
        return True

    def get_rule(self, rule_id: str) -> Optional[Rule]:
        """获取规则"""
        return self._rules.get(rule_id)

    def list_rules(self, only_enabled: bool = False) -> list[Rule]:
        """列出所有规则（按优先级降序）"""
        rules = list(self._rules.values())
        if only_enabled:
            rules = [r for r in rules if r.enabled]
        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules

    async def evaluate(self, context: dict) -> list[Rule]:
        """评估所有规则，返回匹配的规则列表

        Args:
            context: 评估上下文（如 {"quantity": 5, "safety_stock": 50}）

        Returns:
            匹配的规则列表（按优先级降序）
            - enabled=False 的规则被跳过
            - 条件表达式语法错误的规则被跳过并记录警告
        """
        matched: list[Rule] = []
        for rule in self._rules.values():
            if not rule.enabled:
                continue
            try:
                result = safe_eval(rule.condition, context)
                if bool(result):
                    matched.append(rule)
            except SafeEvalError as e:
                logger.warning(
                    "rule_engine.eval_failed",
                    rule_id=rule.rule_id,
                    name=rule.name,
                    condition=rule.condition,
                    error=str(e),
                )
                continue
        # 按优先级降序
        matched.sort(key=lambda r: r.priority, reverse=True)
        return matched

    async def execute(self, context: dict) -> list[dict]:
        """执行匹配规则的动作

        Args:
            context: 评估上下文

        Returns:
            执行结果列表，每个元素是一个字典，包含：
            - rule_id: 触发的规则 ID
            - rule_name: 规则名称
            - action: 规则定义的动作描述
            - priority: 规则优先级
            - context_snapshot: 触发时的上下文快照
            - status: 执行状态（"triggered"）
        """
        matched = await self.evaluate(context)
        results = []
        for rule in matched:
            result = {
                "rule_id": rule.rule_id,
                "rule_name": rule.name,
                "action": rule.action,
                "priority": rule.priority,
                "context_snapshot": dict(context),
                "status": "triggered",
            }
            results.append(result)
            logger.info(
                "rule_engine.rule_triggered",
                rule_id=rule.rule_id,
                name=rule.name,
                action=rule.action,
            )
        return results

    def get_stats(self) -> dict:
        """统计信息"""
        enabled_count = sum(1 for r in self._rules.values() if r.enabled)
        priority_dist: dict[str, int] = {}
        for r in self._rules.values():
            bucket = f"priority_{r.priority}"
            priority_dist[bucket] = priority_dist.get(bucket, 0) + 1
        return {
            "total_rules": len(self._rules),
            "enabled_rules": enabled_count,
            "disabled_rules": len(self._rules) - enabled_count,
            "priority_distribution": priority_dist,
        }

    def clear(self) -> None:
        """清空所有规则（测试与重置使用）"""
        self._rules.clear()


# =============================================================================
# 预置业务规则
# =============================================================================

def _builtin_rules() -> list[Rule]:
    """预置业务规则

    所有预置规则的 rule_id 使用稳定标识符（非随机 UUID），
    便于通过 API 启用/禁用/查询。
    """
    return [
        Rule(
            rule_id="builtin:low_inventory_replenishment",
            name="库存低于安全线触发补货",
            condition="quantity < safety_stock",
            action="trigger_replenishment",
            priority=80,
            description="当库存数量低于安全库存线时，自动触发补货流程",
            metadata={"category": "inventory", "risk_level": "medium"},
        ),
        Rule(
            rule_id="builtin:large_amount_consensus",
            name="大额操作需多Agent共识",
            condition="amount > 100000",
            action="require_multi_agent_consensus",
            priority=90,
            description="金额超过10万的操作必须经过多Agent共识投票",
            metadata={"category": "finance", "risk_level": "high"},
        ),
        Rule(
            rule_id="builtin:off_hours_large_transfer_reject",
            name="非工作时间大额转账拒绝",
            condition="amount > 50000 and (hour < 9 or hour > 18)",
            action="reject_transfer",
            priority=100,
            description="非工作时间（18:00后或09:00前）的大额转账（>5万）直接拒绝",
            metadata={"category": "finance", "risk_level": "critical"},
        ),
        Rule(
            rule_id="builtin:low_supplier_credit_warning",
            name="供应商信用分低警告",
            condition="supplier_credit_score < 60",
            action="warning_supplier_credit",
            priority=70,
            description="供应商信用分低于60时发出警告，建议复核采购订单",
            metadata={"category": "procurement", "risk_level": "medium"},
        ),
    ]


def load_builtin_rules(engine: RuleEngine) -> int:
    """向规则引擎注入预置业务规则

    Returns:
        成功注入的规则数量
    """
    count = 0
    for rule in _builtin_rules():
        engine.add_rule(rule)
        count += 1
    logger.info("rule_engine.builtin_rules_loaded", count=count)
    return count


# 全局单例
rule_engine = RuleEngine()

# 模块加载时自动注入预置规则（与 audit_chain 等模块的「全局单例即用」模式一致）
load_builtin_rules(rule_engine)
