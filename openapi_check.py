"""通过 OpenAPI 验证路由"""
import sys, json
sys.path.insert(0, "e:\\企业数字化大脑\\backend")

OUT = r"e:\企业数字化大脑\openapi_check.txt"
lines = []

from app.main import app

# 获取 OpenAPI 规范
openapi = app.openapi()
paths = openapi.get("paths", {})
lines.append(f"OpenAPI paths count: {len(paths)}")

# 找出所有 knowledge 路径
knowledge_paths = sorted([p for p in paths if "/knowledge" in p])
lines.append(f"\nKnowledge paths: {len(knowledge_paths)}")
for p in knowledge_paths:
    methods = list(paths[p].keys())
    lines.append(f"  {p} [{','.join(methods).upper()}]")

# 找出 auth/rbac/workflow 路径
for prefix in ["/auth", "/rbac", "/workflow"]:
    matching = sorted([p for p in paths if prefix in p])
    lines.append(f"\n{prefix} paths: {len(matching)}")
    for p in matching[:5]:
        methods = list(paths[p].keys())
        lines.append(f"  {p} [{','.join(methods).upper()}]")

# 统计所有路径
lines.append(f"\nAll paths:")
for p in sorted(paths.keys()):
    methods = list(paths[p].keys())
    lines.append(f"  {p} [{','.join(methods).upper()}]")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("DONE")
