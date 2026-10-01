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

Phase F4.4 的两个补充真相源（避免"管理端改了、运行时没变"）：

- **发布目录**：``settings.skills_overlay_dir``（默认 ``data/skills_overlay``）是管理端
  发布/回滚物化出的 YAML 目录，与出厂目录同名时**以发布目录为准**；出厂目录
  ``app/core/agent/skills/`` 保持只读，源码里的手写 YAML 是出厂默认值；
- **启停**：``agent_customizations`` 里 ``skill:{name}`` 行的 ``enabled`` 即开关
  （停用=收紧即时生效；启用=放宽须人审）。运行时 ``get()`` 只返回启用技能，
  管理端目录 ``to_catalog()`` 仍列出停用项并标注 ``enabled=false``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog
import yaml

logger = structlog.get_logger()

SKILLS_DIR = Path(__file__).parent / "skills"
# 发布目录（管理端物化产物）。相对路径锚定 backend/，与进程 cwd 无关
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def overlay_dir() -> Path:
    """管理端发布目录（settings.skills_overlay_dir，相对路径按 backend/ 解析）。"""
    from app.core.config import settings

    raw = settings.skills_overlay_dir or ""
    p = Path(raw)
    return p if p.is_absolute() else _BACKEND_ROOT / p


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
    version: str = ""                 # 发布版本号（出厂 YAML 未标注则为空）
    published: bool = False           # 来自发布目录 = 经人审发布的版本

    @property
    def valid(self) -> bool:
        return not self.invalid_reason

    @property
    def enabled(self) -> bool:
        """启停开关（定制层 skill:{name}；无定制行 = 启用）。"""
        return _skill_enabled(self.name)


def _skill_enabled(name: str) -> bool:
    from app.core.agent.customization import customization_service

    entry = customization_service.get_override("skill", name)
    if entry is None:
        return True
    return bool(entry.get("enabled", True))


class SkillRegistry:
    """Skill 注册表（出厂目录 + 发布目录，目录热加载）。"""

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self.skills_dir = Path(skills_dir)
        self._dir_mtime: str = ""
        self._skills: dict[str, Skill] = {}

    def _dirs(self) -> list[Path]:
        """加载顺序：出厂目录 → 发布目录（后者同名覆盖前者）。"""
        return [self.skills_dir, overlay_dir()]

    def _scan(self) -> None:
        """扫描目录（mtime 变化才重读，热加载）。"""
        dirs, files = self._dirs(), []
        for d in dirs:
            try:
                files.extend(sorted(d.glob("*.yaml")))
            except OSError:
                continue
        # 指纹含目录路径、文件名与纳秒 mtime：换目录或同秒内增删文件都能触发热加载
        fingerprint = "|".join([f"@{d}" for d in dirs] +
                               [f"{p.parent.name}/{p.name}:{p.stat().st_mtime_ns}"
                                for p in files])
        if fingerprint == self._dir_mtime and self._skills:
            return
        self._dir_mtime = fingerprint

        skills: dict[str, Skill] = {}
        for path in files:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                name = data.get("name") or path.stem
                skills[name] = Skill(
                    name=name,
                    display_name=data.get("display_name", name),
                    description=data.get("description", ""),
                    domain=data.get("domain", ""),
                    tools=list(data.get("tools") or []),
                    instructions=data.get("instructions") or "",
                    guardrails=dict(data.get("guardrails") or {}),
                    version=str(data.get("version") or ""),
                    published=path.parent == overlay_dir(),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("skills.load_failed", file=path.name, error=str(e))
        self._skills = skills
        logger.info("skills.loaded", count=len(skills),
                    invalid=sum(1 for s in skills.values() if not s.valid))

    def list_skills(self, domain: Optional[str] = None,
                    include_disabled: bool = True) -> list[Skill]:
        """目录视图（管理端用）：默认含停用项，运行时请用 :meth:`get`。"""
        self._scan()
        skills = [s for s in self._skills.values()
                  if include_disabled or s.enabled]
        if domain:
            skills = [s for s in skills if s.domain == domain]
        return sorted(skills, key=lambda s: s.name)

    def get(self, name: str) -> Optional[Skill]:
        """取启用中的技能（停用项返回 None —— 运行时的启停即在此生效）。"""
        self._scan()
        skill = self._skills.get(name)
        if skill is None or not skill.enabled:
            return None
        return skill

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
                "enabled": s.enabled,
                "version": s.version,
                "published": s.published,
            })
        return result


# 进程内单例
skill_registry = SkillRegistry()
