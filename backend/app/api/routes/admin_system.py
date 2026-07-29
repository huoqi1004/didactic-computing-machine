"""
CIO 管理员系统 API 路由
======================

提供 CIO 专属的 7 大管理模块 HTTP API 端点：

- ``/admin/monitor/*``:      系统监控（实时指标/历史/告警/服务状态）
- ``/admin/lifecycle/*``:    Agent 生命周期管理（注册/启停/回滚/健康检查）
- ``/admin/config/*``:       配置管理（CRUD/版本/导入导出/热更新）
- ``/admin/security/*``:     安全控制（策略/审计/IP/API密钥/安全评分）
- ``/admin/governance/*``:   数据治理（分类/血缘/质量/保留/脱敏/合规）
- ``/admin/integration/*``:  集成平台（连接/Webhook/同步任务/健康度）
- ``/admin/backup/*``:       备份恢复（全量/增量/调度/恢复/DR计划）

所有路由均需 ``admin:read`` 或 ``admin:manage`` 权限，CIO 专属。
"""
import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Any

from app.core.admin import (
    system_monitor,
    lifecycle_manager,
    config_manager,
    security_controller,
    data_governance_manager,
    integration_platform,
    backup_manager,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/admin", tags=["CIO管理系统"])


# =============================================================================
# 1. 系统监控
# =============================================================================

@router.get("/monitor/metrics")
async def get_realtime_metrics():
    """获取实时系统监控指标"""
    return system_monitor.get_realtime_metrics()


@router.get("/monitor/metrics/history")
async def get_history_metrics(
    start_time: str = Query(..., description="起始时间 ISO 8601"),
    end_time: str = Query(..., description="结束时间 ISO 8601"),
    metric_name: Optional[str] = Query(None, description="指标名称过滤"),
):
    """查询历史监控指标"""
    return system_monitor.get_history_metrics(start_time, end_time, metric_name)


@router.get("/monitor/alerts")
async def get_monitor_alerts(severity: Optional[str] = Query(None)):
    """获取监控告警列表"""
    return system_monitor.get_alerts(severity)


@router.get("/monitor/services")
async def get_service_status():
    """获取各服务组件状态"""
    return system_monitor.get_service_status()


@router.post("/monitor/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """确认告警"""
    result = system_monitor.acknowledge_alert(alert_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# =============================================================================
# 2. Agent 生命周期管理
# =============================================================================

class RegisterAgentRequest(BaseModel):
    agent_id: str = Field(..., description="Agent唯一标识")
    name: str = Field(..., description="Agent名称")
    domain: str = Field(..., description="业务域")
    config: dict = Field(default_factory=dict, description="Agent配置")


class UpdateAgentConfigRequest(BaseModel):
    config: dict = Field(..., description="新配置")


class SetResourceLimitsRequest(BaseModel):
    cpu_limit: float = Field(..., description="CPU配额（核）")
    memory_limit: int = Field(..., description="内存配额（MB）")


class AddDependencyRequest(BaseModel):
    depends_on: str = Field(..., description="依赖的Agent ID")


@router.get("/lifecycle/agents")
async def list_agents(
    status: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
):
    """列出所有Agent及其生命周期状态"""
    return lifecycle_manager.list_agents(status, domain)


@router.post("/lifecycle/agents/register")
async def register_agent(req: RegisterAgentRequest):
    """注册新Agent"""
    result = lifecycle_manager.register_agent(req.agent_id, req.name, req.domain, req.config)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    logger.info("admin.agent_registered", agent_id=req.agent_id, name=req.name)
    return result


@router.post("/lifecycle/agents/{agent_id}/start")
async def start_agent(agent_id: str):
    """启动Agent"""
    result = lifecycle_manager.start_agent(agent_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/lifecycle/agents/{agent_id}/pause")
async def pause_agent(agent_id: str):
    """暂停Agent"""
    result = lifecycle_manager.pause_agent(agent_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/lifecycle/agents/{agent_id}/resume")
async def resume_agent(agent_id: str):
    """恢复Agent"""
    result = lifecycle_manager.resume_agent(agent_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/lifecycle/agents/{agent_id}/stop")
async def stop_agent(agent_id: str):
    """停止Agent"""
    result = lifecycle_manager.stop_agent(agent_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.put("/lifecycle/agents/{agent_id}/config")
async def update_agent_config(agent_id: str, req: UpdateAgentConfigRequest):
    """更新Agent运行时配置"""
    result = lifecycle_manager.update_config(agent_id, req.config)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/lifecycle/agents/{agent_id}/rollback")
async def rollback_agent(agent_id: str, version: str = Query(..., description="回滚目标版本")):
    """回滚Agent到指定版本"""
    result = lifecycle_manager.rollback_agent(agent_id, version)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/lifecycle/agents/{agent_id}/health")
async def health_check(agent_id: str):
    """Agent健康检查"""
    result = lifecycle_manager.health_check(agent_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/lifecycle/agents/{agent_id}/history")
async def get_lifecycle_history(agent_id: str):
    """获取Agent生命周期历史"""
    result = lifecycle_manager.get_lifecycle_history(agent_id)
    return {"agent_id": agent_id, "history": result}


@router.put("/lifecycle/agents/{agent_id}/resources")
async def set_resource_limits(agent_id: str, req: SetResourceLimitsRequest):
    """设置Agent资源限制"""
    result = lifecycle_manager.set_resource_limits(agent_id, req.cpu_limit, req.memory_limit)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/lifecycle/agents/{agent_id}/dependencies")
async def add_dependency(agent_id: str, req: AddDependencyRequest):
    """添加Agent依赖关系"""
    result = lifecycle_manager.add_dependency(agent_id, req.depends_on)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# =============================================================================
# 3. 配置管理
# =============================================================================

class SetConfigRequest(BaseModel):
    key: str = Field(..., description="配置键名")
    value: Any = Field(..., description="配置值")
    category: str = Field("general", description="配置分类")
    updated_by: str = Field("system", description="更新者")


class ImportConfigRequest(BaseModel):
    config_data: dict = Field(..., description="配置数据")
    imported_by: str = Field("system", description="导入者")


@router.get("/config")
async def get_config(
    category: Optional[str] = Query(None),
    key: Optional[str] = Query(None),
):
    """获取系统配置"""
    return config_manager.get_config(category, key)


@router.post("/config")
async def set_config(req: SetConfigRequest):
    """设置系统配置"""
    result = config_manager.set_config(req.key, req.value, req.category, req.updated_by)
    logger.info("admin.config_set", key=req.key, category=req.category, by=req.updated_by)
    return result


@router.delete("/config")
async def delete_config(
    key: str = Query(..., description="配置键名"),
    category: str = Query("general", description="配置分类"),
):
    """删除系统配置"""
    result = config_manager.delete_config(key, category)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/config/history")
async def get_config_history(
    key: str = Query(..., description="配置键名"),
    category: str = Query("general", description="配置分类"),
):
    """获取配置变更历史"""
    return config_manager.get_config_history(key, category)


@router.get("/config/export")
async def export_config():
    """导出全部配置"""
    return config_manager.export_config()


@router.post("/config/import")
async def import_config(req: ImportConfigRequest):
    """导入配置"""
    result = config_manager.import_config(req.config_data, req.imported_by)
    logger.info("admin.config_imported", by=req.imported_by)
    return result


@router.get("/config/categories")
async def get_config_categories():
    """获取所有配置分类"""
    return {"categories": config_manager.get_categories()}


@router.get("/config/env")
async def get_env_vars():
    """获取环境变量"""
    return config_manager.get_env_vars()


@router.post("/config/env")
async def set_env_var(name: str = Query(...), value: str = Query(...)):
    """设置环境变量"""
    return config_manager.set_env_var(name, value)


# =============================================================================
# 4. 安全控制
# =============================================================================

class UpdatePolicyRequest(BaseModel):
    config: dict = Field(..., description="策略配置")


class RespondEventRequest(BaseModel):
    action: str = Field(..., description="响应动作 (lock/unlock/force_logout/block_ip)")
    notes: str = Field("", description="备注")


class AddIpRuleRequest(BaseModel):
    ip: str = Field(..., description="IP地址或CIDR")
    rule_type: str = Field(..., description="规则类型 (blacklist/whitelist)")
    reason: str = Field("", description="原因")


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., description="API密钥名称")
    scopes: list[str] = Field(default_factory=list, description="权限范围")
    expires_in_days: int = Field(365, description="有效天数")


@router.get("/security/policies")
async def get_security_policies():
    """获取安全策略列表"""
    return security_controller.get_security_policies()


@router.put("/security/policies/{policy_id}")
async def update_security_policy(policy_id: str, req: UpdatePolicyRequest):
    """更新安全策略"""
    result = security_controller.update_security_policy(policy_id, req.config)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/security/audit-logs")
async def get_audit_logs(
    start_time: str = Query(..., description="起始时间"),
    end_time: str = Query(..., description="结束时间"),
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
):
    """查询安全审计日志"""
    return security_controller.get_audit_logs(start_time, end_time, actor, action)


@router.post("/security/events/{event_id}/respond")
async def respond_security_event(event_id: str, req: RespondEventRequest):
    """响应安全事件"""
    result = security_controller.respond_security_event(event_id, req.action, req.notes)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    logger.info("admin.security_event_responded", event_id=event_id, action=req.action)
    return result


@router.get("/security/ip-rules")
async def get_ip_rules():
    """获取IP黑白名单"""
    return security_controller.get_ip_rules()


@router.post("/security/ip-rules")
async def add_ip_rule(req: AddIpRuleRequest):
    """添加IP规则"""
    result = security_controller.add_ip_rule(req.ip, req.rule_type, req.reason)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/security/ip-rules/{ip}")
async def remove_ip_rule(ip: str):
    """移除IP规则"""
    result = security_controller.remove_ip_rule(ip)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/security/api-keys")
async def get_api_keys():
    """获取API密钥列表"""
    return security_controller.get_api_keys()


@router.post("/security/api-keys")
async def create_api_key(req: CreateApiKeyRequest):
    """创建API密钥"""
    result = security_controller.create_api_key(req.name, req.scopes, req.expires_in_days)
    logger.info("admin.api_key_created", name=req.name)
    return result


@router.delete("/security/api-keys/{key_id}")
async def revoke_api_key(key_id: str):
    """吊销API密钥"""
    result = security_controller.revoke_api_key(key_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/security/score")
async def get_security_score():
    """获取系统安全评分"""
    return security_controller.get_security_score()


@router.get("/security/encryption")
async def get_encryption_status():
    """获取数据加密状态"""
    return security_controller.get_encryption_status()


# =============================================================================
# 5. 数据治理
# =============================================================================

class ClassifyDataRequest(BaseModel):
    data_id: str = Field(..., description="数据资产ID")
    classification: str = Field(..., description="分类级别 (public/internal/confidential/top_secret)")
    owner: str = Field(..., description="数据所有者")
    name: str = Field("", description="数据名称")
    contains_pii: bool = Field(False, description="是否包含个人信息")
    fields: Optional[list[str]] = Field(None, description="字段列表")


class SetRetentionPolicyRequest(BaseModel):
    data_type: str = Field(..., description="数据类型")
    retention_days: int = Field(..., description="保留天数")
    action: str = Field("archive", description="到期动作 (archive/delete/anonymize)")


class AddMaskingRuleRequest(BaseModel):
    field_pattern: str = Field(..., description="字段匹配模式")
    mask_type: str = Field(..., description="脱敏类型 (full/partial/hash/replace)")
    mask_char: str = Field("*", description="脱敏字符")


@router.get("/governance/classifications")
async def get_data_classifications():
    """获取数据分类分级列表"""
    return data_governance_manager.get_data_classifications()


@router.post("/governance/classifications")
async def classify_data(req: ClassifyDataRequest):
    """对数据进行分类分级"""
    result = data_governance_manager.classify_data(
        req.data_id, req.classification, req.owner,
        req.name, req.contains_pii, req.fields,
    )
    logger.info("admin.data_classified", data_id=req.data_id, level=req.classification)
    return result


@router.get("/governance/lineage/{data_id}")
async def get_data_lineage(data_id: str):
    """获取数据血缘关系"""
    result = data_governance_manager.get_data_lineage(data_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/governance/quality")
async def get_data_quality_metrics():
    """获取数据质量指标"""
    return data_governance_manager.get_data_quality_metrics()


@router.get("/governance/retention-policies")
async def get_retention_policies():
    """获取数据保留策略列表"""
    return data_governance_manager.get_retention_policies()


@router.post("/governance/retention-policies")
async def set_retention_policy(req: SetRetentionPolicyRequest):
    """设置数据保留策略"""
    result = data_governance_manager.set_retention_policy(
        req.data_type, req.retention_days, req.action,
    )
    logger.info("admin.retention_policy_set", data_type=req.data_type, days=req.retention_days)
    return result


@router.get("/governance/access-audit")
async def get_access_audit(
    data_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    """查询数据访问审计记录"""
    return data_governance_manager.get_access_audit(data_id, user_id, start_time, end_time)


@router.get("/governance/masking-rules")
async def get_masking_rules():
    """获取数据脱敏规则"""
    return data_governance_manager.get_masking_rules()


@router.post("/governance/masking-rules")
async def add_masking_rule(req: AddMaskingRuleRequest):
    """添加数据脱敏规则"""
    result = data_governance_manager.add_masking_rule(
        req.field_pattern, req.mask_type, req.mask_char,
    )
    return result


@router.get("/governance/compliance")
async def compliance_check():
    """执行合规检查"""
    return data_governance_manager.compliance_check()


# =============================================================================
# 6. 集成平台
# =============================================================================

class CreateConnectionRequest(BaseModel):
    name: str = Field(..., description="连接名称")
    type: str = Field(..., description="连接类型 (erp/crm/oa/bank/tax/warehouse/logistics/custom)")
    config: dict = Field(..., description="连接配置")


class UpdateConnectionRequest(BaseModel):
    config: dict = Field(..., description="新连接配置")


class CreateWebhookRequest(BaseModel):
    name: str = Field(..., description="Webhook名称")
    url: str = Field(..., description="回调URL")
    events: list[str] = Field(..., description="订阅事件列表")
    secret: str = Field("", description="签名密钥")


class CreateSyncTaskRequest(BaseModel):
    name: str = Field(..., description="同步任务名称")
    source: str = Field(..., description="源连接ID")
    target: str = Field(..., description="目标连接ID")
    schedule: str = Field(..., description="调度表达式")
    mapping: dict = Field(default_factory=dict, description="字段映射")


@router.get("/integration/connections")
async def get_connections():
    """获取外部系统连接列表"""
    return integration_platform.get_connections()


@router.post("/integration/connections")
async def create_connection(req: CreateConnectionRequest):
    """创建外部系统连接"""
    result = integration_platform.create_connection(req.name, req.type, req.config)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info("admin.connection_created", name=req.name, type=req.type)
    return result


@router.put("/integration/connections/{connection_id}")
async def update_connection(connection_id: str, req: UpdateConnectionRequest):
    """更新连接配置"""
    result = integration_platform.update_connection(connection_id, req.config)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/integration/connections/{connection_id}/test")
async def test_connection(connection_id: str):
    """测试连接"""
    result = integration_platform.test_connection(connection_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.delete("/integration/connections/{connection_id}")
async def delete_connection(connection_id: str):
    """删除连接"""
    result = integration_platform.delete_connection(connection_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/integration/webhooks")
async def get_webhooks():
    """获取Webhook列表"""
    return integration_platform.get_webhooks()


@router.post("/integration/webhooks")
async def create_webhook(req: CreateWebhookRequest):
    """创建Webhook"""
    result = integration_platform.create_webhook(req.name, req.url, req.events, req.secret)
    logger.info("admin.webhook_created", name=req.name, url=req.url)
    return result


@router.delete("/integration/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str):
    """删除Webhook"""
    result = integration_platform.delete_webhook(webhook_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/integration/sync-tasks")
async def get_sync_tasks():
    """获取数据同步任务列表"""
    return integration_platform.get_sync_tasks()


@router.post("/integration/sync-tasks")
async def create_sync_task(req: CreateSyncTaskRequest):
    """创建数据同步任务"""
    result = integration_platform.create_sync_task(
        req.name, req.source, req.target, req.schedule, req.mapping,
    )
    logger.info("admin.sync_task_created", name=req.name)
    return result


@router.post("/integration/sync-tasks/{task_id}/execute")
async def execute_sync_task(task_id: str):
    """执行同步任务"""
    result = integration_platform.execute_sync_task(task_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/integration/health")
async def get_integration_health():
    """获取集成平台健康度"""
    return integration_platform.get_integration_health()


# =============================================================================
# 7. 备份和恢复
# =============================================================================

class CreateBackupRequest(BaseModel):
    type: str = Field("full", description="备份类型 (full/incremental)")
    description: str = Field("", description="备份描述")


class SetBackupScheduleRequest(BaseModel):
    cron_expression: str = Field(..., description="调度表达式")
    backup_type: str = Field("full", description="备份类型")
    retention_days: int = Field(30, description="保留天数")


class UpdateDRPlanRequest(BaseModel):
    plan: dict = Field(..., description="灾难恢复计划")


@router.get("/backup")
async def get_backups(
    backup_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """获取备份列表"""
    return backup_manager.get_backups(backup_type, status)


@router.post("/backup")
async def create_backup(req: CreateBackupRequest):
    """创建备份"""
    result = backup_manager.create_backup(req.type, req.description)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info("admin.backup_created", type=req.type)
    return result


@router.post("/backup/{backup_id}/restore")
async def restore_backup(backup_id: str, target: str = Query("current", description="恢复目标")):
    """从备份恢复"""
    result = backup_manager.restore_backup(backup_id, target)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    logger.info("admin.backup_restored", backup_id=backup_id, target=target)
    return result


@router.post("/backup/{backup_id}/verify")
async def verify_backup(backup_id: str):
    """验证备份完整性"""
    result = backup_manager.verify_backup(backup_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.delete("/backup/{backup_id}")
async def delete_backup(backup_id: str):
    """删除备份"""
    result = backup_manager.delete_backup(backup_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/backup/schedule")
async def get_backup_schedule():
    """获取备份调度计划"""
    return backup_manager.get_backup_schedule()


@router.post("/backup/schedule")
async def set_backup_schedule(req: SetBackupScheduleRequest):
    """设置备份调度"""
    result = backup_manager.set_backup_schedule(
        req.cron_expression, req.backup_type, req.retention_days,
    )
    logger.info("admin.backup_schedule_set", cron=req.cron_expression, type=req.backup_type)
    return result


@router.get("/backup/dr-plan")
async def get_dr_plan():
    """获取灾难恢复计划"""
    return backup_manager.get_disaster_recovery_plan()


@router.put("/backup/dr-plan")
async def update_dr_plan(req: UpdateDRPlanRequest):
    """更新灾难恢复计划"""
    result = backup_manager.update_disaster_recovery_plan(req.plan)
    logger.info("admin.dr_plan_updated")
    return result


@router.get("/backup/storage")
async def get_backup_storage():
    """获取备份存储使用情况"""
    return backup_manager.get_storage_usage()


@router.get("/backup/restore-history")
async def get_restore_history():
    """获取恢复历史记录"""
    return backup_manager.get_restore_history()
