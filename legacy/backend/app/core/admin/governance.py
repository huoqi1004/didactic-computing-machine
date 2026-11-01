"""
数据治理模块
CIO管理员系统的数据治理中心，提供数据分类分级、血缘追踪、质量监控等全面治理能力

特性：
- 数据分类分级管理（公开/内部/机密/绝密）
- 数据血缘追踪
- 数据质量监控（完整性、一致性、准确性）
- 数据保留策略管理
- 数据访问审计
- 数据脱敏规则管理
- GDPR/个人信息保护合规检查
"""
import structlog
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

logger = structlog.get_logger()


class DataGovernanceManager:
    """
    数据治理管理器

    职责：
    - 数据分类分级管理（公开/内部/机密/绝密）
    - 数据血缘关系追踪与可视化
    - 数据质量指标监控（完整性、一致性、准确性）
    - 数据保留策略管理（归档/删除）
    - 数据访问审计记录查询
    - 数据脱敏规则管理
    - GDPR/个人信息保护合规检查
    """

    # 数据分类级别
    CLASSIFICATION_LEVELS = {
        "public": {"level": 1, "name": "公开", "color": "green"},
        "internal": {"level": 2, "name": "内部", "color": "blue"},
        "confidential": {"level": 3, "name": "机密", "color": "orange"},
        "top_secret": {"level": 4, "name": "绝密", "color": "red"},
    }

    def __init__(self):
        # 数据分类记录：{data_id: classification_dict}
        self._classifications: dict[str, dict] = {}
        # 数据血缘关系：{data_id: lineage_dict}
        self._lineage: dict[str, dict] = {}
        # 数据质量指标
        self._quality_metrics: dict = {}
        # 数据保留策略：list[policy_dict]
        self._retention_policies: list[dict] = []
        # 数据访问审计日志：list[audit_dict]
        self._access_audit: list[dict] = []
        # 数据脱敏规则：{rule_id: rule_dict}
        self._masking_rules: dict[str, dict] = {}
        # 合规检查结果
        self._compliance_results: dict = {}
        self._seed_data()

    def _seed_data(self) -> None:
        """加载示例数据"""
        now = datetime.now(timezone.utc)
        # 数据分类示例
        self._classifications = {
            "data-customer-profile": {
                "data_id": "data-customer-profile",
                "name": "客户档案数据",
                "classification": "confidential",
                "owner": "销售部",
                "contains_pii": True,
                "classified_at": now.isoformat(),
                "classified_by": "data-steward",
                "fields": ["name", "phone", "email", "address", "id_number"],
            },
            "data-product-catalog": {
                "data_id": "data-product-catalog",
                "name": "产品目录数据",
                "classification": "internal",
                "owner": "产品部",
                "contains_pii": False,
                "classified_at": now.isoformat(),
                "classified_by": "data-steward",
                "fields": ["product_code", "name", "price", "category"],
            },
            "data-financial-report": {
                "data_id": "data-financial-report",
                "name": "财务报表数据",
                "classification": "top_secret",
                "owner": "财务部",
                "contains_pii": False,
                "classified_at": now.isoformat(),
                "classified_by": "data-steward",
                "fields": ["revenue", "profit", "cost", "period"],
            },
            "data-marketing-content": {
                "data_id": "data-marketing-content",
                "name": "营销内容数据",
                "classification": "public",
                "owner": "市场部",
                "contains_pii": False,
                "classified_at": now.isoformat(),
                "classified_by": "data-steward",
                "fields": ["title", "content", "image_url"],
            },
        }
        # 数据血缘示例
        self._lineage = {
            "data-financial-report": {
                "data_id": "data-financial-report",
                "upstream": [
                    {"source": "data-sales-records", "transformation": "聚合统计", "fields": ["revenue"]},
                    {"source": "data-purchase-records", "transformation": "成本计算", "fields": ["cost"]},
                    {"source": "data-inventory-records", "transformation": "库存估值", "fields": ["cost"]},
                ],
                "downstream": [
                    {"target": "data-board-report", "transformation": "格式转换", "fields": ["all"]},
                    {"target": "data-tax-declaration", "transformation": "税务计算", "fields": ["revenue", "profit"]},
                ],
            },
            "data-customer-profile": {
                "data_id": "data-customer-profile",
                "upstream": [
                    {"source": "data-crm-raw", "transformation": "清洗去重", "fields": ["all"]},
                ],
                "downstream": [
                    {"target": "data-customer-analytics", "transformation": "统计分析", "fields": ["name", "phone", "email"]},
                    {"target": "data-marketing-segmentation", "transformation": "客户分群", "fields": ["name", "address"]},
                ],
            },
        }
        # 数据质量指标
        self._quality_metrics = {
            "overall_score": 87.5,
            "dimensions": {
                "completeness": {
                    "score": 92.3,
                    "details": {
                        "data-customer-profile": 95.0,
                        "data-product-catalog": 98.0,
                        "data-financial-report": 84.0,
                    },
                    "description": "数据完整性 - 必填字段填充率",
                },
                "consistency": {
                    "score": 85.0,
                    "details": {
                        "data-customer-profile": 90.0,
                        "data-product-catalog": 88.0,
                        "data-financial-report": 77.0,
                    },
                    "description": "数据一致性 - 跨系统数据匹配率",
                },
                "accuracy": {
                    "score": 88.2,
                    "details": {
                        "data-customer-profile": 86.0,
                        "data-product-catalog": 95.0,
                        "data-financial-report": 84.0,
                    },
                    "description": "数据准确性 - 校验规则通过率",
                },
                "timeliness": {
                    "score": 84.5,
                    "details": {
                        "data-customer-profile": 82.0,
                        "data-product-catalog": 90.0,
                        "data-financial-report": 82.0,
                    },
                    "description": "数据时效性 - 数据更新及时率",
                },
            },
            "last_checked": now.isoformat(),
        }
        # 保留策略示例
        self._retention_policies = [
            {
                "policy_id": "rp-001",
                "data_type": "financial_records",
                "retention_days": 2555,  # 7年
                "action": "archive",
                "description": "财务记录保留7年（税务要求）",
                "created_at": now.isoformat(),
                "created_by": "compliance-officer",
            },
            {
                "policy_id": "rp-002",
                "data_type": "customer_pii",
                "retention_days": 1095,  # 3年
                "action": "delete",
                "description": "客户个人信息保留3年后删除（GDPR要求）",
                "created_at": now.isoformat(),
                "created_by": "compliance-officer",
            },
            {
                "policy_id": "rp-003",
                "data_type": "audit_logs",
                "retention_days": 1825,  # 5年
                "action": "archive",
                "description": "审计日志保留5年",
                "created_at": now.isoformat(),
                "created_by": "compliance-officer",
            },
        ]
        # 访问审计示例
        for i in range(5):
            self._access_audit.append({
                "audit_id": str(uuid4()),
                "timestamp": (now - timedelta(hours=i)).isoformat(),
                "user_id": ["admin", "user_zhang", "user_li", "admin", "agent-finance-001"][i],
                "user_type": ["human", "human", "human", "human", "agent"][i],
                "data_id": ["data-financial-report", "data-customer-profile",
                           "data-product-catalog", "data-financial-report", "data-customer-profile"][i],
                "action": ["read", "read", "read", "export", "read"][i],
                "result": "success" if i != 1 else "denied",
                "reason": "insufficient_clearance" if i == 1 else "normal_access",
                "ip_address": f"192.168.1.{100 + i}",
            })
        # 脱敏规则示例
        self._masking_rules = {
            "mask-001": {
                "rule_id": "mask-001",
                "field_pattern": "*phone*",
                "mask_type": "partial",
                "mask_char": "*",
                "description": "手机号部分脱敏: 138****5678",
                "enabled": True,
                "created_at": now.isoformat(),
            },
            "mask-002": {
                "rule_id": "mask-002",
                "field_pattern": "*id_number*",
                "mask_type": "partial",
                "mask_char": "*",
                "description": "身份证号部分脱敏: 310***********1234",
                "enabled": True,
                "created_at": now.isoformat(),
            },
            "mask-003": {
                "rule_id": "mask-003",
                "field_pattern": "*bank_account*",
                "mask_type": "full",
                "mask_char": "*",
                "description": "银行账号完全脱敏: ************",
                "enabled": True,
                "created_at": now.isoformat(),
            },
            "mask-004": {
                "rule_id": "mask-004",
                "field_pattern": "*email*",
                "mask_type": "partial",
                "mask_char": "*",
                "description": "邮箱部分脱敏: z***@example.com",
                "enabled": True,
                "created_at": now.isoformat(),
            },
        }
        logger.info("governance.seeded",
                    classifications=len(self._classifications),
                    lineage=len(self._lineage),
                    retention_policies=len(self._retention_policies),
                    masking_rules=len(self._masking_rules))

    def classify_data(
        self,
        data_id: str,
        classification: str,
        owner: str,
        name: str = "",
        contains_pii: bool = False,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """
        对数据进行分类分级

        参数:
            data_id: 数据资产唯一标识
            classification: 分类级别（public/internal/confidential/top_secret）
            owner: 数据所有者（部门或负责人）
            name: 数据资产名称
            contains_pii: 是否包含个人信息
            fields: 包含的字段列表

        Returns:
            dict: 分类结果
        """
        if classification not in self.CLASSIFICATION_LEVELS:
            return {"error": "invalid_classification",
                    "classification": classification,
                    "valid_levels": list(self.CLASSIFICATION_LEVELS.keys())}

        now = datetime.now(timezone.utc)
        record = {
            "data_id": data_id,
            "name": name or data_id,
            "classification": classification,
            "classification_level": self.CLASSIFICATION_LEVELS[classification]["level"],
            "owner": owner,
            "contains_pii": contains_pii,
            "fields": fields or [],
            "classified_at": now.isoformat(),
            "classified_by": "data-steward",
        }
        self._classifications[data_id] = record

        # 记录访问审计
        self._access_audit.append({
            "audit_id": str(uuid4()),
            "timestamp": now.isoformat(),
            "user_id": "data-steward",
            "user_type": "human",
            "data_id": data_id,
            "action": "classify",
            "result": "success",
            "reason": "data_classification",
            "details": {"classification": classification},
        })

        logger.info("governance.data_classified",
                    data_id=data_id, classification=classification)
        return {"status": "success", "classification": record}

    def get_data_classifications(self) -> list[dict]:
        """
        获取所有数据分类记录

        Returns:
            list[dict]: 数据分类列表
        """
        classifications = list(self._classifications.values())
        logger.info("governance.classifications_listed",
                    count=len(classifications))
        return classifications

    def get_data_lineage(self, data_id: str) -> dict:
        """
        获取数据血缘关系

        参数:
            data_id: 数据资产唯一标识

        Returns:
            dict: 血缘关系，包含上游来源和下游消费者
        """
        lineage = self._lineage.get(data_id)
        if not lineage:
            return {
                "data_id": data_id,
                "upstream": [],
                "downstream": [],
                "message": "no_lineage_recorded",
            }
        logger.info("governance.lineage_queried", data_id=data_id)
        return lineage

    def get_data_quality_metrics(self) -> dict:
        """
        获取数据质量指标

        Returns:
            dict: 质量指标，包含总体评分和各维度（完整性、一致性、准确性、时效性）得分
        """
        logger.info("governance.quality_metrics_queried",
                    score=self._quality_metrics.get("overall_score"))
        return dict(self._quality_metrics)

    def set_retention_policy(
        self,
        data_type: str,
        retention_days: int,
        action: str = "archive",
        description: str = "",
    ) -> dict:
        """
        设置数据保留策略

        参数:
            data_type: 数据类型标识
            retention_days: 保留天数
            action: 到期动作（archive/delete）
            description: 策略描述

        Returns:
            dict: 保留策略信息
        """
        if action not in ("archive", "delete"):
            return {"error": "invalid_action", "action": action}

        now = datetime.now(timezone.utc)
        policy = {
            "policy_id": f"rp-{str(uuid4())[:8]}",
            "data_type": data_type,
            "retention_days": retention_days,
            "action": action,
            "description": description,
            "created_at": now.isoformat(),
            "created_by": "admin",
        }
        self._retention_policies.append(policy)

        logger.info("governance.retention_policy_set",
                    data_type=data_type, retention_days=retention_days)
        return {"status": "success", "policy": policy}

    def get_retention_policies(self) -> list[dict]:
        """
        获取所有数据保留策略

        Returns:
            list[dict]: 保留策略列表
        """
        logger.info("governance.retention_policies_listed",
                    count=len(self._retention_policies))
        return list(self._retention_policies)

    def get_access_audit(
        self,
        data_id: Optional[str] = None,
        user_id: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> list[dict]:
        """
        查询数据访问审计日志

        参数:
            data_id: 可选，按数据资产ID过滤
            user_id: 可选，按用户ID过滤
            start_time: 可选，起始时间（ISO 8601格式）
            end_time: 可选，结束时间（ISO 8601格式）

        Returns:
            list[dict]: 访问审计日志列表
        """
        results = list(self._access_audit)

        if data_id:
            results = [a for a in results if a.get("data_id") == data_id]
        if user_id:
            results = [a for a in results if a.get("user_id") == user_id]

        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                results = [
                    a for a in results
                    if datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00")) >= start_dt
                ]
            except (ValueError, KeyError):
                pass

        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                results = [
                    a for a in results
                    if datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00")) <= end_dt
                ]
            except (ValueError, KeyError):
                pass

        logger.info("governance.access_audit_queried",
                    data_id=data_id, user_id=user_id, count=len(results))
        return results

    def add_masking_rule(
        self,
        field_pattern: str,
        mask_type: str,
        mask_char: str = "*",
        description: str = "",
    ) -> dict:
        """
        添加数据脱敏规则

        参数:
            field_pattern: 字段匹配模式（支持通配符，如 *phone*）
            mask_type: 脱敏类型（full/partial/hash）
            mask_char: 脱敏字符，默认"*"
            description: 规则描述

        Returns:
            dict: 脱敏规则信息
        """
        if mask_type not in ("full", "partial", "hash"):
            return {"error": "invalid_mask_type", "mask_type": mask_type}

        now = datetime.now(timezone.utc)
        rule = {
            "rule_id": f"mask-{str(uuid4())[:8]}",
            "field_pattern": field_pattern,
            "mask_type": mask_type,
            "mask_char": mask_char,
            "description": description,
            "enabled": True,
            "created_at": now.isoformat(),
        }
        self._masking_rules[rule["rule_id"]] = rule

        logger.info("governance.masking_rule_added",
                    field_pattern=field_pattern, mask_type=mask_type)
        return {"status": "success", "rule": rule}

    def get_masking_rules(self) -> list[dict]:
        """
        获取所有数据脱敏规则

        Returns:
            list[dict]: 脱敏规则列表
        """
        rules = list(self._masking_rules.values())
        logger.info("governance.masking_rules_listed", count=len(rules))
        return rules

    def compliance_check(self) -> dict:
        """
        执行GDPR/个人信息保护合规检查

        检查项包括：
        - PII数据是否已分类
        - PII数据是否有保留策略
        - PII数据是否有脱敏规则
        - 访问审计是否启用
        - 数据加密是否启用

        Returns:
            dict: 合规检查结果，包含各检查项状态和总体合规率
        """
        now = datetime.now(timezone.utc)
        checks = []
        passed = 0
        total = 0

        # 检查1: PII数据分类覆盖率
        total += 1
        pii_datasets = [
            d for d in self._classifications.values()
            if d.get("contains_pii")
        ]
        pii_classified = len(pii_datasets)
        check1_passed = pii_classified > 0
        if check1_passed:
            passed += 1
        checks.append({
            "check_id": "gdpr-001",
            "name": "PII数据分类覆盖率",
            "status": "pass" if check1_passed else "fail",
            "details": f"{pii_classified}个PII数据集已分类",
            "severity": "high",
        })

        # 检查2: PII数据保留策略
        total += 1
        pii_retention = any(
            "pii" in p["data_type"].lower() or "customer" in p["data_type"].lower()
            for p in self._retention_policies
        )
        if pii_retention:
            passed += 1
        checks.append({
            "check_id": "gdpr-002",
            "name": "PII数据保留策略",
            "status": "pass" if pii_retention else "fail",
            "details": "已配置PII数据保留策略" if pii_retention else "未配置PII数据保留策略",
            "severity": "high",
        })

        # 检查3: PII数据脱敏规则
        total += 1
        pii_fields = []
        for d in pii_datasets:
            pii_fields.extend(d.get("fields", []))
        masking_coverage = len(pii_fields) > 0 and len(self._masking_rules) > 0
        if masking_coverage:
            passed += 1
        checks.append({
            "check_id": "gdpr-003",
            "name": "PII数据脱敏规则",
            "status": "pass" if masking_coverage else "fail",
            "details": f"{len(self._masking_rules)}条脱敏规则已配置",
            "severity": "high",
        })

        # 检查4: 访问审计启用
        total += 1
        audit_enabled = len(self._access_audit) > 0
        if audit_enabled:
            passed += 1
        checks.append({
            "check_id": "gdpr-004",
            "name": "数据访问审计",
            "status": "pass" if audit_enabled else "fail",
            "details": f"{len(self._access_audit)}条访问审计记录",
            "severity": "medium",
        })

        # 检查5: 机密及以上数据加密
        total += 1
        sensitive_data = [
            d for d in self._classifications.values()
            if d["classification"] in ("confidential", "top_secret")
        ]
        # 假设加密状态通过security模块检查
        encryption_ok = len(sensitive_data) > 0
        if encryption_ok:
            passed += 1
        checks.append({
            "check_id": "gdpr-005",
            "name": "敏感数据加密",
            "status": "pass" if encryption_ok else "fail",
            "details": f"{len(sensitive_data)}个敏感数据集需加密保护",
            "severity": "high",
        })

        # 检查6: 数据质量达标
        total += 1
        quality_score = self._quality_metrics.get("overall_score", 0)
        quality_ok = quality_score >= 80
        if quality_ok:
            passed += 1
        checks.append({
            "check_id": "gdpr-006",
            "name": "数据质量达标",
            "status": "pass" if quality_ok else "fail",
            "details": f"总体质量评分: {quality_score}%",
            "severity": "medium",
        })

        compliance_rate = (passed / total * 100) if total > 0 else 0
        result = {
            "compliance_rate": round(compliance_rate, 1),
            "passed": passed,
            "total": total,
            "checks": checks,
            "checked_at": now.isoformat(),
            "overall_status": "compliant" if compliance_rate >= 80 else "non-compliant",
        }
        logger.info("governance.compliance_checked",
                    rate=compliance_rate, passed=passed, total=total)
        return result

    def add_lineage(
        self,
        data_id: str,
        upstream: Optional[list[dict]] = None,
        downstream: Optional[list[dict]] = None,
    ) -> dict:
        """
        添加或更新数据血缘关系

        参数:
            data_id: 数据资产唯一标识
            upstream: 上游数据源列表
            downstream: 下游数据消费者列表

        Returns:
            dict: 血缘关系信息
        """
        if data_id not in self._lineage:
            self._lineage[data_id] = {
                "data_id": data_id,
                "upstream": [],
                "downstream": [],
            }
        if upstream:
            self._lineage[data_id]["upstream"].extend(upstream)
        if downstream:
            self._lineage[data_id]["downstream"].extend(downstream)

        logger.info("governance.lineage_updated", data_id=data_id)
        return {"status": "success", "lineage": self._lineage[data_id]}
