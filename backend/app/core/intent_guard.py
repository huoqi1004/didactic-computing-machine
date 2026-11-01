"""输入层意图识别 + 数字分身防护（D-R6 裁决落地）。

- 意图分类：creation / query / action / chitchat（规则路径确定性）；
- **恶意意图研判**：越权获取、诱导套取、角色越狱、提示注入检测；
  命中 → 立即中断对话（发提示）+ 双管理员告警（部门管理员+系统管理员
  经系统内部消息）+ 审计留痕（溯源审计入口）。
"""
from __future__ import annotations

import re
from typing import Any

# 恶意意图模式（D-R6：越权/诱导/套取/冒充/注入）
_THREAT_PATTERNS = [
    (r"(越权|无权|绕过|绕开).{0,8}(访问|查看|获取|审批)", "privilege_escalation"),
    (r"(套取|窃取|导出|下载).{0,10}(他人|别的部门|全公司|所有).{0,8}(工资|薪酬|合同|客户|数据)", "data_exfiltration"),
    (r"(假装|扮演|你现在是).{0,10}(管理员|总经理|董事长|系统)", "role_impersonation"),
    (r"(忽略|无视|忘记)(之前|以上|所有|前面的)?(指令|规则|提示|设定)", "prompt_injection"),
    (r"(诱导|引导|教我).{0,8}(绕过|规避|违反).{0,8}(审批|流程|制度)", "policy_bypass"),
    (r"(窃|偷|泄露).{0,6}(密码|密钥|token|凭证)", "credential_theft"),
]

# 告警级别映射
_SEVERITY = {"privilege_escalation": "high", "data_exfiltration": "critical",
             "role_impersonation": "high", "prompt_injection": "medium",
             "policy_bypass": "high", "credential_theft": "critical"}


def classify(message: str) -> dict:
    """规则路径意图分类（确定性；LLM 路径由对话网关叠加）。"""
    lower = message.lower()
    if any(re.search(p, message) or re.search(p, lower) for p, _ in
           [(r"(写|生成|编制|起草).{0,20}(大纲|方案|报告|纪要|文档)", 0)]):
        return {"intent": "creation", "source": "rules"}
    if re.search(r"(查|看|统计|多少|状态|进展|列表|余额|报表)", message):
        return {"intent": "query", "source": "rules"}
    if re.search(r"(提交|审批|转账|创建|修改|下单|确认|支付|取消)", message):
        return {"intent": "action", "source": "rules"}
    return {"intent": "chitchat", "source": "rules"}


def detect_threats(message: str) -> list[dict]:
    """恶意意图研判：返回命中威胁列表（空列表 = 未检出）。"""
    threats = []
    for pattern, kind in _THREAT_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            threats.append({"kind": kind, "severity": _SEVERITY.get(kind, "medium"),
                            "pattern": pattern})
    return threats


def is_malicious(message: str) -> tuple[bool, list[dict]]:
    """便捷判定：是否恶意（用于数字分身对话入口的快速闸门）。"""
    threats = detect_threats(message)
    return bool(threats), threats
