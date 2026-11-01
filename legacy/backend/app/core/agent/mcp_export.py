"""
MCP 出口暴露策略（路线图 Phase F4.3）
=====================================

"MCP 出口"指本系统把治理面工具导出为 MCP server 供**外部系统调用**
（``app/mcp/server.py``）。D1-D2 时代"暴露哪些域"写死在 ``_MCP_DOMAINS`` 里，
增删一个出口域要改代码发版。本模块把它降级为**内置默认层**，其上叠定制层：

- **构建侧**：ASGI 应用按生效定制决定挂载哪些域、导出哪些工具；
- **请求侧**：每次工具调用前重查暴露状态（读内存缓存，无 IO），
  使"关闭某域 / 停用某工具"即时生效，不依赖重启——这是"server 端过滤"的含义：
  过滤发生在被信任的服务端，而不是指望客户端自觉不请求。

管理端掌握的是**开关**，不是凭空造域：候选域仍是代码登记的
``DEFAULT_EXPORT_DOMAINS``（新增一个业务域涉及工具归属与鉴权映射，必须过代码评审）。
工具级启停不在此另建真相源，复用 ``tool`` 类定制（``customization_service``）。

失败安全：定制层读不到（无 DB / 加载异常）时回退内置默认清单，
且回退只会"维持现状"，不会放大暴露面。
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger()

# 内置默认出口域（= D1-D2 的 _MCP_DOMAINS，现降级为默认层而非唯一真相）
DEFAULT_EXPORT_DOMAINS = (
    "reimbursement", "treasury", "payroll", "hr", "crm", "sales", "contract",
)

CTYPE = "mcp_export"


def _domain_exposed(domain: str) -> bool:
    """纯缓存判定（可在同步上下文与请求热路径调用）。"""
    from app.core.agent.customization import customization_service

    entry = customization_service.get_override(CTYPE, domain)
    if entry is None:
        return domain in DEFAULT_EXPORT_DOMAINS
    return bool(entry.get("enabled", True))


def is_domain_exposed(domain: str) -> bool:
    """出口域是否对外可用（请求热路径用）。"""
    try:
        return _domain_exposed(domain)
    except Exception as e:  # noqa: BLE001
        # 判定异常一律按"不暴露"处理：出口治理宁可拒绝，不可误放
        logger.warning("mcp_export.check_failed", domain=domain, error=str(e))
        return False


def is_tool_exportable(tool_name: str, domain: str) -> bool:
    """工具可导出 = 所属域已暴露 且 该工具未被 tool 类定制停用。"""
    return is_domain_exposed(domain) and tool_enabled(tool_name)


def tool_enabled(tool_name: str) -> bool:
    """工具级出口开关：直接复用 tool 类定制，不另建第二套真相源。"""
    from app.core.agent.customization import customization_service

    return customization_service.is_tool_enabled(tool_name)


async def resolve() -> list[dict]:
    """管理端视图：候选域 × 暴露状态 × 工具计数（含数据来源，供 UI 标注"默认/已定制"）。"""
    from app.core.agent.customization import customization_service
    from app.core.agent.tools_v2 import tool_registry_v2
    from app.core.rbac.seed import RESOURCES

    await customization_service.ensure_fresh()
    out = []
    for domain in DEFAULT_EXPORT_DOMAINS:
        entry = customization_service.get_override(CTYPE, domain)
        specs = tool_registry_v2.list_specs(domain=domain)
        out.append({
            "domain": domain,
            "label": RESOURCES.get(domain, domain),
            "exposed": _domain_exposed(domain),
            "customized": entry is not None,
            "tool_total": len(specs),
            "tool_exposed": len([s for s in specs if tool_enabled(s.name)])
            if _domain_exposed(domain) else 0,
        })
    return out


async def set_domain_exposed(domain: str, exposed: bool, by: str,
                             comment: str = "",
                             idempotency_key: str = "") -> dict:
    """
    开关一个出口域。

    关闭 = 收紧（即时生效）；打开 = 放宽（对外多开一条调用通道，须人审，红线 2）。
    判定在 ``customization_service._classify_change``，本函数只负责落库与回传。
    """
    from app.core.agent.customization import CustomizationError, customization_service

    if domain not in DEFAULT_EXPORT_DOMAINS:
        raise CustomizationError(
            f"非候选出口域: {domain}（新增业务域须先在代码里登记工具归属）")
    # 先对齐缓存，再判分级：放宽/收紧的比较基准必须是"其他实例刚生效的状态"，
    # 否则本实例的旧缓存会把一次重新开放误判成收紧（红线 2 漏审批）。
    await customization_service.ensure_fresh()
    return await customization_service.apply_change(
        CTYPE, domain, {}, enabled=exposed, changed_by=by,
        comment=comment or ("开放 MCP 出口域" if exposed else "关闭 MCP 出口域"),
        idempotency_key=idempotency_key,
    )
