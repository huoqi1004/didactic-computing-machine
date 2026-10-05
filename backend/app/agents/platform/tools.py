"""
平台级工具（D-B 裁决：自研"ERP 只读查询"治理面工具）
======================================================

委员会裁决 D-B：MCP 首批以**自研只读查询**替代第三方 sqlite MCP
（本项目为 MySQL 存储不匹配）。本工具经治理面注册（readonly）：

- **表白名单**：仅开放业务查询表（禁止 user/role/permission/audit 等
  敏感面——权限与审计数据不经自然语言通道暴露）；
- **按表二次鉴权**：工具级只校验 `platform:read`，而表本身分属各业务域，
  故执行前再按表校验 `<域>:read`——否则"会问就能查"会把 payroll/treasury
  等最高敏域的读权限放大到平台级；
- **SELECT-only**：语句首 token 与黑名单关键字双重校验；
- **强制 LIMIT**：无 LIMIT 自动补 200，上限 1000（防全表拖库）；
- 全量审计：经治理面自动落审计链（readonly→low 风险）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

import structlog
from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool

logger = structlog.get_logger()

# 只读开放表白名单（业务查询面；敏感面一律不开放）。
# 每一项都对应 app/db/models.py 的一个 __tablename__——不在 ORM 里的表
# 不进白名单（曾列 purchase_orders，本仓无此 model，放行只会得到 1146 报错）。
# 值 = 该表的 RBAC 资源：查询前按 `<资源>:read` 二次鉴权。
_TABLE_RESOURCE: dict[str, str] = {
    "reimbursements": "reimbursement",
    "sales_orders": "sales",
    "contracts": "contract",
    "customers": "crm",
    "inventory_items": "inventory",
    "fund_transfers": "treasury",
    "payment_schedules": "treasury",
    "payroll_records": "payroll",
    "projects": "project",
    "invoices": "invoice",
    "budgets": "budget",
    "risks": "risk",
    "departments": "hr",
    "employees": "hr",
}

_ALLOWED_TABLES = set(_TABLE_RESOURCE)

_FORBIDDEN_TOKENS = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", ";", "--", "/*", "into outfile", "information_schema",
    "mysql.", "sys.", "performance_schema",
    # 红队用例补强（test_platform_sandbox_g）：UNION 注入 / 子查询 / 时间盲注
    "union", "select", "sleep(", "benchmark(", "xor ",
)

_MAX_LIMIT = 1000
_DEFAULT_LIMIT = 200


class ReadOnlyQueryArgs(BaseModel):
    """只读查询参数"""
    table: str = Field(..., description=f"查询表名（白名单：{sorted(_ALLOWED_TABLES)}）")
    columns: str = Field("*", description="逗号分隔列名，默认 *")
    where: str = Field("", description="WHERE 条件（参数化值请内联为字面量，禁止子查询）")
    order_by: str = Field("", description="排序列，如 created_at DESC")
    limit: int = Field(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT, description="返回行数上限")


def _validate_query(args: ReadOnlyQueryArgs) -> str:
    """白名单 + SELECT-only + LIMIT 强制校验，返回安全 SQL。"""
    table = args.table.strip().lower()
    if table not in _ALLOWED_TABLES:
        raise ValueError(
            f"表 {table} 不在只读白名单（开放：{sorted(_ALLOWED_TABLES)}）")

    columns = args.columns.strip() or "*"
    if columns != "*" and not re.fullmatch(r"[a-zA-Z_,\s]+", columns):
        raise ValueError("columns 仅允许逗号分隔的列名")

    where = args.where.strip()
    lowered = (columns + " " + where).lower()
    if any(tok in lowered for tok in _FORBIDDEN_TOKENS):
        raise ValueError("查询包含非只读/禁止 token")
    # WHERE 安全字符白名单（字母数字/引号/比较符/空格/括号/点/逗号/百分号）
    if where and not re.fullmatch(r"[a-zA-Z0-9_ =<>!().,''%+-]+", where):
        raise ValueError("WHERE 含不安全字符（反引号/分号/注释等已禁）")

    where_clause = f"WHERE {where}" if where else ""
    order = f"ORDER BY {args.order_by}" if args.order_by.strip() else ""
    if order and not re.fullmatch(r"[a-zA-Z_,\s]+(DESC|ASC)?", args.order_by.strip(),
                                  re.IGNORECASE):
        raise ValueError("order_by 仅允许列名与 DESC/ASC")
    limit = min(max(args.limit, 1), _MAX_LIMIT)
    return (f"SELECT {columns} FROM {table} {where_clause} "
            f"{order} LIMIT {limit}").strip()


@erp_tool(
    name="erp_readonly_query",
    description=(
        "ERP 业务数据只读查询（SQL）：表白名单 + 按表域二次鉴权 + SELECT-only + "
        "强制 LIMIT。用于对各业务表做自定义统计与明细查询"
    ),
    args_model=ReadOnlyQueryArgs,
    risk_level=ToolRisk.READONLY,
    resource="platform", action="read",
    agent_domain="platform",
    idempotent=True,
    business_key_field=None,
)
async def erp_readonly_query(ctx: ToolContext, table: str, columns: str = "*",
                             where: str = "", order_by: str = "",
                             limit: int = _DEFAULT_LIMIT) -> Any:
    from sqlalchemy import text

    from app.core.database import get_engine

    # 表名先归一：_validate_query 内部按 strip().lower() 判定白名单，而下面的
    # 按表鉴权要查 _TABLE_RESOURCE——两处口径必须一致，否则 LLM 给出
    # "Sales_orders" 这种大小写变体会先过白名单、再在字典查表时 KeyError。
    table = (table or "").strip().lower()
    args = ReadOnlyQueryArgs(table=table, columns=columns, where=where,
                             order_by=order_by, limit=limit)
    sql = _validate_query(args)

    # 按表二次鉴权：platform:read 只代表"会用查询工具"，不代表能读 payroll/treasury。
    # 治理面只校验工具声明的 platform:read，敏感域的读权限必须在这里按表收回来。
    from app.core.rbac import rbac_engine  # noqa: PLC0415

    domain_resource = _TABLE_RESOURCE[table]
    if not rbac_engine.check_permission(ctx.user_id, domain_resource, "read"):
        raise PermissionError(
            f"表 {table} 属 {domain_resource} 域，当前用户无 {domain_resource}:read 权限")

    engine = get_engine()
    if engine is None:
        raise RuntimeError("数据库不可用")
    from app.core.database import _is_mysql_engine  # noqa: PLC0415

    async with engine.connect() as conn:
        if _is_mysql_engine(engine):
            await conn.execute(text("SET SESSION MAX_EXECUTION_TIME=5000"))
        rows = (await conn.execute(text(sql))).mappings().all()
    logger.info("platform.readonly_query", table=table, rows=len(rows),
                user=ctx.user_id)
    return {"sql": sql, "rows": [dict(r) for r in rows], "total": len(rows)}


def get_allowed_tables() -> set[str]:
    return set(_ALLOWED_TABLES)
