"""Skill / MCP 能力爬取（M8）——GitHub + ClawHub → 恶意脚本筛查 → 入库能力市场。

用法：
    python scripts/fetch_capabilities.py          # 爬取 + 筛查 + 输出 JSON
    python scripts/fetch_capabilities.py --seed   # 在爬取基础上入库 market_items

安全筛查（内容级）：
- ClawHub skill：按 install.reference 拉 raw.githubusercontent.com 的 SKILL.md
- GitHub MCP：拉仓库 README.md
- 命中高危模式（管道执行远端脚本/反向 shell/凭据窃取/挖矿）→ malicious；
  中危（混淆执行/可疑外传通道/敏感文件读取）→ suspicious；
  其余 clean。入库时 clean → approved，其余 → pending（管理员复核）。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "data" / "capabilities.json"

# 各方向关键词（覆盖企业通用方向）
SKILL_KEYWORDS = [
    "excel", "pdf", "email", "calendar", "git", "sql", "data-analysis",
    "chart", "report", "meeting", "resume", "translate", "web-scraping",
    "image", "finance", "accounting", "invoice", "hr", "legal", "marketing",
    "seo", "crm", "project-management", "notes", "knowledge", "docs",
    "presentation", "slack", "notion", "confluence",
]
MCP_KEYWORDS = [
    "github mcp server", "postgres mcp server", "filesystem mcp server",
    "browser mcp server", "search mcp server", "database mcp server",
    "slack mcp server", "google-drive mcp server", "memory mcp server",
    "excel mcp server", "email mcp server", "pdf mcp server",
    "jira mcp server", "confluence mcp server", "notion mcp server",
    "kubernetes mcp server", "docker mcp server", "finance mcp server",
    "maps mcp server", "sentry mcp server", "gitlab mcp server",
    "sqlite mcp server", "puppeteer mcp server", "fetch mcp server",
    "time mcp server", "sequential-thinking mcp server",
    "mysql mcp server", "redis mcp server", "elasticsearch mcp server",
    "windows mcp server", "office mcp server", "word mcp server",
    "powerpoint mcp server", "outlook mcp server", "teams mcp server",
    "youtube mcp server", "wikipedia mcp server", "arxiv mcp server",
    "weather mcp server", "calendar mcp server", "todo mcp server",
    "erp mcp server", "odoo mcp server", "sap mcp server",
    "salesforce mcp server", "hubspot mcp server", "shopify mcp server",
    "playwright mcp server", "puppeteer mcp", "python mcp server",
    "calculator mcp server", "chart mcp server", "image mcp server",
]

# ============ 恶意脚本筛查模式 ============
HIGH_PATTERNS = [
    (r"curl[^|;&]{0,120}\|\s*(sudo\s+)?(ba|z)?sh", "远程脚本管道执行"),
    (r"curl[^|;&]{0,120}\|\s*powershell", "远程脚本管道执行(PowerShell)"),
    (r"wget[^|;&]{0,120};?\s*(ba|z)?sh", "下载并执行"),
    (r"Invoke-Expression|\bIEX\s*\(", "PowerShell 动态执行"),
    (r"eval\s*\(\s*(atob|Base64|decode|Buffer\[)", "混淆代码动态执行"),
    (r"exec\s*\(\s*base64|exec\s*\(\s*decode", "混淆代码动态执行(py)"),
    (r"/dev/tcp/|\bnc\s+-e\b|mkfifo.*0x|socat\s+EXEC", "反向 shell"),
    (r"\.ssh/(id_rsa|authorized_keys)", "SSH 私钥窃取"),
    (r"\.aws/credentials|\.netrc|\.npmrc|\.docker/config", "云凭据窃取"),
    (r"(exfiltrat|steal|harvest)[a-z]*\s+(credential|token|password|secret)", "明文窃取凭据意图"),
    (r"(rm\s+-rf\s+(/|~)\s)", "删除根/家目录"),
    (r"(keylogger|keyboard\s*hook|pynput.*Listener)", "键盘记录"),
    (r"(crypto(\s|-)?(miner|mining)|xmrig|monero.*pool)", "挖矿脚本"),
]
MEDIUM_PATTERNS = [
    (r"base64\s+-d|frombase64string|b64decode", "Base64 解码执行"),
    (r"chmod\s+(777|\+s)", "高危权限修改"),
    (r"/etc/(passwd|shadow)", "系统账户文件读取"),
    (r"(webhook\.site|requestbin|pipedream\.net|ngrok\.io|pastebin\.com/raw)", "可疑外传通道"),
    (r"(api[_-]?key|token|secret|password)[^\n]{0,60}(send|post|forward|upload)\s", "凭据外发组合"),
    (r"child_process\.(exec|spawn)", "Node 子进程执行"),
    (r"subprocess\.(call|run|Popen)\(", "Python 子进程执行"),
    (r"os\.system\(", "os.system 执行"),
    (r"telemetry[a-z]*\s*(post|send)\s*.*(token|key|env)", "遥测携带敏感数据"),
]
COMPILED_HIGH = [(re.compile(p, re.IGNORECASE), label) for p, label in HIGH_PATTERNS]
COMPILED_MED = [(re.compile(p, re.IGNORECASE), label) for p, label in MEDIUM_PATTERNS]


def scan_content(text: str) -> dict:
    """内容级恶意筛查：返回 {verdict, findings}。"""
    if not text:
        return {"verdict": "unknown", "findings": ["未取到内容，仅通过描述筛查"]}
    findings = []
    high = med = 0
    for pat, label in COMPILED_HIGH:
        m = pat.search(text)
        if m:
            high += 1
            findings.append(f"[高] {label}: …{text[max(0, m.start()-30):m.end()+40][:90]}…")
    for pat, label in COMPILED_MED:
        if pat.search(text):
            med += 1
            findings.append(f"[中] {label}")
    if high:
        verdict = "malicious"
    elif med >= 2:
        verdict = "suspicious"
    elif med == 1:
        verdict = "suspicious"
    else:
        verdict = "clean"
    return {"verdict": verdict, "findings": findings}


def scan_text_shallow(text: str) -> dict:
    """描述级筛查（未取到内容时的兜底）。"""
    findings = []
    for pat, label in COMPILED_HIGH:
        if pat.search(text):
            return {"verdict": "malicious", "findings": [f"[高] {label}（命中描述）"]}
    for pat, label in COMPILED_MED:
        if pat.search(text):
            findings.append(f"[中] {label}（命中描述）")
    return {"verdict": "suspicious" if findings else "clean", "findings": findings}


def fetch_raw(candidate_refs: list[str], client: httpx.Client) -> str:
    """尝试多个 raw 地址取文件内容，全失败返回空串。"""
    for url in candidate_refs:
        try:
            r = client.get(url, timeout=15)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:
            continue
        time.sleep(0.15)
    return ""


def collect_clawhub_skills(client: httpx.Client, target: int = 100) -> list[dict]:
    seen: dict[str, dict] = {}
    for kw in SKILL_KEYWORDS:
        try:
            r = client.get("https://clawhub.ai/api/v1/search",
                           params={"q": kw, "sort": "downloads", "limit": 10},
                           timeout=20)
            results = (r.json() or {}).get("results", [])
        except Exception as e:
            print(f"  [clawhub] {kw}: failed ({e})")
            continue
        for it in results:
            ref = (it.get("install") or {}).get("reference", "")
            if not ref or it.get("id") in seen:
                continue
            seen[it["id"]] = {
                "source": "clawhub", "kind": "skill", "ref": ref,
                "name": it.get("displayName") or ref,
                "description": (it.get("summary") or "")[:300],
                "metrics": f"downloads:{it.get('downloads', 0)}",
                "url": "https://clawhub.ai" + (it.get("links") or {}).get("canonical", ""),
            }
        print(f"  [clawhub] {kw}: +{len(results)} (total {len(seen)})")
        time.sleep(0.4)
        if len(seen) >= target * 1.6:
            break
    items = sorted(seen.values(), key=lambda x: -int(str(x["metrics"]).split(":")[1] or 0))
    return items[:target]


def collect_github_mcps(client: httpx.Client, target: int = 100) -> list[dict]:
    seen: dict[str, dict] = {}
    headers = {"Accept": "application/vnd.github+json"}
    for kw in MCP_KEYWORDS:
        try:
            r = client.get("https://api.github.com/search/repositories",
                           params={"q": kw, "sort": "stars", "per_page": 6},
                           headers=headers, timeout=20)
            data = r.json()
            for it in data.get("items", []):
                full = it["full_name"]
                if full in seen:
                    continue
                seen[full] = {
                    "source": "github", "kind": "mcp", "ref": full,
                    "name": it.get("name", full),
                    "description": (it.get("description") or "")[:300],
                    "metrics": f"stars:{it.get('stargazers_count', 0)}",
                    "url": it.get("html_url", f"https://github.com/{full}"),
                }
        except Exception as e:
            print(f"  [github-mcp] {kw}: failed ({e})")
            continue
        print(f"  [github-mcp] {kw}: total {len(seen)}")
        time.sleep(1.2)  # search API 限流保护
        if len(seen) >= target * 1.5:
            break
    items = sorted(seen.values(),
                   key=lambda x: -int(str(x["metrics"]).split(":")[1] or 0))
    return items[:target]


def screen(items: list[dict]) -> None:
    """对每个条目做内容级筛查（raw SKILL.md / README）。"""
    with httpx.Client(follow_redirects=True) as client:
        for i, it in enumerate(items):
            if it["source"] == "clawhub":
                refs = [f"https://raw.githubusercontent.com/{it['ref']}/main/SKILL.md",
                        f"https://raw.githubusercontent.com/{it['ref']}/master/SKILL.md",
                        f"https://raw.githubusercontent.com/{it['ref']}/main/README.md"]
            else:
                refs = [f"https://raw.githubusercontent.com/{it['ref']}/main/README.md",
                        f"https://raw.githubusercontent.com/{it['ref']}/master/README.md"]
            content = fetch_raw(refs, client)
            result = scan_content(content) if content else scan_text_shallow(
                f"{it['name']} {it['description']}")
            it["scan"] = result
            if (i + 1) % 10 == 0:
                print(f"  [scan] {i + 1}/{len(items)}")


def main():
    seed = "--seed" in sys.argv
    print("== 1/3 收集 ClawHub skills ==")
    with httpx.Client(follow_redirects=True) as client:
        skills = collect_clawhub_skills(client)
        print(f"共 {len(skills)} 个候选 skill")
        print("== 2/3 收集 GitHub MCP servers ==")
        mcps = collect_github_mcps(client)
    print(f"共 {len(mcps)} 个候选 MCP")
    print("== 3/3 恶意脚本筛查 ==")
    screen(skills + mcps)
    data = {"skills": skills, "mcps": mcps}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    stats = {}
    for it in skills + mcps:
        stats[it["scan"]["verdict"]] = stats.get(it["scan"]["verdict"], 0) + 1
    print(f"筛查结果: {stats}")
    print(f"已写入 {OUT}")

    if seed:
        import asyncio
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.core import database
        from app.core.database import get_session
        from app.models import MarketItem

        async def do_seed():
            engine = await database.init_db()
            session = get_session()
            added = 0
            async with session as s:
                have = set((await s.execute(
                    __import__("sqlalchemy").select(MarketItem.name)
                    .where(MarketItem.uploader.in_(["clawhub", "github"])))
                ).scalars().all())
                for it in skills + mcps:
                    if it["name"] in have:
                        continue
                    verdict = it["scan"]["verdict"]
                    findings = "；".join(it["scan"]["findings"][:4]) or "无命中"
                    session.add(MarketItem(
                        kind=it["kind"], name=it["name"][:120],
                        description=(it["description"] or "")[:500],
                        payload=json.dumps({"source": it["source"], "ref": it["ref"],
                                            "url": it["url"], "metrics": it["metrics"],
                                            "scan": verdict},
                                           ensure_ascii=False),
                        status="approved" if verdict == "clean" else "pending",
                        uploader=it["source"],
                        review_comment=f"安全筛查: {verdict} | {findings}"[:400]))
                    added += 1
                await s.commit()
            print(f"入库 {added} 条（clean→已上架，可疑→待审核）")

        asyncio.run(do_seed())


if __name__ == "__main__":
    main()
