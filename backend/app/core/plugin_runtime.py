"""插件运行时 v1（M13 · 能力市场插件在对话中的真实执行）。

按插件名称/描述匹配类别，执行对应的内置动作并把结果注入对话上下文：
- 翻译类   → LLM 翻译动作（中英互译/指定目标语言）
- 搜索类   → web_plugin.web_search
- 网页/剪藏/阅读类 → web_plugin.fetch_page（消息中的 URL，或插件主页）
- 表格类   → 标记为"生成文件"意图（由 AI 意图决策生成 xlsx/csv）
- 通用     → 仅注入插件能力描述（现状行为）

执行失败不阻塞对话，thinking 记录错误。
"""
from __future__ import annotations

import re

import structlog

from app.core import web_plugin

logger = structlog.get_logger()

CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("translate", re.compile(r"(翻译|translate|saladict|词典|dictionary|双语)", re.I)),
    ("search", re.compile(r"(搜索|search|tavily|引擎|联网)", re.I)),
    ("webclip", re.compile(r"(剪藏|网页|clip|reader|阅读模式|浏览器|browser|extension)", re.I)),
    ("sheet", re.compile(r"(表格|excel|xlsx|sheet|电子表格)", re.I)),
    ("meeting", re.compile(r"(会议|meeting|字幕|sokuji|实时翻译)", re.I)),
]


def classify_plugin(name: str, description: str) -> str:
    text = f"{name} {description}"
    for cat, pat in CATEGORY_RULES:
        if pat.search(text):
            return cat
    return "generic"


async def execute_plugin(plugin_name: str, description: str,
                         user_message: str, llm_translate) -> tuple[str, list[dict]]:
    """执行插件动作。返回 (上下文片段, thinking步骤)。

    llm_translate: async (text, target_lang) -> str 的翻译动作回调。
    """
    cat = classify_plugin(plugin_name, description)
    steps: list[dict] = []
    base = f"插件[{plugin_name}]"

    try:
        if cat == "translate":
            target = "英文" if re.search(r"[一-龥]", user_message) else "中文"
            translated = await llm_translate(user_message[:1000], target)
            ctx = (f"插件 {base}（翻译动作）执行结果：\n{translated}")
            steps.append({"kind": "plugin", "status": "done",
                          "detail": f"{base} 已执行翻译动作 → {target}"})
            return ctx, steps

        if cat == "search":
            q = re.sub(r"(帮我|请|搜索|查一下|搜一下|一下|的资料|资料)", " ",
                       user_message).strip()[:50] or user_message[:40]
            sr = await web_plugin.web_search(q)
            if sr["ok"]:
                lines = [f"- 【{r['title']}】{r['snippet']}（{r['url']}）"
                         for r in sr["results"][:5]]
                ctx = f"插件 {base}（搜索动作）检索结果：\n" + "\n".join(lines)
                steps.append({"kind": "plugin", "status": "done",
                              "detail": f"{base} 已执行联网搜索，命中 {len(sr['results'])} 条"})
                return ctx, steps
            steps.append({"kind": "plugin", "status": "error",
                          "detail": f"{base} 搜索失败：{sr.get('error', '')}"})
            return "", steps

        if cat == "webclip":
            urls = web_plugin.extract_urls(user_message)
            if urls:
                page = await web_plugin.fetch_page(urls[0])
                if page["ok"]:
                    ctx = (f"插件 {base}（网页抓取动作）浏览【{page['title']}】：\n"
                           f"{page['text'][:2500]}")
                    steps.append({"kind": "plugin", "status": "done",
                                  "detail": f"{base} 已抓取网页：{page['title'][:40]}"})
                    return ctx, steps
            # 无 URL：注入插件阅读模式说明
            ctx = f"插件 {base}（网页抓取动作）已就绪：在消息中附上 URL 即可自动抓取正文。"
            steps.append({"kind": "plugin", "status": "done",
                          "detail": f"{base} 待命（消息中未发现 URL）"})
            return ctx, steps

        if cat == "sheet":
            ctx = (f"插件 {base}（表格动作）已激活：请把上述内容整理为结构化表格，"
                   "并由系统生成 xlsx/csv 工作区文件。")
            steps.append({"kind": "plugin", "status": "done",
                          "detail": f"{base} 表格动作已激活（将联动文件生成）"})
            return ctx, steps

        if cat == "meeting":
            ctx = (f"插件 {base}（会议动作）：可提供会议转写/纪要结构化支持，"
                   "请粘贴会议内容或要点。")
            steps.append({"kind": "plugin", "status": "done",
                          "detail": f"{base} 会议动作就绪"})
            return ctx, steps

        return "", steps  # generic：保持原有 persona 注入行为
    except Exception as e:
        logger.warning("plugin.exec_failed", plugin=plugin_name, error=str(e))
        steps.append({"kind": "plugin", "status": "error",
                      "detail": f"{base} 执行失败：{e}"})
        return "", steps
