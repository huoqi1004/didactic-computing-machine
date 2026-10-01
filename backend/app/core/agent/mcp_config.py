"""
外部 MCP Server 管理端配置（路线图 Phase F4.1 / F4.2）
======================================================

把 D3 时代"只能写环境变量"的外部 MCP 接入升级为管理端可配置的运行时资产：

- **连接清单**（本模块 + ``mcp_servers`` 表）：name / url / description / 探测结果；
  URL 保存前与每次出站前都过 §2.4 SSRF 校验（``app/core/security/url_guard``）。
- **暴露清单**（定制层）：``agent_customizations`` 里 ``type="mcp"`` 的生效行，
  ``overrides.tools`` = 已批准接入的外部工具名。放这里而不是本表，是为了免费拿到
  版本流、放宽审批、回滚与审计（红线 2：**新外部 MCP 接入必须走人审后生效**）。
- **增量注册**（F4.2）：批准后把白名单工具桥接进 ``tool_registry_v2``（append-only）。
  工具名固定 ``external_{server}_{tool}``、risk=readonly、resource=external，
  调用仍逐次过治理面；撤销接入=收紧（即时），走 ``tool`` 类停用即可。
- 未登记定制行的 server 一律不暴露任何工具（接入 ≠ 暴露，失败安全）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

import structlog

logger = structlog.get_logger()

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_TOOLS_PER_SERVER = 32


class McpConfigError(Exception):
    """配置非法（名称/URL/未知 server）。"""


def _row_to_dict(row: Any) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "description": row.description or "",
        "last_probe": row.last_probe or None,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat(sep=" ", timespec="seconds")
        if row.created_at else None,
        "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds")
        if row.updated_at else None,
    }


def _check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise McpConfigError("名称须为小写字母开头的 a-z0-9_（≤64 字符），它决定工具名前缀")
    return name


async def _get_row(name: str) -> Optional[Any]:
    from sqlalchemy import select

    from app.db.models import mcp_servers
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return None
    async with session_scope() as session:
        return (await session.execute(
            select(mcp_servers).where(mcp_servers.c.name == name)
        )).first()


async def server_exists(name: str) -> bool:
    """供定制层校验 target 合法性（防配置错别字，与 tool 类同语义）。"""
    return await _get_row(name) is not None


async def list_servers() -> list[dict]:
    """连接清单 + 各自已批准生效的暴露工具（合成视图）。"""
    from sqlalchemy import select

    from app.core.agent.customization import customization_service
    from app.db.models import mcp_servers
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return []
    async with session_scope() as session:
        rows = (await session.execute(
            select(mcp_servers).order_by(mcp_servers.c.name.asc())
        )).all()

    out = []
    for row in rows:
        entry = _row_to_dict(row)
        ov = customization_service.get_override("mcp", row.name) or {}
        entry["exposed_tools"] = list((ov.get("overrides") or {}).get("tools") or [])
        entry["exposure_enabled"] = bool(ov.get("enabled"))
        out.append(entry)
    return out


async def create_server(name: str, url: str, description: str, by: str) -> dict:
    """登记外部 MCP Server（不暴露任何工具，因此即时可建）。"""
    from sqlalchemy import func, insert, select

    from app.core.security import url_guard
    from app.db.models import mcp_servers
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise McpConfigError("存储不可用")
    _check_name(name)
    url = await url_guard.validate_outbound_url(url)

    async with session_scope() as session:
        dup = (await session.execute(
            select(func.count()).select_from(mcp_servers).where(mcp_servers.c.name == name)
        )).scalar()
        if dup:
            raise McpConfigError(f"名称已存在: {name}")
        await session.execute(insert(mcp_servers).values(
            name=name, url=url, description=description[:255] or None,
            created_by=by, updated_by=by,
        ))
    logger.info("mcp_config.created", name=name, by=by)
    return {"status": "created", "name": name, "url": url}


async def update_server(name: str, *, url: Optional[str] = None,
                        description: Optional[str] = None, by: str = "system") -> dict:
    """
    改连接信息。

    已暴露工具的 server 禁止改 URL：桥接工具的 handler 在注册时绑定地址，
    注册表是 append-only，改地址不会在运行期生效——与其留一个"改了不生效"的坑，
    不如要求先撤销暴露（收紧，即时）再改地址。
    """
    from sqlalchemy import update

    from app.core.agent.customization import customization_service
    from app.core.security import url_guard
    from app.db.models import mcp_servers
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise McpConfigError("存储不可用")
    row = await _get_row(name)
    if row is None:
        raise McpConfigError(f"未登记的 MCP Server: {name}")

    values: dict[str, Any] = {"updated_by": by}
    if description is not None:
        values["description"] = description[:255] or None
    if url is not None and url != row.url:
        ov = customization_service.get_override("mcp", name) or {}
        if (ov.get("overrides") or {}).get("tools") and ov.get("enabled", True):
            raise McpConfigError(
                "该 server 已有生效中的外部工具，改地址不会在运行期生效；"
                "请先撤销暴露（清空工具白名单，收紧即时）再修改地址")
        values["url"] = await url_guard.validate_outbound_url(url)

    if len(values) <= 1:
        return {"status": "noop", "reason": "没有需要修改的字段"}

    async with session_scope() as session:
        await session.execute(
            update(mcp_servers).where(mcp_servers.c.name == name).values(**values)
        )
    logger.info("mcp_config.updated", name=name, by=by, fields=sorted(values))
    return {"status": "updated", "name": name}


async def delete_server(name: str, by: str = "system") -> dict:
    """删除登记并撤销其暴露（撤销=收紧，即时生效）。"""
    from sqlalchemy import delete

    from app.core.agent.customization import customization_service
    from app.db.models import mcp_servers
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise McpConfigError("存储不可用")
    if await _get_row(name) is None:
        raise McpConfigError(f"未登记的 MCP Server: {name}")

    # 先撤销暴露再删行：apply_change 会校验 target 已登记，且残留的 mcp 生效行
    # 会在下次同步时指向一个不存在的 server。
    await customization_service.apply_change(
        "mcp", name, {"tools": []}, enabled=False,
        changed_by=by, comment=f"删除 MCP Server 登记（{by}）",
    )
    async with session_scope() as session:
        await session.execute(delete(mcp_servers).where(mcp_servers.c.name == name))

    logger.info("mcp_config.deleted", name=name, by=by)
    return {"status": "deleted", "name": name}


async def probe_url(url: str) -> dict:
    """连接测试 + 工具发现（未保存的地址同样强制 SSRF 校验）。"""
    from app.core.agent.external_tools import discover_tools
    from app.core.security import url_guard

    try:
        safe = await url_guard.validate_outbound_url(url)
    except url_guard.UnsafeURL as e:
        return {"ok": False, "error": str(e), "tools": []}
    res = await discover_tools(safe, check_url=False)
    return {"ok": bool(res["ok"]), "url": safe,
            "tools": res["tools"], "error": res["error"]}


async def probe_server(name: str, by: str = "system") -> dict:
    """对已登记的 server 做连接测试，结果写回 last_probe（供管理端显示）。"""
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.db.models import mcp_servers
    from app.db.repositories.base import has_db, session_scope

    row = await _get_row(name)
    if row is None:
        raise McpConfigError(f"未登记的 MCP Server: {name}")

    result = await probe_url(row.url)
    payload = {
        "ok": bool(result["ok"]),
        "tools": result["tools"],
        "error": result.get("error", ""),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "by": by,
    }
    if has_db():
        async with session_scope() as session:
            await session.execute(
                update(mcp_servers).where(mcp_servers.c.name == name)
                .values(last_probe=payload)
            )
    logger.info("mcp_config.probed", name=name, ok=payload["ok"],
                tools=len(payload["tools"]), error=payload["error"][:200])
    return {"status": "probed", "name": name, **payload}


async def set_exposed_tools(name: str, tools: list[str], by: str,
                            comment: str = "",
                            idempotency_key: str = "") -> dict:
    """
    勾选外部工具（F4.2 的"增量注册 + 放宽审批"入口）。

    扩面（新增工具/首次接入）= 放宽 → 后端返回 pending_approval，批准后才注册；
    收缩/撤销 = 收紧 → 即时生效。判定在 customization_service._classify_change。
    """
    from app.core.agent.customization import customization_service

    row = await _get_row(name)
    if row is None:
        raise McpConfigError(f"未登记的 MCP Server: {name}")

    cleaned = sorted({t.strip() for t in tools if t and t.strip()})
    if len(cleaned) > MAX_TOOLS_PER_SERVER:
        raise McpConfigError(f"单个 server 最多暴露 {MAX_TOOLS_PER_SERVER} 个外部工具")

    result = await customization_service.apply_change(
        "mcp", name, {"tools": cleaned}, enabled=bool(cleaned),
        changed_by=by, comment=comment or "管理端外部工具勾选",
        idempotency_key=idempotency_key,
    )
    if result.get("status") == "applied":
        synced = await sync_server(name)
        result["registered"] = synced.get("registered", [])
    return result


# ===========================================================================
# 运行期同步（启动 / 批准生效后）
# ===========================================================================

async def sync_server(name: str) -> dict:
    """按生效定制把一个 server 的白名单工具桥接进注册表（幂等）。"""
    from app.core.agent.customization import customization_service
    from app.core.agent.external_tools import bridge_server

    await customization_service.ensure_fresh()
    ov = customization_service.get_override("mcp", name) or {}
    tools = list((ov.get("overrides") or {}).get("tools") or [])
    if not ov.get("enabled", True) or not tools:
        return {"name": name, "registered": [], "skipped": [], "error": "",
                "reason": "未暴露任何工具"}

    row = await _get_row(name)
    if row is None:
        return {"name": name, "registered": [], "skipped": tools,
                "error": f"未登记的 MCP Server: {name}", "reason": "登记已删除"}

    res = await bridge_server(name, row.url, tools)
    return {"name": name, "registered": res["registered"],
            "skipped": res["skipped"], "error": res["error"], "reason": ""}


async def sync_from_db() -> dict:
    """lifespan 调用：同步所有已登记 server 的生效暴露。"""
    from app.core.agent.customization import customization_service
    from app.db.repositories.base import has_db

    if not has_db():
        return {"servers": 0, "registered": 0, "errors": []}

    await customization_service.ensure_fresh()
    names = [
        item["target"] for item in customization_service.list_active()
        if item["type"] == "mcp"
    ]
    registered, errors = 0, []
    for name in names:
        res = await sync_server(name)
        registered += len(res["registered"])
        if res["error"]:
            errors.append(f"{name}: {res['error']}")
    if registered or errors:
        logger.info("mcp_config.synced", servers=len(names),
                    registered=registered, errors=len(errors))
    return {"servers": len(names), "registered": registered, "errors": errors}


async def on_apply_decision(target: str, overrides: dict, approved: bool,
                            decided_by: str) -> dict:
    """
    定制审批钩子（apply_pending 在 ctype=="mcp" 时调用）。

    批准：把提案里的白名单真正桥接进注册表；驳回：生效配置未动，无需回滚。
    """
    if not approved:
        return {"status": "rejected", "name": target, "registered": []}
    res = await sync_server(target)
    res["status"] = "executed" if not res["error"] else "partial"
    logger.info("mcp_config.decision_applied", name=target,
                registered=len(res["registered"]), error=res["error"][:200])
    return res
