"""Phase 4 验证脚本"""
import sys, asyncio, traceback
sys.path.insert(0, "e:\\企业数字化大脑\\backend")

OUT = r"e:\企业数字化大脑\p4_result.md"
results = []

def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, name, detail))

async def run_tests():
    output = ["# Phase 4 测试结果\n"]

    # 1. Agent 导入和实例化
    output.append("## 1. Agent 导入与实例化\n")
    try:
        from app.agents.hr.agent import HRAgent
        from app.agents.recruitment.agent import RecruitmentAgent
        from app.agents.performance.agent import PerformanceAgent
        from app.agents.payroll.agent import PayrollAgent
        from app.agents.crm.agent import CRMAgent
        from app.agents.sales.agent import SalesAgent
        from app.agents.marketing.agent import MarketingAgent
        from app.agents.customer_service.agent import CustomerServiceAgent
        check("8个Agent导入成功", True)
    except Exception as e:
        check("8个Agent导入成功", False, str(e))
        output.append(f"```\n{traceback.format_exc()}\n```\n")
        write_output(output)
        return False

    # 实例化
    agents = {}
    try:
        agents["hr"] = HRAgent()
        agents["recruitment"] = RecruitmentAgent()
        agents["performance"] = PerformanceAgent()
        agents["payroll"] = PayrollAgent()
        agents["crm"] = CRMAgent()
        agents["sales"] = SalesAgent()
        agents["marketing"] = MarketingAgent()
        agents["cs"] = CustomerServiceAgent()
        check("8个Agent实例化成功", True)
    except Exception as e:
        check("8个Agent实例化成功", False, str(e))
        output.append(f"```\n{traceback.format_exc()}\n```\n")
        write_output(output)
        return False

    # 检查名称和域
    check("HRAgent名称", agents["hr"].name == "人力资源Agent", agents["hr"].name)
    check("RecruitmentAgent名称", agents["recruitment"].name == "招聘Agent", agents["recruitment"].name)
    check("PerformanceAgent名称", agents["performance"].name == "绩效Agent", agents["performance"].name)
    check("PayrollAgent名称", agents["payroll"].name == "薪酬Agent", agents["payroll"].name)
    check("CRMAgent名称", agents["crm"].name == "客户关系Agent", agents["crm"].name)
    check("SalesAgent名称", agents["sales"].name == "销售Agent", agents["sales"].name)
    check("MarketingAgent名称", agents["marketing"].name == "市场Agent", agents["marketing"].name)
    check("CustomerServiceAgent名称", agents["cs"].name == "客服Agent", agents["cs"].name)
    output.append(f"  导入与实例化: {sum(1 for s,_,_ in results if s=='PASS')} 通过\n")

    # 2. HR Agent 功能测试
    output.append("## 2. HR Agent 功能测试\n")
    hr = agents["hr"]
    emps = hr.get_employees()
    check("HR获取员工列表", len(emps) > 0, f"count={len(emps)}")
    check("HR有组织架构", len(hr.get_org_chart()) > 0)
    check("HR统计有数据", "total_employees" in hr.get_stats())
    # 添加员工
    new_emp = hr.add_employee(name="测试员工", department="技术部", position="测试工程师", entry_date="2026-07-28")
    check("HR添加员工成功", new_emp.get("employee_id") is not None)
    # 考勤
    att = hr.record_attendance(employee_id=new_emp["employee_id"], date="2026-07-28", check_in="09:00", check_out="18:00", status="normal")
    check("HR记录考勤成功", att.get("employee_id") == new_emp["employee_id"])
    # 请假
    leave = hr.request_leave(employee_id=new_emp["employee_id"], leave_type="年假", start_date="2026-08-01", end_date="2026-08-03", reason="家庭事务")
    check("HR请假申请成功", leave.get("leave_id") is not None)
    output.append(f"  HR Agent: {sum(1 for s,_,_ in results[-7:] if s=='PASS')} 通过\n")

    # 3. Recruitment Agent 功能测试
    output.append("## 3. Recruitment Agent 功能测试\n")
    rec = agents["recruitment"]
    positions = rec.get_positions()
    check("Recruitment获取职位列表", len(positions) > 0, f"count={len(positions)}")
    # 添加候选人
    cand = rec.add_candidate(name="张三", position_id=positions[0]["position_id"], source="官网", resume_summary="5年Java经验")
    check("Recruitment添加候选人成功", cand.get("candidate_id") is not None)
    # 安排面试
    interview = rec.schedule_interview(candidate_id=cand["candidate_id"], interview_date="2026-08-01", interviewer="技术总监", round_num=1)
    check("Recruitment安排面试成功", interview.get("interview_id") is not None)
    check("Recruitment统计有数据", "total_positions" in rec.get_stats())
    output.append(f"  Recruitment Agent: {sum(1 for s,_,_ in results[-4:] if s=='PASS')} 通过\n")

    # 4. Performance Agent 功能测试
    output.append("## 4. Performance Agent 功能测试\n")
    perf = agents["performance"]
    kpis = perf.get_kpi_templates()
    check("Performance获取KPI模板", len(kpis) > 0, f"count={len(kpis)}")
    # 创建绩效周期
    cycle = perf.create_review_cycle(name="2026Q3绩效", period="2026-Q3", start_date="2026-07-01", end_date="2026-09-30")
    check("Performance创建绩效周期成功", cycle.get("cycle_id") is not None)
    # 设定目标
    goal = perf.set_goal(employee_id="EMP0001", goal_type="personal", description="完成3个大项目", target_date="2026-09-30")
    check("Performance设定目标成功", goal.get("goal_id") is not None)
    check("Performance统计有数据", "total_kpi_templates" in perf.get_stats())
    output.append(f"  Performance Agent: {sum(1 for s,_,_ in results[-4:] if s=='PASS')} 通过\n")

    # 5. Payroll Agent 功能测试
    output.append("## 5. Payroll Agent 功能测试\n")
    pay = agents["payroll"]
    # 获取社保配置
    si = pay.get_social_insurance_config()
    check("Payroll获取社保配置", "pension" in si or "rates" in si, str(si)[:100])
    # 计算个税
    tax = pay.calculate_individual_tax(20000)
    check("Payroll个税计算", tax > 0, f"tax={tax}")
    # 获取薪资结构
    struct = pay.get_salary_structure("EMP0001")
    check("Payroll获取薪资结构", struct is not None)
    # 计算薪酬
    payroll = pay.calculate_payroll("EMP0001", "2026-07")
    check("Payroll计算薪酬成功", payroll is not None and "net_salary" in payroll, str(payroll)[:100] if payroll else "None")
    check("Payroll统计有数据", "total_records" in pay.get_stats() or "total_employees" in pay.get_stats())
    output.append(f"  Payroll Agent: {sum(1 for s,_,_ in results[-5:] if s=='PASS')} 通过\n")

    # 6. CRM Agent 功能测试
    output.append("## 6. CRM Agent 功能测试\n")
    crm = agents["crm"]
    customers = crm.get_customers()
    check("CRM获取客户列表", len(customers) > 0, f"count={len(customers)}")
    # 创建客户
    cust = crm.create_customer(name="测试客户公司", industry="科技", segment="B", contact_name="李四", contact_phone="13800000000")
    check("CRM创建客户成功", cust.get("customer_id") is not None)
    # 创建商机
    opp = crm.create_opportunity(customer_id=cust["customer_id"], name="测试商机", amount=500000, stage="prospecting", expected_close_date="2026-09-30")
    check("CRM创建商机成功", opp.get("opportunity_id") is not None)
    check("CRM统计有数据", "total_customers" in crm.get_stats())
    output.append(f"  CRM Agent: {sum(1 for s,_,_ in results[-4:] if s=='PASS')} 通过\n")

    # 7. Sales Agent 功能测试
    output.append("## 7. Sales Agent 功能测试\n")
    sales = agents["sales"]
    orders = sales.get_orders()
    check("Sales获取订单列表", len(orders) > 0, f"count={len(orders)}")
    # 创建订单
    order = await sales.create_order(customer_id="CUST001", items=[{"product": "产品A", "qty": 10, "price": 1000}], total_amount=10000, delivery_date="2026-08-15")
    check("Sales创建订单成功", order.get("order_id") is not None)
    # 获取目标
    targets = sales.get_targets()
    check("Sales获取目标列表", len(targets) > 0, f"count={len(targets)}")
    check("Sales统计有数据", "total_orders" in sales.get_stats())
    output.append(f"  Sales Agent: {sum(1 for s,_,_ in results[-4:] if s=='PASS')} 通过\n")

    # 8. Marketing Agent 功能测试
    output.append("## 8. Marketing Agent 功能测试\n")
    mkt = agents["marketing"]
    campaigns = mkt.get_campaigns()
    check("Marketing获取活动列表", len(campaigns) > 0, f"count={len(campaigns)}")
    # 创建活动
    camp = mkt.create_campaign(name="夏季促销", budget=50000, channels=["微信", "微博"], start_date="2026-07-01", end_date="2026-07-31", target_audience="企业客户")
    check("Marketing创建活动成功", camp.get("campaign_id") is not None)
    # 添加线索
    lead = mkt.add_lead(name="王五", source="官网", contact_info="wangwu@test.com", interest="产品A", campaign_id=camp.get("campaign_id"))
    check("Marketing添加线索成功", lead.get("lead_id") is not None)
    check("Marketing统计有数据", "total_campaigns" in mkt.get_stats())
    output.append(f"  Marketing Agent: {sum(1 for s,_,_ in results[-4:] if s=='PASS')} 通过\n")

    # 9. Customer Service Agent 功能测试
    output.append("## 9. Customer Service Agent 功能测试\n")
    cs = agents["cs"]
    tickets = cs.get_tickets()
    check("CS获取工单列表", len(tickets) > 0, f"count={len(tickets)}")
    # 创建工单
    ticket = cs.create_ticket(customer_id="CUST001", subject="产品咨询", description="想了解产品A的详细参数", priority="normal", category="consultation")
    check("CS创建工单成功", ticket.get("ticket_id") is not None)
    # 搜索FAQ
    faq_results = cs.search_faq("账户")
    check("CS搜索FAQ", isinstance(faq_results, list))
    check("CS统计有数据", "total_tickets" in cs.get_stats())
    output.append(f"  Customer Service Agent: {sum(1 for s,_,_ in results[-4:] if s=='PASS')} 通过\n")

    # 10. API 路由检查
    output.append("## 10. API 路由检查\n")
    try:
        from app.api.routes.hr import router as hr_router
        from app.api.routes.sales_marketing import router as sm_router
        hr_routes = [r for r in hr_router.routes if hasattr(r, "path")]
        sm_routes = [r for r in sm_router.routes if hasattr(r, "path")]
        check("HR路由数量>30", len(hr_routes) > 30, f"routes={len(hr_routes)}")
        check("销售营销路由数量>35", len(sm_routes) > 35, f"routes={len(sm_routes)}")
    except Exception as e:
        check("API路由导入", False, str(e))
        output.append(f"```\n{traceback.format_exc()}\n```\n")
    output.append(f"  API路由: {sum(1 for s,_,_ in results[-2:] if s=='PASS')} 通过\n")

    # 11. App 路由验证
    output.append("## 11. App 路由验证\n")
    try:
        from app.main import app
        openapi = app.openapi()
        paths = openapi.get("paths", {})
        hr_paths = [p for p in paths if any(x in p for x in ["/hr/", "/recruitment/", "/performance/", "/payroll/"])]
        sm_paths = [p for p in paths if any(x in p for x in ["/crm/", "/sales/", "/marketing/", "/cs/"])]
        check("App包含HR路由", len(hr_paths) > 20, f"count={len(hr_paths)}")
        check("App包含销售营销路由", len(sm_paths) > 25, f"count={len(sm_paths)}")
        total = len(paths)
        check("App总路由>180", total > 180, f"total={total}")
    except Exception as e:
        check("App路由验证", False, str(e))
        output.append(f"```\n{traceback.format_exc()}\n```\n")
    output.append(f"  App路由: {sum(1 for s,_,_ in results[-3:] if s=='PASS')} 通过\n")

    # 汇总
    total = len(results)
    passed = sum(1 for s, _, _ in results if s == "PASS")
    failed = sum(1 for s, _, _ in results if s == "FAIL")
    output.append("## 测试汇总\n")
    output.append(f"| 状态 | 数量 |")
    output.append(f"|------|------|")
    output.append(f"| PASS | {passed} |")
    output.append(f"| FAIL | {failed} |")
    output.append(f"| 总计 | {total} |")
    if failed > 0:
        output.append("\n### 失败项\n")
        for s, n, d in results:
            if s == "FAIL":
                output.append(f"- {n}" + (f" — {d}" if d else ""))

    write_output(output)
    return failed == 0

def write_output(output):
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(output))

if __name__ == "__main__":
    success = asyncio.run(run_tests())
    print(f"DONE: success={success}")
    sys.exit(0 if success else 1)
