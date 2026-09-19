# 企业数字化大脑（Agent-Native ERP）

智能体原生 ERP 系统 —— 以自主 AI Agent 为基本业务单元的企业数字化大脑。
**33 个业务 Agent 全部经 LangGraph 编排真实执行业务操作**（工具调用循环 + 治理面 + 人在环 + 可回滚），
非演示外壳。

> 当前状态：生产化路线图 Phase A~E 全部交付（编排基座 / 批量推广 / 多 Agent 协同 / MCP 化 / 生产加固）。
> 设计与验收基线见 `docs/roadmap-agent-production-2026-09-19.md` 与 `docs/deployment-production.md`。

## 核心能力

### 智能体编排（LangGraph）
- **场景图**：意图理解 → 工具调用循环 → 综合答复；子域场景图按域编译缓存
- **MySQL Checkpointer**：人审断点持久化——审批等待中重启不丢，批准后从断点恢复执行
- **Supervisor 跨域协同**：LLM 意图分类（规则兜底）→ 逐域委派 → 综合答复，人审透传

### 工具治理面（AgentGovernor——一切工具调用的强制通道）
- **24+ 治理面工具**：查询 15（报销/资金/薪酬/HR/CRM/销售/合同）+ 操作 9（下单/审批/薪资/调拨…）
- 风险分级：`readonly` / `write_low`（免审+事后抽样）/ `write_high`（强制人审）
- **可回滚才可执行**：写工具必须注册幂等补偿函数（缺失拒绝注册），全量演练进 CI
- RBAC 权限 + 参数 schema 校验 + dry-run 预演 + 审计哈希链 + 限流 + token 预算护栏

### 人在环（Human-in-the-Loop）
- LangGraph `interrupt()` + `Command(resume)` 现行模式；审批等待跨重启恢复
- 审批中心前端（批准/驳回/看预演）+ 频道内审批卡片，决定后自动恢复执行并广播结果

### 跨域业务工作流
报销审批流 / 采购到支付 / 合同审批流——确定性规则节点 + 分级人审门，
全程 Checkpointer 持久化、可中断恢复、逐节点审计。

### MCP 能力出口
7 域 26 工具经 FastMCP 导出为标准 MCP 服务器（streamable HTTP + Bearer 鉴权），
与本地调用**双跑一致性**测试保证；外部 MCP 工具白名单接入（治理面强制过桥）。

### 知识库（WeKnora 模式适配）
四路混合检索（TF-IDF + BM25 + 知识图谱 + **语义向量**）、结构化分块、
MySQL 分块/向量持久化（**重启不归零**）、`knowledge_search/answer` 治理面工具
（引用溯源，全部 Agent 可调用）、Embedding 可插拔（OpenAI 兼容 API / 零成本哈希兜底）。

### 安全与治理基线
- 写操作分级审批（write_low 免审+抽样 / write_high 强制人审）
- 代码执行类工具默认禁用（沙箱容器四件套隔离：无网络/只读FS/非root/资源限额）
- LLM 成本治理：per-agent 日 token 预算超限自动降级、402 欠费 0 空转（已验证）、token 消耗入审计
- 会话/吊销/限流 Redis 共享（多实例一致）、事件/审计保留策略、审计哈希链重启续链

## 技术栈

| 层级 | 技术 |
|------|------|
| 编排 | **LangGraph 1.2 / LangChain 1.4**（StateGraph / interrupt / Checkpointer） |
| 后端 | Python 3.11 / FastAPI / SQLAlchemy 2.0 (async) / Pydantic 2 |
| LLM | DeepSeek（OpenAI 兼容），客户端层冷却/熔断/降级 |
| MCP | fastmcp 4（server）/ mcp 2.2 官方客户端（streamable HTTP） |
| 前端 | React 18 / TypeScript / Vite 5 / Tailwind CSS / Zustand |
| 数据 | MySQL 8.0（业务+Checkpointer+审计）/ Redis 7（会话/限流/版本信号） |
| 部署 | Docker Compose（backend/frontend/mysql/redis/mcp/sandbox profiles） |

## 快速开始

### 本地开发

```bash
git clone https://github.com/huoqi1004/didactic-computing-machine.git
cd didactic-computing-machine

# 后端
cp backend/.env.example backend/.env   # 填入 ERP_LLM_API_KEY 与 MySQL/JWT 配置
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000   # 启动时自动建表/补列/播种/水合

# 前端（新终端）
cd frontend && npm install && npm run dev   # http://localhost:5173

# 默认账号 admin / admin123（生产环境种子密码随机生成，见启动日志一次性打印）
```

### Docker 全栈

```bash
cp backend/.env.example backend/.env    # 生产环境按 docs/deployment-production.md 修改
docker compose up -d                     # 前端 3000 / API 8000
# MCP 能力出口（可选）：docker compose --profile mcp up -d erp-mcp  （8100）
```

首次启动自动完成：建表/补列（models.py 唯一事实源）→ RBAC 播种 → Agent 水合 → 知识库水合。

## 测试与质量门禁

```bash
cd backend && python -m pytest tests/ -q          # 全量（约 1100+ 用例）
python -m pytest tests/drill/ -q                  # 回滚演练（全部写工具补偿一致性）
python -m pytest tests/test_agent_evals.py -q     # 编排评测集 50 用例
python -m pytest tests/perf/ -q -m perf           # Checkpointer 性能基准
```

- **回滚演练红线**：全部写工具"执行→补偿→一致性"必须通过，否则 CI 阻断
- **评测集**：50 用例覆盖 7 域（意图→工具序列→落库→人审路由）；在线模式（真 LLM）
  经 `ERP_EVAL_LLM_ENABLED=true` 开启，作提示词/模型漂移门禁

## 项目结构

```
├── backend/
│   ├── app/
│   │   ├── agents/            # 33 业务 Agent + 各域场景工具（tools.py）
│   │   ├── api/routes/        # REST 路由（含 agent-ops 编排运维）
│   │   ├── core/
│   │   │   ├── agent/         # runtime/governor/supervisor/checkpoint_mysql/tools_v2
│   │   │   ├── knowledge/     # 混合检索/embeddings/持久化/图谱/规则
│   │   │   ├── mcp/           # MCP Server（7 域导出）
│   │   │   └── maintenance/   # 保留策略等后台任务
│   │   ├── db/models.py       # 全部表模型（bootstrap 差集建表/补列）
│   │   └── main.py            # 应用入口（lifespan 编排初始化）
│   ├── tests/                 # 单测/集成/评测/演练/性能
│   └── requirements.txt
├── frontend/                  # React（含审批中心/Agent对话/发票识别）
├── docs/                      # 路线图/评估/部署/迭代日志
├── infra/                     # k8s/沙箱/监控
└── docker-compose.yml         # backend/frontend/mysql/redis/mcp(profile)/sandbox(profile)
```

## 安全基线

- 真实密钥仅存 `.env`（不入库）；compose 密码支持环境覆盖，生产必改
- MCP 出口强制 Bearer 鉴权（`ERP_MCP_AUTH_TOKEN`）；未配置不得对外暴露
- write_high 操作 100% 人审（审计哈希链可追溯）；种子用户首登强制改密
- 生产部署核对清单与回滚预案：`docs/deployment-production.md`

## 文档索引

| 文档 | 内容 |
|------|------|
| `docs/roadmap-agent-production-2026-09-19.md` | Agent 生产化路线图（A~E 决策与验收基线） |
| `docs/deployment-production.md` | 生产部署定稿（拓扑/灰度开关/回滚预案/LiteLLM 结论） |
| `docs/iteration-roadmap-2026-09-03.md` | 原始迭代路线图（Phase 1/2 数据与安全基线） |
| `docs/api-reference.md` / `docs/architecture.md` | API 与架构参考 |
| `docs/iteration-log-2026-09-13.md` | 迭代执行日志 |
