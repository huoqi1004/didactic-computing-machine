# 迭代执行日志（2026-09-13）

- **依据**：《迭代路线图（2026-09-03）》（`docs/iteration-roadmap-2026-09-03.md`）
- **执行范围**：Phase 1 收尾 + Phase 2 全部核心任务（2.1～2.9）
- **执行方式**：单次自主迭代，全程以自动化测试验证；提交前以改动前基线对照甄别历史遗留失败

---

## 1. 本次交付总览

| 提交 | 内容 | 对应任务 |
|------|------|---------|
| `448c0aa` | 种子密码治理 + alembic 真实基线 + 列级差集补列 + 冒烟测试 | 1.2 收尾 / 1.4 / 1.6 |
| `1a14d35` | Redis 落地 + 会话/JWT 纵深 + 审计链续链与业务审计 + 保留策略 | 2.5 / 2.6 / 2.7 / 2.8 |
| `668e1e7` | Repository 层下沉 + 批次一迁移（payroll/hr/crm/sales）+ 分页 + 行级隔离首批 | 2.1 / 2.2 / 2.4 / 2.9 |
| 本次 | 批次二迁移（treasury + contract）+ 迭代日志 | 2.3 |

**Phase 1 至此全部完成，Phase 2 任务清单 2.1～2.9 全部落地。**

---

## 2. Phase 1 收尾（448c0aa）

### 1.4 种子弱密码治理（D-04）
- 24 个种子用户废除 `{username}123` 弱密码：默认每用户随机生成 12 位强密码，**首次播种后一次性打印**到服务端日志（重复启动不重复打印，避免与库内密码混淆）；可经 `ERP_SEED_PASSWORD` 统一注入（仅限演示/联调）。
- `users` 表新增 `must_change_password` 列（`server_default=1`，存量库经 bootstrap 列差集自动补列，存量弱密码用户全部被标记）；登录响应与 `/auth/me` 携带该标记；新增 `POST /api/v1/auth/change-password`（改密后全端注销会话）。
- 登录成功/失败（user_not_found / password_mismatch / user_inactive）与改密全量审计。

### 1.2 建库链路收尾（C-01/C-03）
- alembic 唯一迁移从 `pass` 空壳重建为真实基线：以 `app/db/models.py` metadata `create_all`（幂等，`alembic upgrade head` 已在 sqlite 实测两次无报错并建出 34 表 + alembic_version）；存量库用 `alembic stamp head` 纳管。
- `bootstrap_schema` 升级为**表级差集 + 列级差集**：存量表缺列时自动 `ADD COLUMN`（仅增量，无默认值的 NOT NULL 列降级为可空以避免锁表失败），破坏性变更仍走 alembic。**这是后续所有批次扩展列能在云端平滑生效的前提**。
- `init-db.sql` 正式删除（compose 挂载此前已移除），部署文档同步。

### 1.6 冒烟测试（C-01/B-01 回归保护网）
- `test_bootstrap_smoke.py`：sqlite 全新库建表/幂等/补列保数据（始终执行）+ 真实 MySQL 冒烟（`ERP_TEST_MYSQL_URL` 启用，权限不足显式 skip 并注明原因）。
- `test_llm_cost_governance.py`：402 欠费模拟下出站请求被封顶在冷却阈值（对照 B-01 基线 3,600 次/小时）；冷却到期自动恢复；成功重置计数；treasury on_tick forecast 缓存命中时不重算、不重复发事件。

### 附带修复
- `chat_messages` 重复索引定义（sqlite 全局索引命名空间冲突，导致建表中断）。
- sqlite 引擎不接受 MySQL 连接池参数（`_build_engine` 方言感知）。

---

## 3. Phase 2（1a14d35 + 668e1e7 + 本次）

### 2.5/2.6 Redis 落地与会话纵深（A-01/D-03）
- 新增 `app/core/redis_store.py` 安全访问层。**借鉴 LiteLLM 网关的 fail-open 模式**：Redis 故障时读→未命中、写→忽略，调用方降级进程内实现，单实例行为与落地前完全一致；降级告警按类别 60 秒节流（吸取 C-01 静默降级教训）。
- **lifespan 补上 `init_redis()`**（此前 Redis 仅在 /health 懒加载，业务零使用——A-01 的根因之一）。
- 会话双层存储：内存 L1 + Redis L2（TTL=会话剩余有效期，键为令牌 SHA-256 摘要，明文令牌不落 Redis）；**A 实例登录的会话 B 实例可查，A 实例登出后 B 实例旧令牌立即失效**——这解除了 9/4 生产部署"多 worker 必须单 worker"的限制（见 `.workbuddy/memory/2026-09-04.md`）。
- 限流：Redis 共享固定窗口（INCR+EXPIRE）优先，多实例全局生效；降级回进程内滑动窗口。
- RBAC 跨实例新鲜度：9 个变更方法递增 `erp:rbac:version`，认证中间件经 `ensure_fresh()`（5 秒节流）检测漂移并从库重载。
- JWT：密钥仅环境变量注入核实通过（`.env.example` 占位、`backend/.env` 不入库）。

### 2.7 审计链纵深（D-05）
- `restore_chain_from_db()`：启动时从 `audit_logs` 恢复哈希链尾，**重启不再断链**（挂入 lifespan）。
- 四类关键写操作 100% 审计：登录（1.4 顺带）、RBAC 变更 5 端点（user/role 增删改，risk=high）、薪酬写入 4 端点、销售下单/确认。
- 内存审计条目上限 10000（完整历史仍在库中）。

### 2.8 保留策略（B-03）
- `app/core/maintenance/retention.py`：events 7 天 / snapshots 30 天 / audit_logs 90 天（`ERP_*_RETENTION_DAYS` 可配，审计保留期断言长于事件）；MySQL 分批 DELETE（5000/批）防长锁；lifespan 后台任务启动清一次 + 每 6 小时周期执行，关闭时取消。

### 2.1 Repository 层下沉（E-01/C-02）
- 架构采用路线图 §3.2 决策：**下沉 repository 层，而非 33 个 Agent 各自直连 DB**（参照系统内 messaging 直用 AsyncSession 与 rbac_engine 双轨范本；Dify 的 message_repository 模式同构）。
- `RepositoryBase` 提供通用 upsert/list/count，子类声明 ORM 模型与双向字段映射；`schedule()` 承载 fire-and-forget 双写。

### 2.2 批次一迁移：payroll/hr/crm/sales
- 表结构（全部经 bootstrap 差集平滑上云）：新增 `salary_structures` 表；`payroll_records` 扩展 record_id/allowances/gross_salary/housing_fund/taxable_income/individual_tax/source/detail_json；`employees` 新增 department_name（**命名避开同名 relationship 冲突**——这是联调中抓到的真实坑）；`sales_orders` 扩展 customer_name/items/confirmed_at；`customers` 新增 owner_department。
- Agent 集成 `hydrate_from_db()`：**DB 优先、内存种子兜底、空库回播种**；写方法全部写穿（薪资结构/薪酬计算/事件同步记录/员工/考勤/客户/订单创建与确认）。
- lifespan 在 Agent 启动前执行水合，**顺序敏感**：crm 先于 sales（FK 前置）。
- 修复历史假数据缺陷：CRM 种子客户改用稳定 ID `CUST-SEED-001..005`，与销售种子订单引用对齐（此前订单引用不存在的客户）。
- BigInteger 自增主键统一 `BigIntPK`（`with_variant(Integer, "sqlite")`），保证测试（sqlite）与生产（MySQL）行为一致。

### 2.3 批次二迁移：treasury + contract
- 新增 `payment_schedules` 表（get_payment_schedule 接口数据真实化的前提）；`fund_transfers` 扩展 reason/requires_human_approval/rejected_at；`contracts` 扩展 contract_type/department/terms/approved_by/approval_comment/approved_at/completion_notes。
- treasury：调拨创建/审批、支付排程生成写穿；现金流缓存与现金余额不做持久化（余额应基于事件流重放，属 Phase 3 事件溯源范畴，已在代码注释声明）。
- contract：创建写穿 + 审批/执行/完成状态增量更新；模板与执行记录暂不持久化（Phase 3）。

### 2.4 分页首批（A-02）
- `/crm/customers`、`/sales/orders`、`/hr/employees`、`/payroll/records` 统一 `skip/limit`（total 为过滤后总数，响应体有界）；未迁移列表保持原状。

### 2.9 行级隔离首批（D-02，灰度）
- 数据层：`customer_repository.list_by_department()` 落库侧过滤（归属部门为空默认全可见——语义与 Odoo ir.rule 的"全局规则不可放宽"思路一致）。
- 路由层：`GET /crm/customers` 无 `crm:manage` 权限的用户仅见归属本部门客户，**过滤动作记 `row_level_filter_applied` 观察日志**（按路线图灰度要求：先日志观察再扩展）。

---

## 4. 参考借鉴（调研结论的落地对应）

| 借鉴来源 | 落地点 |
|---------|--------|
| LiteLLM Redis fail-open（降级不放大 + 显式告警） | `redis_store.py` 全部助手的降级语义与节流告警 |
| Dify message_repository（Agent 状态走统一 Repository） | `app/db/repositories/` 整体结构 |
| Odoo ir.rule（全局/组规则合成，行级规则下沉数据层） | `list_by_department` 的"空归属默认可见"规则下沉 |
| Frappe audit trail / RFC 6962（追加式哈希链） | 审计链续链、`rbac.*` 变更 high risk 审计 |
| SAP Joule / Agentforce（Agent 与人同一套 RBAC/审批） | 薪酬/销售写端点的 `require_permission` + 金额阈值人工审批沿用 |

---

## 5. 验证结果

- 新增测试 7 个文件 / 41 个用例全绿：seed_password(5)、llm_cost_governance(4)、bootstrap_smoke(3+1 MySQL 可选)、redis_backend(7)、retention(5)、agent_persistence(7 含批次二)、本次批次二并入 agent_persistence。
- 受影响存量回归（bootstrap/payroll/hr/sales/crm/treasury/contract/api/seed/retention/redis）92+53 passed。
- 全量套件约 1014 passed；**17 个失败经改动前基线（git stash 对照）确认全部为历史遗留**（e2e 事件契约漂移 + 跨文件测试隔离问题），与本次改动无关，已列入 Phase 3 任务 3.6（测试保护网重建）处理。

## 6. 遗留与下一步（Phase 3 对齐）

1. **3.6 测试保护网**：修复 e2e 流程测试的事件契约漂移（如 `REPLENISH_PROPOSED` 无订阅方）、跨文件全局单例污染导致的 agent 测试偶发失败。
2. **3.1 剩余 Agent 迁移**：project/esg/bi 等 13 个 `_seed_data` Agent 按同模式推进（repository 模式已固化，边际成本大幅下降）。
3. **3.3 RAG 向量化 / 3.2 LLM 编排试点 / 3.5 前端对齐**：按路线图推进；前端需适配 `must_change_password` 首登改密引导与分页参数。
4. **部署注意**：云端更新后首次启动时，bootstrap 会自动补列（users/payroll_records/sales_orders/customers/contracts/fund_transfers）并新建 salary_structures/payment_schedules 表；**种子用户首次登录密码在首次部署日志中一次性打印**（`rbac.seed_credentials_one_time`），旧 `admin123` 已失效；行级过滤处于灰度观察期，关注 `row_level_filter_applied` 日志。
