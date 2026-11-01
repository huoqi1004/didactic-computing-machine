"""全局 Agent 引用 - 供 API 路由访问

本模块维护各业务 Agent 的全局引用。Agent 实例由 main.py 的
lifespan 在应用启动时创建并通过 ``set_agents`` 注入，路由函数
通过对应的 ``get_xxx_agent`` 获取实例。

若 Agent 尚未启动（返回 None），路由函数应返回 503 错误。
"""
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.hr.agent import HRAgent
    from app.agents.recruitment.agent import RecruitmentAgent
    from app.agents.performance.agent import PerformanceAgent
    from app.agents.payroll.agent import PayrollAgent
    from app.agents.crm.agent import CRMAgent
    from app.agents.sales.agent import SalesAgent
    from app.agents.marketing.agent import MarketingAgent
    from app.agents.customer_service.agent import CustomerServiceAgent
    from app.agents.legal.agent import LegalAgent
    from app.agents.risk.agent import RiskAgent
    from app.agents.contract.agent import ContractAgent
    from app.agents.quality.agent import QualityAgent
    from app.agents.asset.agent import AssetAgent
    from app.agents.project.agent import ProjectAgent
    from app.agents.budget.agent import BudgetAgent
    from app.agents.bi.agent import BIAgent
    from app.agents.esg.agent import ESGAgent
    from app.agents.admin.agent import AdminAgent
    from app.agents.project_supervisor.agent import ProjectSupervisorAgent

# 全局引用（由 main.py lifespan 初始化时设置）
_hr_agent: Optional["HRAgent"] = None
_recruitment_agent: Optional["RecruitmentAgent"] = None
_performance_agent: Optional["PerformanceAgent"] = None
_payroll_agent: Optional["PayrollAgent"] = None
_crm_agent: Optional["CRMAgent"] = None
_sales_agent: Optional["SalesAgent"] = None
_marketing_agent: Optional["MarketingAgent"] = None
_customer_service_agent: Optional["CustomerServiceAgent"] = None
# Phase 5
_legal_agent: Optional["LegalAgent"] = None
_risk_agent: Optional["RiskAgent"] = None
_contract_agent: Optional["ContractAgent"] = None
_quality_agent: Optional["QualityAgent"] = None
_asset_agent: Optional["AssetAgent"] = None
_project_agent: Optional["ProjectAgent"] = None
_budget_agent: Optional["BudgetAgent"] = None
_bi_agent: Optional["BIAgent"] = None
_esg_agent: Optional["ESGAgent"] = None
_admin_agent: Optional["AdminAgent"] = None
_project_supervisor_agent: Optional["ProjectSupervisorAgent"] = None


def set_agents(**kwargs):
    """注入 Agent 实例。

    用法::

        set_agents(
            hr=hr_agent,
            recruitment=recruitment_agent,
            ...
        )
    """
    global _hr_agent, _recruitment_agent, _performance_agent, _payroll_agent
    global _crm_agent, _sales_agent, _marketing_agent, _customer_service_agent
    global _legal_agent, _risk_agent, _contract_agent, _quality_agent
    global _asset_agent, _project_agent, _budget_agent, _bi_agent, _esg_agent
    global _admin_agent, _project_supervisor_agent
    for key, val in kwargs.items():
        if key == "hr":
            _hr_agent = val
        elif key == "recruitment":
            _recruitment_agent = val
        elif key == "performance":
            _performance_agent = val
        elif key == "payroll":
            _payroll_agent = val
        elif key == "crm":
            _crm_agent = val
        elif key == "sales":
            _sales_agent = val
        elif key == "marketing":
            _marketing_agent = val
        elif key == "customer_service":
            _customer_service_agent = val
        elif key == "legal":
            _legal_agent = val
        elif key == "risk":
            _risk_agent = val
        elif key == "contract":
            _contract_agent = val
        elif key == "quality":
            _quality_agent = val
        elif key == "asset":
            _asset_agent = val
        elif key == "project":
            _project_agent = val
        elif key == "budget":
            _budget_agent = val
        elif key == "bi":
            _bi_agent = val
        elif key == "esg":
            _esg_agent = val
        elif key == "admin":
            _admin_agent = val
        elif key == "project_supervisor":
            _project_supervisor_agent = val


def get_hr_agent() -> Optional["HRAgent"]:
    return _hr_agent


def get_recruitment_agent() -> Optional["RecruitmentAgent"]:
    return _recruitment_agent


def get_performance_agent() -> Optional["PerformanceAgent"]:
    return _performance_agent


def get_payroll_agent() -> Optional["PayrollAgent"]:
    return _payroll_agent


def get_crm_agent() -> Optional["CRMAgent"]:
    return _crm_agent


def get_sales_agent() -> Optional["SalesAgent"]:
    return _sales_agent


def get_marketing_agent() -> Optional["MarketingAgent"]:
    return _marketing_agent


def get_customer_service_agent() -> Optional["CustomerServiceAgent"]:
    return _customer_service_agent


def get_legal_agent() -> Optional["LegalAgent"]:
    return _legal_agent


def get_risk_agent() -> Optional["RiskAgent"]:
    return _risk_agent


def get_contract_agent() -> Optional["ContractAgent"]:
    return _contract_agent


def get_quality_agent() -> Optional["QualityAgent"]:
    return _quality_agent


def get_asset_agent() -> Optional["AssetAgent"]:
    return _asset_agent


def get_project_agent() -> Optional["ProjectAgent"]:
    return _project_agent


def get_budget_agent() -> Optional["BudgetAgent"]:
    return _budget_agent


def get_bi_agent() -> Optional["BIAgent"]:
    return _bi_agent


def get_esg_agent() -> Optional["ESGAgent"]:
    return _esg_agent


def get_admin_agent() -> Optional["AdminAgent"]:
    return _admin_agent


def get_project_supervisor_agent() -> Optional["ProjectSupervisorAgent"]:
    return _project_supervisor_agent
