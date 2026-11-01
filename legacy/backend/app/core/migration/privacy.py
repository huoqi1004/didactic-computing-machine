"""
数据脱敏与隐私工具（合规隐私模块）
====================================

P3-3 企业就绪阶段的数据合规能力，面向 GDPR / 个人信息保护法（PIPL）：
- ``DataMasker``：字段级数据脱敏（手机号/身份证/邮箱/姓名/银行卡号/自定义）
- ``detect_sensitive_fields``：基于字段名模式的敏感字段自动检测
- ``PrivacyPolicy``：导出合规策略（校验敏感数据是否脱敏、按策略统一脱敏）

设计要点：
- 脱敏函数对 ``None`` / 空字符串 / 短字符串 / 带分隔符格式均安全处理
- 全部为纯内存实现，离线可用，无任何外部依赖
"""
import re
import structlog

logger = structlog.get_logger()

# 敏感字段名匹配模式（子串匹配，忽略大小写）→ 默认脱敏类型
SENSITIVE_FIELD_PATTERNS: dict[str, str] = {
    "phone": "phone",
    "mobile": "phone",
    "tel": "phone",
    "contact_number": "phone",
    "email": "email",
    "mail": "email",
    "id_card": "id_card",
    "idcard": "id_card",
    "id_no": "id_card",
    "id_number": "id_card",
    "idnumber": "id_card",
    "cert_no": "id_card",
    "certificate_no": "id_card",
    "account": "account",
    "bank_account": "account",
    "card_no": "account",
    "card_number": "account",
    "bank_card": "account",
    "bankcard": "account",
    "name": "name",
    "address": "custom",
    "addr": "custom",
}


class DataMasker:
    """
    数据脱敏器

    支持脱敏类型：
    - ``phone``:   138****5678（保留前3后4，忽略分隔符）
    - ``id_card``: 1101**********1234（保留前4后4）
    - ``email``:   a***@x.com（保留用户名首字符 + 3个星号 + 域名）
    - ``name``:    张*（2位）/ 张*三（3位及以上，保留首尾）
    - ``account``: 6222 **** 1234（保留前4后4，空格分组）
    - ``custom``:  自定义（head_keep / tail_keep / mask_char）
    """

    def mask(self, value, mask_type: str, **kwargs):
        """
        对单个值执行脱敏。

        Args:
            value: 原始值（None / 空串 / 短字符串均安全处理）
            mask_type: phone | id_card | email | name | account | custom
            **kwargs: custom 类型时支持 head_keep/tail_keep/mask_char

        Returns:
            脱敏后的字符串；``None`` 输入返回 ``None``，空串返回空串。
        """
        if value is None:
            return None
        if isinstance(value, (int, float)):
            value = str(value)
        value = str(value).strip()
        if not value:
            return ""
        if mask_type == "phone":
            return self._mask_phone(value)
        if mask_type == "id_card":
            return self._mask_id_card(value)
        if mask_type == "email":
            return self._mask_email(value)
        if mask_type == "name":
            return self._mask_name(value)
        if mask_type == "account":
            return self._mask_account(value)
        if mask_type == "custom":
            return self._mask_custom(value, **kwargs)
        logger.warning("migration.unsupported_mask_type", mask_type=mask_type)
        raise ValueError(f"unsupported mask_type: {mask_type}")

    # ------------------------------------------------------------------
    # 各类型实现
    # ------------------------------------------------------------------

    def _mask_phone(self, value: str) -> str:
        """手机号：保留前3后4。容忍空格/横线/加号等分隔符；自动剥离国际区号。"""
        digits = re.sub(r"\D", "", value)
        if not digits:
            return ""
        # 超过 11 位视为带国际区号（如 +86），只取后 11 位
        if len(digits) > 11:
            digits = digits[-11:]
        if len(digits) <= 3:
            return "*" * len(digits)
        if len(digits) < 8:
            return digits[0] + "*" * (len(digits) - 1)
        return digits[:3] + "****" + digits[-4:]

    def _mask_id_card(self, value: str) -> str:
        """身份证号：保留前4后4，中间星号填充。"""
        if len(value) <= 4:
            return "*" * len(value)
        if len(value) < 9:
            return value[0] + "*" * (len(value) - 1)
        return value[:4] + "*" * (len(value) - 8) + value[-4:]

    def _mask_email(self, value: str) -> str:
        """邮箱：保留用户名首字符 + 3个星号 + @域名。"""
        if "@" not in value:
            # 无域名结构，退化为首字符保留 + 星号
            if len(value) <= 1:
                return "*" if value else ""
            return value[0] + "*" * min(3, len(value) - 1)
        local, _, domain = value.partition("@")
        if not local:
            return value
        if len(local) == 1:
            return "*@" + domain
        return local[0] + "***@" + domain

    def _mask_name(self, value: str) -> str:
        """姓名：2位保留首字符；3位及以上保留首尾，中间星号。"""
        if len(value) <= 1:
            return "*" if value else ""
        if len(value) == 2:
            return value[0] + "*"
        return value[0] + "*" * (len(value) - 2) + value[-1]

    def _mask_account(self, value: str) -> str:
        """银行卡号/账号：保留前4后4，中间以 ' **** ' 分组显示。"""
        digits = re.sub(r"\D", "", value)
        if not digits:
            return ""
        if len(digits) <= 4:
            return "*" * len(digits)
        if len(digits) < 8:
            return digits[:4] + " *" * (len(digits) - 4)
        return digits[:4] + " **** " + digits[-4:]

    def _mask_custom(self, value: str, head_keep: int = 0,
                     tail_keep: int = 0, mask_char: str = "*") -> str:
        """自定义脱敏：保留前 head_keep 位与后 tail_keep 位，其余替换。"""
        if not value:
            return ""
        total_keep = int(head_keep) + int(tail_keep)
        if len(value) <= total_keep:
            head = value[: int(head_keep)]
            return head + mask_char * max(1, len(value) - len(head))
        middle_len = len(value) - total_keep
        tail = value[-int(tail_keep):] if int(tail_keep) > 0 else ""
        return value[: int(head_keep)] + mask_char * middle_len + tail

    # ------------------------------------------------------------------
    # 批量脱敏
    # ------------------------------------------------------------------

    def _infer_mask_type(self, field: str) -> str:
        """根据字段名推断默认脱敏类型（无法识别时使用 custom 全遮蔽）。"""
        lowered = field.lower()
        for pattern, mask_type in SENSITIVE_FIELD_PATTERNS.items():
            if pattern in lowered:
                return mask_type
        return "custom"

    def mask_dict(self, data: dict, fields) -> dict:
        """
        批量脱敏一个字典。

        Args:
            data: 原始记录
            fields:
                - dict 形式：{字段名: mask_type}，按显式类型脱敏
                - list 形式：[字段名...]，按字段名自动推断脱敏类型

        Returns:
            脱敏后的新字典（不修改原字典）。
        """
        result = dict(data)
        if isinstance(fields, dict):
            for field, mask_type in fields.items():
                if field in result:
                    result[field] = self.mask(result[field], mask_type)
        else:
            for field in fields:
                if field in result:
                    result[field] = self.mask(result[field], self._infer_mask_type(field))
        logger.info("migration.masked_dict", fields=list(fields))
        return result

    def mask_list(self, records: list[dict], fields) -> list[dict]:
        """
        批量脱敏记录列表。

        Args:
            records: 记录列表
            fields: dict {字段: 类型} 或 list[字段名]

        Returns:
            脱敏后的记录列表（不修改原记录）。
        """
        return [self.mask_dict(rec, fields) for rec in records]


def detect_sensitive_fields(record: dict) -> list[str]:
    """
    自动检测记录中可能包含敏感信息的字段（字段名模式匹配）。

    匹配模式：phone / mobile / tel / email / mail / id_card / id_no /
    cert_no / account / card_no / name / address / addr 等。

    Args:
        record: 单条记录字典

    Returns:
        命中的敏感字段名列表（按记录键顺序）。
    """
    detected = []
    for field in record.keys():
        lowered = field.lower()
        for pattern in SENSITIVE_FIELD_PATTERNS:
            if pattern in lowered:
                detected.append(field)
                break
    return detected


class PrivacyPolicy:
    """
    合规策略（GDPR / 个人信息保护法 PIPL）

    职责：
    - ``check_exports``：校验导出请求中敏感数据是否已被脱敏
    - ``redact_for_export``：按策略对导出记录统一脱敏
    """

    # 内置策略：策略名 → 适用的敏感字段模式集合
    BUILTIN_POLICIES: dict[str, set[str]] = {
        "gdpr": {
            "phone", "mobile", "email", "mail", "name", "address",
            "account", "card_no", "id_card", "id_no",
        },
        "pipl": {
            "phone", "mobile", "email", "mail", "name", "address",
            "account", "card_no", "id_card", "id_no", "cert_no",
        },
    }

    def __init__(self, policy: str = "pipl"):
        """
        Args:
            policy: 合规策略名（gdpr / pipl），默认 pipl（个人信息保护法）
        """
        self.policy = policy
        self._masker = DataMasker()

    def check_exports(self, export_request: dict) -> dict:
        """
        校验导出请求是否包含未脱敏的敏感数据。

        Args:
            export_request: 形如
                {
                    "records": [ {...}, ... ],
                    "masked_fields": ["phone", ...] | None,
                }

        Returns:
            {
                "compliant": bool,
                "policy": str,
                "issues": [{"record_index", "field", "issue"}],
                "checked_records": int,
            }
        """
        records = export_request.get("records", [])
        masked = set(export_request.get("masked_fields") or [])
        issues = []
        for idx, rec in enumerate(records):
            for field in detect_sensitive_fields(rec):
                if field not in masked:
                    issues.append({
                        "record_index": idx,
                        "field": field,
                        "issue": "sensitive_field_not_masked",
                    })
        compliant = len(issues) == 0
        logger.info("migration.compliance_check",
                    policy=self.policy, compliant=compliant,
                    issues=len(issues), records=len(records))
        return {
            "compliant": compliant,
            "policy": self.policy,
            "issues": issues,
            "checked_records": len(records),
        }

    def redact_for_export(self, records: list[dict], policy=None) -> list[dict]:
        """
        按策略对导出记录统一脱敏。

        Args:
            records: 待导出记录
            policy:
                - dict 形式：{字段名: mask_type} 显式脱敏规则
                - str 形式：策略名（gdpr/pipl/auto），按敏感模式自动脱敏
                - None：等同 "auto"，自动检测并脱敏

        Returns:
            脱敏后的记录列表。
        """
        redacted = []
        for rec in records:
            if isinstance(policy, dict):
                redacted.append(self._masker.mask_dict(rec, policy))
            else:
                fields = detect_sensitive_fields(rec)
                if fields:
                    redacted.append(self._masker.mask_dict(rec, fields))
                else:
                    redacted.append(dict(rec))
        logger.info("migration.redacted_for_export",
                    records=len(records), policy=policy or "auto")
        return redacted
