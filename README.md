# 企业数字化大脑（Agent-Native ERP · 全面重构版）

智能体原生 ERP 系统 —— 以自主 AI 智能体为基本业务单元的企业数字化大脑。
以[腾讯 Octop](https://github.com/TencentCloud/Octop) / [AgentScope](https://github.com/agentscope-ai/agentscope)
为能力底座借鉴**全面重构**的新一代架构：五层角色 RBAC、智能体会话（三权限模式 + 工作区 +
AI 意图自动生成文档）、42 位专家与 6 大专家团、三级权限知识库、四类 IM 会话、
能力市场（专家/Skill/MCP/插件 + 上传安全审查）、多图表经营看板、模型接入与 token 额度治理。

> 当前状态：**全面重构 M0~M12 全部交付**（PRD 见 `docs/prd/`，增量批次 PRD-2026-11-02-platform-enhancement v1.0）。
> 旧架构（LangGraph 编排版）已下线，完整代码只读归档于 `legacy/` 目录。

## 核心能力

### 智能体会话（WorkBuddy / Trae 式）
- **三种权限模式**：完全权限（自主执行，工作区可增删改查）/ 审批+自主（默认，高风险需人审）/ 沙箱（产物先落 `_sandbox/`，人工批准后才写入工作区）
- **工作区**：每个会话独立目录，承载附件与产物文件，右键删除连带清理
- **AI 意图自动生成文件**：按对话内容自动判断是否生成产物及最优格式（docx / pdf / pptx / xlsx / csv / md），一次模型决策同时给出格式、理由与内容 spec——用户不必明说"生成文件"
- **长期记忆**（AgentScope 式）：自动抽取偏好/事实 → 相关性+重要度+新近度召回 → 跨会话注入个性化上下文
- **能力引用**：知识库条目 / 资料库定稿 / 专家·专家团 / Skill / MCP / 插件 / 数字分身，一键挂载进对话；专家团触发"成员分工 → 分视角分析 → 汇总"协同
- **上网能力**：内置 Web 插件内核——消息含 URL 自动浏览正文，搜索意图自动联网检索（Bing 优先 / DuckDuckGo 兜底）
- **思考过程可视化**：意图解析 → 能力装配 → 记忆召回 → 网络动作 → 模型调用（含 token 数）→ 文件落盘，全程时间线可折叠展示
- **会话标题 AI 自动生成**；模式/工作区/模型内联于输入卡片，新建会话零弹窗

### 模型接入与额度治理
- 管理台接入任意 **OpenAI 兼容 / Claude 双协议**模型（Base URL + Key + 模型 ID），启停/设默认/移除，API Key 掩码展示
- **token 日额度**按用户分配/停用/恢复，网关计量入账、超限拒绝；用户侧额度进度条实时可见
- 网关级 429/5xx 指数退避重试

### 专家体系（世界 500 强通用方向 · 42 专家 + 6 专家团）
- 旧系统 33 个子 Agent 全量改建为专家角色（人设 + 职责 + 方法论 + 技能标签），覆盖财经 / 供应链与制造 / 技术与研发 / 人力与组织 / 法务与合规 / 市场与客户六大域
- 补齐技术与研发方向（技术/研发/产品/信息安全/知识产权）、公关、培训发展、后勤保障等通用岗位
- 跨域专家团协同：引用后按成员分工、分专业视角分析并汇总

### 知识库（三级权限）
- **公司库**：全员可读，仅系统管理员可维护
- **部门库**：本部门可读，仅本部门管理员可维护
- **个人库**：仅所有者可见可维护——**对他人与其余智能体完全不可感知**（对话装配同源过滤）
- **共享申请流**：个人条目可申请共享至部门（部门管理员审批）或公司（系统管理员审批），批准即生效
- 内置 20 条 AI/Agent 行业精选（白皮书/开源书籍/经典论文，含来源链接）

### 能力市场（五类目 + 安全审查）
- 专家 / 专家团 / Skill / MCP / **插件** 五类目，领域过滤、技能标签、官方徽章
- **上传即安全扫描**：10 类风险模式（远程脚本执行/后门/钓鱼/凭据窃取/数据外传/挖矿/混淆执行/高危权限/系统文件访问/键盘记录），高危判恶意、中危判可疑
- 风险条目自动推送**管理员待办告警**并向**上传者**返回命中详情与处置建议；管理台审核 Tab 批准/驳回闭环
- 内置能力库：Skill 148+ / MCP 99+ / 办公场景插件 59（均经安全扫描，自 GitHub 与 ClawHub 爬取）

### IM 消息中心（四类会话）
公司全员群 / 部门全员群 / 员工单聊（幂等建会话，仅双方可见）/ 数字分身私聊（管理员保留治理视角），发言权限按频道类型校验

### 经营看板（多图表）
收支盈亏折线、支出构成饼图、项目预算柱状、部门预算横向柱、项目进度卡（状态徽章 + 超支警示）、部门人员分布、会话活跃趋势——分管领导与董事长全域驾驶舱分级呈现

### 安全基线
- **审计哈希链**：全部关键动作（对话/拦截/审批/共享审核）SHA-256 逐条链式留痕，防篡改可验证
- **D-R6 数字分身恶意意图研判**：越权/套取/冒充/注入/提示词套取六类模式实时拦截 + 双管理员告警
- 五层角色服务端强制（sys_admin / chairman / leader / dept_admin / employee），前后端权限矩阵一致
- 真实密钥仅存 `.env`（不入库，仓库内均为占位模板）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.13 / FastAPI / SQLAlchemy 2.0 (async) / SQLite（默认，零配置）/ MySQL 可选 |
| LLM | OpenAI 兼容 + Claude 双协议网关（httpx 直连，多供应商可插拔） |
| 文档生成 | python-docx / openpyxl / python-pptx / reportlab（CJK 字体自动注册） |
| 前端 | React 18 / TypeScript / Vite 5 / Tailwind CSS / Zustand / Recharts / lucide-react |
| 网络插件 | httpx + BeautifulSoup（Bing/DDG 搜索引擎适配） |
| 部署 | 本地直跑（uvicorn + Vite）；`infra/` 与 `docker-compose.yml` 提供容器化参考 |

## 快速开始

```bash
git clone https://github.com/huoqi1004/didactic-computing-machine.git
cd didactic-computing-machine

# ---- 后端（Python 3.10+）----
cd backend
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
copy .env.example .env                             # 填入 ERP_LLM_API_KEY（OpenAI 兼容）
python -m uvicorn app.main:app --port 8000         # 启动自动建表/增量种子（用户/看板数据/专家/模型）

# ---- 前端（Node 18+，新终端）----
cd frontend
npm install
npm run dev                                        # http://localhost:5173
```

> `.env` 最小配置：`ERP_LLM_BASE_URL` / `ERP_LLM_MODEL` / `ERP_LLM_API_KEY` / `ERP_LLM_ENABLED=true`。
> 未配置模型时系统可运行（对话降级为意图回执），其余功能不受影响。

### 种子账号（启动自动播种，五层角色）

| 用户名 | 密码 | 角色 |
|---|---|---|
| `sysadmin` | `SysAdmin@2026` | 系统管理员 |
| `chairman` | `Chairman@2026` | 董事长（全域驾驶舱） |
| `ceo` / `cfo` / `cio` | `CEO@2026` / `CFO@2026` / `CIO@2026` | 主管领导（看板） |
| `dept_admin_finance` | `DeptFin@2026` | 部门管理员 |
| `employee_finance` / `employee_it` | `EmpFin@2026` / `EmpIT@2026` | 普通员工 |

> 开发演示用途；生产环境请通过「个人设置」改密并禁用种子账号。

### 测试

```bash
cd backend && python -m pytest tests/ -q    # 新架构测试基线 10/10
```

## 项目结构

```
├── backend/
│   ├── app/
│   │   ├── main.py            # 入口（lifespan：建表/增量种子/模型关闭钩子）
│   │   ├── models.py          # 全部表模型（用户/审计链/知识/资料/IM/会话/模型/额度/财务/项目…）
│   │   ├── routers/           # auth·conversation·im·knowledge·records·market·dashboard·admin·admin_models·todos
│   │   └── core/              # llm_gateway(双协议+额度)·memory(长期记忆)·office_gen(六格式文档)
│   │                          # web_plugin(上网)·security_scan(内容安全)·expert_catalog(42专家)
│   │                          # intent_guard(D-R6研判)·audit_chain(哈希链)·llm·jwt·rbac
│   ├── scripts/               # fetch_capabilities.py(Skill/MCP爬取+筛查)·fetch_plugins.py·seed_knowledge.py
│   └── tests/                 # 测试基线
├── frontend/src/
│   ├── components/Shell.tsx   # 布局壳（会话历史/导航/待办/额度）
│   └── pages/                 # Chat(会话)·Knowledge·Records·IM·Market·Dashboard·AdminConsole·Settings·Login
├── docs/
│   ├── prd/                   # PRD 版本管理（full-rebuild + platform-enhancement + CHANGELOG）
│   └── …                      # 部署/架构/评审归档
├── legacy/                    # 旧架构只读归档（LangGraph 编排版，1521 例测试基线）
└── infra/                     # k8s/沙箱/监控参考
```

## 文档索引

| 文档 | 内容 |
|------|------|
| `docs/prd/PRD-2026-10-05-full-rebuild-v0.1.md` | 全面重构 PRD v1.0（已交付：M0~M5） |
| `docs/prd/PRD-2026-11-02-platform-enhancement-v1.0.md` | 平台化增量 PRD v1.0（已交付：M6~M12） |
| `docs/prd/CHANGELOG.md` | PRD 版本变更总账 |
| `docs/deployment-production.md` / `docs/architecture.md` | 旧架构生产部署与架构参考（归档） |
