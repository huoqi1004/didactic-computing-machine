"""办公场景浏览器插件爬取（M11）——GitHub 高分项目 → 安全筛查 → 入库插件市场。

检索方向：Chrome 扩展与 Office 插件的公司办公场景（效率/邮件/日历/翻译/
截图/笔记/表格/文档/PDF/密码管理/会议/网页剪藏 等）。
用法：python scripts/fetch_plugins.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.fetch_capabilities import screen  # 复用内容级安全筛查

PLUGIN_KEYWORDS = [
    "chrome extension productivity", "chrome extension email", "chrome extension calendar",
    "chrome extension screenshot", "chrome extension translator", "chrome extension notes",
    "chrome extension todo", "chrome extension password manager", "chrome extension grammar",
    "chrome extension reading mode", "chrome extension web clipper", "chrome extension pdf",
    "office add-in excel", "office add-in word", "office add-in powerpoint",
    "office add-in outlook", "excel add-in", "word plugin writer",
    "notion integration", "obsidian plugin", "joplin plugin",
    "browser extension tab manager", "browser extension bookmark",
    "browser extension meeting", "browser extension crm",
    "chrome extension automation", "chrome extension form filler",
    "browser extension rss reader", "browser extension markdown",
    "chrome extension dark mode", "chrome extension highlighter",
    "browser extension time tracking", "office plugin libreoffice",
    "chrome extension invoicing", "browser extension kanban",
]

EXCLUDE_NAME_PAT = None


def collect_plugins(client: httpx.Client, target: int = 50) -> list[dict]:
    seen: dict[str, dict] = {}
    headers = {"Accept": "application/vnd.github+json"}
    for kw in PLUGIN_KEYWORDS:
        try:
            r = client.get("https://api.github.com/search/repositories",
                           params={"q": kw + " stars:>50", "sort": "stars",
                                   "per_page": 5},
                           headers=headers, timeout=20)
            for it in r.json().get("items", []):
                full = it["full_name"]
                if full in seen:
                    continue
                # 过滤明显非插件的学习教程/清单仓库
                desc = (it.get("description") or "")
                if it.get("language") not in (None, "JavaScript", "TypeScript",
                                              "HTML", "CSS", "Vue", "Python", "Dart"):
                    continue
                seen[full] = {
                    "source": "github", "kind": "plugin", "ref": full,
                    "name": (it.get("name") or full)[:80],
                    "description": desc[:300],
                    "metrics": f"stars:{it.get('stargazers_count', 0)}",
                    "url": it.get("html_url", f"https://github.com/{full}"),
                }
        except Exception as e:
            print(f"  [plugin] {kw}: failed ({e})")
            continue
        print(f"  [plugin] {kw}: total {len(seen)}")
        time.sleep(1.2)
    items = sorted(seen.values(),
                   key=lambda x: -int(str(x["metrics"]).split(":")[1] or 0))
    return items[:target]


async def seed_plugins(items: list[dict]) -> None:
    from sqlalchemy import select

    from app.core import database
    from app.core.database import get_session
    from app.models import MarketItem

    await database.init_db()
    session = get_session()
    added = 0
    async with session as s:
        have = set((await s.execute(
            select(MarketItem.name).where(MarketItem.uploader == "github")
        )).scalars().all())
        for it in items:
            if it["name"] in have:
                continue
            verdict = it["scan"]["verdict"]
            findings = "；".join(it["scan"]["findings"][:4]) or "无命中"
            s.add(MarketItem(
                kind="plugin", name=it["name"][:120],
                description=(it["description"] or "")[:500],
                payload=json.dumps({"source": it["source"], "ref": it["ref"],
                                    "url": it["url"], "metrics": it["metrics"],
                                    "scan": verdict}, ensure_ascii=False),
                status="approved" if verdict == "clean" else "pending",
                uploader=it["source"],
                review_comment=f"安全筛查: {verdict} | {findings}"[:400]))
            added += 1
        await s.commit()
    print(f"插件入库 {added} 条（clean→已上架，可疑→待审核）")


def main():
    print(f"== 收集 GitHub 办公插件（目标 50）==")
    with httpx.Client(follow_redirects=True) as client:
        items = collect_plugins(client)
    print(f"共 {len(items)} 个候选")
    print("== 安全筛查 ==")
    screen(items)
    stats: dict[str, int] = {}
    for it in items:
        stats[it["scan"]["verdict"]] = stats.get(it["scan"]["verdict"], 0) + 1
    print(f"筛查结果: {stats}")
    Path("scripts/data").mkdir(parents=True, exist_ok=True)
    Path("scripts/data/plugins.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    asyncio.run(seed_plugins(items))


if __name__ == "__main__":
    main()
