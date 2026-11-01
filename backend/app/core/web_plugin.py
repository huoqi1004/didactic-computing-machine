"""Web 插件内核（M11 · 赋能智能体上网能力）。

- ``fetch_page``：抓取网页（内置 Chromium UA + 反爬头），HTML → 正文
  （bs4 剥离 script/style，保留标题/段落/表格文本），限长防上下文爆炸
- ``web_search``：联网搜索（DuckDuckGo HTML 端点，无需密钥），
  返回标题/链接/摘要
- 对话链路（conversation.chat）自动调用：消息含 URL → 浏览注入正文；
  命中"搜索"意图 → 检索并注入 Top 结果
"""
from __future__ import annotations

import re

import httpx
import structlog

logger = structlog.get_logger()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
MAX_CHARS = 6000
URL_PAT = re.compile(r"https?://[^\s<>()\[\]\"']+")
SEARCH_INTENT_PAT = re.compile(r"(搜索|检索|查一下|上网查|搜一下|查查|联网).{0,24}?(关于|有关|相关)?|"
                               r"网上.{0,8}(找|查|搜)", re.IGNORECASE)


def _strip_html(html: str) -> tuple[str, str]:
    """HTML → (title, text)。剥离 script/style/nav 噪声。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
        text = re.sub(r"<[^>]+>", " ", text)
        return "", re.sub(r"\s{2,}", " ", text)[:MAX_CHARS]
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "iframe", "noscript", "svg"]):
        tag.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "")
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title[:200], text[:MAX_CHARS]


async def fetch_page(url: str, timeout: float = 20.0) -> dict:
    """抓取单个网页，返回 {ok, url, title, text / error}。"""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                     headers=HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return {"ok": False, "url": url,
                    "error": f"非网页内容（{ctype.split(';')[0]}），已跳过"}
        title, text = _strip_html(resp.text)
        if not text:
            return {"ok": False, "url": url, "error": "页面无可提取正文"}
        return {"ok": True, "url": str(resp.url), "title": title or url,
                "text": text}
    except Exception as e:
        logger.warning("webplugin.fetch_failed", url=url, error=str(e))
        return {"ok": False, "url": url, "error": f"抓取失败：{e}"}


async def web_search(query: str, top_k: int = 6) -> dict:
    """联网搜索：Bing 中国版优先（可达性好），DuckDuckGo 兜底。

    返回 {ok, results: [{title, url, snippet}]}。
    """
    for engine in (_search_bing, _search_ddg):
        try:
            results = await engine(query, top_k)
            if results:
                return {"ok": True, "results": results, "error": ""}
        except Exception as e:
            logger.warning("webplugin.engine_failed", engine=engine.__name__,
                           error=str(e))
    return {"ok": False, "results": [], "error": "所有搜索源均不可达"}


async def _search_bing(query: str, top_k: int) -> list[dict]:
    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0,
                                 headers=HEADERS) as client:
        resp = await client.get("https://cn.bing.com/search", params={"q": query})
        resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select("li.b_algo")[:top_k * 2]:
        a = item.select_one("h2 a")
        if not a:
            continue
        snippet_el = item.select_one(".b_caption p") or item.select_one("p")
        results.append({"title": a.get_text(strip=True)[:120],
                        "url": a.get("href", ""),
                        "snippet": snippet_el.get_text(" ", strip=True)[:220] if snippet_el else ""})
        if len(results) >= top_k:
            break
    return results


async def _search_ddg(query: str, top_k: int) -> list[dict]:
    from urllib.parse import unquote

    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0,
                                 headers=HEADERS) as client:
        resp = await client.post("https://html.duckduckgo.com/html/",
                                 data={"q": query})
        resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select(".result")[:top_k * 2]:
        a = item.select_one(".result__a")
        if not a:
            continue
        snippet_el = item.select_one(".result__snippet")
        href = a.get("href", "")
        m = re.search(r"uddg=([^&]+)", href)  # DDG 重定向包装
        if m:
            href = unquote(m.group(1))
        results.append({"title": a.get_text(strip=True)[:120],
                        "url": href,
                        "snippet": snippet_el.get_text(" ", strip=True)[:220] if snippet_el else ""})
        if len(results) >= top_k:
            break
    return results


def extract_urls(message: str) -> list[str]:
    """提取消息中的 URL（去重，最多 3 个）。"""
    seen: list[str] = []
    for u in URL_PAT.findall(message):
        u = u.rstrip(".,;，。；）")
        if u not in seen:
            seen.append(u)
    return seen[:3]


def wants_search(message: str) -> bool:
    """是否命中联网搜索意图。"""
    return bool(SEARCH_INTENT_PAT.search(message))


async def gather_web_context(message: str) -> tuple[str, list[dict]]:
    """对话链路入口：按消息内容收集网络上下文。

    返回 (系统提示词片段, thinking步骤)；无网络动作返回 ("", [])。
    """
    steps: list[dict] = []
    parts: list[str] = []

    urls = extract_urls(message)
    for url in urls:
        page = await fetch_page(url)
        if page["ok"]:
            parts.append(f"网页浏览【{page['title']}】({page['url']})：\n{page['text'][:3000]}")
            steps.append({"kind": "web", "status": "done",
                          "detail": f"已浏览网页：{page['title'][:40]}（{page['url'][:60]}）"})
        else:
            steps.append({"kind": "web", "status": "error",
                          "detail": f"网页抓取失败：{url[:60]}（{page.get('error', '')}）"})

    if not urls and wants_search(message):
        q = re.sub(r"(帮我|请|搜索|检索|查一下|上网查|搜一下|查查|联网|网上|关于|有关|相关|一下|的资料|资料|信息)",
                   " ", message).strip()[:60] or message[:40]
        sr = await web_search(q)
        if sr["ok"]:
            lines = [f"- 【{r['title']}】{r['snippet']}（{r['url']}）"
                     for r in sr["results"]]
            parts.append(f"联网检索结果（关键词：{q}）：\n" + "\n".join(lines))
            steps.append({"kind": "web", "status": "done",
                          "detail": f"联网搜索「{q[:30]}」命中 {len(sr['results'])} 条结果"})
        else:
            steps.append({"kind": "web", "status": "error",
                          "detail": f"联网搜索失败：{sr.get('error', '')}"})

    if parts:
        ctx = ("以下是通过内置浏览器插件获取的实时网络内容，供回答参考（注意标注来源）：\n\n"
               + "\n\n".join(parts))
        return ctx, steps
    return "", steps
