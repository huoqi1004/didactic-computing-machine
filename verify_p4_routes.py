"""验证App路由总数"""
import sys
sys.path.insert(0, "e:\\企业数字化大脑\\backend")

OUT = r"e:\企业数字化大脑\app_routes_p4.txt"
lines = []

from app.main import app
openapi = app.openapi()
paths = openapi.get("paths", {})

lines.append(f"App total paths: {len(paths)}")

# 统计各模块路由
modules = {
    "HR": ["/hr/"],
    "Recruitment": ["/recruitment/"],
    "Performance": ["/performance/"],
    "Payroll": ["/payroll/"],
    "CRM": ["/crm/"],
    "Sales": ["/sales/"],
    "Marketing": ["/marketing/"],
    "Customer Service": ["/cs/"],
    "Knowledge": ["/knowledge/"],
    "Auth": ["/auth/"],
    "RBAC": ["/rbac/"],
    "Workflow": ["/workflow/"],
}

for name, prefixes in modules.items():
    count = sum(1 for p in paths if any(px in p for px in prefixes))
    lines.append(f"  {name}: {count} routes")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("DONE")
