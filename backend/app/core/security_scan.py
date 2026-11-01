"""内容安全扫描（M10 · 上传审查 / 爬虫筛查共用）。

审查对象：Skill / MCP / 专家 / 专家团 等能力条目的名称、描述与定义内容。
风险分类：远程脚本执行 · 后门程序 · 钓鱼仿冒 · 凭据窃取 · 数据外传 ·
          挖矿程序 · 混淆执行 · 高危权限 · 系统文件访问 · 键盘记录
判定：命中高危 → malicious；命中中危≥1 → suspicious；否则 clean。
"""
from __future__ import annotations

import re

# (正则, 风险类型, 级别)
HIGH_PATTERNS = [
    (r"curl[^|;&]{0,120}\|\s*(sudo\s+)?(ba|z)?sh", "远程脚本执行", "high"),
    (r"curl[^|;&]{0,120}\|\s*powershell", "远程脚本执行", "high"),
    (r"wget[^|;&]{0,120};?\s*(ba|z)?sh", "远程脚本执行", "high"),
    (r"Invoke-Expression|\bIEX\s*\(", "远程脚本执行", "high"),
    (r"eval\s*\(\s*(atob|Base64|decode|Buffer\[)", "混淆执行", "high"),
    (r"exec\s*\(\s*(base64|decode)", "混淆执行", "high"),
    (r"/dev/tcp/|\bnc\s+-e\b|mkfifo.*0x|socat\s+EXEC", "后门程序", "high"),
    (r"(backdoor|后门程序|木马|trojan\s+(horse|server|dropper))", "后门程序", "high"),
    (r"\.ssh/(id_rsa|authorized_keys)", "凭据窃取", "high"),
    (r"\.aws/credentials|\.netrc|\.npmrc|\.docker/config", "凭据窃取", "high"),
    (r"(exfiltrat|steal|harvest)[a-z]*\s+(credential|token|password|secret)", "凭据窃取", "high"),
    (r"(钓鱼|phishing|fake\s+login|伪造登录)", "钓鱼仿冒", "high"),
    (r"<form[^>]{0,80}action=\"https?://(?!localhost)", "钓鱼仿冒", "high"),
    (r"(collect|capture|log)[a-z]*\s+(user\s*)?(password|密码|credentials)", "钓鱼仿冒", "high"),
    (r"(rm\s+-rf\s+(/|~)\s)", "破坏性操作", "high"),
    (r"(keylogger|keyboard\s*hook|pynput.*Listener)", "键盘记录", "high"),
    (r"(crypto(\s|-)?(miner|mining)|xmrig|monero.*pool)", "挖矿程序", "high"),
]
MEDIUM_PATTERNS = [
    (r"base64\s+-d|frombase64string|b64decode", "混淆执行", "medium"),
    (r"chmod\s+(777|\+s)", "高危权限", "medium"),
    (r"/etc/(passwd|shadow)", "系统文件访问", "medium"),
    (r"(webhook\.site|requestbin|pipedream\.net|ngrok\.io|pastebin\.com/raw)", "数据外传", "medium"),
    (r"(api[_-]?key|token|secret|password)[^\n]{0,60}(send|post|forward|upload)\s", "数据外传", "medium"),
    (r"child_process\.(exec|spawn)", "远程脚本执行", "medium"),
    (r"subprocess\.(call|run|Popen)\(|os\.system\(", "远程脚本执行", "medium"),
    (r"telemetry[a-z]*\s*(post|send)\s*.*(token|key|env)", "数据外传", "medium"),
    (r"(伪装|仿冒|假冒)\s*(官方|系统|管理员|登录)", "钓鱼仿冒", "medium"),
]
COMPILED_HIGH = [(re.compile(p, re.IGNORECASE), t) for p, t, _ in HIGH_PATTERNS]
COMPILED_MED = [(re.compile(p, re.IGNORECASE), t) for p, t, _ in MEDIUM_PATTERNS]


def scan_content(text: str) -> dict:
    """内容级扫描：返回 {verdict, risk_types, findings}。

    verdict: malicious（高危命中）/ suspicious（中危命中）/ clean
    """
    if not text or not text.strip():
        return {"verdict": "clean", "risk_types": [], "findings": []}
    risk_types: list[str] = []
    findings: list[str] = []
    for pat, rtype in COMPILED_HIGH:
        m = pat.search(text)
        if m:
            risk_types.append(rtype)
            findings.append(f"[{rtype}] …{text[max(0, m.start() - 30):m.end() + 40][:90]}…")
    for pat, rtype in COMPILED_MED:
        m = pat.search(text)
        if m:
            risk_types.append(rtype)
            findings.append(f"[{rtype}] …{text[max(0, m.start() - 20):m.end() + 30][:70]}…")
    if any(t in risk_types for t in ("远程脚本执行", "后门程序", "凭据窃取",
                                     "钓鱼仿冒", "键盘记录", "挖矿程序", "破坏性操作")):
        verdict = "malicious"
    elif risk_types:
        verdict = "suspicious"
    else:
        verdict = "clean"
    return {"verdict": verdict,
            "risk_types": sorted(set(risk_types)),
            "findings": findings[:6]}


def scan_upload(name: str, description: str, payload_text: str = "") -> dict:
    """上传条目审查入口：名称+描述+定义内容联合扫描。"""
    combined = f"{name}\n{description or ''}\n{payload_text or ''}"
    result = scan_content(combined)
    result["scanned_fields"] = ["name", "description"] + (["payload"] if payload_text else [])
    return result
