# 可观测与沙箱回滚 · 运维手册（G5.5 演练剧本 + 运行期操作）

- **文档日期**：2026-10-03
- **适用**：《可观测与沙箱回滚路线图》（roadmap-logging-sandbox-2026-10-01.md）G1~G5 交付后的运行期
- **配套**：《Agent 定制层运维手册》（runbook-agent-customization-2026-10-01.md）、
  代码沙箱评估（sandbox-code-exec-assessment-2026-10-03.md）
- **取证口径**：每个命令/端点均对应已交付代码；权限点见路线图附录 B
  （operationlog:read/export、operation_rollback:read/execute、audit_verify:run）

---

## 1. 运行期组件速查

| 组件 | 位置 | 说明 |
|------|------|------|
| 操作日志查询页 | 管理台 `/cio-admin` →「操作日志」 | 单据号第一入口；trace 同链视图 |
| 查询/导出 API | `GET/GET /api/v1/agent-ops/operation-logs[?...]` | LIMIT≤500；导出≤1 万行；行为自身审计 |
| 审计链校验 | `POST /api/v1/agent-ops/operation-logs/verify/run` | 手动全量即时校验；调度循环每小时自检 |
| 告警死信 | `GET/POST /api/v1/agent-ops/operation-logs/dead-letters[/retry]` | webhook 失败兜底 |
| 回滚账本 | `GET /api/v1/agent-ops/operation-rollback/ledger` | 双键/状态/工具过滤 |
| 回滚发起 | `POST /api/v1/agent-ops/operation-rollback/{ledger_id}/request` | 前置检查→write_high 审批 |
| Saga 表 | `sagas`（saga_type='workflow_saga'） | 留存豁免，勿手工清理 |
| 告警外发 | 环境变量 `ERP_ALERT_WEBHOOK_URL` | 飞书/钉钉/企微机器人通用 JSON POST |

## 2. 演练剧本一：工作流中断自动补偿（UAT-G5-1 前半）

**目的**：验证采购到支付工作流中途失败时，已执行步骤逆序自动补偿。

1. 以测试 PO 发起采购到支付工作流（`POST /api/v1/agent-ops/workflows/p2p/start`，
   `document_key` 填演练单号如 `PO-DRILL-SAGA`）；
2. 人工制造下游失败：临时把后续节点依赖的 handler 置错（测试环境 monkeypatch），
   或使用测试 DSL 定义在 `p2p.pay` 后注入失败节点；
3. 预期（后端日志/审计链）：
   - `workflow.saga_compensated` 审计条目出现，`compensated` 列表含
     `p2p.schedule`（付款计划行被删除）；
   - `sagas` 表该 saga 行 status 变为 `compensated`（若有 write_high/状态推进步骤则
     `needs_manual` + error 级告警 `workflow_saga_needs_manual` 送达消息中心）；
4. 核对：`payment_schedules` 无该 PO 的 scheduled 行；告警在消息中心可见。

**回滚方法**：演练数据由 Saga 自清；告警条目按保留策略自然过期。

## 3. 演练剧本二：kill -9 重启后的 Saga 恢复补偿（UAT-G5-1 后半）

**目的**：验证补偿执行到一半进程死亡后，重启续跑（G5.2）。

1. 重复剧本一第 1~2 步，但在 Saga 补偿中途 `docker kill erp-backend`
   （或本地 `taskkill //PID <uvicorn_pid> //F`）——制造 `sagas` 行停留
   `executing`/补偿未完成的状态；
2. 重启后端（`docker-compose up -d backend`；restart 不会应用 override，必须 up -d）；
3. 预期：启动日志出现 `app.workflow_saga_recovered`（或 `app.workflow_saga_clean` 表示
   无遗留）；`sagas` 行 status 变为 `compensated`/`needs_manual`；
4. 核对：残留的 scheduled 付款计划被清除；`workflow.saga_compensated` 审计条目
   reason="进程重启恢复（G5.2）"。

## 4. 演练剧本三：回滚失败人工介入（UAT-G4-2 运维侧）

**目的**：验证补偿失败后的待办与重放路径（G4.4）。

1. 构造补偿失败（测试环境让目标工具的补偿抛错），从账本发起回滚并批准；
2. 预期：账本行 `compensate_failed` + `compensation_error` 落库；
   error 级告警 `rollback_compensation_failed` 送达（含 ledger_id 与排查指引）；
3. 人工介入：修复补偿故障后重新 `POST .../operation-rollback/{ledger_id}/request`
   → 审批批准 → 重放成功（`compensated`）；
4. 核对：`rollback.failed` 与 `rollback.executed` 两条审计均在链上（不可抵赖的完整轨迹）。

## 5. 演练剧本四：审计篡改检测（UAT-G3-1）

1. 库内手工篡改一条审计记录：
   `UPDATE audit_logs SET details='{"i": 999}' WHERE entry_id='<某条 entry_id>'`；
2. 管理台「操作日志」→「立即全量校验」（或 `POST .../operation-logs/verify/run`）；
3. 预期：`status=broken`，`broken_at` 点名被篡改条目，`expected/actual` 哈希并列；
   critical 级告警 `audit_chain_broken` 送达；
4. 恢复：演练库直接重置；生产库**不得**用本剧本（篡改即安全事件，走事件响应流程）。

## 6. 例行运维操作

- **对账**：账本对账（幽灵操作缺口）随小时级校验循环自动执行；手动触发场景=
  怀疑登记窗口丢行时重启后端即可（`ledger_reconcile` 每循环执行）；
- **死信补推**：webhook 恢复后 `POST .../dead-letters/retry`（或等调度自动补推，
  最多重试 5 次）；
- **告警通道配置**：设置 `ERP_ALERT_WEBHOOK_URL` 后重启；无 webhook 时告警仅进
  消息中心 + 日志（仍然三路中的两路，不丢失）；
- **LLM 全文日志**：默认摘要模式；临时排查需全文时经定制中心
  `agent:observability` 参数申请（放宽审批=双人确认，≤7 天自动过期）；
- **留存**：审计 365 天 / 操作日志热 30 天→归档 180 天，`retention_loop` 自动执行；
  `sagas` 表在豁免清单内——**任何情况下不要对它做手工清理**（长挂起 Saga 的
  恢复能力依赖它）。

## 7. 故障速查

| 症状 | 首查 |
|------|------|
| `ledger_ghost_operation` 告警 | 对账已自动补登记（backfilled=1）；查该时间窗后端是否有崩溃/OOM |
| `oplog_flush_failure` 持续 | MySQL 可用性 + `operation_logs` 表存在性；缓冲在内存重试不丢 |
| 回滚发起返回 `rejected_precheck` | 正常红线行为：按返回的 dependencies 清单先处理下游引用 |
| 回滚发起返回"工具已不在注册表" | 注册表漂移（工具被下线）；走数据修复流程，勿绕过账本 |
| 校验报告 `skipped` | 查 `error` 字段：db_unavailable / audit_logs 缺表（bootstrap-only 库） |
