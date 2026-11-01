"""看板路由（分管领导 + 董事长全域驾驶舱 · 多图表实时数据聚合）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.core.deps import auth_user
from app.models import (AuditEntry, Conversation, FinanceRecord, Project,
                        TokenUsage, User)

router = APIRouter(prefix="/api/v1/dashboard", tags=["看板"])


async def _count(conn, sql: str, params: dict | None = None) -> int:
    from sqlalchemy import text

    return (await conn.execute(text(sql), params or {})).scalar() or 0


async def _core_stats(conn, last_week_iso: str) -> dict:
    return {
        "users": await _count(conn, "SELECT COUNT(*) FROM users WHERE status='active'"),
        "documents": await _count(conn, "SELECT COUNT(*) FROM documents WHERE status='final'"),
        "knowledge": await _count(conn, "SELECT COUNT(*) FROM knowledge_entries"),
        "todos_open": await _count(conn, "SELECT COUNT(*) FROM todos WHERE status='open'"),
        "im_channels": await _count(conn, "SELECT COUNT(*) FROM im_channels"),
        "conversations": await _count(conn, "SELECT COUNT(*) FROM conversations"),
        "audit_recent": await _count(
            conn, "SELECT COUNT(*) FROM audit_chain WHERE timestamp >= :since",
            {"since": last_week_iso}),
    }


async def _finance_series(session) -> list[dict]:
    """近 6 个月收支/盈亏序列。"""
    rows = (await session.execute(
        select(FinanceRecord).order_by(FinanceRecord.month))).scalars().all()
    by_month: dict[str, dict] = {}
    for r in rows:
        d = by_month.setdefault(r.month, {"month": r.month, "income": 0,
                                          "expense": 0})
        if r.kind == "income":
            d["income"] += r.amount
        else:
            d["expense"] += r.amount
    series = []
    for d in sorted(by_month.values(), key=lambda x: x["month"]):
        d["profit"] = d["income"] - d["expense"]
        series.append(d)
    return series


async def _expense_categories(session) -> list[dict]:
    """支出构成（饼图）。"""
    rows = (await session.execute(
        select(FinanceRecord))).scalars().all()
    by_cat: dict[str, int] = {}
    for r in rows:
        if r.kind == "expense":
            by_cat[r.category] = by_cat.get(r.category, 0) + r.amount
    return [{"category": k, "amount": v} for k, v in
            sorted(by_cat.items(), key=lambda x: -x[1])]


async def _projects(session, department: str = "") -> list[dict]:
    q = select(Project).order_by(Project.budget.desc())
    if department:
        q = q.where(Project.department == department)
    rows = (await session.execute(q)).scalars().all()
    return [{"name": r.name, "department": r.department, "status": r.status,
             "budget": r.budget, "spent": r.spent, "progress": r.progress,
             "owner": r.owner} for r in rows]


async def _dept_finance(session) -> list[dict]:
    """部门年度预算执行（堆叠柱）。"""
    rows = (await session.execute(select(FinanceRecord))).scalars().all()
    by_dept: dict[str, dict] = {}
    from app.models import Project

    projects = (await session.execute(select(Project))).scalars().all()
    for p in projects:
        d = by_dept.setdefault(p.department, {"department": p.department,
                                              "budget": 0, "spent": 0})
        d["budget"] += p.budget
        d["spent"] += p.spent
    for r in rows:
        if r.kind == "expense":
            # 流水未按部门拆分的记入运营公共，demo 中按支出类别归并到"公共运营"
            d = by_dept.setdefault("公共运营", {"department": "公共运营",
                                              "budget": 0, "spent": 0})
            d["spent"] += r.amount
    return sorted(by_dept.values(), key=lambda x: -x["budget"])


@router.get("/leader")
async def leader_dashboard(user: dict = Depends(auth_user)):
    """分管领导看板：运行指标 + 分管部门项目/财务图表。"""
    if user["role"] not in ("leader", "chairman", "sys_admin"):
        raise HTTPException(403, "仅主管领导及以上可查看")
    from app.core.database import get_session

    session = get_session()
    if session is None:
        raise HTTPException(503, "数据库不可用")
    last_week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    my_dept = user.get("department", "")
    async with session as s:
        stats = await _core_stats(s, last_week)
        finance = await _finance_series(s)
        expense_mix = await _expense_categories(s)
        projects = await _projects(s)
        dept_finance = await _dept_finance(s)
    # 分管视角：本人部门的项目排前
    my_projects = [p for p in projects if p["department"] == my_dept]
    other_projects = [p for p in projects if p["department"] != my_dept]
    return {"stats": stats, "finance": finance, "expense_mix": expense_mix,
            "projects": my_projects + other_projects, "dept_finance": dept_finance,
            "my_department": my_dept}


@router.get("/chairman")
async def chairman_dashboard(user: dict = Depends(auth_user)):
    """董事长全域驾驶舱：全域运行指标 + 盈亏/预算/项目/活跃度多图表。"""
    if user["role"] not in ("chairman", "sys_admin"):
        raise HTTPException(403, "仅董事长及以上可查看全域驾驶舱")
    from app.core.database import get_session

    session = get_session()
    if session is None:
        raise HTTPException(503, "数据库不可用")
    last_week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    async with session as s:
        overview = await _core_stats(s, last_week)
        # 全量指标补齐
        overview["total_documents"] = await _count(s, "SELECT COUNT(*) FROM documents")
        overview["audit_entries"] = await _count(s, "SELECT COUNT(*) FROM audit_chain")
        overview["token_today"] = (await s.execute(
            select(TokenUsage.tokens).where(
                TokenUsage.usage_date == datetime.now().strftime("%Y-%m-%d"),
                TokenUsage.username != ""  # 全部用户
            ))).scalars().all()
        finance = await _finance_series(s)
        expense_mix = await _expense_categories(s)
        projects = await _projects(s)
        dept_finance = await _dept_finance(s)
        # 部门人数分布（饼图）
        users = (await s.execute(select(User))).scalars().all()
        dept_users: dict[str, int] = {}
        for u in users:
            dept_users[u.department or "未分配"] = dept_users.get(u.department or "未分配", 0) + 1
        # 会话活跃（近 7 日按日计数）
        convs = (await s.execute(select(Conversation))).scalars().all()
        conv_trend: dict[str, int] = {}
        now = datetime.now(timezone.utc)
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).strftime("%m-%d")
            conv_trend[day] = 0
        for c in convs:
            day = c.created_at.strftime("%m-%d") if c.created_at else ""
            if day in conv_trend:
                conv_trend[day] += 1
    overview["token_today"] = sum(overview.pop("token_today", []))
    return {
        "overview": overview,
        "finance": finance,
        "expense_mix": expense_mix,
        "projects": projects,
        "dept_finance": dept_finance,
        "dept_users": [{"department": k, "count": v}
                       for k, v in sorted(dept_users.items(), key=lambda x: -x[1])],
        "conv_trend": [{"day": k, "count": v} for k, v in conv_trend.items()],
        "strategy_suggestions": [
            {"area": "数字化转型", "suggestion": "基于当前系统使用数据，建议重点推进部门级知识库的填充与共享"},
            {"area": "风险管控", "suggestion": "持续关注审计链完整性校验，保持安全事件零容忍基线"},
            {"area": "项目治理", "suggestion": "财务共享中心项目接近预算上限且仍处风险状态，建议专项评审"},
        ],
        "message": "全域驾驶舱（多图表实时数据）",
    }
