"""验证 app 级别路由"""
import sys
sys.path.insert(0, "e:\\企业数字化大脑\\backend")

OUT = r"e:\企业数字化大脑\verify_app_routes.txt"
lines = []

from app.main import app

all_routes = []
for route in app.routes:
    if hasattr(route, "path"):
        all_routes.append(route.path)
    elif hasattr(route, "routes"):
        for sub in route.routes:
            if hasattr(sub, "path"):
                all_routes.append(sub.path)

knowledge_routes = sorted([p for p in all_routes if "/knowledge" in p])
lines.append(f"App total routes: {len(all_routes)}")
lines.append(f"Knowledge routes: {len(knowledge_routes)}")
for p in knowledge_routes:
    lines.append(f"  {p}")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("DONE")
