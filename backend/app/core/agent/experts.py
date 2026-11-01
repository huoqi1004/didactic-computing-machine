"""
专家体系内核（PRD expert-team-orchestration v1.0 · FR-E1/E2/E4）
================================================================

**专家 = Agent 之上的角色层**：把岗位方法论（SOP）、技能集、工具白名单、
预算与检查清单**结构化封装**为专家档案——回答"这位专家凭什么可信"。

借鉴映射（评审裁决 D-P1：借鉴 AgentScope 自研薄层，不引框架）：
- AgentScope 的 Agent 抽象 → 本项目的 BaseAgent（执行体）不变；
- 专家档案 = 角色层新增件：**绑定基座 Agent + 方法论 SOP + 技能集 +
  工具白名单子集 + 日预算 + 检查清单**；
- Agent 缺席时专家降级为**建议模式**（仅方法论与检查清单，不执行工具）。

加载模型复用 Skill 体系成熟范式（SPACE-1 前已验证）：
- 出厂目录 ``app/core/agent/experts/``（手写 YAML，只读）；
- 发布目录 ``settings.experts_overlay_dir``（管理端发布物，同名覆盖）；
- 校验：技能引用须在 skill_registry、工具须在 tool_registry_v2、
  绑定 Agent 建议在 orchestrator（缺席降级不阻断）；
- 惰性校验（目录查询时现算，工具注册时序无关——同 Skill 口径）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog
import yaml

from app.core.config import settings

logger = structlog.get_logger()

_FACTORY_DIR = Path(__file__).parent / "experts"


@dataclass
class ExpertProfile:
    """专家档案（角色层新增件，绑定基座 Agent 执行体）"""

    name: str                       # 档案标识（experts/{name}.yaml）
    display_name: str = ""
    domain: str = ""
    role: str = ""                  # 岗位角色描述
    agent: str = ""                 # 绑定基座 Agent 名（缺席→建议模式）
    methodology: str = ""           # 方法论 SOP（渐进式披露第 2 层：命中后加载）
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)   # 工具白名单子集
    checks: list[str] = field(default_factory=list)  # 检查清单（专家自检）
    budget_daily: int = 200_000     # 专家日 token 预算（团级预算的成员分项）
    kpi: dict = field(default_factory=dict)
    invalid_reason: str = ""        # 非空 = 档案有错（技能/工具引用失败等）
    source: str = "factory"         # factory / overlay

    @property
    def valid(self) -> bool:
        return not self.invalid_reason

    def to_discovery(self) -> dict:
        """目录发现视图（渐进式披露第 1 层：元数据常驻）。"""
        return {
            "name": self.name, "display_name": self.display_name,
            "domain": self.domain, "role": self.role,
            "agent": self.agent, "skills": self.skills,
            "valid": self.valid, "source": self.source,
        }

    def to_detail(self) -> dict:
        """详情视图（渐进式披露第 2 层：方法论全文 + 检查清单）。"""
        d = {
            "name": self.name, "display_name": self.display_name,
            "domain": self.domain, "role": self.role,
            "agent": self.agent, "methodology": self.methodology,
            "skills": self.skills, "tools": self.tools,
            "checks": self.checks, "budget_daily": self.budget_daily,
            "kpi": self.kpi, "invalid_reason": self.invalid_reason,
            "source": self.source,
        }
        return d


class ExpertRegistry:
    """专家注册与发现（出厂目录 + 发布目录覆盖，同 Skill 口径）。"""

    def __init__(self) -> None:
        self._experts: dict[str, ExpertProfile] = {}
        self._scanned_mtime: Optional[tuple] = None

    # ------------------------------------------------------------------
    def _dirs(self) -> list[tuple[Path, str]]:
        overlay = Path(getattr(settings, "experts_overlay_dir", "") or "")
        dirs = [(overlay, "overlay")] if overlay.exists() else []
        dirs.append((_FACTORY_DIR, "factory"))
        return dirs

    def _scan(self) -> None:
        """扫描目录（mtime 指纹变化时重扫；发布目录同名覆盖出厂）。"""
        mtimes = tuple(
            (str(d), tuple(sorted((f.name, f.stat().st_mtime_ns) for f in d.glob("*.yaml")) if d.exists() else ()))
            for d, _ in self._dirs()
        )
        if mtimes == self._scanned_mtime:
            return
        self._scanned_mtime = mtimes
        self._experts.clear()
        for d, source in self._dirs():
            if not d.exists():
                continue
            for f in sorted(d.glob("*.yaml")):
                try:
                    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                except Exception as e:  # noqa: BLE001
                    logger.warning("experts.yaml_parse_failed", file=str(f), error=str(e))
                    continue
                name = str(data.get("name") or f.stem)
                if name in self._experts and source == "factory":
                    continue  # overlay 同名覆盖出厂
                self._experts[name] = self._validate(ExpertProfile(
                    name=name,
                    display_name=str(data.get("display_name") or name),
                    domain=str(data.get("domain") or ""),
                    role=str(data.get("role") or ""),
                    agent=str(data.get("agent") or ""),
                    methodology=str(data.get("methodology") or ""),
                    skills=list(data.get("skills") or []),
                    tools=list(data.get("tools") or []),
                    checks=list(data.get("checks") or []),
                    budget_daily=int(data.get("budget_daily") or 200_000),
                    kpi=dict(data.get("kpi") or {}),
                    source=source,
                ))
        logger.debug("experts.scanned", count=len(self._experts))

    @staticmethod
    def _validate(skill_owner: ExpertProfile) -> ExpertProfile:
        """惰性校验：技能/工具引用须已注册；绑定 Agent 缺席仅告警（建议模式）。"""
        from app.core.agent.skills import skill_registry
        from app.core.agent.tools_v2 import tool_registry_v2

        unknown_skills = [s for s in skill_owner.skills if skill_registry.get(s) is None]
        unknown_tools = [t for t in skill_owner.tools if tool_registry_v2.get(t) is None]
        reasons = []
        if unknown_skills:
            reasons.append(f"引用未注册技能: {', '.join(unknown_skills)}")
        if unknown_tools:
            reasons.append(f"引用未注册工具: {', '.join(unknown_tools)}")
        skill_owner.invalid_reason = "；".join(reasons)
        return skill_owner

    # ------------------------------------------------------------------
    # 公开接口（FR-E2 注册与发现）
    # ------------------------------------------------------------------

    def list_experts(self, domain: Optional[str] = None) -> list[dict]:
        """专家目录发现（FR-E2）。"""
        self._scan()
        items = [e.to_discovery() for e in self._experts.values()
                 if not domain or e.domain == domain]
        return sorted(items, key=lambda x: x["name"])

    def get(self, name: str) -> Optional[ExpertProfile]:
        self._scan()
        return self._experts.get(name)

    # ------------------------------------------------------------------
    # FR-E4：专家 KPI（从操作日志聚合；M1 口径 = Python 聚合，规模化进 P2-7）
    # ------------------------------------------------------------------

    async def kpi(self, expert: ExpertProfile, days: int = 30) -> dict:
        """专家 KPI：近 N 天调用次数/token 消耗/降级次数（绑定域口径）。"""
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import text

        from app.core.database import get_engine

        engine = get_engine()
        if engine is None:
            return {"calls": 0, "tokens": 0, "degraded": 0, "available": False}
        since = datetime.now(timezone.utc) - timedelta(days=days)
        target_domain = expert.domain or expert.agent
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    "SELECT details FROM operation_logs "
                    "WHERE action = 'llm.interaction' AND ts >= :since "
                    "ORDER BY ts DESC LIMIT 5000"
                ), {"since": since})).mappings().all()
            calls = tokens = degraded = 0
            for r in rows:
                raw = r["details"]
                if isinstance(raw, (str, bytes)):
                    import json as _json

                    try:
                        details = _json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                else:
                    details = dict(raw or {})
                if str(details.get("domain") or "") != target_domain:
                    continue
                calls += 1
                tokens += int(details.get("tokens") or 0)
                if details.get("degraded"):
                    degraded += 1
            return {"calls": calls, "tokens": tokens, "degraded": degraded,
                    "window_days": days, "available": True}
        except Exception as e:  # noqa: BLE001
            logger.warning("experts.kpi_failed", expert=expert.name, error=str(e))
            return {"calls": 0, "tokens": 0, "degraded": 0, "available": False,
                    "error": str(e)[:200]}


# 进程内单例
expert_registry = ExpertRegistry()
