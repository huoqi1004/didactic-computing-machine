"""
RBAC 种子数据
定义 23 个角色、全量权限定义（34 资源 × 6 动作）、角色-权限映射、
16 个部门、24 个默认用户账号。

角色层级（L1-L5）：
    L1  董事长           chairman
    L2  CEO              ceo
    L3  CTO/CFO/CIO/CMO/CHO/CRO/COO
    L4  各部门经理        fin_manager / legal_manager / ...
    L5  各部门员工        dept_staff

权限模型：``资源:动作`` 二维权限，如 ``accounting:read`` / ``treasury:approve``
动作类型：read / create / update / delete / approve / manage

种子密码治理（评估报告 D-04）：种子用户不再使用"{username}123"规律弱密码，
默认为每用户随机生成强密码并在首次播种后一次性打印到服务端日志；
也可通过 ERP_SEED_PASSWORD 统一注入（仅限演示/联调环境）。
所有种子用户标记 must_change_password，首次登录响应要求改密。
"""
import secrets

import structlog

from app.core.auth import PasswordHasher
from app.core.config import settings

logger = structlog.get_logger()


# ===================================================================
# 种子密码治理（D-04）
# ===================================================================

# 随机模式下的初始密码表：username -> 明文（仅在用户实际新播种时打印一次）
SEED_PASSWORDS: dict[str, str] = {}


def _seed_password_hash(username: str) -> str:
    """为种子用户生成密码哈希（杜绝"{username}123"规律弱密码）。

    - ``ERP_SEED_PASSWORD`` 已设置：全部种子用户共用该密码（演示/联调用）；
    - 未设置：每个用户独立随机生成 12 字符强密码，明文记入
      ``SEED_PASSWORDS``，由 ``print_seed_credentials`` 在新用户实际
      播种后一次性输出（重复启动不重复打印，避免与库内密码混淆）。
    """
    if settings.seed_password:
        return PasswordHasher.hash_password(settings.seed_password)
    password = secrets.token_urlsafe(9)
    SEED_PASSWORDS[username] = password
    return PasswordHasher.hash_password(password)


def print_seed_credentials(usernames: list[str] | None = None) -> None:
    """一次性打印种子用户初始密码（仅对新播种用户调用）。

    :param usernames: 实际新播种的用户名列表；None 表示打印全部已知密码。
    """
    if settings.seed_password:
        logger.warning(
            "rbac.seed_credentials_shared",
            message="种子用户使用 ERP_SEED_PASSWORD 统一初始密码，请尽快修改",
            users=usernames if usernames is not None else [],
        )
        return
    targets = usernames if usernames is not None else list(SEED_PASSWORDS.keys())
    credentials = {u: SEED_PASSWORDS[u] for u in targets if u in SEED_PASSWORDS}
    if not credentials:
        return
    logger.warning(
        "rbac.seed_credentials_one_time",
        message="以下种子用户为随机初始密码（仅此一次打印，请立即保存并转交用户）",
        credentials=credentials,
    )


# ===================================================================
# 资源定义
# ===================================================================

RESOURCES: dict[str, str] = {
    "system": "系统管理",
    "agent": "Agent管理",
    "security": "安全管理",
    "inventory": "库存管理",
    "accounting": "会计凭证",
    "invoice": "发票管理",
    "reimbursement": "报销管理",
    "tax": "税务管理",
    "procurement": "采购管理",
    "treasury": "资金管理",
    "production": "生产管理",
    "audit": "内审管理",
    "compliance": "合规管理",
    "user": "用户管理",
    "role": "角色管理",
    "permission": "权限管理",
    "crm": "客户关系管理",
    "sales": "销售管理",
    "marketing": "市场营销",
    "customer_service": "客户服务",
    "hr": "人力资源",
    "payroll": "薪酬管理",
    "recruitment": "招聘管理",
    "performance": "绩效管理",
    "contract": "合同管理",
    "legal": "法务管理",
    "risk": "风险管理",
    "quality": "质量管理",
    "asset": "资产管理",
    "project": "项目管理",
    "budget": "预算管理",
    "bi": "商业智能",
    "esg": "ESG管理",
    "customization": "Agent定制",
    "knowledge": "知识管理",
    "admin": "行政管理",
    "document": "文档管理",
    "message": "内部通讯",
}

ACTIONS: list[str] = ["read", "create", "update", "delete", "approve", "manage"]

# 高危资源：这些资源的 delete/approve/manage 操作自动标记为 high_risk
_CRITICAL_RESOURCES: set[str] = {
    "treasury", "permission", "security", "system",
    "agent", "user", "role", "audit",
    "payroll",  # 薪酬数据属企业最高敏数据（评估报告 D-01）
    "customization",  # Agent 定制：放宽类变更可降低审批强度（Phase F1）
}


def _sensitivity(resource: str, action: str) -> str:
    """根据资源和动作计算权限敏感度"""
    if action == "read":
        return "normal"
    if action in ("delete", "manage"):
        return "high_risk"
    if action == "approve" and resource in _CRITICAL_RESOURCES:
        return "high_risk"
    return "sensitive"


# ===================================================================
# 全量权限定义（35 资源 × 6 动作 = 210 条权限）
# ===================================================================

SEED_PERMISSIONS: list[dict] = [
    {
        "permission_id": f"{resource}:{action}",
        "resource": resource,
        "action": action,
        "description": f"{desc} - {action}",
        "sensitivity": _sensitivity(resource, action),
    }
    for resource, desc in RESOURCES.items()
    for action in ACTIONS
]

# 内部通讯模块的扩展权限（非标准动作 file / command）
SEED_PERMISSIONS.extend([
    {
        "permission_id": "message:file",
        "resource": "message",
        "action": "file",
        "description": "内部通讯 - file（收发文件）",
        "sensitivity": "sensitive",
    },
    {
        "permission_id": "message:command",
        "resource": "message",
        "action": "command",
        "description": "内部通讯 - command（发送Agent指令）",
        "sensitivity": "high_risk",
    },
])

# Agent 定制平台的扩展权限（附录 B 非标准动作 publish / export，Phase F3）
SEED_PERMISSIONS.extend([
    {
        "permission_id": "customization:publish",
        "resource": "customization",
        "action": "publish",
        "description": "Agent定制 - publish（工作流/Skill 发布、环境晋升、回滚）",
        "sensitivity": "high_risk",
    },
    {
        "permission_id": "customization:export",
        "resource": "customization",
        "action": "export",
        "description": "Agent定制 - export（配置快照导出）",
        "sensitivity": "sensitive",
    },
])

# 可观测与沙箱回滚平台的扩展权限（附录 B，roadmap-logging-sandbox G3/G4/G5）
SEED_PERMISSIONS.extend([
    {
        "permission_id": "operationlog:read",
        "resource": "operationlog",
        "action": "read",
        "description": "操作日志 - read（查询操作日志/回滚账本）",
        "sensitivity": "sensitive",
    },
    {
        "permission_id": "operationlog:export",
        "resource": "operationlog",
        "action": "export",
        "description": "操作日志 - export（CSV 导出，含敏感面；导出行为自身审计）",
        "sensitivity": "high_risk",
    },
    {
        "permission_id": "operation_rollback:read",
        "resource": "operation_rollback",
        "action": "read",
        "description": "操作回滚 - read（回滚账本查询）",
        "sensitivity": "sensitive",
    },
    {
        "permission_id": "operation_rollback:execute",
        "resource": "operation_rollback",
        "action": "execute",
        "description": "操作回滚 - execute（发起回滚，仍需 write_high 审批人批准）",
        "sensitivity": "high_risk",
    },
    {
        "permission_id": "audit_verify:run",
        "resource": "audit_verify",
        "action": "run",
        "description": "审计链 - run（手动触发完整性校验/查看报告）",
        "sensitivity": "sensitive",
    },
])


# ===================================================================
# 23 个角色定义
# ===================================================================

SEED_ROLES: list[dict] = [
    # ---- 可观测平台角色（附录 B：审计与操作分离）----
    {
        "role_id": "role_027",
        "role_code": "log_auditor",
        "role_name": "日志审计员",
        "level": 2,
        "parent_role_id": "role_001",
        "description": "只读操作日志/回滚账本 + 审计链校验报告，无任何业务操作权（内控审计与操作分离）",
    },
    # ---- Phase F1：Agent 定制平台角色（附录 B）----
    {
        "role_id": "role_025",
        "role_code": "customization_admin",
        "role_name": "定制平台管理员",
        "level": 2,
        "parent_role_id": "role_001",
        "description": "Agent 定制平台全域管理：工具启停/风险分级/阈值/工作流/Skill/MCP",
    },
    {
        "role_id": "role_026",
        "role_code": "customization_approver",
        "role_name": "定制变更审批人",
        "level": 1,
        "parent_role_id": "role_001",
        "description": "放宽类定制变更的审批（administrator 之外角色，防自我审批）",
    },

    # ---- L1 ----
    {
        "role_id": "role_001",
        "role_code": "chairman",
        "role_name": "董事长",
        "level": 1,
        "parent_role_id": None,
        "description": "战略决策、全局监督、重大事项终审",
    },
    # ---- L2 ----
    {
        "role_id": "role_002",
        "role_code": "ceo",
        "role_name": "CEO",
        "level": 2,
        "parent_role_id": "role_001",
        "description": "全面经营管理、跨部门协调、预算审批",
    },
    # ---- L3: C-level ----
    {
        "role_id": "role_003",
        "role_code": "cto",
        "role_name": "CTO",
        "level": 3,
        "parent_role_id": "role_002",
        "description": "技术战略、系统架构、技术团队管理",
    },
    {
        "role_id": "role_004",
        "role_code": "cfo",
        "role_name": "CFO",
        "level": 3,
        "parent_role_id": "role_002",
        "description": "财务战略、预算管控、资金审批",
    },
    {
        "role_id": "role_005",
        "role_code": "cio",
        "role_name": "CIO",
        "level": 3,
        "parent_role_id": "role_002",
        "description": "信息系统、数据安全、合规审计",
    },
    {
        "role_id": "role_006",
        "role_code": "cmo",
        "role_name": "CMO",
        "level": 3,
        "parent_role_id": "role_002",
        "description": "市场战略、品牌管理、营销规划",
    },
    {
        "role_id": "role_007",
        "role_code": "cho",
        "role_name": "CHO",
        "level": 3,
        "parent_role_id": "role_002",
        "description": "人力资源战略、组织发展、人才管理",
    },
    {
        "role_id": "role_008",
        "role_code": "cro",
        "role_name": "CRO",
        "level": 3,
        "parent_role_id": "role_002",
        "description": "风险管理、内控体系、合规监督",
    },
    {
        "role_id": "role_009",
        "role_code": "coo",
        "role_name": "COO",
        "level": 3,
        "parent_role_id": "role_002",
        "description": "运营管理、跨部门协调、生产监督",
    },
    # ---- L4: 部门经理 ----
    {
        "role_id": "role_010",
        "role_code": "fin_manager",
        "role_name": "财务部经理",
        "level": 4,
        "parent_role_id": "role_004",
        "description": "日常账务、凭证审批、税务申报",
    },
    {
        "role_id": "role_011",
        "role_code": "legal_manager",
        "role_name": "法务部经理",
        "level": 4,
        "parent_role_id": "role_008",
        "description": "合同审查、合规检查、法律风险评估",
    },
    {
        "role_id": "role_012",
        "role_code": "admin_manager",
        "role_name": "行政部经理",
        "level": 4,
        "parent_role_id": "role_009",
        "description": "行政采购、资产管理、日常运营",
    },
    {
        "role_id": "role_013",
        "role_code": "hr_manager",
        "role_name": "人事部经理",
        "level": 4,
        "parent_role_id": "role_007",
        "description": "员工管理、薪酬核算、绩效评估",
    },
    {
        "role_id": "role_014",
        "role_code": "prod_manager",
        "role_name": "产品部经理",
        "level": 4,
        "parent_role_id": "role_003",
        "description": "产品规划、需求分析、市场预测",
    },
    {
        "role_id": "role_015",
        "role_code": "tech_manager",
        "role_name": "技术部经理",
        "level": 4,
        "parent_role_id": "role_003",
        "description": "系统开发、技术支持、运维管理",
    },
    {
        "role_id": "role_016",
        "role_code": "mfg_manager",
        "role_name": "生产部经理",
        "level": 4,
        "parent_role_id": "role_009",
        "description": "生产计划、BOM管理、质量控制",
    },
    {
        "role_id": "role_017",
        "role_code": "proc_manager",
        "role_name": "采购部经理",
        "level": 4,
        "parent_role_id": "role_009",
        "description": "采购执行、供应商管理、交期跟踪",
    },
    {
        "role_id": "role_018",
        "role_code": "biz_manager",
        "role_name": "业务部经理",
        "level": 4,
        "parent_role_id": "role_002",
        "description": "客户管理、销售执行、业务拓展",
    },
    {
        "role_id": "role_019",
        "role_code": "sales_manager",
        "role_name": "销售部经理",
        "level": 4,
        "parent_role_id": "role_006",
        "description": "销售管理、客户开发、合同执行",
    },
    {
        "role_id": "role_020",
        "role_code": "mkt_manager",
        "role_name": "市场部经理",
        "level": 4,
        "parent_role_id": "role_006",
        "description": "市场推广、品牌运营、营销活动",
    },
    {
        "role_id": "role_021",
        "role_code": "cs_manager",
        "role_name": "客服部经理",
        "level": 4,
        "parent_role_id": "role_006",
        "description": "客户服务、投诉处理、客户满意度",
    },
    {
        "role_id": "role_022",
        "role_code": "quality_manager",
        "role_name": "质量部经理",
        "level": 4,
        "parent_role_id": "role_009",
        "description": "质量管理、质量检验、质量改进",
    },
    {
        "role_id": "role_023",
        "role_code": "pm_manager",
        "role_name": "项目部经理",
        "level": 4,
        "parent_role_id": "role_003",
        "description": "项目管理、项目规划、项目执行",
    },
    # ---- L5 ----
    {
        "role_id": "role_024",
        "role_code": "dept_staff",
        "role_name": "各部门员工",
        "level": 5,
        "parent_role_id": None,
        "description": "本部门日常操作（按部门动态分配）",
    },
]


# ===================================================================
# 角色-权限映射矩阵
# 格式: {role_code: {resource: {action1, action2, ...}}}
# ===================================================================

# 所有动作的简写
_ALL = {"read", "create", "update", "delete", "approve", "manage"}
_R = {"read"}
_RC = {"read", "create"}
_RCU = {"read", "create", "update"}
_RA = {"read", "approve"}

# 内部通讯权限动作集（含非标准动作 file / command）
_MSG_ALL = {"read", "create", "manage", "file", "command"}

ROLE_PERMISSION_MATRIX: dict[str, dict[str, set[str]]] = {
    # ---- chairman (L1): 全局只读 + 审计/合规/权限/预算/风险审批 ----
    "chairman": {
        **{r: _R for r in RESOURCES},  # 全部资源只读
        "audit": _RA,
        "compliance": _RA,
        "permission": _RA,
        "budget": _RA,
        "risk": _RA,
    },

    # ---- ceo (L2): 全部读写审批 + 系统/Agent/用户/预算全权 ----
    # P1-5 整改（production-acceptance-review-2026-10-03，决策留痕）：
    # ceo 的 system 从 _ALL 收紧为只读——管理台（system:manage 守卫）操作权
    # 收归 cio，杜绝"兜底通配放大越权面"；ceo 保留全局业务可见性。
    # 如需恢复按域/按人授权，走定制中心放宽审批（红线 2），不走种子回改。
    "ceo": {
        **{r: {"read", "create", "update", "approve"} for r in RESOURCES},
        "system": {"read"},
        "agent": {"read"},
        "user": _ALL,
        "role": _ALL,
        "budget": _ALL,
        "bi": _ALL,
        "admin": _ALL,
        "permission": _RA,
    },

    # ---- cto (L3): 技术/系统/Agent/生产/质量/项目全权 ----
    "cto": {
        "system": _ALL,
        "agent": _ALL,
        "security": _R,
        "production": _ALL,
        "quality": _ALL,
        "project": _ALL,
        "inventory": _R,
        "asset": _R,
        "bi": _R,
        "knowledge": _R,
        "document": _RCU,
        "reimbursement": _RC,
    },

    # ---- cfo (L3): 财务/税务/资金/发票/报销/预算全权 + 薪酬只读 ----
    "cfo": {
        "accounting": _ALL,
        "invoice": _ALL,
        "reimbursement": _ALL,
        "tax": _ALL,
        "treasury": _ALL,
        "budget": _ALL,
        "payroll": _R,
        "procurement": _R,
        "audit": _R,
        "compliance": _R,
        "contract": _RA,
        "risk": _R,
        "bi": _R,
        "asset": _R,
        "document": _RC,
    },

    # ---- cio (L3): 安全/审计/合规/用户/权限全权 + 全模块只读 ----
    "cio": {
        "system": _ALL,
        "security": _ALL,
        "audit": _ALL,
        "compliance": _ALL,
        "user": _ALL,
        "role": _ALL,
        "permission": _ALL,
        "admin": _ALL,
        "agent": _ALL,
        "bi": _ALL,
        "knowledge": _ALL,
        "risk": _ALL,
        "esg": _ALL,
        "document": _ALL,
        "reimbursement": _RC,
        # Phase 5: 运营战略全模块
        "legal": _RC,
        "contract": _R,
        "quality": _R,
        "asset": _R,
        "project": _R,
        "budget": _RA,
        # 业务模块只读
        "inventory": _R,
        "accounting": _R,
        "invoice": _R,
        "tax": _R,
        "procurement": _R,
        "treasury": _R,
        "production": _R,
        "hr": _R,
        "recruitment": _R,
        "performance": _R,
        "crm": _R,
        "sales": _R,
        "marketing": _R,
        "customer_service": _R,
    },

    # ---- cmo (L3): 市场/销售/CRM/客服全权 ----
    "cmo": {
        "crm": _ALL,
        "sales": _ALL,
        "marketing": _ALL,
        "customer_service": _ALL,
        "bi": _R,
        "budget": _RA,
        "contract": _R,
        "document": _RC,
        "reimbursement": _RC,
    },

    # ---- cho (L3): 人力资源/招聘/绩效/薪酬全权 ----
    "cho": {
        "hr": _ALL,
        "recruitment": _ALL,
        "performance": _ALL,
        "payroll": _ALL,
        "reimbursement": _RA,
        "budget": _R,
        "bi": _R,
        "user": _R,
        "document": _RC,
    },

    # ---- cro (L3): 风险/合规/内审全权 ----
    "cro": {
        "risk": _ALL,
        "compliance": _ALL,
        "audit": _ALL,
        "contract": _RA,
        "legal": _R,
        "security": _R,
        "bi": _R,
        "document": _RC,
        "reimbursement": _RC,
    },

    # ---- coo (L3): 运营/生产/采购/库存/资产/项目审批 ----
    "coo": {
        "production": _RA,
        "procurement": _RA,
        "inventory": _RA,
        "asset": _RA,
        "project": _RA,
        "quality": _RA,
        "admin": _ALL,
        "bi": _R,
        "system": _R,
        "agent": _R,
        "document": _RC,
        "reimbursement": _RC,
    },

    # ---- fin_manager (L4): 会计/发票/税务管理 + 资金审批 ----
    "fin_manager": {
        "accounting": _ALL,
        "invoice": _ALL,
        "tax": _ALL,
        "reimbursement": _ALL,
        "treasury": _RA,
        "budget": _RCU,
        "procurement": _R,
        "contract": _R,
        "bi": _R,
        "document": _RC,
    },

    # ---- legal_manager (L4): 合规/合同/法务管理 ----
    "legal_manager": {
        "compliance": _ALL,
        "contract": _ALL,
        "legal": _ALL,
        "risk": _R,
        "audit": _R,
        "bi": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- admin_manager (L4): 行政/资产管理 + 库存采购操作 ----
    "admin_manager": {
        "asset": _ALL,
        "admin": _ALL,
        "inventory": _RCU,
        "procurement": _RCU,
        "budget": _R,
        "hr": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- hr_manager (L4): 人力/招聘/绩效/报销管理 + 薪酬读写（无删除/审批） ----
    "hr_manager": {
        "hr": _ALL,
        "recruitment": _ALL,
        "performance": _ALL,
        "payroll": _RCU,
        "reimbursement": _ALL,
        "budget": _R,
        "bi": _R,
        "user": _R,
        "document": _RC,
    },

    # ---- prod_manager (L4): 产品/生产/质量管理 + 项目操作 ----
    "prod_manager": {
        "production": _ALL,
        "quality": _ALL,
        "inventory": _R,
        "project": _RCU,
        "procurement": _R,
        "bi": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- tech_manager (L4): 系统开发/Agent/运维管理 ----
    "tech_manager": {
        "system": _RCU,
        "agent": _ALL,
        "admin": _RCU,
        "knowledge": _RCU,
        "bi": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- mfg_manager (L4): 生产/质量/库存管理 ----
    "mfg_manager": {
        "production": _ALL,
        "quality": _ALL,
        "inventory": _ALL,
        "procurement": _R,
        "asset": _R,
        "project": _R,
        "bi": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- proc_manager (L4): 采购管理 + 库存操作 ----
    "proc_manager": {
        "procurement": _ALL,
        "inventory": _RCU,
        "asset": _R,
        "budget": _R,
        "contract": _R,
        "bi": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- biz_manager (L4): CRM/销售管理 + 客服操作 ----
    "biz_manager": {
        "crm": _ALL,
        "sales": _ALL,
        "customer_service": _RCU,
        "inventory": _R,
        "marketing": _R,
        "reimbursement": _RC,
        "bi": _R,
        "budget": _R,
        "document": _RC,
    },

    # ---- sales_manager (L4): 销售管理 + 合同操作 ----
    "sales_manager": {
        "crm": _ALL,
        "sales": _ALL,
        "customer_service": _R,
        "contract": _RC,
        "bi": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- mkt_manager (L4): 市场营销管理 ----
    "mkt_manager": {
        "marketing": _ALL,
        "crm": _R,
        "sales": _R,
        "bi": _R,
        "budget": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- cs_manager (L4): 客户服务管理 + CRM操作 ----
    "cs_manager": {
        "customer_service": _ALL,
        "crm": _RCU,
        "sales": _R,
        "contract": _R,
        "bi": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- quality_manager (L4): 质量管理 + 生产/库存只读 ----
    "quality_manager": {
        "quality": _ALL,
        "production": _R,
        "inventory": _R,
        "procurement": _R,
        "compliance": _R,
        "bi": _R,
        "risk": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- pm_manager (L4): 项目管理 + 预算操作 ----
    "pm_manager": {
        "project": _ALL,
        "budget": _RCU,
        "production": _R,
        "procurement": _R,
        "bi": _R,
        "risk": _R,
        "reimbursement": _RC,
        "document": _RC,
    },

    # ---- dept_staff (L5): 本部门只读 + 报销提交 ----
    "dept_staff": {
        "inventory": _R,
        "accounting": _R,
        "invoice": _R,
        "reimbursement": _RC,
        "tax": _R,
        "procurement": _R,
        "production": _R,
        "crm": _R,
        "sales": _R,
        "hr": _R,
        "quality": _R,
        "knowledge": _R,
        "bi": _R,
        "project": _R,
        "document": _R,
    },
}

# 为所有角色添加内部通讯权限（message:read / create / manage / file / command）
# 董事长、CEO、CFO、CIO、CTO、CMO、CHO、CRO、COO 及所有部门经理、员工角色均可使用内部通讯
for _role_code in ROLE_PERMISSION_MATRIX:
    ROLE_PERMISSION_MATRIX[_role_code]["message"] = _MSG_ALL

# ---- Phase F1：Agent 定制平台角色（附录 B）----
# publish/export 为定制平台专属动作（附录 B 的 5 权限点）
_CUSTOM_ALL = _ALL | {"publish", "export"}
_CUSTOM_RA = _RA | {"publish"}
ROLE_PERMISSION_MATRIX["customization_admin"] = {"customization": _CUSTOM_ALL}
ROLE_PERMISSION_MATRIX["customization_approver"] = {"customization": _CUSTOM_RA}
# export 一并给 cio：定制包导出是 UAT-F4-2 的必经动线（管理员自助改造要能把
# 技能定义带走），只读导出不改运行态，风险面低于已授予的 publish。
ROLE_PERMISSION_MATRIX["cio"]["customization"] = {"read", "update", "approve",
                                                 "create", "publish", "export"}
ROLE_PERMISSION_MATRIX["chairman"]["customization"] = {"read", "approve", "create",
                                                       "publish"}

# ---- 可观测与沙箱回滚平台角色（附录 B，G3/G4）----
# cio：日志查询/导出 + 回滚账本读写 + 审计链校验（回滚执行仍需 write_high 人审）
ROLE_PERMISSION_MATRIX["cio"]["operationlog"] = {"read", "export"}
ROLE_PERMISSION_MATRIX["cio"]["operation_rollback"] = {"read", "execute"}
ROLE_PERMISSION_MATRIX["cio"]["audit_verify"] = {"run"}
# log_auditor（日志审计员）：只读日志与账本 + 校验报告，零业务操作权
ROLE_PERMISSION_MATRIX["log_auditor"] = {
    "operationlog": {"read"},
    "operation_rollback": {"read"},
    "audit_verify": {"run"},
}

# P1-5 整改：定制平台两个专用角色补 system:manage（进管理台的路由守卫）
# 与 agent 读写/审批——此前"有 customization 权限却进不了管理台"，
# 职责分离名存实亡（admin-guide 附录 B 落差清单第 3/4 行闭环）
ROLE_PERMISSION_MATRIX["customization_admin"]["system"] = {"manage"}
ROLE_PERMISSION_MATRIX["customization_admin"]["agent"] = {"read"}
ROLE_PERMISSION_MATRIX["customization_approver"]["system"] = {"manage"}
ROLE_PERMISSION_MATRIX["customization_approver"]["agent"] = {"read", "approve"}



def _expand_matrix_to_permissions() -> dict[str, list[str]]:
    """
    将角色-权限矩阵展开为 {role_code: [permission_id, ...]} 映射。

    permission_id 格式为 ``resource:action``。
    """
    mapping: dict[str, list[str]] = {}
    for role_code, resource_actions in ROLE_PERMISSION_MATRIX.items():
        perm_ids: list[str] = []
        for resource, actions in resource_actions.items():
            for action in sorted(actions):
                perm_ids.append(f"{resource}:{action}")
        mapping[role_code] = perm_ids
    return mapping


# 角色-权限 ID 映射（展开后）
# ---- Phase F1：Agent 定制平台角色（附录 B）----
# customization_admin：定制平台管理员（全部定制权限，全域范围）

ROLE_PERMISSION_IDS: dict[str, list[str]] = _expand_matrix_to_permissions()


# ===================================================================
# 部门定义
# ===================================================================

SEED_DEPARTMENTS: list[dict] = [
    {"department_id": "dept_001", "department_code": "board", "department_name": "董事会",
     "parent_department_id": None},
    {"department_id": "dept_002", "department_code": "ceo_office", "department_name": "CEO办公室",
     "parent_department_id": "dept_001"},
    {"department_id": "dept_003", "department_code": "tech", "department_name": "技术部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_004", "department_code": "finance", "department_name": "财务部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_005", "department_code": "it", "department_name": "信息部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_006", "department_code": "marketing", "department_name": "市场部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_007", "department_code": "hr", "department_name": "人事部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_008", "department_code": "risk", "department_name": "风控部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_009", "department_code": "operations", "department_name": "运营部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_010", "department_code": "business", "department_name": "业务部",
     "parent_department_id": "dept_002"},
    {"department_id": "dept_011", "department_code": "production", "department_name": "生产部",
     "parent_department_id": "dept_009"},
    {"department_id": "dept_012", "department_code": "procurement", "department_name": "采购部",
     "parent_department_id": "dept_009"},
    {"department_id": "dept_013", "department_code": "legal", "department_name": "法务部",
     "parent_department_id": "dept_008"},
    {"department_id": "dept_014", "department_code": "quality", "department_name": "质量部",
     "parent_department_id": "dept_009"},
    {"department_id": "dept_015", "department_code": "project", "department_name": "项目部",
     "parent_department_id": "dept_003"},
    {"department_id": "dept_016", "department_code": "admin", "department_name": "行政部",
     "parent_department_id": "dept_009"},
]


# ===================================================================
# 默认用户（密码预哈希）
# ===================================================================

def _build_seed_users() -> list[dict]:
    """构建默认用户列表，密码经 :func:`_seed_password_hash` 生成（D-04 治理）。

    为每个系统角色预置一个登录账户，方便测试和演示。
    全部用户标记 ``must_change_password=True``，首次登录后必须改密。
    """
    _users = [
        # ===== 高管层 =====
        {
            "user_id": "user_admin",
            "username": "admin",
            "password_hash": _seed_password_hash("admin"),
            "full_name": "系统管理员",
            "department": "信息部",
            "email": "admin@erp.com",
            "role_code": "cio",
        },
        {
            "user_id": "user_chairman",
            "username": "chairman",
            "password_hash": _seed_password_hash("chairman"),
            "full_name": "董事长",
            "department": "董事会",
            "email": "chairman@erp.com",
            "role_code": "chairman",
        },
        {
            "user_id": "user_ceo",
            "username": "ceo",
            "password_hash": _seed_password_hash("ceo"),
            "full_name": "首席执行官",
            "department": "CEO办公室",
            "email": "ceo@erp.com",
            "role_code": "ceo",
        },
        {
            "user_id": "user_cto",
            "username": "cto",
            "password_hash": _seed_password_hash("cto"),
            "full_name": "首席技术官",
            "department": "技术部",
            "email": "cto@erp.com",
            "role_code": "cto",
        },
        {
            "user_id": "user_cfo",
            "username": "cfo",
            "password_hash": _seed_password_hash("cfo"),
            "full_name": "首席财务官",
            "department": "财务部",
            "email": "cfo@erp.com",
            "role_code": "cfo",
        },
        {
            "user_id": "user_cmo",
            "username": "cmo",
            "password_hash": _seed_password_hash("cmo"),
            "full_name": "首席营销官",
            "department": "市场部",
            "email": "cmo@erp.com",
            "role_code": "cmo",
        },
        {
            "user_id": "user_cho",
            "username": "cho",
            "password_hash": _seed_password_hash("cho"),
            "full_name": "首席人力官",
            "department": "人事部",
            "email": "cho@erp.com",
            "role_code": "cho",
        },
        {
            "user_id": "user_cro",
            "username": "cro",
            "password_hash": _seed_password_hash("cro"),
            "full_name": "首席风险官",
            "department": "风控部",
            "email": "cro@erp.com",
            "role_code": "cro",
        },
        {
            "user_id": "user_coo",
            "username": "coo",
            "password_hash": _seed_password_hash("coo"),
            "full_name": "首席运营官",
            "department": "运营部",
            "email": "coo@erp.com",
            "role_code": "coo",
        },
        # ===== 部门经理层 =====
        {
            "user_id": "user_fin_mgr",
            "username": "fin_manager",
            "password_hash": _seed_password_hash("fin_manager"),
            "full_name": "财务部经理",
            "department": "财务部",
            "email": "fin_manager@erp.com",
            "role_code": "fin_manager",
        },
        {
            "user_id": "user_legal_mgr",
            "username": "legal_manager",
            "password_hash": _seed_password_hash("legal_manager"),
            "full_name": "法务部经理",
            "department": "法务部",
            "email": "legal_manager@erp.com",
            "role_code": "legal_manager",
        },
        {
            "user_id": "user_admin_mgr",
            "username": "admin_manager",
            "password_hash": _seed_password_hash("admin_manager"),
            "full_name": "行政部经理",
            "department": "行政部",
            "email": "admin_manager@erp.com",
            "role_code": "admin_manager",
        },
        {
            "user_id": "user_hr_mgr",
            "username": "hr_manager",
            "password_hash": _seed_password_hash("hr_manager"),
            "full_name": "人事部经理",
            "department": "人事部",
            "email": "hr_manager@erp.com",
            "role_code": "hr_manager",
        },
        {
            "user_id": "user_prod_mgr",
            "username": "prod_manager",
            "password_hash": _seed_password_hash("prod_manager"),
            "full_name": "产品部经理",
            "department": "技术部",
            "email": "prod_manager@erp.com",
            "role_code": "prod_manager",
        },
        {
            "user_id": "user_tech_mgr",
            "username": "tech_manager",
            "password_hash": _seed_password_hash("tech_manager"),
            "full_name": "技术部经理",
            "department": "技术部",
            "email": "tech_manager@erp.com",
            "role_code": "tech_manager",
        },
        {
            "user_id": "user_mfg_mgr",
            "username": "mfg_manager",
            "password_hash": _seed_password_hash("mfg_manager"),
            "full_name": "生产部经理",
            "department": "生产部",
            "email": "mfg_manager@erp.com",
            "role_code": "mfg_manager",
        },
        {
            "user_id": "user_proc_mgr",
            "username": "proc_manager",
            "password_hash": _seed_password_hash("proc_manager"),
            "full_name": "采购部经理",
            "department": "采购部",
            "email": "proc_manager@erp.com",
            "role_code": "proc_manager",
        },
        {
            "user_id": "user_biz_mgr",
            "username": "biz_manager",
            "password_hash": _seed_password_hash("biz_manager"),
            "full_name": "业务部经理",
            "department": "业务部",
            "email": "biz_manager@erp.com",
            "role_code": "biz_manager",
        },
        {
            "user_id": "user_sales_mgr",
            "username": "sales_manager",
            "password_hash": _seed_password_hash("sales_manager"),
            "full_name": "销售部经理",
            "department": "业务部",
            "email": "sales_manager@erp.com",
            "role_code": "sales_manager",
        },
        {
            "user_id": "user_mkt_mgr",
            "username": "mkt_manager",
            "password_hash": _seed_password_hash("mkt_manager"),
            "full_name": "市场部经理",
            "department": "市场部",
            "email": "mkt_manager@erp.com",
            "role_code": "mkt_manager",
        },
        {
            "user_id": "user_cs_mgr",
            "username": "cs_manager",
            "password_hash": _seed_password_hash("cs_manager"),
            "full_name": "客服部经理",
            "department": "业务部",
            "email": "cs_manager@erp.com",
            "role_code": "cs_manager",
        },
        {
            "user_id": "user_quality_mgr",
            "username": "quality_manager",
            "password_hash": _seed_password_hash("quality_manager"),
            "full_name": "质量部经理",
            "department": "质量部",
            "email": "quality_manager@erp.com",
            "role_code": "quality_manager",
        },
        {
            "user_id": "user_pm_mgr",
            "username": "pm_manager",
            "password_hash": _seed_password_hash("pm_manager"),
            "full_name": "项目部经理",
            "department": "项目部",
            "email": "pm_manager@erp.com",
            "role_code": "pm_manager",
        },
        # ===== 员工层 =====
        {
            "user_id": "user_staff",
            "username": "staff",
            "password_hash": _seed_password_hash("staff"),
            "full_name": "普通员工",
            "department": "业务部",
            "email": "staff@erp.com",
            "role_code": "dept_staff",
        },
    ]
    # D-04：全部种子用户标记首登强制改密（列默认值同为 True，此处显式声明便于测试断言）
    for _u in _users:
        _u["must_change_password"] = True
    return _users


SEED_USERS: list[dict] = _build_seed_users()


# ===================================================================
# 种子数据加载
# ===================================================================

def load_seed_data(engine: "RBACEngine") -> None:
    """
    将全量种子数据加载到 RBAC 引擎。

    加载顺序：
    1. 权限定义（210 条）
    2. 角色定义（24 条）
    3. 角色-权限映射
    4. 部门（16 条）
    5. 默认用户（3 个）+ 角色分配

    Args:
        engine: RBACEngine 实例
    """
    # 1. 加载权限
    for perm in SEED_PERMISSIONS:
        engine.add_permission(perm)

    logger.info("rbac.seed.permissions_loaded", count=len(SEED_PERMISSIONS))

    # 2. 加载角色
    for role in SEED_ROLES:
        engine.add_role(role)

    logger.info("rbac.seed.roles_loaded", count=len(SEED_ROLES))

    # 3. 加载角色-权限映射
    for role_code, perm_ids in ROLE_PERMISSION_IDS.items():
        for perm_id in perm_ids:
            engine.assign_permission_to_role(role_code, perm_id)

    total_mappings = sum(len(v) for v in ROLE_PERMISSION_IDS.values())
    logger.info("rbac.seed.role_permissions_loaded", count=total_mappings)

    # 4. 加载部门
    for dept in SEED_DEPARTMENTS:
        engine.add_department(dept)

    logger.info("rbac.seed.departments_loaded", count=len(SEED_DEPARTMENTS))

    # 5. 加载默认用户 + 分配角色
    # 注意：使用 .get() 而非 .pop()，避免修改 SEED_USERS 全局常量——
    # pop 会永久移除 role_code 键，导致后续依赖该键的
    # persist_seed_data() 无法读取角色分配信息。
    for user_data in SEED_USERS:
        role_code = user_data.get("role_code")
        engine.add_user(
            user_id=user_data["user_id"],
            username=user_data["username"],
            password_hash=user_data["password_hash"],
            full_name=user_data["full_name"],
            department=user_data["department"],
            email=user_data["email"],
            must_change_password=user_data.get("must_change_password", True),
        )
        if role_code:
            engine.assign_role(user_data["user_id"], role_code)

    logger.info("rbac.seed.users_loaded", count=len(SEED_USERS))
    logger.info("rbac.seed.complete",
                roles=len(SEED_ROLES),
                permissions=len(SEED_PERMISSIONS),
                departments=len(SEED_DEPARTMENTS),
                users=len(SEED_USERS))

    # 注意：此处不打印凭据。导入期 DB 工厂尚未初始化，无法区分部署模式；
    # 打印责任在调用方——DB 模式由 _persist_seed_data_locked 仅对新播种
    # 用户打印，无 DB 降级模式由 lifespan 降级分支打印（密码才真实有效）。


async def persist_seed_data(engine: "RBACEngine") -> None:
    """
    将全量种子数据幂等播种（upsert）到 MySQL。

    调用时机：应用 lifespan 中 ``init_db()`` 成功之后、
    ``load_from_db()`` 之前。这样即使面对全新空库，种子数据也能完整落库，
    解决"导入期 DB 写入全部失败、DB 永远不会被播种"的启动时序问题。

    幂等性：逐条复用引擎的 ``_db_*`` 写入方法（均为先查后插、
    已存在则跳过），因此重复调用 / 重复启动不会产生重复数据。

    容错性：任一条目写入失败仅记录日志、继续处理后续条目，
    不中断整体播种（与引擎 fire-and-forget 的失败语义一致）；
    整体异常也不向上抛出，避免阻塞应用启动。

    Args:
        engine: RBACEngine 实例（通常为全局单例 ``rbac_engine``）
    """
    from app.core.database import _is_mysql_engine, get_engine, get_session_factory
    from sqlalchemy import text as _sql_text

    if get_session_factory() is None:
        logger.warning(
            "rbac.persist_seed_no_factory",
            message="数据库会话工厂未初始化，跳过种子数据播种（降级为内存种子数据）",
        )
        return

    # 与 bootstrap_schema 使用同一把命名锁：多个 uvicorn worker / 多实例
    # 同时启动时，建库与播种全程串行，避免并发写同一批种子行。
    db_engine = get_engine()
    if db_engine is not None and _is_mysql_engine():
        async with db_engine.connect() as lock_conn:
            got = (
                await lock_conn.execute(
                    _sql_text("SELECT GET_LOCK('erp_schema_bootstrap', 300)")
                )
            ).scalar()
            if not got:
                logger.warning("rbac.persist_seed_lock_timeout")
                return
            try:
                await _persist_seed_data_locked(engine)
            finally:
                await lock_conn.execute(
                    _sql_text("SELECT RELEASE_LOCK('erp_schema_bootstrap')")
                )
        return

    await _persist_seed_data_locked(engine)


async def _persist_seed_data_locked(engine: "RBACEngine") -> None:
    """persist_seed_data 的锁内实现（调用方保证已持有顾问锁）。"""
    try:
        # 1. 权限定义
        for perm in SEED_PERMISSIONS:
            await engine._db_add_permission(perm)

        # 2. 角色定义
        for role in SEED_ROLES:
            await engine._db_add_role(role)

        # 3. 角色-权限映射
        total_mappings = sum(len(v) for v in ROLE_PERMISSION_IDS.values())
        for role_code, perm_ids in ROLE_PERMISSION_IDS.items():
            for perm_id in perm_ids:
                await engine._db_assign_permission(role_code, perm_id)

        # 4. 部门
        for dept in SEED_DEPARTMENTS:
            await engine._db_add_department(dept)

        # 5. 用户 + 用户-角色映射（记录实际新播种的用户，用于一次性打印初始密码）
        newly_seeded: list[str] = []
        for user_data in SEED_USERS:
            role_code = user_data.get("role_code")
            inserted = await engine._db_add_user(user_data)
            if inserted:
                newly_seeded.append(user_data["username"])
            if role_code:
                await engine._db_assign_role(user_data["user_id"], role_code)

        logger.info(
            "rbac.seed_persisted",
            permissions=len(SEED_PERMISSIONS),
            roles=len(SEED_ROLES),
            role_permissions=total_mappings,
            departments=len(SEED_DEPARTMENTS),
            users=len(SEED_USERS),
            newly_seeded=len(newly_seeded),
        )
        if newly_seeded:
            print_seed_credentials(newly_seeded)
    except Exception as e:
        logger.error(
            "rbac.persist_seed_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
