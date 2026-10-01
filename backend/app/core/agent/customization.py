"""
Agent 定制化配置中心（路线图 Phase F / F1.3，管理端可配置化核心）
====================================================================

分层模型：**内置默认（代码常量）→ 定制覆盖（MySQL 定制层）→ 运行时合成**。

- **读取**（运行时各执行点）：``get_tool_override`` / ``get_threshold`` /
  ``is_tool_enabled``——读内存缓存（合成视图），5 秒节流经 Redis 版本信号
  ``erp:cust:version`` 检测漂移并重载（多实例收敛，复用 RBAC 模式）；
- **变更**（管理端 API）：收紧类即时生效；**放宽类（红线）自动转人审**——
  只在 customization_versions 落一条 status=pending_approval 的提案行 + 审批行，
  **不触碰 agent_customizations**（该表按 type+target+environment 唯一，写进去会把
  当前生效的收紧顶掉，缓存重载后等于未批先松）；批准后提案内容才落生效表；
- **版本与回滚**：每次变更写 customization_versions（生效快照不可变；待批提案行在
  决定时改写为终态 approved_executed / rejected，避免同一次变更留两条历史），
  支持回滚到任意历史版本或出厂默认；
- **审计**：全部变更（含批准/驳回/回滚）入审计哈希链。

定制类型白名单：tool / gate / agent / workflow / skill / mcp / mcp_export
（§红线：超集拒绝）。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.core.metrics import CUSTOMIZATION_DEGRADED

logger = structlog.get_logger()

CUSTOMIZATION_VERSION_KEY = "erp:cust:version"
SCHEMA_VERSION = 1

# 定制类型白名单（红线：超集拒绝）
# mcp = 外部 MCP Server 的工具暴露清单（Phase F4.2），扩面一律走人审
CUSTOMIZATION_TYPES = ("tool", "gate", "agent", "workflow", "skill", "mcp", "mcp_export")

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

# 管理端表单单据名（/meta 下发，前端不硬编码阈值键）
THRESHOLD_LABELS: dict[str, str] = {
    "reimbursement_auto_approve_limit": "报销免审线（元）",
    "payment_human_gate_limit": "大额付款人审线（元）",
    "agent_daily_token_budget": "单 Agent 日 token 预算",
}

# gate 的可配 target 全集 = 阈值注册表 + 审批路由矩阵。
# 矩阵与阈值同挂 gate 类型：漏掉矩阵会打断 F2.5 的审批路由保存。
GATE_TARGETS = set(THRESHOLD_DEFAULTS) | {"approval_route_matrix"}


def gate_effective_value(entry: Optional[dict], default: float) -> float:
    """gate 阈值条目的**生效**数值：无条目/已停用/缺 value/非数字都算内置默认。"""
    if entry and entry.get("enabled", True):
        try:
            return float((entry.get("overrides") or {})["value"])
        except (TypeError, ValueError, KeyError):
            return default
    return default


class CustomizationService:
    """定制层核心（进程内单例；缓存 + 版本信号 + 放宽审批路由）。"""

    def __init__(self) -> None:
        # 合成视图缓存：{f"{type}:{target}": {"enabled": bool, "overrides": dict}}
        self._cache: dict[str, dict] = {}
        self._cache_version: int = -1
        self._loaded_from_db: bool = False
        # 幂等键去重（防重放）：key -> 时间戳（5 分钟窗口）
        self._idempotency: dict[str, float] = {}
        # 同一 (type,target) 的生效写入串行化（见 _write_lock）
        self._write_locks: dict[str, tuple] = {}
        # 降级状态（F5.4）：读不到定制层时业务继续跑，但必须让运维看得见
        self._degraded: bool = False
        self._degraded_mode: str = ""       # defaults=回退内置默认 / stale_cache=吃旧缓存
        self._degraded_reason: str = ""
        self._degraded_since: float = 0.0
        self._degraded_at_wall: str = ""
        self._degrade_events: int = 0
        self._reload_failures: int = 0
        # 至少成功加载过一次吗——决定失败该报 defaults 还是 stale_cache。
        # 不能用 _loaded_from_db 判断：它在冷启动失败时也会被置真（那是节流闸门，
        # 不是"读到过"），F5.4 真机演练就把这种情形误报成"在吃旧缓存"。
        self._load_ok: bool = False

    def _write_lock(self, ctype: str, target: str) -> asyncio.Lock:
        """
        「落库 → 写缓存 → 广播版本」按 (type,target) 串行。

        并发改同一个 target 时，两笔变更的**提交顺序**与**缓存写入顺序**可以错位
        （A 先提交、卡在 finalize；B 后提交却先写缓存）。此时库里是 B、缓存停在 A，
        而版本号是各笔自己 bump 的——最后 bump 的不一定是最后提交的，
        漂移检测看不见这次分歧，缓存与库就**永久**不一致（F5.3 并发实测复现：
        50 路同 target 写入，缓存 0.27 / 库 0.32，强制 ensure_fresh 也修不回来）。

        锁按「当前事件循环」缓存：进程内单例的生命周期比一个 loop 长
        （测试每个用例一个新 loop），跨 loop 复用同一把 asyncio.Lock 会
        RuntimeError: bound to a different event loop。
        """
        key = f"{ctype}:{target}"
        loop = asyncio.get_running_loop()
        entry = self._write_locks.get(key)
        if entry is None or entry[0] is not loop:
            entry = self._write_locks[key] = (loop, asyncio.Lock())
        return entry[1]

    # ------------------------------------------------------------------
    # 缓存与版本信号
    # ------------------------------------------------------------------

    async def ensure_fresh(self) -> None:
        """
        版本信号漂移检测（5 秒节流）——漂移则重载缓存。

        治理闸口、MCP 出口、工作流阈值这些**运行时热路径**都会 await 它，
        所以它自己必须永不抛异常（F5.4 的「0 不可用」）：任何意外都收成降级状态，
        调用方继续用现有缓存（冷启动时即内置默认）跑下去。
        """
        now = time.monotonic()
        if now - getattr(self, "_checked_at", 0.0) < 5.0 and self._loaded_from_db:
            return
        self._checked_at = now

        try:
            version = await self._read_version_signal()
            if version is None and self._loaded_from_db:
                return  # redis 不可用且已加载——保持现状
            if version != self._cache_version or not self._loaded_from_db:
                # 认领 _reload 返回的版本（=这份快照对应的那一刻），不是重载后再读一次信号
                self._cache_version = await self._reload() or 0
        except Exception as e:  # noqa: BLE001
            self._note_reload_failure(f"ensure_fresh:{type(e).__name__}: {e}")

    async def _read_version_signal(self) -> Optional[int]:
        """
        定制层版本信号统一成 int。

        ``redis_store.get_key_value`` 返回字符串，而 ``_bump_version`` 存的是 int，
        ``"7" != 7`` 恒真 → 本进程每次写完都要多重载一次缓存。与已验证的
        ``redis_store.get_rbac_version`` 口径对齐。
        """
        from app.core import redis_store

        raw = await redis_store.get_key_value(CUSTOMIZATION_VERSION_KEY)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def _reload(self) -> Optional[int]:
        """
        从 MySQL 重载定制缓存（active 状态行），返回"这份快照对应的版本信号"。

        版本必须在读数据之前取：读当口若有人写入并 bump，用读**后**的新版本去认领
        这份旧快照，缓存就会长期停在"缺那次写"的状态（本进程再也看不到漂移）。
        返回读前的版本号，下一次 ensure_fresh 就会看到漂移并重载，
        收敛上界由上面的 5 秒节流决定。
        """
        from app.db.repositories.base import has_db, session_scope

        snapshot_version = await self._read_version_signal()

        if not has_db():
            # 没配库同样是"读不到定制层"。静默跑默认值会让运维以为管理端改动生效了。
            self._note_reload_failure("mysql_unavailable")
            return snapshot_version

        cache: dict[str, dict] = {}
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
            self._note_reload_failure(f"reload:{type(e).__name__}: {e}")
            return snapshot_version

        self._cache = cache
        self._loaded_from_db = True
        self._load_ok = True
        self._clear_degraded()
        logger.info("customization.cache_reloaded", entries=len(cache))
        return snapshot_version

    def _note_reload_failure(self, reason: str) -> None:
        """
        记录一次"读定制层失败"，并把缓存放在 failure-safe 的位置上。

        成功加载过 -> 保住旧缓存（定制仍生效，只是可能过期）；
        从没读到过 -> 空缓存 = 各执行点回退代码里的内置默认。
        这两种情形对运维是两个结论，degradation_status() 用 mode 分开报。
        """
        self._reload_failures += 1
        # 失败不许动缓存：能吃的旧值（成功加载来的、或本进程自己写进去的）继续吃，
        # 真的一条都没有才叫"回退内置默认"。
        # 版本照旧认领（见 _reload 开头），否则每次读都去撞已经坏掉的库。
        self._loaded_from_db = True   # 节流闸门：试过一轮就别每个请求都去撞库
        self._mark_degraded("stale_cache" if self._cache else "defaults", reason)

    # ------------------------------------------------------------------
    # 降级状态（F5.4：读取失败回退内置默认 + 显式告警）
    # ------------------------------------------------------------------

    def _mark_degraded(self, mode: str, reason: str) -> None:
        """
        标记定制层降级。

        只在**状态跳变**时打日志：ensure_fresh 每 5 秒就会重试一次，
        每次一条 ERROR 会把真正的故障淹掉（同一个键在 Redis 上也有 `_warn_throttled`）。
        """
        self._degraded_reason = reason
        if self._degraded:
            self._degraded_mode = mode
            return
        self._degraded = True
        self._degraded_mode = mode
        self._degraded_since = time.monotonic()
        self._degraded_at_wall = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._degrade_events += 1
        logger.error("customization.degraded",
                     mode=mode,
                     reason=reason,
                     serving=("built_in_defaults" if mode == "defaults" else "last_known_cache"),
                     cache_entries=len(self._cache),
                     degrade_events=self._degrade_events,
                     reload_failures=self._reload_failures)
        self._set_degraded_metric(1)

    def _clear_degraded(self) -> None:
        """重载成功：退出降级并告警恢复（降级了多久一并给出）。"""
        if not self._degraded:
            return
        held = round(time.monotonic() - self._degraded_since, 1)
        self._degraded = False
        self._degraded_mode = ""
        self._degraded_reason = ""
        logger.info("customization.degraded_recovered",
                    degraded_seconds=held, entries=len(self._cache),
                    reload_failures=self._reload_failures)
        self._set_degraded_metric(0)

    @staticmethod
    def _set_degraded_metric(value: int) -> None:
        """Prometheus gauge 写值；指标本身出问题不许把业务带崩。"""
        try:
            CUSTOMIZATION_DEGRADED.set(value)
        except Exception:  # noqa: BLE001
            pass

    def degradation_status(self) -> dict:
        """
        降级快照——管理端/巡检脚本读它，不必翻日志（路线图 §2.4「显式告警」的可查面）。

        mode：
        - ``defaults``：读不到定制层且手里没有旧缓存 → 全量回退内置默认；
        - ``stale_cache``：读不到但手里有上一次成功加载的缓存 → 吃旧值（可能过期）。
        """
        return {
            "degraded": self._degraded,
            "mode": self._degraded_mode or None,
            "reason": self._degraded_reason or None,
            "serving": ("built_in_defaults" if self._degraded and self._degraded_mode == "defaults"
                        else "last_known_cache" if self._degraded
                        else "customization_cache"),
            "since": self._degraded_at_wall or None,
            "degraded_seconds": (round(time.monotonic() - self._degraded_since, 1)
                                 if self._degraded else 0.0),
            "degrade_events": self._degrade_events,
            "reload_failures": self._reload_failures,
            # 报的是"成功读到过"，不是"试过读"——后者在冷启动失败时也为真，
            # 会把"从没拿到过定制"误报成"在吃最后一次成功的缓存"（F5.4 真机演练踩到）
            "loaded_from_db": self._load_ok,
            "cache_entries": len(self._cache),
            "cache_version": self._cache_version,
        }


    # ------------------------------------------------------------------
    # 运行时读取（治理面/工作流/各执行点）
    # ------------------------------------------------------------------

    def get_override(self, ctype: str, target: str) -> Optional[dict]:
        """
        读某定制项的**原始条目**（含 enabled 标记；未加载时返回 None）。

        给"看启停态"的消费点用（技能启停、MCP 暴露开关、管理端视图）。
        要拿"生效中的覆盖内容"请用 :meth:`get_effective_override`——
        停用的条目在这里仍然是条目，但已经不算覆盖了。
        """
        return self._cache.get(f"{ctype}:{target}")

    def get_effective_override(self, ctype: str, target: str) -> Optional[dict]:
        """生效中的覆盖条目；无记录或已停用 = None（消费点据此回退内置默认）。"""
        entry = self._cache.get(f"{ctype}:{target}")
        if entry is None or not entry.get("enabled", True):
            return None
        return entry

    def is_tool_enabled(self, tool_name: str) -> bool:
        """工具是否启用（无定制记录 = 启用；定制 enabled=False = 停用）。"""
        entry = self._cache.get(f"tool:{tool_name}")
        if entry is None:
            return True
        return bool(entry.get("enabled", True))

    def get_tool_risk_override(self, tool_name: str) -> Optional[str]:
        """工具风险分级覆盖（readonly/write_low/write_high 或 None；停用=无覆盖）。"""
        entry = self.get_effective_override("tool", tool_name)
        if entry is None:
            return None
        return (entry.get("overrides") or {}).get("risk_override")

    def get_threshold(self, key: str) -> Optional[float]:
        """阈值覆盖（gate 类型，target=key）。无覆盖/已停用返回 None（用内置默认）。"""
        entry = self.get_effective_override("gate", key)
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
        entry = self.get_effective_override("gate", "approval_route_matrix")
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
        """读取域业务参数（agent:{domain} 定制的 overrides.params；停用=用默认）。"""
        entry = self.get_effective_override("agent", domain)
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
        - mcp：外部工具白名单扩面 / 首次接入 = 放宽；收缩或撤销暴露 = 收紧；
        - mcp_export：开放 MCP 出口域 = 放宽；关闭 = 收紧；
        - skill：发布新版本 / 停用→启用 = 放宽；启用→停用 = 收紧；
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
        if ctype == "mcp_export":
            # MCP 出口域开关（F4.3）：关闭对外通道=收紧即时；开放通道=放宽须人审。
            # 无定制行时以内置默认清单为准（在列 = 已暴露）。
            from app.core.agent.mcp_export import DEFAULT_EXPORT_DOMAINS

            old_enabled = bool(old.get("enabled")) if old else (
                target in DEFAULT_EXPORT_DOMAINS)
            new_enabled = bool(new.get("enabled", True))
            return "relax" if (new_enabled and not old_enabled) else "tighten"
        if ctype == "mcp":
            # 外部 MCP 工具暴露（F4.2）：扩面=放宽（红线 2 明确点名"新外部 MCP 接入"）
            old_ov = (old or {}).get("overrides") or {}
            old_tools = set(old_ov.get("tools") or [])
            old_enabled = bool((old or {}).get("enabled", False)) if old else False
            new_tools = set(new.get("overrides", {}).get("tools") or [])
            new_enabled = bool(new.get("enabled", True))
            if not new_enabled or not new_tools:
                return "tighten"          # 撤销暴露 = 收紧，即时
            if not old_enabled or not old_tools:
                return "relax"            # 首次接入
            return "relax" if new_tools - old_tools else "tighten"
        if ctype == "skill":
            # 技能指令会注入 LLM 系统提示，影响面等同 DSL：发布一律人审（红线 2）。
            # 启停按方向判定：停用=收紧即时，启用=放宽须批；无变化按收紧（幂等写行）。
            ov = new.get("overrides") or {}
            if ov.get("action") == "publish":
                return "relax"
            old_enabled = bool(old.get("enabled", True)) if old else True
            new_enabled = bool(new.get("enabled", True))
            if old_enabled == new_enabled:
                return "tighten"
            return "tighten" if old_enabled else "relax"
        if ctype == "gate" and target == "approval_route_matrix":
            return "relax"  # 审批路由矩阵变更影响面大，一律人审（红线 2）
        if ctype == "gate" and target in THRESHOLD_DEFAULTS:
            # 两侧都按**生效值**比较：新值藏在 new["overrides"]["value"]，
            # 早先从 new["value"] 取（恒缺省）→ 拿旧值和旧值比 → 阈值上调被判成收紧、
            # 绕过人审直接落库（红线 2 实测漏网）；停用一份比默认更紧的覆盖，
            # 生效值反而是升回默认，方向同样是放宽。
            default = THRESHOLD_DEFAULTS[target]
            old_val = gate_effective_value(old, default)
            new_val = gate_effective_value(new, default)
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
        # mcp 类型 target 必须是已登记的外部 Server（未登记的地址无从校验、也无从桥接）
        elif ctype == "mcp":
            from app.core.agent import mcp_config

            if not await mcp_config.server_exists(target):
                raise CustomizationError(f"未登记的 MCP Server: {target}")
        # skill 类型 target 必须是已存在的技能（草稿/已发布/出厂目录），防错别字配置
        elif ctype == "skill":
            from app.core.agent import skill_versions

            if not await skill_versions.skill_exists(target):
                raise CustomizationError(f"未登记的 Skill: {target}")
        # gate 类型只允许阈值注册表里的键 + 审批路由矩阵（本文件顶部承诺的白名单）。
        # 缺这道校验时白名单外的键能写进生效层：运行期无人读取，管理端却显示"已覆盖"，
        # 管理员会以为自己在改风控线（F5.6 UAT-F5-1 暴露）。
        elif ctype == "gate":
            if target not in GATE_TARGETS:
                raise CustomizationError(
                    f"未知的 gate 配置项: {target}（可配: {sorted(GATE_TARGETS)}）")
            if enabled and target in THRESHOLD_DEFAULTS:
                # 生效值必须是非负数字：get_threshold 读不懂 value 会静默回退内置默认，
                # 结果就是"保存成功、界面显示已改、工作流仍按出厂线跑"
                raw = (overrides or {}).get("value")
                if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
                    raise CustomizationError(
                        f"阈值 {target} 非法：overrides.value 须为非负数字")

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
            # 放宽类：只落版本流（status=pending_approval）+ 审批行。
            # 绝不写 agent_customizations——该表按 (type,target,environment) 唯一，
            # 把待批内容写进去会把当前生效的收紧顶掉，缓存重载后等于"未批先松"。
            await self._supersede_pending(ctype, target)
            await self._finalize(
                ctype, target, old_entry, {"overrides": overrides, "enabled": enabled},
                status="pending_approval", changed_by=changed_by,
                approved_by=None, comment=comment, environment=environment,
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

        # 收紧类：直接生效（同 target 串行，见 _write_lock）
        async with self._write_lock(ctype, target):
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
            # 撤销=回到变更前，若那比现在更松，同样须经人审（红线 2）。
            # 与 apply_change 的放宽分支同一落点：只写提案行 + 审批行。
            thread_id = f"cust:{ctype}:{target}:{rolled_by[:16]}"
            await self._supersede_pending(ctype, target)
            await self._finalize(
                ctype, target, old_entry, {"overrides": overrides, "enabled": enabled},
                status="pending_approval", changed_by=rolled_by, approved_by=None,
                comment=f"撤销至版本 {version_id} 前状态", environment="prod",
            )
            await self._create_approval_record(
                ctype, target, old_entry, {"overrides": overrides, "enabled": enabled},
                rolled_by, f"回滚版本 {version_id}（回滚目标为放宽态）", thread_id,
            )
            return {"status": "pending_approval", "thread_id": thread_id,
                    "reason": "回滚目标为放宽状态，需人工审批"}

        async with self._write_lock(ctype, target):
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
        """
        审批决定后应用/驳回待批提案（decide 路由与 orchestration 恢复都走这里）。

        提案内容取自 customization_versions 的 pending 行（new_summary），
        批准才落 agent_customizations；驳回只改写提案行终态，**当前生效配置不动**。
        """
        from app.db.repositories.base import has_db, session_scope

        if not has_db():
            return {"status": "error", "reply": "存储不可用"}
        async with session_scope() as session:
            from sqlalchemy import select

            from app.db.models import customization_versions

            row = (await session.execute(
                select(customization_versions).where(
                    customization_versions.c.type == ctype,
                    customization_versions.c.target == target,
                    customization_versions.c.status == "pending_approval",
                ).order_by(customization_versions.c.id.desc())
            )).first()
            if row is None:
                return {"status": "error", "reply": "待审批定制不存在"}
            summary = row.new_summary or {}
            proposed = summary.get("overrides") or row.overrides or {}
            enabled = bool(summary.get("enabled", True))

        async with session_scope() as session:
            from sqlalchemy import update

            from app.db.models import customization_versions

            await session.execute(
                update(customization_versions)
                .where(customization_versions.c.id == row.id)
                .values(status="approved_executed" if approved else "rejected",
                        approved_by=decided_by,
                        comment=(comment[:512] if comment else row.comment))
            )

        await self._audit(ctype, target, decided_by, approved, comment)
        # 卡片终态与提案终态同步（提案已被这条决定消耗，卡片不得继续留在 pending）
        await self._close_approval_card(
            ctype, target,
            "executed" if approved else "rejected", decided_by,
            ("定制变更已批准并生效：" if approved else "定制变更已驳回：")
            + f"{ctype}:{target}",
        )

        if not approved:
            if ctype == "workflow":
                from app.core.agent.workflow_versions import on_publish_decision

                await on_publish_decision(target, proposed, False, decided_by)
            elif ctype == "skill":
                from app.core.agent import skill_versions

                await skill_versions.on_apply_decision(target, proposed, False,
                                                        decided_by)
            return {"status": "rejected", "reply": "定制变更已驳回（当前生效配置未改动）"}

        # 批准：提案落生效表 + 版本信号
        # （锁只包这段：下面的 on_publish_decision / MCP 桥接可能回到本服务写同 key，
        #   放进去就是自锁）
        async with self._write_lock(ctype, target):
            await self._upsert_customization(
                ctype, target, proposed, enabled,
                status="active", updated_by=row.changed_by, environment=row.environment,
            )
            self._set_cache(ctype, target, proposed, enabled)
            await self._bump_version()

        # 工作流定义发布：审批通过后落地版本（F3.5，DSL 发布必须经此门）
        if ctype == "workflow":
            from app.core.agent.workflow_versions import on_publish_decision

            published = await on_publish_decision(target, proposed, True, decided_by)
            return {"status": "executed",
                    "reply": f"定制变更已生效：{ctype}:{target}",
                    "publish": published}

        # 外部 MCP 工具暴露：审批通过后把白名单增量桥接进注册表（F4.2）
        if ctype == "mcp":
            from app.core.agent import mcp_config

            synced = await mcp_config.on_apply_decision(
                target, proposed, True, decided_by)
            detail = (f"，已注册 {len(synced['registered'])} 个外部工具"
                      if synced["registered"] else
                      f"，未注册：{synced['error'] or '白名单为空'}")
            return {"status": "executed",
                    "reply": f"定制变更已生效：{ctype}:{target}{detail}",
                    "mcp": synced}

        # Skill 发布：审批通过后落地版本并物化 YAML（F4.4，与 DSL 同款人审门）
        if ctype == "skill":
            from app.core.agent import skill_versions

            decided = await skill_versions.on_apply_decision(
                target, proposed, True, decided_by)
            return {"status": "executed",
                    "reply": decided.get("reply") or f"定制变更已生效：{ctype}:{target}",
                    "skill": decided}

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

    async def _supersede_pending(self, ctype: str, target: str) -> int:
        """同一 (type,target) 只留一条待批提案：旧提案置 superseded。

        审批卡按 thread_id（每 target+发起人一格）定位，而 apply_pending 取的是
        id 最大的一条待批提案；两者不同源时"批的内容"就可能不等于"卡片显示的内容"
        （红线 2：放宽必须让人审到真正要放的那一条）。
        """
        from app.db.repositories.base import has_db, session_scope

        if not has_db():
            return 0
        try:
            async with session_scope() as session:
                from sqlalchemy import update

                from app.db.models import customization_versions

                result = await session.execute(
                    update(customization_versions)
                    .where(customization_versions.c.type == ctype,
                           customization_versions.c.target == target,
                           customization_versions.c.status == "pending_approval")
                    .values(status="superseded")
                )
                return int(result.rowcount or 0)
        except Exception as e:  # noqa: BLE001
            logger.error("customization.supersede_pending_failed",
                         ctype=ctype, target=target, error=str(e))
            return 0

    async def _close_approval_card(self, ctype: str, target: str, status: str,
                                   decided_by: str, reply: str) -> None:
        """决定后把该目标的待批审批卡落到终态。

        apply_pending 是两条审批入口（/approvals/{thread_id}/decide 与
        /customizations/{ctype}/{target}/decide）的公共落点：只在前者回写卡片，
        后者批完后卡片会永远停在 pending（可被反复批准）。
        """
        from app.db.repositories.base import has_db, session_scope

        if not has_db():
            return
        try:
            async with session_scope() as session:
                from sqlalchemy import update

                from app.db.models import agent_approval_requests

                await session.execute(
                    update(agent_approval_requests)
                    .where(agent_approval_requests.c.domain == "customization",
                           agent_approval_requests.c.tool == f"{ctype}:{target}",
                           agent_approval_requests.c.status == "pending")
                    .values(status=status, decided_by=decided_by,
                            decided_at=datetime.now(timezone.utc),
                            result_reply=reply[:4000])
                )
        except Exception as e:  # noqa: BLE001
            logger.error("customization.close_card_failed",
                         ctype=ctype, target=target, error=str(e))

    async def _create_approval_record(self, ctype, target, old_entry, new_entry,
                                      changed_by, comment, thread_id) -> None:
        from app.db.repositories.base import dialect_upsert_stmt, has_db, session_scope

        if not has_db():
            return
        try:
            async with session_scope() as session:
                from app.db.models import agent_approval_requests

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
                    # thread_id 每 (type,target,发起人) 只有一格，复用时必须把上一轮
                    # 的决定字段清空：否则卡片同时显示"已批"和"待批"，审计串号。
                    decided_by=None, decided_at=None, result_reply=None,
                )
                # 方言感知 upsert：MySQL 生产与 sqlite 测试同一条路径
                # （此前手写 mysql_insert，在 sqlite 上整条审批行写入失败，
                #   导致"已转人审"在测试环境里根本没有可批的任务）
                await session.execute(dialect_upsert_stmt(
                    agent_approval_requests, values,
                    conflict_cols=["thread_id"],
                ))
        except Exception as e:  # noqa: BLE001
            logger.error("customization.approval_record_failed", error=str(e))

    async def _bump_version(self) -> None:
        from app.core import redis_store

        version = await redis_store.bump_key_value(CUSTOMIZATION_VERSION_KEY)
        if version is not None:
            self._cache_version = version

    async def bump_version_signal(self) -> None:
        """广播定制层版本失效（供工作流定义等运行时资产变更复用同一信号通道）。"""
        await self._bump_version()

    def seen_idempotency_key(self, key: str) -> bool:
        """防重放（供不走 apply_change 的管理端动作复用，如定制包导入）。"""
        return self._seen_idempotency_key(key) if key else False

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
