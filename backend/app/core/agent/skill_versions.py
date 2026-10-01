"""
Skill 定义版本管理（路线图 Phase F / F4.4-F4.5）
================================================

把 D4 的"目录式 YAML 技能"接进定制平台的**编辑—发布—历史—回滚—启停**闭环，
治理语义与 :mod:`app.core.agent.workflow_versions` 完全对齐：

- 发布走人审（技能指令会进 LLM 系统提示，影响面等同 DSL）：经定制层
  ``skill:{name}`` 变更审批通道，批准后由 :func:`on_apply_decision` 落地；
- 版本不可变：已发布（stable/superseded）版本内容不可原地改，只能另存新版本；
  草稿可覆盖；
- **单一运行时真相源**：发布/回滚把版本物化为 ``settings.skills_overlay_dir``
  下的 YAML，由 ``SkillRegistry`` 按 mtime 热加载（同名以发布目录优先，
  源码目录 = 出厂默认）；DB 表只承载版本流与审批，不重复充当运行时；
- **启停**：``skill:{name}`` 生效行的 ``enabled`` 就是开关（停用=收紧即时生效，
  启用=放宽须人审），运行时 ``SkillRegistry.get()`` 只返回启用技能；
- 导入只落草稿，**绝不自动发布**（导入 = 又一次放宽，仍须过审批门）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
import yaml

logger = structlog.get_logger()

BUNDLE_FORMAT = "erp-skill-bundle"
BUNDLE_VERSION = 1
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# YAML 落盘字段顺序（编辑器表单与之对应）
_DEF_FIELDS = ("name", "display_name", "description", "domain",
               "tools", "guardrails", "instructions")


class SkillVersionError(Exception):
    """Skill 版本操作被拒绝。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# 校验
# ===========================================================================

def validate_definition(defn: Any) -> list[str]:
    """结构校验（草稿即可保存的最低门槛）。返回错误列表。"""
    if not isinstance(defn, dict):
        return ["定义必须是对象"]
    errors: list[str] = []
    name = str(defn.get("name") or "")
    if not _NAME_RE.match(name):
        errors.append("name 必须为小写字母开头的标识符（[a-z][a-z0-9_]{1,63}）")
    if not str(defn.get("domain") or "").strip():
        errors.append("domain 不能为空（决定归属场景图）")
    tools = defn.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tools 至少需要 1 个工具（工具白名单）")
    elif not all(isinstance(t, str) and t for t in tools):
        errors.append("tools 必须是工具名字符串列表")
    if not str(defn.get("instructions") or "").strip():
        errors.append("instructions 不能为空（技能指令会注入系统提示）")
    gr = defn.get("guardrails", {})
    if gr is not None and not isinstance(gr, dict):
        errors.append("guardrails 必须是对象")
    unknown = set(defn) - set(_DEF_FIELDS) - {"version", "created_by", "comment"}
    if unknown:
        errors.append("含未支持字段: " + ", ".join(sorted(unknown)))
    return errors


def validate_publishable(defn: Any) -> list[str]:
    """发布校验：结构 + 引用的工具必须都已在治理面注册（防配置错别字）。"""
    from app.core.agent.tools_v2 import tool_registry_v2

    errors = validate_definition(defn)
    for t in (defn or {}).get("tools") or []:
        if isinstance(t, str) and tool_registry_v2.get(t) is None:
            errors.append(f"工具未在治理面注册: {t}")
    return errors


# ===========================================================================
# 读写
# ===========================================================================

async def list_versions(name: Optional[str] = None, limit: int = 100) -> list[dict]:
    from sqlalchemy import select

    from app.db.models import skill_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return []
    async with session_scope() as session:
        stmt = select(skill_definitions).order_by(
            skill_definitions.c.id.desc()).limit(limit)
        if name:
            stmt = stmt.where(skill_definitions.c.name == name)
        rows = (await session.execute(stmt)).all()
    return [{
        "id": r.id, "name": r.name, "version": r.version, "status": r.status,
        "domain": (r.definition or {}).get("domain"),
        "display_name": (r.definition or {}).get("display_name"),
        "tool_count": len((r.definition or {}).get("tools") or []),
        "comment": r.comment, "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "published_at": r.published_at.isoformat() if r.published_at else None,
    } for r in rows]


async def get_definition(name: str, version: str) -> Optional[dict]:
    from sqlalchemy import select

    from app.db.models import skill_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return None
    async with session_scope() as session:
        row = (await session.execute(
            select(skill_definitions).where(
                skill_definitions.c.name == name,
                skill_definitions.c.version == version)
        )).first()
    if row is None:
        return None
    return dict(definition=row.definition, status=row.status,
                comment=row.comment, created_by=row.created_by, id=row.id)


async def skill_exists(name: str) -> bool:
    """定制 target 合法性：已发布/已存草稿，或出厂目录里就有该技能。"""
    from sqlalchemy import func, select

    from app.core.agent.skills import skill_registry
    from app.db.models import skill_definitions
    from app.db.repositories.base import has_db, session_scope

    if any(s.name == name for s in skill_registry.list_skills()):
        return True
    if not has_db():
        return False
    async with session_scope() as session:
        n = (await session.execute(
            select(func.count()).select_from(skill_definitions)
            .where(skill_definitions.c.name == name))).scalar()
    return bool(n)


async def pending_proposal(name: str) -> Optional[dict]:
    """待批提案（编辑器显示"发布申请审批中"用，来源=定制层提案行）。"""
    from sqlalchemy import select

    from app.db.models import customization_versions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        return None
    async with session_scope() as session:
        row = (await session.execute(
            select(customization_versions).where(
                customization_versions.c.type == "skill",
                customization_versions.c.target == name,
                customization_versions.c.status == "pending_approval")
            .order_by(customization_versions.c.id.desc())
        )).first()
    if row is None:
        return None
    summary = row.new_summary or {}
    return {"version_id": row.id, "comment": row.comment,
            "changed_by": row.changed_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "proposed": summary.get("overrides") or {}}


async def save_draft(name: str, version: str, definition: dict,
                     created_by: str, comment: str = "") -> dict:
    """保存/覆盖草稿（发布前编辑态）。已发布版本不可变。"""
    from app.db.models import skill_definitions
    from app.db.repositories.base import dialect_upsert_stmt, has_db, session_scope

    if not name or not version:
        raise SkillVersionError("name/version 不能为空")
    if not has_db():
        raise SkillVersionError("存储不可用")

    defn = dict(definition or {})
    defn["name"] = name                      # 路径参数为准，防正文与 URL 分裂
    errors = validate_definition(defn)
    if errors:
        raise SkillVersionError("定义校验未通过：" + "；".join(errors))

    existing = await get_definition(name, version)
    if existing and existing["status"] in ("stable", "superseded"):
        raise SkillVersionError(f"版本 {version} 已发布，内容不可变（请另存新版本）")

    values = dict(name=name, version=version, status="draft",
                  definition=defn, comment=comment[:512], created_by=created_by)
    async with session_scope() as session:
        await session.execute(dialect_upsert_stmt(
            skill_definitions, values, conflict_cols=["name", "version"]))
    await _audit("skill.draft_saved", name,
                 {"version": version, "comment": comment}, created_by)
    logger.info("skill.draft_saved", name=name, version=version, by=created_by)
    return {"status": "draft", "name": name, "version": version,
            "tools": len(defn.get("tools") or [])}


async def request_publish(name: str, version: str, changed_by: str,
                          comment: str = "",
                          idempotency_key: str = "") -> dict:
    """申请发布：技能指令进系统提示 → 影响面大 → 一律走定制层人审（红线 2）。"""
    from app.core.agent.customization import customization_service
    from app.db.repositories.base import has_db

    if not has_db():
        raise SkillVersionError("存储不可用")
    row = await get_definition(name, version)
    if row is None:
        raise SkillVersionError(f"版本不存在: {name}@{version}")
    errors = validate_publishable(row["definition"] or {})
    if errors:
        raise SkillVersionError("定义校验未通过：" + "；".join(errors))

    # 首次发布时沿用当前启停态（无生效行 = 启用），避免发布顺带把停用技能打开
    entry = customization_service.get_override("skill", name)
    enabled = True if entry is None else bool(entry.get("enabled", True))

    result = await customization_service.apply_change(
        ctype="skill", target=name,
        overrides={"action": "publish", "version": version, "comment": comment},
        enabled=enabled, changed_by=changed_by,
        comment=f"发布技能 {name}@{version}",
        idempotency_key=idempotency_key,
    )
    return {**result, "name": name, "version": version}


async def set_enabled(name: str, enabled: bool, changed_by: str,
                      comment: str = "", idempotency_key: str = "") -> dict:
    """启停：停用即时生效（收紧），启用须人审（放宽）。"""
    from app.core.agent.customization import customization_service
    from app.db.repositories.base import has_db

    if not has_db():
        raise SkillVersionError("存储不可用")
    if not await skill_exists(name):
        raise SkillVersionError(f"未登记的 Skill: {name}")
    return await customization_service.apply_change(
        ctype="skill", target=name,
        overrides={"action": "set_enabled", "enabled": bool(enabled)},
        enabled=bool(enabled), changed_by=changed_by,
        comment=f"{'启用' if enabled else '停用'}技能 {name}"
                + (f"：{comment}" if comment else ""),
        idempotency_key=idempotency_key,
    )


async def on_apply_decision(name: str, overrides: dict, approved: bool,
                            decided_by: str) -> dict:
    """定制审批决定回调（``customization_service.apply_pending`` 触发）。"""
    overrides = overrides or {}
    action = overrides.get("action")
    label = "发布" if action == "publish" else "启停"
    if not approved:
        await _audit("skill.change.rejected", name,
                     {"action": action, "version": overrides.get("version")},
                     decided_by)
        return {"status": "rejected", "reply": f"{label}申请已驳回：{name}"}
    if action != "publish":
        # 纯开关变更：生效行与版本信号都由定制层通用路径负责，这里无需再动
        verb = "启用" if overrides.get("enabled", True) else "停用"
        return {"status": "executed", "reply": f"技能{verb}已生效：{name}"}
    result = await publish(name, overrides.get("version") or "",
                           decided_by, source="approval")
    return {**result, "reply": f"技能 {name}@{overrides.get('version')} 已发布"}


async def publish(name: str, version: str, published_by: str = "system",
                  source: str = "approval") -> dict:
    """落地发布：目标版本 → stable，旧 stable → superseded，并物化 YAML。"""
    from sqlalchemy import select, update

    from app.db.models import skill_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise SkillVersionError("存储不可用")
    row = await get_definition(name, version)
    if row is None:
        raise SkillVersionError(f"版本不存在: {name}@{version}")
    errors = validate_publishable(row["definition"] or {})
    if errors:
        raise SkillVersionError("定义校验未通过：" + "；".join(errors))

    async with session_scope() as session:
        prev = [(p.version, p.status) for p in (await session.execute(
            select(skill_definitions.c.version, skill_definitions.c.status)
            .where(skill_definitions.c.name == name,
                   skill_definitions.c.status == "stable")
        )).all() if p.version != version]
        await session.execute(
            update(skill_definitions)
            .where(skill_definitions.c.name == name,
                   skill_definitions.c.status == "stable",
                   skill_definitions.c.version != version)
            .values(status="superseded"))
        await session.execute(
            update(skill_definitions)
            .where(skill_definitions.c.name == name,
                   skill_definitions.c.version == version)
            .values(status="stable", published_at=_now()))

    path = materialize(name, version, row["definition"] or {})
    await _bump_signal()
    await _audit("skill.published", name,
                 {"from": [f"{st}:{v}" for v, st in prev],
                  "to": f"stable:{version}", "source": source,
                  "file": str(path)}, published_by)
    logger.info("skill.published", name=name, version=version,
                source=source, by=published_by)
    return {"status": "published", "skill": name, "version": version,
            "file": str(path), "previous": [f"{st}:{v}" for v, st in prev]}


async def rollback(name: str, version: str, rolled_by: str) -> dict:
    """回滚到历史版本（该版本此前已通过审批，属收紧方向 → 即时生效）。"""
    from sqlalchemy import select, update

    from app.db.models import skill_definitions
    from app.db.repositories.base import has_db, session_scope

    if not has_db():
        raise SkillVersionError("存储不可用")
    row = await get_definition(name, version)
    if row is None:
        raise SkillVersionError(f"版本不存在: {name}@{version}")
    if row["status"] not in ("stable", "superseded"):
        raise SkillVersionError(f"草稿不可回滚（未通过审批）：{name}@{version}")

    async with session_scope() as session:
        prev = [(p.version, p.status) for p in (await session.execute(
            select(skill_definitions.c.version, skill_definitions.c.status)
            .where(skill_definitions.c.name == name,
                   skill_definitions.c.status.in_(("stable",)))
        )).all() if p.version != version]
        await session.execute(
            update(skill_definitions)
            .where(skill_definitions.c.name == name,
                   skill_definitions.c.status == "stable")
            .values(status="superseded"))
        await session.execute(
            update(skill_definitions)
            .where(skill_definitions.c.name == name,
                   skill_definitions.c.version == version)
            .values(status="stable", published_at=_now()))

    path = materialize(name, version, row["definition"] or {})
    await _bump_signal()
    await _audit("skill.rolled_back", name,
                 {"from": [f"{st}:{v}" for v, st in prev],
                  "to": f"stable:{version}", "file": str(path)}, rolled_by)
    logger.info("skill.rolled_back", name=name, version=version, by=rolled_by)
    return {"status": "rolled_back", "skill": name, "version": version,
            "file": str(path), "previous": [f"{st}:{v}" for v, st in prev]}


# ===========================================================================
# 物化（DB 版本 → 发布目录 YAML，热加载即生效）
# ===========================================================================

def to_yaml(defn: dict, version: str) -> str:
    payload = {k: defn.get(k) for k in _DEF_FIELDS
               if k != "instructions" and defn.get(k) is not None}
    payload["version"] = version
    payload["instructions"] = defn.get("instructions", "")
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=1000)


def materialize(name: str, version: str, definition: dict):
    """写发布目录（同名文件覆盖，SkillRegistry 的 mtime 指纹下一轮即热加载）。"""
    from app.core.agent.skills import overlay_dir

    d = overlay_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.yaml"
    path.write_text(to_yaml(definition, version), encoding="utf-8")
    return path


# ===========================================================================
# 导出 / 导入（F4.5：跨环境搬运，JSON 模板）
# ===========================================================================

async def export_bundle(name: Optional[str] = None, include_draft: bool = False,
                        include_runtime: bool = True) -> dict:
    """
    导出技能定义（默认只导已发布版本；include_draft 连带草稿）。

    include_runtime：把运行时可见但**没有版本流行**的出厂技能一并带出
    （version 标 ``factory``）。出厂 YAML 只读、不在 skill_definitions 里，
    不带它们的话新环境导出永远是空包，跨环境搬运（UAT-F4-2）就走不通；
    导出的出厂项在目标环境仍只落草稿，须逐条过人审才生效。
    """
    rows = await list_versions(name, limit=500)
    skills, seen = [], set()
    for r in rows:
        if not include_draft and r["status"] == "draft":
            continue
        if r["name"] in seen:
            continue                      # 同名取最新一条（列表按 id 倒序）
        seen.add(r["name"])
        row = await get_definition(r["name"], r["version"])
        if row is None:
            continue
        skills.append({"name": r["name"], "version": r["version"],
                       "status": r["status"], "definition": row["definition"]})

    if include_runtime:
        from app.core.agent import skills as skills_mod

        for s in skills_mod.skill_registry.list_skills():
            if (name and s.name != name) or s.name in seen:
                continue
            seen.add(s.name)
            skills.append({
                "name": s.name, "version": s.version or "factory",
                "status": "published" if s.published else "factory",
                "definition": {"name": s.name, "display_name": s.display_name,
                               "description": s.description, "domain": s.domain,
                               "tools": list(s.tools),
                               "guardrails": dict(s.guardrails),
                               "instructions": s.instructions},
            })

    return {"format": BUNDLE_FORMAT, "bundle_version": BUNDLE_VERSION,
            "exported_at": _now().isoformat(), "count": len(skills),
            "skills": skills}


async def import_bundle(bundle: dict, changed_by: str) -> dict:
    """
    导入技能包：**逐条落草稿**，绝不自动发布（导入内容未经本环境审批）。

    Returns: {"imported": n, "failed": [{"name","errors"}]}
    """
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        raise SkillVersionError(f"非法定制包格式（应为 {BUNDLE_FORMAT}）")
    items = bundle.get("skills")
    if not isinstance(items, list) or not items:
        raise SkillVersionError("定制包中没有技能（skills 为空）")

    imported, failed = [], []
    for item in items:
        defn = (item or {}).get("definition") or {}
        name = str(defn.get("name") or (item or {}).get("name") or "")
        version = str((item or {}).get("version") or "v1")
        errors = validate_definition(defn)
        if errors or not name:
            failed.append({"name": name or version,
                           "errors": errors or ["缺少 name"]})
            continue
        try:
            await save_draft(name, version, defn, changed_by,
                             comment="导入自定制包")
            imported.append({"name": name, "version": version})
        except SkillVersionError as e:
            failed.append({"name": name, "errors": [str(e)]})
    return {"imported": len(imported), "items": imported, "failed": failed,
            "note": "导入内容为草稿，需逐条申请发布并经人审后才会生效"}


# ===========================================================================
# 设计器元数据
# ===========================================================================

def form_metadata() -> dict:
    """编辑器表单所需的白名单（工具清单按域 + 出厂技能模板）。"""
    from app.core.agent import skills as skills_mod
    from app.core.agent.tools_v2 import tool_registry_v2

    by_domain: dict[str, list[str]] = {}
    for spec in tool_registry_v2.list_specs():
        # ToolSpecV2 的归属域字段是 agent_domain（to_dict 才对外叫 domain），
        # 未标注域的工具归入 "_"，否则编辑器按域分组会丢工具。
        by_domain.setdefault(spec.agent_domain or "_", []).append(spec.name)
    template = {"name": "", "display_name": "", "description": "",
                "domain": "", "tools": [], "instructions": "",
                "guardrails": {}}
    return {
        "tools_by_domain": {k: sorted(v) for k, v in sorted(by_domain.items())},
        "domains": sorted(by_domain.keys()),
        "skills": skills_mod.skill_registry.to_catalog(),
        "template": template,
        "overlay_dir": str(skills_mod.overlay_dir()),
        "name_pattern": _NAME_RE.pattern,
    }


# ===========================================================================
# 内部
# ===========================================================================

async def _bump_signal() -> None:
    from app.core.agent.customization import customization_service

    await customization_service.bump_version_signal()


async def _audit(action: str, name: str, details: dict, actor: str) -> None:
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(
            actor_did=actor or "unknown", action=action,
            resource=f"skill:{name}", result="success", details=details,
            risk_level="high" if action in ("skill.published",
                                            "skill.change.rejected")
            else "medium")
    except Exception as e:  # noqa: BLE001
        logger.warning("skill.version_audit_failed", action=action, error=str(e))
