# 生产部署定稿（Phase E / E6）

- **文档日期**：2026-09-19
- **适用**：Agent 生产化路线图（roadmap-agent-production-2026-09-19.md）Phase A~E 全部交付后的部署基线
- **前置**：Phase A~D 已完成（编排基座/24+工具/工作流/审批前端/Supervisor/MCP/Skills）

---

## 1. 全栈服务拓扑（docker-compose）

| 服务 | 端口 | 说明 |
|------|------|------|
| erp-backend | 8000 | FastAPI + LangGraph 编排（内嵌） |
| erp-frontend | 3000 | React（vite build 产物） |
| erp-mysql | 3306（内网） | 业务库 + Checkpointer + 审计 |
| erp-redis | 6379（内网） | 会话/吊销/限流/RBAC 版本信号 |
| **erp-mcp**（profile: mcp） | 8100 | MCP 能力出口（7 域 26 工具，streamable HTTP） |
| **erp-sandbox**（profile: sandbox，默认关闭） | 无网络 | 代码执行隔离容器（四件套隔离基线见 infra/sandbox/README.md） |

MCP 启动：`docker compose --profile mcp up -d erp-mcp`
沙箱启用：`ERP_SANDBOX_ENABLED=true` + `--profile sandbox`（默认无代码执行类工具，无需启动）

## 2. 环境变量基线（生产必改项）

| 变量 | 要求 |
|------|------|
| ERP_JWT_SECRET | 随机强值（勿用示例占位） |
| ERP_MYSQL_URL | 随机强密码；erp 账号最小权限 |
| ERP_MCP_AUTH_TOKEN | **MCP 出口必须配置**（未配置为开发模式无鉴权） |
| ERP_SEED_PASSWORD | 留空（随机密码首次播种打印一次）；生产改密后作废 |
| ERP_LLM_API_KEY / ERP_LLM_ENABLED | 有效 DeepSeek Key；关闭则全部编排降级规则路径 |
| ERP_AGENT_DAILY_TOKEN_BUDGET | 单 Agent 日 token 预算（默认 200 万） |
| ERP_EVENT_RETENTION_DAYS 等 | 保留策略（events 7/snapshots 30/audit_logs 90） |

## 3. 灰度开关（按域逐步放开自主性）

| 开关 | 效果 |
|------|------|
| ERP_LLM_ENABLED=false | 全部 Agent 降级规则主干（编排/工具调用停用） |
| 工具风险分级 | write_low 免审+事后抽样；write_high 强制人审（审批中心） |
| ERP_AGENT_DAILY_TOKEN_BUDGET | 超限该域编排自动降级，不影响其他域 |
| MCP profile | 不启动 = 无外部能力出口；启动必配 token |

## 4. 回滚预案

1. **单工具回滚**：全部写工具注册了幂等补偿（Saga 语义），治理面审计可追溯；
   演练覆盖见 `tests/drill/test_rollback_drills.py`（CI 阻断线）；
2. **编排层回退**：LLM 异常/预算超限自动回退单发对话与规则主干（无人工干预）；
3. **工作流中断恢复**：人审等待中的图状态在 MySQL Checkpointer，重启不丢；
   状态损坏（校验和不符）显式报错，删除对应 thread 重建；
4. **版本回退**：`git revert` + `docker compose up -d backend`；schema 演进
   为增量（bootstrap 表/列差集），回退代码后新表/列无害滞留；
5. **应急停用 Agent 自主性**：`ERP_LLM_ENABLED=false` 一键全局降级（业务页面不受影响）。

## 5. LiteLLM 网关评估结论（决策 D3 约定）

**结论：暂不引入，保持现有治理。**
- 现状已满足：per-agent token 计量入审计哈希链（llm.tokens）+ 日预算超限自动
  降级 + 客户端层冷却/熔断/降级 + LLM 失败冷却（402 场景 0 空转，已验证）；
- LiteLLM 的增量价值在**多模型路由/故障切换**——当前单模型（DeepSeek）无此需求；
- 触发重评估条件：①需要多模型灰度/回退 ②出现第二个 LLM 供应商 ③网关级
  预算报表需求（现以审计表聚合可满足）。

## 6. 上线核对清单

- [ ] 手动 push 本地 main → 服务器 git pull
- [ ] `docker compose build && docker compose up -d`（bootstrap 自动建表/补列）
- [ ] 观察启动日志：`bootstrap_column_added`（补列清单）、`scenario_tools_registered`
      （26 工具）、`app.agent_hydration_complete` ×7、`app.supervisor_agent_ready`
- [ ] 种子密码：首次播种日志 `rbac.seed_credentials_one_time`（仅新库）
- [ ] 充值 LLM 账户；在总调度频道验证跨域协同、资金管家验证人审批恢复
- [ ] MCP 出口（可选）：配 `ERP_MCP_AUTH_TOKEN` 后 `--profile mcp` 启动
- [ ] 冒烟：登录 → 总调度发跨域指令 → 审批中心批准 → 回频道看执行结果
