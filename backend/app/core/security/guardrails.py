"""
安全护栏
输入输出实时过滤提示注入、越狱与敏感词
对抗性防御，保护Agent免受恶意诱导
"""
import re
import structlog
from typing import Optional
from enum import Enum

logger = structlog.get_logger()


class GuardrailAction(str, Enum):
    """护栏动作"""
    ALLOW = "allow"            # 允许通过
    BLOCK = "block"            # 阻止
    SANITIZE = "sanitize"      # 清洗后通过
    ESCALATE = "escalate"      # 升级人类审批


class ThreatType(str, Enum):
    """威胁类型"""
    PROMPT_INJECTION = "prompt_injection"  # 提示注入
    JAILBREAK = "jailbreak"                # 越狱攻击
    SENSITIVE_DATA = "sensitive_data"      # 敏感数据泄露
    MALICIOUS_CODE = "malicious_code"      # 恶意代码
    SUSPICIOUS_PATTERN = "suspicious_pattern"  # 可疑模式


class SecurityGuardrails:
    """
    安全护栏 - 输入输出双向过滤

    输入护栏：
    - 检测提示注入攻击
    - 检测越狱尝试
    - 过滤敏感词
    - 检测恶意代码

    输出护栏：
    - 防止敏感数据泄露
    - 检测不当内容
    - 防止指令泄露
    """

    # 提示注入检测模式
    INJECTION_PATTERNS = [
        r"ignore\s+(previous|above|all)\s+(instructions|prompts|rules)",
        r"disregard\s+(your|the)\s+(system|initial)\s+prompt",
        r"you\s+are\s+now\s+(a|an)\s+(different|new|jailbroken)",
        r"(\[SYSTEM\]|\[ADMIN\]|\[ROOT\])",
        r"reveal\s+(your|the)\s+(system\s+)?prompt",
        r"override\s+(safety|content|security)\s+(filter|policy|rules)",
        r"pretend\s+you\s+(have\s+no|don't\s+have)\s+restrictions",
        r"act\s+as\s+(if\s+you\s+(are|have)|an?\s+unrestricted)",
    ]

    # 越狱检测模式
    JAILBREAK_PATTERNS = [
        r"DAN\s+mode",
        r"developer\s+mode",
        r"jailbreak",
        r"bypass\s+(safety|security|filter)",
        r"unrestricted\s+mode",
        r"evil\s+(mode|assistant|chatbot)",
        r"do\s+anything\s+now",
    ]

    # 敏感数据检测模式
    SENSITIVE_PATTERNS = [
        r"\b\d{16,19}\b",                    # 信用卡号
        r"\b\d{3}-\d{2}-\d{4}\b",            # SSN
        r"password\s*[=:]\s*\S+",
        r"api[_\s-]?key\s*[=:]\s*\S+",
        r"secret\s*[=:]\s*\S+",
        r"private[_-]?key\s*[=:]\s*\S+",
        r"token\s*[=:]\s*[A-Za-z0-9]{20,}",
    ]

    # 敏感词列表（可扩展）
    SENSITIVE_WORDS = [
        "机密", "绝密", "内部", "confidential", "top secret",
        "classified", "proprietary",
    ]

    # 凭证形态：SENSITIVE_PATTERNS 里"泄露即事故"的子集，按模式前缀认领，避免两处维护同一串正则。
    # 卡号/SSN 不在此列——业务正文里 16 位单号极常见，整段撤回会误伤正确答案。
    _CREDENTIAL_PATTERN_PREFIXES = ("password", "api", "secret", "private", "token")

    def __init__(self):
        self._injection_regex = [re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS]
        self._jailbreak_regex = [re.compile(p, re.IGNORECASE) for p in self.JAILBREAK_PATTERNS]
        self._sensitive_regex = [re.compile(p, re.IGNORECASE) for p in self.SENSITIVE_PATTERNS]
        self._credential_regex = [
            regex for regex, pattern in zip(self._sensitive_regex, self.SENSITIVE_PATTERNS)
            if pattern.startswith(self._CREDENTIAL_PATTERN_PREFIXES)
        ]
        self._blocked_count = 0
        self._sanitized_count = 0
        self._allowed_count = 0

    def find_credential_leaks(self, text: str) -> list[dict]:
        """只读探测凭证形态：不累加脱敏计数、不打日志。

        流式护栏每 240 字调一次，走 ``check_output`` 会把 ``get_stats()`` 的
        脱敏计数灌满并刷出告警噪声，这里要的是判定而不是记录。
        """
        return [
            {
                "type": ThreatType.SENSITIVE_DATA.value,
                "pattern": regex.pattern,
                "severity": "critical",
            }
            for regex in self._credential_regex if regex.search(text)
        ]

    def check_input(self, text: str, agent_did: str = "") -> tuple[GuardrailAction, list[dict]]:
        """
        检查输入内容
        返回 (动作, 检测到的威胁列表)
        """
        threats = []

        # 检测提示注入
        for regex in self._injection_regex:
            if regex.search(text):
                threats.append({
                    "type": ThreatType.PROMPT_INJECTION.value,
                    "pattern": regex.pattern,
                    "severity": "high",
                })

        # 检测越狱尝试
        for regex in self._jailbreak_regex:
            if regex.search(text):
                threats.append({
                    "type": ThreatType.JAILBREAK.value,
                    "pattern": regex.pattern,
                    "severity": "critical",
                })

        # 检测恶意代码
        if self._detect_malicious_code(text):
            threats.append({
                "type": ThreatType.MALICIOUS_CODE.value,
                "severity": "high",
            })

        # 决定动作
        action = self._decide_action(threats)

        if action == GuardrailAction.BLOCK:
            self._blocked_count += 1
            logger.warning("guardrail.input_blocked",
                          agent=agent_did,
                          threats=[t["type"] for t in threats])
        elif action == GuardrailAction.SANITIZE:
            self._sanitized_count += 1
        else:
            self._allowed_count += 1

        return action, threats

    def check_output(self, text: str, agent_did: str = "") -> tuple[GuardrailAction, str, list[dict]]:
        """
        检查输出内容
        返回 (动作, 清洗后文本, 检测到的威胁列表)
        """
        threats = []
        sanitized_text = text

        # 检测敏感数据泄露
        for regex in self._sensitive_regex:
            matches = regex.findall(text)
            if matches:
                threats.append({
                    "type": ThreatType.SENSITIVE_DATA.value,
                    "pattern": regex.pattern,
                    "count": len(matches),
                    "severity": "high",
                })
                # 脱敏处理
                sanitized_text = regex.sub("[REDACTED]", sanitized_text)

        # 检测系统提示泄露
        if "system prompt" in text.lower() or "系统提示" in text:
            threats.append({
                "type": ThreatType.SENSITIVE_DATA.value,
                "detail": "possible_system_prompt_leak",
                "severity": "critical",
            })

        if threats:
            self._sanitized_count += 1
            logger.warning("guardrail.output_sanitized",
                          agent=agent_did,
                          threats=[t["type"] for t in threats])
            return GuardrailAction.SANITIZE, sanitized_text, threats

        self._allowed_count += 1
        return GuardrailAction.ALLOW, text, threats

    def _detect_malicious_code(self, text: str) -> bool:
        """检测恶意代码模式"""
        malicious_patterns = [
            r"eval\s*\(",
            r"exec\s*\(",
            r"os\.system\s*\(",
            r"subprocess\.(call|run|Popen)",
            r"__import__\s*\(",
            r"<script[^>]*>",
            r"javascript:",
        ]
        for pattern in malicious_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _decide_action(self, threats: list[dict]) -> GuardrailAction:
        """根据威胁决定动作"""
        if not threats:
            return GuardrailAction.ALLOW

        # 任何critical威胁都阻止
        for t in threats:
            if t.get("severity") == "critical":
                return GuardrailAction.BLOCK

        # 多个high威胁升级审批
        high_count = sum(1 for t in threats if t.get("severity") == "high")
        if high_count >= 2:
            return GuardrailAction.ESCALATE

        # 单个high威胁阻止
        if high_count >= 1:
            return GuardrailAction.BLOCK

        return GuardrailAction.SANITIZE

    def get_stats(self) -> dict:
        return {
            "allowed": self._allowed_count,
            "blocked": self._blocked_count,
            "sanitized": self._sanitized_count,
            "block_rate": self._blocked_count / max(self._blocked_count + self._allowed_count + self._sanitized_count, 1),
        }


# 全局安全护栏单例
guardrails = SecurityGuardrails()
