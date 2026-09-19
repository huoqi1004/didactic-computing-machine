"""
Skill 规范与加载器（路线图 Phase D / D4）
==========================================

Skill = 可复用的"能力组合"封装：**指令模板 + 工具白名单 + 护栏档案**。
让常见业务场景（报销办理/资金日报/合同风险预审等）以声明式文件定义，
热加载、可版本化（目录式 YAML）。

文件规范（app/core/agent/skills/<name>.yaml）::

    name: reimbursement_handle        # 唯一标识（与文件名一致）
    display_name: 报销办理
    description: 查询报销单并协助提交报销申请
    domain: reimbursement             # 归属业务域（决定场景图）
    tools:                            # 工具白名单（必须已在治理面注册）
      - reimbursement_query_list
      - reimbursement_submit
    instructions: |                   # 注入场景图系统提示的技能指令
      你是报销办理专员。…
    guardrails:                       # 可选护栏参数（透传治理面）
      max_amount: 10000

- 热加载：目录 mtime 变化时 list()/get() 自动重扫；
- 工具白名单校验：引用未注册工具的 skill 标记 invalid（不阻断加载）；
- 编排侧：``AgentRuntime`` 场景图可按 skill 过滤工具子集并注入
  instructions（ctx.extra["skill"]）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog
import yaml

logger = structlog.get_logger()

SKILLS_DIR = Path(__file__).parent / "skills"


@dataclass
class Skill:
    """Skill 定义（声明式 YAML）。"""

    name: str
    display_name: str
    description: str
    domain: str
    tools: list[str] = field(default_factory=list)
    instructions: str = ""
    guardrails: dict = field(default_factory=dict)
    invalid_reason: str = ""          # 非空 = 定义有错（如引用未注册工具）

    @property
    def valid(self) -> bool:
        return not self.invalid_reason


class SkillRegistry:
    """Skill 注册表（目录热加载）。"""

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self.skills_dir = Path(skills_dir)
        self._dir_mtime: str = ""
        self._skills: dict[str, Skill] = {}

    def _scan(self) -> None:
        """扫描目录（mtime 变化才重读，热加载）。"""
        try:
            files = sorted(self.skills_dir.glob("*.yaml"))
            # 指纹含文件名与纳秒 mtime：同秒内增删文件也能触发热加载
            fingerprint = "|".join(
                f"{p.name}:{p.stat().st_mtime_ns}" for p in files
            )
        except FileNotFoundError:
            fingerprint = ""
        if fingerprint == self._dir_mtime and self._skills:
            return
        self._dir_mtime = fingerprint

        skills: dict[str, Skill] = {}
        for path in sorted(self.skills_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                name = data.get("name") or path.stem
                skill = Skill(
                    name=name,
                    display_name=data.get("display_name", name),
                    description=data.get("description", ""),
                    domain=data.get("domain", ""),
                    tools=list(data.get("tools", [])),
                    instructions=data.get("instructions", ""),
                    guardrails=dict(data.get("guardrails", {})),
                )
                skills[name] = skill
            except Exception as e:  # noqa: BLE001
                logger.warning("skills.load_failed", file=path.name, error=str(e))
        self._skills = skills
        logger.info("skills.loaded", count=len(skills),
                    invalid=sum(1 for s in skills.values() if not s.valid))

    def list_skills(self, domain: Optional[str] = None) -> list[Skill]:
        self._scan()
        skills = list(self._skills.values())
        if domain:
            skills = [s for s in skills if s.domain == domain]
        return sorted(skills, key=lambda s: s.name)

    def get(self, name: str) -> Optional[Skill]:
        self._scan()
        return self._skills.get(name)

    @staticmethod
    def _validate(skill: Skill) -> Skill:
        """惰性白名单校验（工具注册时序无关，目录查询时现算）。"""
        from app.core.agent.tools_v2 import tool_registry_v2

        unknown = [t for t in skill.tools if tool_registry_v2.get(t) is None]
        skill.invalid_reason = (
            f"引用未注册工具: {', '.join(unknown)}" if unknown else ""
        )
        return skill

    def to_catalog(self, domain: Optional[str] = None) -> list[dict]:
        result = []
        for s in self.list_skills(domain):
            self._validate(s)
            result.append({
                "name": s.name,
                "display_name": s.display_name,
                "description": s.description,
                "domain": s.domain,
                "tools": s.tools,
                "valid": s.valid,
                "invalid_reason": s.invalid_reason,
            })
        return result


# 进程内单例
skill_registry = SkillRegistry()
