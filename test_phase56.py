"""Phase 5+6 功能测试"""
import urllib.request
import json

BASE = "http://localhost:8001"

# 1. Login as CIO (admin user)
login_data = json.dumps({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(
    f"{BASE}/api/v1/auth/login",
    data=login_data,
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req)
token = json.loads(resp.read())["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
print("Login: OK (admin/CIO)")

passed = 0
failed = 0

def test(name, url, method="GET", body=None):
    global passed, failed
    try:
        data = None
        if body:
            data = json.dumps(body).encode()
        req = urllib.request.Request(f"{BASE}{url}", data=data, headers=headers, method=method)
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read())
        passed += 1
        return result
    except Exception as e:
        failed += 1
        print(f"  FAIL: {name} - {e}")
        return None

# === CIO Admin System Tests ===
print("\n--- CIO Admin System ---")

r = test("Monitor metrics", "/api/v1/admin/monitor/metrics")
if r:
    print(f"  Monitor: status={r.get('status')}, cpu={r.get('system',{}).get('cpu_percent')}%")

r = test("Security score", "/api/v1/admin/security/score")
if r:
    print(f"  Security: score={r.get('score')}/100, grade={r.get('grade')}")

r = test("Lifecycle agents", "/api/v1/admin/lifecycle/agents")
if r is not None:
    print(f"  Lifecycle: {len(r)} agents")

r = test("Backup list", "/api/v1/admin/backup")
if r is not None:
    print(f"  Backups: {len(r)} records")

r = test("Integration health", "/api/v1/admin/integration/health")
if r:
    print(f"  Integration: status={r.get('status')}")

r = test("Governance compliance", "/api/v1/admin/governance/compliance")
if r:
    print(f"  Governance: {r.get('overall_status')}")

r = test("Config categories", "/api/v1/admin/config/categories")
if r:
    print(f"  Config: {len(r.get('categories',[]))} categories")

r = test("Monitor services", "/api/v1/admin/monitor/services")
if r:
    print(f"  Services: {len(r.get('components',{}))} components")

r = test("Encryption status", "/api/v1/admin/security/encryption")
if r:
    print(f"  Encryption: {r.get('overall_status')}")

r = test("DR Plan", "/api/v1/admin/backup/dr-plan")
if r:
    print(f"  DR Plan: rpo={r.get('rpo_minutes')}min, rto={r.get('rto_minutes')}min")

# === Operations Strategy Tests ===
print("\n--- Operations Strategy ---")

r = test("Risk stats", "/api/v1/risk/stats")
if r:
    print(f"  Risk: {r.get('total_risks')} risks, {r.get('alerts')} alerts")

r = test("Risk matrix", "/api/v1/risk/matrix")
if r:
    crit = r.get('critical', {}).get('count', 0)
    high = r.get('high', {}).get('count', 0)
    print(f"  Risk matrix: critical={crit}, high={high}")

r = test("BI stats", "/api/v1/bi/stats")
if r:
    print(f"  BI: {r.get('reports')} reports, {r.get('dashboards')} dashboards, {r.get('kpis')} KPIs")

r = test("ESG score", "/api/v1/esg/score")
if r:
    print(f"  ESG: score={r.get('overall_score')}, rating={r.get('rating')}")

r = test("Budget stats", "/api/v1/budget/stats")
if r:
    print(f"  Budget: {r.get('total_budgets')} budgets")

r = test("Project stats", "/api/v1/projects/stats")
if r:
    print(f"  Project: {r.get('total_projects')} projects")

r = test("Asset stats", "/api/v1/assets/stats")
if r:
    print(f"  Asset: {r.get('total_assets')} assets")

r = test("Quality stats", "/api/v1/quality/stats")
if r:
    print(f"  Quality: {r.get('total_inspections')} inspections")

r = test("Contract stats", "/api/v1/contracts/stats")
if r:
    print(f"  Contract: {r.get('total_contracts')} contracts")

r = test("Legal stats", "/api/v1/legal/stats")
if r:
    print(f"  Legal: {r.get('consultations')} consultations, {r.get('lawsuits')} lawsuits")

# === Write Operations Tests ===
print("\n--- Write Operations ---")

r = test("Create risk", "/api/v1/risk/identify", "POST", {
    "title": "API测试风险",
    "category": "operational",
    "description": "测试创建的风险",
    "source": "manual",
})
if r:
    print(f"  Risk created: {r.get('risk_id')}")

r = test("Create consultation", "/api/v1/legal/consultations", "POST", {
    "title": "API测试咨询",
    "question": "合同条款咨询",
    "department": "法务部",
    "priority": "normal",
})
if r:
    print(f"  Consultation created: {r.get('consultation_id')}")

r = test("Create backup", "/api/v1/admin/backup", "POST", {
    "type": "full",
    "description": "API测试备份",
})
if r:
    print(f"  Backup created: {r.get('backup_id')}")

r = test("Set config", "/api/v1/admin/config", "POST", {
    "key": "test_key",
    "value": "test_value",
    "category": "general",
    "updated_by": "cio",
})
if r:
    print(f"  Config set: {r.get('key')}={r.get('value')}")

r = test("Create connection", "/api/v1/admin/integration/connections", "POST", {
    "name": "测试连接",
    "type": "erp",
    "config": {"base_url": "http://example.com", "auth_type": "api_key"},
})
if r:
    print(f"  Connection created: {r.get('connection_id')}")

print(f"\n{'='*40}")
print(f"PASSED: {passed}, FAILED: {failed}")
print(f"Total: {passed + failed}")
