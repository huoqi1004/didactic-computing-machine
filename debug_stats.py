"""检查Agent stats和HR leave"""
import sys, asyncio
sys.path.insert(0, "e:\\企业数字化大脑\\backend")

OUT = r"e:\企业数字化大脑\debug_stats.txt"
lines = []

from app.agents.hr.agent import HRAgent
from app.agents.payroll.agent import PayrollAgent
from app.agents.crm.agent import CRMAgent
from app.agents.sales.agent import SalesAgent
from app.agents.marketing.agent import MarketingAgent
from app.agents.customer_service.agent import CustomerServiceAgent

hr = HRAgent()
pay = PayrollAgent()
crm = CRMAgent()
sales = SalesAgent()
mkt = MarketingAgent()
cs = CustomerServiceAgent()

# HR leave test
try:
    leave = hr.request_leave(employee_id="EMP0001", leave_type="年假", start_date="2026-08-01", end_date="2026-08-03", reason="家庭事务")
    lines.append(f"HR leave result: {leave}")
except Exception as e:
    lines.append(f"HR leave error: {e}")

# Stats
lines.append(f"\nPayroll stats: {pay.get_stats()}")
lines.append(f"\nCRM stats: {crm.get_stats()}")
lines.append(f"\nSales stats: {sales.get_stats()}")
lines.append(f"\nMarketing stats: {mkt.get_stats()}")
lines.append(f"\nCS stats: {cs.get_stats()}")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("DONE")
