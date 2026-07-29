"""检查 api_router 内部结构"""
import sys
sys.path.insert(0, "e:\\企业数字化大脑\\backend")

OUT = r"e:\企业数字化大脑\router_debug.txt"
lines = []

from app.api.routes import api_router

lines.append(f"api_router.routes count: {len(api_router.routes)}")
for i, r in enumerate(api_router.routes):
    lines.append(f"  [{i}] type={type(r).__name__}")
    lines.append(f"      has path: {hasattr(r, 'path')}")
    lines.append(f"      has routes: {hasattr(r, 'routes')}")
    if hasattr(r, 'path'):
        lines.append(f"      path={r.path}")
    if hasattr(r, 'routes'):
        lines.append(f"      sub-routes count: {len(r.routes)}")
    # Check all attributes
    attrs = [a for a in dir(r) if not a.startswith('_')]
    lines.append(f"      attrs: {attrs[:15]}")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("DONE")
