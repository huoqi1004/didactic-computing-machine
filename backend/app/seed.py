"""五层角色种子数据（Fresh State 测试与生产初始化共用）。"""
from __future__ import annotations

from app.core.security import hash_password

SEED_USERS = [
    {"username": "sysadmin", "password": "SysAdmin@2026", "role": "sys_admin",
     "display_name": "系统管理员", "department": "IT部"},
    {"username": "chairman", "password": "Chairman@2026", "role": "chairman",
     "display_name": "董事长", "department": "董事会"},
    {"username": "ceo", "password": "CEO@2026", "role": "leader",
     "display_name": "首席执行官", "department": "CEO办公室"},
    {"username": "cfo", "password": "CFO@2026", "role": "leader",
     "display_name": "首席财务官", "department": "财务部"},
    {"username": "cio", "password": "CIO@2026", "role": "leader",
     "display_name": "首席信息官", "department": "IT部"},
    {"username": "dept_admin_finance", "password": "DeptFin@2026",
     "role": "dept_admin", "display_name": "财务部管理员", "department": "财务部"},
    {"username": "employee_finance", "password": "EmpFin@2026",
     "role": "employee", "display_name": "财务专员", "department": "财务部"},
    {"username": "employee_it", "password": "EmpIT@2026",
     "role": "employee", "display_name": "IT工程师", "department": "IT部"},
]

IM_SEEDS = [
    {"type": "company", "name": "公司全员群", "department": "", "member_ids": []},
    {"type": "department", "name": "财务部群", "department": "财务部"},
    {"type": "department", "name": "IT部群", "department": "IT部"},
]


async def seed(session) -> dict:
    """写入种子数据（分段幂等：每类数据独立判重，老库可增量补种）。"""
    import random
    from sqlalchemy import select

    from app.models import (FinanceRecord, IMChannel, Project, TokenQuota,
                            User)
    result: dict = {}

    # --- 用户 ---
    existing = (await session.execute(select(User.username))).scalars().all()
    if not existing:
        for u in SEED_USERS:
            session.add(User(username=u["username"],
                             password_hash=hash_password(u["password"]),
                             display_name=u["display_name"], role=u["role"],
                             department=u["department"], status="active"))
        for ch in IM_SEEDS:
            session.add(IMChannel(type=ch["type"], name=ch["name"],
                                  department=ch.get("department", "")))
        result["users"] = len(SEED_USERS)

    # --- 看板 demo 数据：近 6 个月收支 + 项目台账 ---
    if not (await session.execute(select(FinanceRecord.id).limit(1))).scalar_one_or_none():
        random.seed(42)
        months = ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
        for i, m in enumerate(months):
            base = 800 + i * 60
            session.add(FinanceRecord(month=m, kind="income", category="主营业务",
                                      amount=base + random.randint(-40, 80)))
            session.add(FinanceRecord(month=m, kind="income", category="其他收入",
                                      amount=random.randint(40, 120)))
            session.add(FinanceRecord(month=m, kind="expense", category="人力成本",
                                      amount=int(base * 0.55) + random.randint(-30, 30)))
            session.add(FinanceRecord(month=m, kind="expense", category="运营支出",
                                      amount=int(base * 0.3) + random.randint(-20, 25)))
            session.add(FinanceRecord(month=m, kind="expense", category="研发投入",
                                      amount=int(base * 0.2) + random.randint(-15, 35)))
        result["finance_months"] = len(months)

    if not (await session.execute(select(Project.id).limit(1))).scalar_one_or_none():
        PROJECT_SEEDS = [
            ("ERP 全面重构", "IT部", "running", 500, 320, 64, "cio"),
            ("数字化营销中台", "CEO办公室", "running", 380, 210, 55, "ceo"),
            ("财务共享中心", "财务部", "risk", 260, 235, 82, "cfo"),
            ("智能审计链平台", "IT部", "running", 180, 60, 33, "cio"),
            ("集团数据驾驶舱", "董事会", "planning", 150, 8, 5, "chairman"),
            ("供应链协同系统", "CEO办公室", "done", 220, 205, 100, "ceo"),
            ("人事自助服务", "财务部", "paused", 90, 42, 40, "cfo"),
        ]
        for name, dept, status, budget, spent, progress, owner in PROJECT_SEEDS:
            session.add(Project(name=name, department=dept, status=status,
                                budget=budget, spent=spent, progress=progress,
                                owner=owner))
        result["projects"] = len(PROJECT_SEEDS)

    # --- 默认 token 额度 ---
    have_quota = set((await session.execute(
        select(TokenQuota.username))).scalars().all())
    for u in SEED_USERS:
        if u["username"] not in have_quota:
            session.add(TokenQuota(
                username=u["username"],
                daily_limit=200000 if u["role"] in ("sys_admin", "chairman")
                else 100000))

    # --- 默认模型接入（来自 .env 的 LLM 配置） ---
    from app.core.config import settings
    from app.models import ModelProvider

    have_model = (await session.execute(
        select(ModelProvider.id).limit(1))).scalar_one_or_none()
    if have_model is None and settings.llm_api_key:
        session.add(ModelProvider(
            name="Agnes-3.0-Flash（系统默认）", protocol="openai",
            base_url=settings.llm_base_url, api_key=settings.llm_api_key,
            model_name=settings.llm_model, enabled=True, is_default=True))
        result["models"] = 1

    await session.flush()
    result["seeded"] = True
    return result
