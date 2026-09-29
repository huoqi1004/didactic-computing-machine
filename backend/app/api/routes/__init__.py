"""
API 路由模块
============

将路由按功能域拆分为独立模块：

- ``legacy``:          原有业务路由（Agent/库存/会计/发票/报销/税务/采购等）
- ``auth``:            登录/登出/刷新令牌/会话管理
- ``rbac``:            用户/角色/权限/部门 CRUD
- ``workflow``:        审批工作流管理
- ``knowledge``:       知识库管理（检索/摄入/审批/分类/图谱/规则）
- ``hr``:              人力资源（员工/考勤/请假/招聘/绩效/薪酬）
- ``sales_marketing``: 销售营销（CRM/销售订单/营销活动/客服）
- ``document``:        文档生成（docx/xlsx/pptx/pdf 业务文档生成与下载）
- ``reimbursement``:   报销发票上传识别（票面要素解析 + 报销表单预填）
"""
from fastapi import APIRouter

from app.api.routes.legacy import router as legacy_router
from app.api.routes.auth import router as auth_router
from app.api.routes.rbac import router as rbac_router
from app.api.routes.workflow import router as workflow_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.hr import router as hr_router
from app.api.routes.sales_marketing import router as sales_marketing_router
from app.api.routes.operations_strategy import router as operations_strategy_router
from app.api.routes.admin_system import router as admin_system_router
from app.api.routes.conversation import router as conversation_router
from app.api.routes.document import router as document_router
from app.api.routes.messaging import router as messaging_router
from app.api.routes.reimbursement import router as reimbursement_router
from app.api.routes.agent_ops import router as agent_ops_router
from app.api.routes.customizations import router as customizations_router

# 兼容旧导入: from app.api.routes import router
router = legacy_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(rbac_router)
api_router.include_router(workflow_router)
api_router.include_router(knowledge_router)
api_router.include_router(hr_router)
api_router.include_router(sales_marketing_router)
api_router.include_router(operations_strategy_router)
api_router.include_router(admin_system_router)
api_router.include_router(conversation_router)
api_router.include_router(document_router)
api_router.include_router(messaging_router)
api_router.include_router(reimbursement_router)
api_router.include_router(agent_ops_router)
api_router.include_router(customizations_router)

# 导出所有子路由器，供 main.py 直接包含
phase2_routers = [
    customizations_router,
    agent_ops_router,
    reimbursement_router,
    auth_router,
    rbac_router,
    workflow_router,
    knowledge_router,
    hr_router,
    sales_marketing_router,
    operations_strategy_router,
    admin_system_router,
    conversation_router,
    document_router,
    messaging_router,
]

__all__ = ["api_router", "router", "phase2_routers",
           "legacy_router", "auth_router", "rbac_router",
           "workflow_router", "knowledge_router",
           "hr_router", "sales_marketing_router",
           "operations_strategy_router", "admin_system_router",
           "conversation_router", "document_router",
           "messaging_router"]
