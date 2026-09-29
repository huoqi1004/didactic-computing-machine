"""
Agent 定制化配置中心（路线图 Phase F / F1.3，管理端可配置化核心）
====================================================================

分层模型：**内置默认（代码常量）→ 定制覆盖（MySQL 定制层）→ 运行时合成**。

- **读取**（运行时各执行点）：``get_tool_override`` / ``get_threshold`` /
  ``is_tool_enabled``——读内存缓存（合成视图），5 秒节流经 Redis 版本信号
  ``erp:cust:version`` 检测漂移并重载（多实例收敛，复用 RBAC 模式）；
- **变更**（管理端 API）：收紧类即时生效；**放宽类（红线）自动转人审**——
  写入 status=pending_approval 的定制行 + 审批行，批准后生效；
- **版本与回滚**：每次变更写 customization_versions（append-only），
  支持回滚到任意历史版本或出厂默认；
- **审计**：全部变更（含批准/驳回/回滚）入审计哈希链。

定制类型白名单：tool / gate / agent / workflow / skill（§红线：超集拒绝）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

logger = structlog.get_logger()

CUSTOMIZATION_VERSION_KEY = "erp:cust:version"
SCHEMA_VERSION = 1

# 定制类型白名单（红线：超集拒绝）
CUSTOMIZATION_TYPES = ("tool", "gate", "agent", "workflow", "skill")

# 放宽类判定规则（放宽 = 降低审批强度/恢复能力——须人审，红线 2）
TOOL_RISK_ORDER = {"readonly": 0, "write_low": 1, "write_high": 2}


class CustomizationError(Exception):
    """定制变更被拒绝（schema 白名单/非法参数）。"""


# ===========================================================================
# 阈值注册表（gate 类型可配的白名单键——超出注册表的键拒绝，防越权配置）
# ===========================================================================

THRESHOLD_DEFAULTS: dict[str, float] = {
    "reimbursement_auto_approve_limit": 5000.0,   # 报销免审线（workflows）
    "payment_human_gate_limit": 100_000.0,        # 大额付款人审线（workflows）
    "agent_daily_token_budget": 2_000_000.0,      # per-agent 日 token 预算（runtime）
}


class CustomizationService:
    """定制层核心（进程内单例；缓存 + 版本信号 + 放宽审批路由）。"""

    def __init__(self) -> None:
        # 合成视图缓存：{f"{type}:{target}": {"enabled": bool, "overrides": dict}}
        self._cache: dict[str, dict] = {}
        self._cache_version: int = -1
        self._loaded_from_db: bool = False
        # 幂等键去重（防重放）：key -> 时间戳（5 分钟窗口）
        self._idempotency: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 缓存与版本信号
    # ------------------------------------------------------------------

    async def ensure_fresh(self) -> None:
        """版本信号漂移检测（5 秒节流）——漂移则重载缓存。"""
        import time as _time

        from app.core import redis_store

        now = _time.monotonic()
        if now - getattr(self, "_checked_at", 0.0) < 5.0 and self._loaded_from_db:
            return
        self._checked_at = now

        version = await redis_store.get_key_value(CUSTOMIZATION_VERSION_KEY)
        if version is None and self._loaded_from_db:
            return  # redis 不可用且已加载——保持现状
        if version != self._cache_version or not self._loaded_from_db:
            await self._reload()
            self._cache_version = version or 0

    async def _reload(self) -> None:
        """从 MySQL 重载定制缓存（active 状态行）。"""
        from app.db.repositories.base import has_db, session_scope

        cache: dict[str, dict] = {}
        if has_db():
            try:
                async with session_scope() as session:
                    from sqlalchemy import select

                    from app.db.models import agent_customizations

                    rows = (await session.execute(
                        select(agent_customizations).where(
                            agent_customizations.c.status == "active")
                    )).all()
                for r in rows:
                    cache[f"{r.type}:{r.target}"] = {
                        "enabled": bool(r.enabled),
                        "overrides": r.overrides or {},
                    }
            except Exception as e:  # noqa: BLE001
                logger.error("customization.reload_failed", error=str(e))
                if self._loaded_from_db:
                    return  # 已有缓存——保留旧值（失败安全）
        self._cache = cache
        self._loaded_from_db = True
        logger.info("customization.cache_reloaded", entries=len(cache))

    # ------------------------------------------------------------------
    # 运行时读取（治理面/工作流/各执行点）
    # ------------------------------------------------------------------

    def get_override(self, ctype: str, target: str) -> Optional[dict]:
        """读取某定制项的 active 覆盖（缓存，未加载时返回 None）。"""
        return self._cache.get(f"{ctype}:{target}")

    def is_tool_enabled(self, tool_name: str) -> bool:
        """工具是否启用（无定制记录 = 启用；定制 enabled=False = 停用）。"""
        entry = self._cache.get(f"tool:{tool_name}")
        if entry is None:
            return True
        return bool(entry.get("enabled", True))

    def get_tool_risk_override(self, tool_name: str) -> Optional[str]:
        """工具风险分级覆盖（readonly/write_low/write_high 或 None）。"""
        entry = self._cache.get(f"tool:{tool_name}")
        if entry is None:
            return None
        return (entry.get("overrides") or {}).get("risk_override")

    def get_threshold(self, key: str) -> Optional[float]:
        """阈值覆盖（gate 类型，target=key）。无覆盖返回 None（用内置默认）。"""
        entry = self._cache.get(f"gate:{key}")
        if entry is None:
            return None
        try:
            return float((entry.get("overrides") or {}).get("value"))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # 审批人路由矩阵（Phase F2 / F2.5）
    # ------------------------------------------------------------------

    def resolve_approve_roles(self, document_type: str,
                              amount: Optional[float] = None) -> Optional[list[str]]:
        """
        按「单据类型 × 金额区间 → 审批角色」矩阵解析审批角色。

        矩阵存于定制层：gate:approval_route_matrix =
        {"rules": [{"document_type": "...", "amount_min": ..., "amount_max": ...,
                    "approve_roles": [...], "mode": "any_of"}],
         "default": "legacy"}

        Returns:
            角色列表；None = 未命中 → 落入存量三级审批链（向后兼容）。
        """
        entry = self._cache.get("gate:approval_route_matrix")
        if entry is None:
            return None
        rules = (entry.get("overrides") or {}).get("rules") or []
        for rule in rules:
            if rule.get("document_type") != document_type:
                continue
            a_min = rule.get("amount_min")
            a_max = rule.get("amount_max")
            if a_min is not None and amount is not None and amount < float(a_min):
                continue
            if a_max is not None and amount is not None and amount > float(a_max):
                continue
            roles = rule.get("approve_roles") or []
            return [str(r) for r in roles] or None
        return None

    # ------------------------------------------------------------------
    # 域业务参数（Phase F2 / F2.3-F2.4：声明式域参数，管理端可调）
    # ------------------------------------------------------------------

    def get_domain_param(self, domain: str, key: str, default: Any = None) -> Any:
        """读取域业务参数（agent:{domain} 定制的 overrides.params）。"""
        entry = self._cache.get(f"agent:{domain}")
        params = (entry or {}).get("overrides", {}).get("params") or {}
        return params.get(key, default)

    # ------------------------------------------------------------------
    # 变更（管理端 API 调用；收紧即时 / 放宽转人审）
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_change(ctype: str, target: str, new: dict, old: Optional[dict]) -> str:
        """
        变更分级：返回 "tighten"（收紧，即时生效）或 "relax"（放宽，须人审）。

        判定规则（红线 2）：
        - tool：风险降低（high→low/readonly、low→readonly）或停用→启用 = 放宽；
          风险提升或启用→停用 = 收紧；
        - gate：阈值上调 = 放宽（审批面变松）；下调 = 收紧；
        - 其他类型：保守起见一律视为放宽（须审批）。
        """
        if ctype == "tool":
            old_enabled = bool(old.get("enabled", True)) if old else True
            new_enabled = bool(new.get("enabled", True))
            old_risk = (old or {}).get("overrides", {}).get("risk_override")
            new_risk = new.get("overrides", {}).get("risk_override")
            # 基线：无旧覆盖时取注册表内置风险（覆盖 write_low→write_high 是收紧）
            if old_risk is None:
                from app.core.agent.tools_v2 import tool_registry_v2

                builtin = tool_registry_v2.get(target)
                old_risk = builtin.risk_level.value if builtin else "readonly"
            if not old_enabled and new_enabled:
                return "relax"  # 停用→启用 = 放宽
            if old_enabled and not new_enabled:
                return "tighten"  # 启用→停用 = 收紧
            if new_risk and new_risk != old_risk:
                if TOOL_RISK_ORDER.get(new_risk, 0) < TOOL_RISK_ORDER.get(old_risk, 0):
                    return "relax"  # 风险降级 = 放宽
                return "tighten"
            return "tighten"
        if ctype == "agent":
            # agent 级定制（人设/温度/预算/白名单/域参数）即时生效：
            # 白名单仅收窄 LLM 可见集（治理面仍逐调用校验），不构成权限放宽
            return "tighten"
        if ctype == "gate" and target == "approval_route_matrix":
            return "relax"  # 审批路由矩阵变更影响面大，一律人审（红线 2）
        if ctype == "gate":
            old_val = float((old or {}).get("overrides", {}).get("value",
                             THRESHOLD_DEFAULTS.get(target, 0)))
            try:
                new_val = float(new.get("value", old_val))
            except (TypeError, ValueError):
                return "tighten"
            return "relax" if new_val > old_val else "tighten"
        return "relax"

    async def apply_change(
        self, ctype: str, target: str, overrides: dict,
        enabled: bool = True, changed_by: str = "system",
        comment: str = "", environment: str = "prod",
        idempotency_key: str = "",
    ) -> dict:
        """
        应用定制变更（管理端 API 入口）。

        Returns:
            {"status": "applied"|"pending_approval"|"rejected",
             "reason": str, "thread_id": str|None}
        """
        from app.db.repositories.base import has_db

        if ctype not in CUSTOMIZATION_TYPES:
            raise CustomizationError(f"非法定制类型: {ctype}（白名单: {CUSTOMIZATION_TYPES}）")
        if not target or len(target) > 128:
            raise CustomizationError("target 非法")
        if idempotency_key and self._seen_idempotency_key(idempotency_key):
            return {"status": "duplicate", "reason": "重复请求（幂等键已存在）",
                    "thread_id": None}

        # 合法性：tool 类型 target 必须是已注册工具（防配置错别字）
        if ctype == "tool":
            from app.core.agent.tools_v2 import tool_registry_v2

            if tool_registry_v2.get(target) is None:
                raise CustomizationError(f"未知工具: {target}")

        # 读取当前 active 覆盖（旧值）
        old_entry = self.get_override(ctype, target) if self._loaded_from_db else None
        change_kind = self._classify_change(ctype, target,
                                            {"overrides": overrides, "enabled": enabled},
                                            old_entry)

        if not has_db():
            # 无 DB：仅更新内存缓存（开发/测试模式，显式标注）
            self._cache[f"{ctype}:{target}"] = {"enabled": enabled, "overrides": overrides}
            logger.warning("customization.applied_no_db", ctype=ctype, target=target)
            return {"status": "applied", "reason": "无 DB 模式（仅内存）", "thread_id": None}

        thread_id = f"cust:{ctype}:{target}:{changed_by[:16]}"

        if change_kind == "relax":
            # 放宽类：写 pending 行 + 审批行（不生效）
            await self._upsert_customization(
                ctype, target, overrides, enabled,
                status="pending_approval", updated_by=changed_by,
                environment=environment,
            )
            await self._create_approval_record(
                ctype, target, old_entry, {"overrides": overrides, "enabled": enabled},
                changed_by, comment, thread_id,
            )
            logger.info("customization.pending_approval",
                        ctype=ctype, target=target, by=changed_by)
            return {"status": "pending_approval",
                    "reason": "放宽类变更需人工审批后生效",
                    "thread_id": thread_id}

        # 收紧类：直接生效
        await self._upsert_customization(
            ctype, target, overrides, enabled,
            status="active", updated_by=changed_by, environment=environment,
        )
        await self._finalize(ctype, target, old_entry,
                             {"overrides": overrides, "enabled": enabled},
                             status="active", changed_by=changed_by,
                             approved_by=None, comment=comment,
                             environment=environment)
        self._set_cache(ctype, target, overrides, enabled)
        await self._bump_version()
        return {"status": "applied", "reason": "收紧类变更即时生效", "thread_id": None}

    async def rollback_to_version(self, ctype: str, target: str, version_id: int,
                                  rolled_by: str) -> dict:
        """回滚到历史版本（恢复该版本的 overrides 快照）。"""
        from app.db.repositories.base import has_db, session_scope

        if not has_db():
            raise CustomizationError("存储不可用")
        async with session_scope() as session:
            from sqlalchemy import select

            from app.db.models import customization_versions

            row = (await session.execute(
                select(customization_versions).where(
                    customization_versions.c.id == version_id,
                    customization_versions.c.type == ctype,
                    customization_versions.c.target == target,
                )
            )).first()
            if row is None:
                raise CustomizationError("历史版本不存在")
            # 恢复目标 = 该版本的"变更前状态"（old_summary）——回滚语义是撤销变更
            old_summary = row.old_summary or {}
            overrides = old_summary.get("overrides") or {}
            enabled = bool(old_summary.get("enabled", True))
            status = "active" if enabled else "removed"

        old_entry = self.get_override(ctype, target)
        # 回滚的放宽判定：以回滚目标的 enabled 状态对比当前——放宽仍须审批
        change_kind = self._classify_change(
            ctype, target, {"overrides": overrides, "enabled": enabled},
            old_entry)
        if change_kind == "relax":
            return {"status": "pending_approval",
                    "reason": "回滚目标为放宽状态，需人工审批"}

        await self._upsert_customization(
            ctype, target, overrides, enabled,
            status="active" if enabled else "removed",
            updated_by=rolled_by, environment="prod",
        )
        await self._finalize(ctype, target, old_entry,
                             {"overrides": overrides, "enabled": enabled},
                             status="rolled_back", changed_by=rolled_by,
                             approved_by=None, comment=f"回滚到版本 {version_id}",
                             environment="prod")
        self._set_cache(ctype, target, overrides, enabled)
        await self._bump_version()
        return {"status": "applied", "reason": f"已回滚到版本 {version_id}"}

    async def apply_pending(self, ctype: str, target: str, approved: bool,
                            decided_by: str, comment: str = "") -> dict:
        """审批决定后应用/驳回 pending 定制（decide 路由调用）。"""
        from app.db.repositories.base import has_db, session_scope

        if not has_db():
            return {"status": "error", "reply": "存储不可用"}
        async with session_scope() as session:
            from sqlalchemy import select

            from app.db.models import agent_customizations

            row = (await session.execute(
                select(agent_customizations).where(
                    agent_customizations.c.type == ctype,
                    agent_customizations.c.target == target,
                    agent_customizations.c.status == "pending_approval",
                )
            )).first()
            if row is None:
                return {"status": "error", "reply": "待审批定制不存在"}

        if not approved:
            await self._upsert_customization(
                ctype, target, row.overrides or {}, True,
                status="rejected", updated_by=decided_by,
                environment=row.environment,
            )
            await self._audit(ctype, target, decided_by, approved, comment)
            return {"status": "rejected", "reply": "定制变更已驳回（未生效）"}

        # 批准：生效 + 版本信号
        await self._upsert_customization(
            ctype, target, row.overrides or {}, row.enabled,
            status="active", updated_by=row.updated_by,
            environment=row.environment,
        )
        await self._finalize(
            ctype, target, None, {"overrides": row.overrides, "enabled": row.enabled},
            status="approved_executed", changed_by=row.updated_by,
            approved_by=decided_by, comment=comment, environment=row.environment)
        self._set_cache(ctype, target, row.overrides or {}, row.enabled)
        await self._bump_version()
        return {"status": "executed",
                "reply": f"定制变更已生效：{ctype}:{target}"}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _upsert_customization(self, ctype, target, overrides, enabled,
                                    status, updated_by, environment) -> None:
        from app.db.repositories.base import dialect_upsert_stmt, has_db, session_scope

        if not has_db():
            return
        async with session_scope() as session:
            from app.db.models import agent_customizations

            values = dict(
                type=ctype, target=target, enabled=bool(enabled),
                overrides=overrides or {}, schema_version=SCHEMA_VERSION,
                status=status, environment=environment,
                updated_by=updated_by,
                updated_at=datetime.now(timezone.utc),
            )
            await session.execute(dialect_upsert_stmt(
                agent_customizations, values,
                conflict_cols=["type", "target", "environment"],
            ))

    async def _finalize(self, ctype, target, old_entry, new_entry, status,
                        changed_by, approved_by, comment, environment) -> None:
        """版本历史 + 审计（变更收尾三件套）。"""
        from app.db.repositories.base import has_db, session_scope

        def _summary(entry) -> dict:
            if not entry:
                return {"enabled": True, "overrides": None}
            return {"enabled": entry.get("enabled", True),
                    "overrides": entry.get("overrides")}
        if not has_db():
            return
        try:
            async with session_scope() as session:
                from sqlalchemy import insert as _insert

                from app.db.models import customization_versions

                await session.execute(_insert(customization_versions).values(
                    type=ctype, target=target, environment=environment,
                    overrides=new_entry.get("overrides") or {},
                    old_summary=_summary(old_entry),
                    new_summary=_summary(new_entry),
                    status=status, comment=(comment[:512] if comment else None),
                    changed_by=changed_by, approved_by=approved_by,
                ))
        except Exception as e:  # noqa: BLE001
            logger.error("customization.version_record_failed", error=str(e))
        try:
            from app.core.security.audit import audit_chain

            await audit_chain.log(
                actor_did=changed_by,
                action="customization.changed",
                resource=f"{ctype}:{target}",
                result="success",
                details={"old": _summary(old_entry), "new": _summary(new_entry),
                         "status": status, "approved_by": approved_by},
                risk_level="high" if status in ("approved_executed", "rolled_back") else "medium",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("customization.audit_failed", error=str(e))

    async def _create_approval_record(self, ctype, target, old_entry, new_entry,
                                      changed_by, comment, thread_id) -> None:
        from app.db.repositories.base import has_db, session_scope

        if not has_db():
            return
        try:
            async with session_scope() as session:
                from app.db.models import agent_approval_requests
                from sqlalchemy.dialects.mysql import insert as mysql_insert

                values = dict(
                    thread_id=thread_id,
                    channel_id=None,
                    agent_key="customization",
                    domain="customization",
                    tool=f"{ctype}:{target}",
                    args_json={"old": old_entry, "new": new_entry},
                    preview=(f"定制变更（放宽类）：{ctype}:{target} "
                             f"{json.dumps(new_entry.get('overrides', {}), ensure_ascii=False)[:200]}"),
                    risk="high",
                    status="pending",
                    requested_by=changed_by,
                )
                stmt = mysql_insert(agent_approval_requests).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    args_json=values["args_json"], preview=values["preview"],
                    status="pending", requested_by=changed_by)
                await session.execute(stmt)
        except Exception as e:  # noqa: BLE001
            logger.error("customization.approval_record_failed", error=str(e))

    async def _bump_version(self) -> None:
        from app.core import redis_store

        version = await redis_store.bump_key_value(CUSTOMIZATION_VERSION_KEY)
        if version is not None:
            self._cache_version = version

    def _seen_idempotency_key(self, key: str) -> bool:
        import time as _time

        now = _time.monotonic()
        stale = [k for k, ts in self._idempotency.items() if now - ts > 300]
        for k in stale:
            self._idempotency.pop(k, None)
        if key in self._idempotency:
            return True
        self._idempotency[key] = now
        return False

    def _set_cache(self, ctype: str, target: str, overrides: dict, enabled: bool) -> None:
        """本地缓存同步（生效路径立即见效；多实例经版本信号收敛）。"""
        self._cache[f"{ctype}:{target}"] = {
            "enabled": bool(enabled), "overrides": overrides or {},
        }
        self._loaded_from_db = True

    @staticmethod
    async def _audit(ctype: str, target: str, decided_by: str,
                     approved: bool, comment: str) -> None:
        try:
            from app.core.security.audit import audit_chain

            await audit_chain.log(
                actor_did=decided_by,
                action="customization.decided",
                resource=f"{ctype}:{target}",
                result="success",
                details={"approved": approved, "comment": comment},
                risk_level="high" if approved else "medium",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("customization.decision_audit_failed", error=str(e))

    def list_active(self) -> list[dict]:
        """列出生效中的定制（缓存视图）。"""
        return [
            {"type": k.split(":", 1)[0], "target": k.split(":", 1)[1], **v}
            for k, v in self._cache.items()
        ]


# 进程内单例
customization_service = CustomizationService()
