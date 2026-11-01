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
    """写入五层角色种子（幂等）。"""
    from sqlalchemy import select

    from app.models import IMChannel, User

    existing = (await session.execute(select(User.username))).scalars().all()
    if existing:
        return {"seeded": False, "reason": "已有用户"}
    for u in SEED_USERS:
        session.add(User(username=u["username"],
                         password_hash=hash_password(u["password"]),
                         display_name=u["display_name"], role=u["role"],
                         department=u["department"], status="active"))
    for ch in IM_SEEDS:
        session.add(IMChannel(type=ch["type"], name=ch["name"],
                              department=ch.get("department", "")))
    await session.flush()
    return {"seeded": True, "users": len(SEED_USERS), "channels": len(IM_SEEDS)}
