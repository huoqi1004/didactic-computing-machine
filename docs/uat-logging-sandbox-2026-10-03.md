# 可观测与沙箱回滚 · UAT 执行支持包（G6.2 交付物）

- **文档日期**：2026-10-03
- **对应**：路线图 v1.1 附录 A 六场景（UAT-G1-1 ~ UAT-G5-1）终验
- **用法**：现场验收由甲方见证、乙方操作；每个场景给出 前置条件 / 步骤 /
  预期证据 / 自动化预演状态（CI 已预演 = 机器可复现，现场为人工见证同一动线）
- **环境**：独立演练实例（勿用生产）；DeepSeek 余额需充足（涉及真实 LLM 的场景）

---

## UAT-G1-1｜全链路溯源（20 分钟）

- **前置**：管理员账号（cio）；一个将提交报销的员工账号。
- **步骤**：
  1. 员工在对话页提交一笔报销（如 666 元办公用品）；
  2. 评审员**仅凭报销单号**（对话回复中给出），打开管理台「操作日志」页，
     在"业务单据号"框输入该单号查询；
  3. 从命中行进入详情，点「查看同链全部操作」；
  4. 另以 trace_id（详情页可见）验证二等入口一致。
- **预期证据**：链路上依次可见 消息/Agent 决策 → `tool.reimbursement_submit.executed`
  → 治理面校验与审计 → 后续审批/工作流节点；每条记录 actor/action/时间完整；
  单据号 = business_key 列值。
- **自动化预演**：`tests/test_trace_g1.py::test_full_chain_trace_consistency_reimbursement_submit`
  （同链断言 + 单据号反查断言已 CI 通过）。
- **不通过判据**：评审员需要借助后台手工查库才能还原链路。

## UAT-G2-1｜非 Agent 操作审计 + 告警送达（10 分钟）

- **前置**：配置 `ERP_ALERT_WEBHOOK_URL`（飞书/钉钉机器人），重启后端。
- **步骤**：
  1. 用错误密码连续尝试登录 5 次；再用无权限账号访问薪酬接口；
  2. 管理员在「操作日志」页按动作前缀 `user.` / `rbac.` 查询；
  3. 做一次 RBAC 变更（如给测试用户分配角色）再查询；
  4. 检查消息中心与 webhook 群机器人。
- **预期证据**：登录失败/越权/RBAC 变更均有操作日志行（来源 http，风险标记）；
  webhook 群收到 JSON 告警（越权类）；`operationlog.query` 自审计行可见（审计者被审计）。
- **自动化预演**：`tests/test_operation_log_g2.py`（订阅流入/登出/知识库接入）+ 
  `tests/test_operation_logs_g3.py`（导出自审计）。

## UAT-G3-1｜篡改检测当场点名（10 分钟）

- **前置**：cio 或 log_auditor；演练库（生产库禁做本场景）。
- **步骤**：
  1. 演练库手工篡改一条审计记录：
     `UPDATE audit_logs SET details='{"i":999}' WHERE entry_id='<任选>'`；
  2. 「操作日志」页 → 完整性卡片 →「立即全量校验」；
  3. （可选次日）验证例行增量校验报告。
- **预期证据**：校验返回 `broken`，`broken_at` = 被篡改条目，`expected/actual`
  哈希并列；critical 告警 `audit_chain_broken` 送达消息中心/webhook。
- **自动化预演**：`tests/test_operation_logs_g3.py::test_verify_chain_detects_tampering`
  （篡改→broken_at 点名→断链告警，已 CI 通过）。

## UAT-G4-1｜误操作回滚 + 反向用例（20 分钟）

- **前置**：cio（发起）+ cfo 等审批人；两个测试单据。
- **步骤**：
  1. **正向**：对话发起一笔资金调拨（write_high）→ 审批单上**先看影响面**
     （金额/账户/调拨后余额/是否转负——影子执行）→ 批准执行；
  2. 从账本（`GET .../operation-rollback/ledger?business_key=<调拨单号>`）
     发起回滚（`POST .../{ledger_id}/request`，reason 填"收款方错误"）；
  3. 审批人在审批中心批准；
  4. **反向**：构造一笔已被下游引用的调拨（测试数据），重复发起回滚。
- **预期证据**：正向——调拨单恢复（status=cancelled）、账本 `compensated`、
  `rollback.requested/executed` 双审计、`rollback:{ledger_id}` 审批卡留痕；
  反向——返回 `rejected_precheck` + 下游引用清单（拒绝是红线行为）。
- **自动化预演**：`tests/test_rollback_g4.py`（全闭环/precheck 拒绝/重放拒绝/失败重放，
  已 CI 通过）。

## UAT-G4-2｜回滚失败人工介入 + 重放幂等（10 分钟）

- **前置**：测试环境可注入补偿故障。
- **步骤**：注入补偿异常 → 发起并批准回滚 → 观察告警与账本 → 修复后重新发起 →
  批准重放 → 再对已成功的账本行重复发起。
- **预期证据**：`compensate_failed` + `compensation_error` + error 告警
  （`rollback_compensation_failed`）；修复后重放成功（`compensated`）；
  对已成功行的重复执行被状态机拒绝（"状态已变更，可能已回滚或存在并发回滚"）。
- **自动化预演**：`tests/test_rollback_g4.py::test_rollback_failure_and_replay` 等。

## UAT-G5-1｜工作流中断补偿 + kill -9 恢复（20 分钟）

- **前置**：演练 DSL 或注入点（见 runbook 剧本一/二）。
- **步骤**：
  1. 发起采购到支付工作流，中途制造失败；
  2. 观察 `workflow.saga_compensated` 审计 + `sagas` 行状态；
  3. 在补偿中途 `docker kill erp-backend` 制造崩溃，重启（`up -d`）；
  4. 观察启动日志 `app.workflow_saga_recovered`。
- **预期证据**：付款计划等创建类步骤被逆序自动清除；资金终态类步骤出现在
  `needs_manual` 名单并触发 `workflow_saga_needs_manual` error 告警；
  崩溃重启后补偿续跑完成（`compensated`）。
- **自动化预演**：`tests/test_workflow_saga_g5.py`（E2E 注入失败→逆序撤销报销单/
  needs_manual 点名/崩溃恢复，已 CI 通过）。

---

## 验收结论模板

| 场景 | 现场 result | 证据留存（截图/导出） | 甲方签署 |
|------|-------------|------------------------|----------|
| G1-1 | | | |
| G2-1 | | | |
| G3-1 | | | |
| G4-1 | | | |
| G4-2 | | | |
| G5-1 | | | |

六场景全过 = 路线图 M-G6 出口达成（§7.5）；任一不过按缺陷流程回修后补验该场景。
