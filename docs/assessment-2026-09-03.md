# 企业数字化大脑（Agent-Native ERP）深度技术评估报告

- **评估日期**：2026-09-03
- **评估人**：架构师 高见远（Bob / software-architect-2）
- **评估范围**：后端 `backend/`（Python 3.11 / FastAPI / SQLAlchemy 2.0 async / aiomysql / Redis），前端仅粗查
- **评估方式**：静态代码审查（带文件路径+行号证据）+ 运行中服务 `localhost:8000` 只读探测（openapi.json / health）
- **基线数据**：app 层 178 个 .py 文件；OpenAPI 实测 **424 个 operation / 350 个 path**；**33 个业务 Agent**；54 个测试文件 / 1029 个测试函数

---

## 1. 执行摘要

**总体阶段判断：原型阶段（Demo-Grade Prototype），远未达到"可用 ERP"，距生产就绪差距巨大。**

一句话结论：**架构外壳的完成度远高于业务实质**。DID 身份、审计哈希链、事件溯源、Saga、熔断器、人在回路、护栏、限流——"企业级组件清单"几乎每项都有代码，但：

1. **业务数据层是空壳**：33 个 Agent 中 **0 个**连接数据库，全部业务数据（销售订单、员工、薪酬、合同、采购……）存放在每个 Agent 实例的 `dict/list` 里，由 `_seed_data()` 硬编码假数据初始化。**重启即全部清零**。424 个 API operation 中仅约 **54 个（≈13%）**真实读写 MySQL（auth/rbac/messaging）。
2. **建库链路断裂**：全仓库没有任何 `create_all` / 有效 Alembic 迁移，当前 35 张表无法复现，全新部署必然失败。
3. **成本治理失效**：资金 Agent 每 tick（≈1 秒）调用一次 LLM，失败被吞掉、不触发熔断，402 欠费状态下仍以 1 次/秒的频率空转打 API。
4. **企业数据安全存在越权硬伤**：任意登录用户可读取任意员工的薪资结构与银行账号明文，全链路无脱敏、无行级隔离。

等级统计：🔴 阻断 **5** ｜ 🟠 严重 **9** ｜ 🟡 一般 **6** ｜ 🔵 建议 **5**

---

## 2. 五维度分述

### A. 性能

**A-01 🟠 Redis 已接入但业务零使用（缓存形同虚设）**
- 证据：`backend/app/core/database.py:208-232` 初始化了 `redis.asyncio` 连接池（`ERP_REDIS_URL=redis://localhost:6379/0`，`.env:29`）；但全仓库 grep `get_redis|_redis_client` 除 `core/database.py` 自身与测试外 **0 个调用点**。
- 影响：RBAC 缓存是"进程内 dict"（`app/core/rbac/engine.py:108-110`）、会话在内存、限流在内存——多实例部署时全部失效。Redis 唯一作用是健康检查 ping。

**A-02 🟡 业务大列表接口无分页**
- 证据：`app/api/routes/hr.py`、`sales_marketing.py`、`operations_strategy.py` 合计 grep `skip|offset|page|limit`（作为查询参数）**0 命中**；Agent 查询方法如 `app/agents/treasury/agent.py:609-613 get_payment_schedule` 全量返回 list。数据量增长后响应体与序列化成本线性恶化。

**A-03 🟡 知识摄取存在同步阻塞 IO**
- 证据：`app/core/knowledge/ingestion.py:116` `pdfplumber.open(...)` 同步解析 PDF（CPU 密集），`ingestion.py:417 ingest()` 为同步方法。当前主要在启动期/管理接口触发，影响有限，但一旦接到热路径 API 会阻塞事件循环。

**A-04 🔵 async 路径整体干净**
- 证据：全仓库唯一的 `time.sleep` 位于 LLM 客户端**同步**分支 `app/core/llm/client.py:643,649`（`chat_sync`），当前无任何业务代码调用 `chat_sync`；未发现 `requests` 库调用；数据库访问全部走 aiomysql/async session。这一点做得规范。

**A-05 🔵 索引设计尚可，启动开销可控**
- 证据：`app/db/models.py` 含 **81 处 `Index(`** 定义，RBAC 查询走 ORM `select()`（`app/core/rbac/engine.py:248-438`），无手写 N+1 温床。lifespan（`app/main.py:338-540`）串行完成 MySQL×2、事件库、审计库、RBAC 播种+加载、知识库注入（仅 5 个 Document + 17 个图节点边，`app/core/knowledge/initializer.py`）、33 个 Agent 注册——秒级启动，不构成问题；小瑕疵是 `init_db` 在 `main.py:362` 和 `main.py:416` **重复调用了两次**。

### B. 成本

**B-01 🔴 资金 Agent 每秒 1 次 LLM 调用，失败无退避、无熔断、无冷却（成本失控实锤）**
- 调用链与频率：`app/core/agent/base.py:283-320` `_run_loop` 每 **1 秒** 执行一次 `on_tick()`；`app/agents/treasury/agent.py:594-607` `on_tick` 无条件调用 `get_cash_flow_forecast(days=7)` → `:384 _forecast_cash_flow_llm` → `llm_analyze` → 真实 LLM API。`.env:50 ERP_LLM_ENABLED=true`、`:45 ERP_LLM_MODEL=deepseek-v4-flash`。
- 失败处理缺陷：`app/core/agent/base.py:844-853` `llm_analyze` 捕获所有异常后仅返回 `{"error": ...}` 字典，**从不调用 `circuit_breaker.record_failure()`**，也没有"连续失败 N 次后暂停 LLM"的冷却逻辑；客户端层 `app/core/llm/client.py` 的 `_raise_for_status` 对 4xx（402）直接抛 `LLMResponseError` 不重试（这部分合理），但上层吞掉后下一秒照样发起调用。
- 量化：当前 402 欠费状态 = **3,600 次失败请求/小时 + 等量告警日志**；一旦充值，最坏 **86,400 次调用/天**（`client.py` 令牌桶 10/s 形同虚设，1 tick 仅 1 并发）。按 flash 模型约 1-2K tokens/次估算，每天数百万 tokens 级别的纯空转成本，且 `treasury.py:460-468` 的规则降级预测（日均净流量线性外推）本身完全够用。
- 修复方向：on_tick 降频到小时级/事件驱动；`llm_analyze` 连续失败触发 agent 级 LLM 开关冷却（如 10 分钟）；forecast 结果按天缓存。

**B-02 🟠 无任何 TTL/结果缓存**
- 证据：`grep lru_cache|TTL` 在核心业务代码 0 命中（仅 RBAC 自建 dict 缓存无过期）。同一份 7 天现金流预测每秒重算重发（`treasury.py:594-607` 每次都 `_publish_event(CASH_FLOW_FORECAST_COMPLETED)`），事件表随之膨胀（见 B-03）。

**B-03 🟠 事件/审计表只增不删，无保留策略**
- 证据：`app/core/event/store.py:65` 事件双写 MySQL（`_db_available=True` 时）；grep `cleanup|retention|purge` 于 `store.py` **0 命中**；`audit_logs` 同理（`app/core/security/audit.py`）。运行中服务实测 event_bus 已 published=541（`/health`）。高频 Agent 事件（如 B-01 的每秒一条 forecast 事件）将以 **8.6 万行/天** 的速度膨胀 events 表。
- 佐证：`docker-compose.yml` backend limit 1G/1CPU（`:80` 附近）本身合理，但存储侧无治理。

### C. 工程效率

**C-01 🔴 数据库 Schema 不可复现，全新部署必然失败**
- 证据链（三条建表路径全部失效）：
  1. `backend/alembic/versions/20260729_2053_c309befbdf7e_initial_schema.py`：全文 30 行，`upgrade()` 内为 **`pass`**（空壳迁移）；
  2. `app/core/migrations.py`（273 行）：仅建 **4 张**系统表 events/snapshots/audit_logs/sagas（`:83,110,126,153`），且只被 `event/store.py:75-86` 与 `security/audit.py:130-141` 为自己调用；
  3. `infra/docker/init-db.sql`：仅 **5 张**旧版遗留表（agents/agent_executions/knowledge_documents/users/user_sessions），与当前 ORM 完全对不上。
- 全仓库 `grep create_all` **0 命中** —— ORM（`app/db/models.py`，34 张表）从未被用来建表。当前 erp_db 的 35 张表来源是历史上某次已丢失的手工操作。
- 影响：全新环境部署后，RBAC/认证/消息等真实持久化路径全部建不出表，系统将"静默降级"到内存模式（所有 `try/except → degraded=memory` 掩盖了失败，`app/main.py:372-466`），表面上 healthy，实际 0 持久化。

**C-02 🟠 19 个 Agent 携带约 1,954 行硬编码假数据，重复度极高**
- 证据：`grep -c _seed_data` → 19 个 `app/agents/*/agent.py`；脚本统计 `_seed_data` 函数体合计 **~1,954 行**（top：project 185 行、esg 147、bi 144、contract 143、hr 127）；104 处 `self._xxx = []/ {}` 内存态属性。这批代码结构高度雷同（构造 dict → append 到内存 list），是典型的应下沉到 repository/fixture 层的重复。

**C-03 🟡 迁移双轨制维护成本**
- 证据：Alembic 空壳（见 C-01）+ 手写 `migrations.py` 并存，两套机制互不知晓；未来任何 schema 变更既没有 Alembic 链，也没有版本化 SQL，只能继续手填。`alembic/env.py` 存在但从未承载真实迁移。

**C-04 🔵 测试数量可观但测不到真问题**
- 证据：54 个测试文件 / **1,029 个 test 函数** vs 424 个 API operation。数量比不差，但测试几乎全部针对内存态 Agent（如 `test_treasury_agent.py`、`test_sales_agent.py`），因此 B-01 的每秒 LLM 空转、C-01 的建库断裂、D-01 的越权均不在测试保护网内——测试给了团队虚假的安全感。

### D. 企业数据安全

**D-01 🔴 任意登录用户可读取任意员工薪资+银行账号明文，全链路无脱敏**
- 证据：`app/api/routes/hr.py:679-688` `GET /payroll/structure/{employee_id}` 依赖仅为 `get_current_user`（对比同文件 `:694` 写接口要求 `require_permission("payroll","create")`，读接口完全放开）；返回体含 `base_salary`、`bank_account` 明文（`app/agents/payroll/agent.py:236-246`），种子数据内置真实格式银行卡号 `6222000000000000001` 等（`payroll/agent.py:473-500`）。
- `grep mask|脱敏|redact` 于 hr/sales_marketing/payroll 路由与 Agent **0 命中**——legacy.py 注释宣称的"意图感知中间件动态脱敏"未覆盖这些路由。薪酬数据是 ERP 最高敏数据之一，属阻断级。

**D-02 🟠 无行级/部门级数据隔离**
- 证据：授权引擎仅有"安全许可等级矩阵"（`app/core/security/authorization.py:137-151`，clearance vs data_level），RBAC 提供路由级权限点，但任何用户/任何角色取到的都是 Agent 单例里的**全量共享内存数据**（如 `get_customers()` 无 owner/department 过滤，`sales_marketing.py:196-199`）。跨部门数据隔离不存在。

**D-03 🟠 会话与令牌吊销仅存内存，JWT 密钥硬编码在仓库内**
- 证据：`app/core/auth/session.py:56` `self._sessions: dict = {}`——登出黑名单/会话表重启即清空（旧 token 复活风险），多实例不一致；`app/core/config.py:144-146` JWT 为 HS256、30 分钟有效期，密钥 `ERP_JWT_SECRET` 硬编码提交在 `backend/.env:66`（且 `.env.example:66` 并存于仓库，密钥随代码库泄露）。无 refresh token 轮换存储、无 jti 持久化吊销。

**D-04 🟠 24 个种子用户使用规律性弱密码**
- 证据：`app/core/rbac/seed.py:764-827`：`admin123 / chairman123 / ceo123 / cto123 / cfo123 / cmo123 / cho123 / cro123 ...` 全部为"职位+123"模式且代码即文档，攻击者可直接枚举。

**D-05 🟡 审计链半成品：有链、有持久化，但覆盖面与连续性不足**
- 正面：哈希链确实串起来了——`app/core/security/audit.py:224-227` `prev_hash=self._last_hash` 且 prev_hash+entry_hash 持久化入库（`:306-309` 参数化 SQL）。
- 缺陷 1：`_last_hash` 是内存变量，**重启后从空重新起链**，审计链断为多段，防篡改证明力大打折扣；
- 缺陷 2：`audit_chain.log` 全仓库仅 **3 个文件 6 处调用**（`agent/base.py:193,255,540,573`、`agent/tools.py:103,116`、`main.py:682`）——只覆盖 Agent 生命周期与工具调用，**用户登录、RBAC 变更、薪酬写入、销售下单等业务写操作 0 审计**。

**D-06 🟡 debug 模式向外网回传完整堆栈**
- 证据：`app/main.py:749-765` 全局异常处理器在 `settings.debug=True` 时把 `traceback` 原文放进 500 响应体。当前 `.env` debug 取值需确认；生产环境若误开 debug 即泄露内部路径与 SQL 细节。

**D-07 🔵（做得对的项）**
- 密码哈希：bcrypt cost=12，降级方案 pbkdf2-sha256 600,000 次迭代（OWASP 2023），恒定时间比较（`app/core/auth/password.py:39-45,118-127`）✅
- SQL 注入：未发现任何 f-string/format 拼 SQL；真实 SQL 全部走 ORM 或 `:param` 参数绑定 ✅
- CORS 默认白名单 localhost 系列、无通配符+credentials（`app/main.py:774-780`）✅
- 日志敏感字段过滤正则（password/token/api_key/credit...，`app/core/logging_config.py:38`）；登录失败只记 username ✅
- API 限流中间件存在：auth 10 次/分/IP、conversation 10 次/分/用户（`app/api/middleware/rate_limit.py:131-152`）——但为进程内存实现（多实例失效，同 A-01）。

### E. 后端功能真实性

**E-01 🔴 33 个 Agent 全部不接数据库，业务数据 100% 内存态**
- 证据：`grep "session|sqlalchemy|select("` 于 `app/agents/**/*.py` 仅命中 1 处字符串常量误报（`admin/agent.py:574 "key": "session_timeout"`）；每个 Agent 状态为 `self._orders/_requests/_salary_structures...` 内存 dict，由 `_seed_data()` 灌入假数据。**重启丢 100% 业务数据**（仅 Agent 框架层的事件/快照/saga 写 MySQL，且快照仅还原 Agent 自身状态版本，不还原业务单据语义）。

**E-02 🔴 424 个 API operation 中仅 ≈13% 真实读写 MySQL**
- 估算方法：逐 route 文件 grep `AsyncSession|get_db|session_factory` 与 Agent 引用：
  - 真实持久化：`auth.py`(6 op，经 jwt/session + rbac DB) + `rbac.py`(~18 op，`rbac_engine` MySQL+缓存双轨) + `messaging.py`(~30 op，直接 AsyncSession) ≈ **54 op**；
  - 内存假数据：`legacy.py`(1444 行) + `hr.py` + `sales_marketing.py` + `operations_strategy.py`（合计约 **250+ op**）全部经 `_agent_refs.py` 调 Agent 内存方法；
  - 其余：`knowledge.py`（内存 TF-IDF）、`workflow.py`（`app/core/workflow/engine.py` 无 DB）、`tenant.py`、`admin_system.py`（内存/文件）、`document.py`（文件生成）、`conversation.py`（直连 LLM）。
- 结论：系统对外呈现 350 个端点的"完整 ERP"，持久化骨干只有认证+RBAC+消息三根柱子。

**E-03 🟠 Agent"自主性" = if-else 规则 + 可选单发 LLM，无编排闭环**
- 证据：15/33 Agent 调用 `llm_analyze`（inventory 4 处、treasury 3 处、tax/procurement/legal/compliance/audit/accounting 各 2 处……），全部是**单次 prompt→JSON** 模式（`base.py:738-853`），没有 tool-loop/多 Agent 协商；决策主干是阈值规则，如 `treasury/agent.py:585-605 make_decision`（>50 万需人工，否则自动批准）。`orchestrator` 主要做注册/状态管理，不做 LLM 编排。把 LLM 关掉（`ERP_LLM_ENABLED=false`）系统业务行为几乎不变。

**E-04 🟠 "RAG/向量检索"为内存 TF-IDF，非真实向量方案**
- 证据：`app/core/knowledge/rag.py:8` "InMemoryVectorStore 使用 TF-IDF + 余弦相似度实现零依赖检索"；`hybrid.py:8` 同。无 embedding API、无向量库、数据不持久化——重启知识库归零（仅 initializer 注入的 5 个示例文档）。

**E-05 抽查结论（sales / hr / contract / procurement / bi）**
- 五个 Agent 均为同一模式：「`_seed_data()` 硬编码假数据 → dict/list CRUD → 纯 Python 聚合计算 → 可选单发 LLM 润色」。业务算法代码本身有真实计算逻辑（如 contract 822 行的审批阈值流、bi 704 行的报告聚合、procurement 812 行的询比价规则），**但输入全是编造的内存假数据、输出不落任何持久层**。判定：**真代码、假数据、无记忆** —— 演示有效，生产无效。

---

## 3. 问题总表

| # | 维度 | 级别 | 问题一句话 | 位置 |
|---|------|------|-----------|------|
| 1 | B | 🔴 | Treasury 每 tick(1s) 调 LLM，失败不熔断不冷却，402 下仍 3600 次/小时空转 | `agent/base.py:283-320,844-853`；`agents/treasury/agent.py:594-607` |
| 2 | C | 🔴 | 三条建表路径全失效（alembic 空壳/migrations 仅4表/无 create_all），35 张表不可复现 | `alembic/versions/...py`；`core/migrations.py:83-153`；`infra/docker/init-db.sql` |
| 3 | E | 🔴 | 0/33 Agent 连数据库，业务数据 100% 内存态，重启全丢 | `app/agents/**`（grep 0 命中 DB） |
| 4 | E | 🔴 | 424 个 operation 仅 ≈13%（≈54个）真实持久化，其余为内存假数据外壳 | `routes/legacy|hr|sales_marketing|operations_strategy.py` |
| 5 | D | 🔴 | 任意登录用户可读任意员工薪资+银行账号明文，全链路无脱敏 | `routes/hr.py:679-688`；`agents/payroll/agent.py:246,473-500` |
| 6 | A | 🟠 | Redis 已连接但业务 0 调用点，缓存/限流/会话全在单进程内存 | `core/database.py:208-232`（0 consumers） |
| 7 | D | 🟠 | 无行级/部门数据隔离，授权引擎仅安全等级矩阵 | `core/security/authorization.py:137-151` |
| 8 | D | 🟠 | 会话/登出黑名单仅内存 dict，重启复活旧 token；JWT 密钥硬编码提交在 .env | `core/auth/session.py:56`；`config.py:144-146`；`.env:66` |
| 9 | D | 🟠 | 24 个种子用户弱密码（admin123/ceo123/...） | `core/rbac/seed.py:764-827` |
| 10 | D | 🟠 | 审计链仅覆盖 Agent 生命周期（3 文件 6 处），业务写操作 0 审计；重启断链 | `core/security/audit.py:224-227`；grep 6 call sites |
| 11 | B | 🟠 | events/audit_logs 只增不删无保留策略，高频事件致 8.6 万行/天膨胀 | `core/event/store.py`（grep retention 0 命中） |
| 12 | E | 🟠 | Agent 自主性=规则+可选单发 LLM（15/33），无 tool-loop 编排，关 LLM 行为不变 | `core/agent/base.py:738-853`；grep llm_analyze 计数 |
| 13 | E | 🟠 | RAG 为内存 TF-IDF，无 embedding/向量库/持久化，重启知识归零 | `core/knowledge/rag.py:8`；`hybrid.py:8` |
| 14 | C | 🟠 | 19 个 Agent 约 1,954 行 `_seed_data` 重复假数据，104 个内存态属性 | `app/agents/*/agent.py`（脚本统计） |
| 15 | A | 🟠 | 业务列表接口无分页，Agent 查询方法全量返回 | `routes/hr|sales_marketing|operations_strategy.py` |
| 16 | A | 🟡 | pdfplumber 同步解析 PDF，接热路径会阻塞事件循环 | `core/knowledge/ingestion.py:116,417` |
| 17 | D | 🟡 | debug=True 时 500 响应回传完整 traceback | `main.py:749-765` |
| 18 | C | 🟡 | alembic 与手写 migrations 双轨并存，schema 变更无版本化出口 | `alembic/env.py` vs `core/migrations.py` |
| 19 | B | 🟡 | 无 TTL/结果缓存，同一 7 天预测每秒重算重发事件 | `agents/treasury/agent.py:594-607` |
| 20 | A | 🟡 | lifespan 重复调用 init_db 两次 | `main.py:362,416` |
| 21 | D | 🔵 | 限流/CORS/日志脱敏/密码哈希/参数化 SQL 五项基础安全配置到位 | rate_limit.py:131-152 等 |
| 22 | A | 🔵 | async 路径无 requests/time.sleep 污染，模型索引 81 处设计尚可 | grep 证据 |
| 23 | C | 🔵 | 1029 个测试函数集中在内存 Agent，保护不到持久化/安全缺陷 | tests/（54 文件） |
| 24 | B | 🔵 | docker-compose 有资源 limit（backend 1G/1CPU），但生产声明全靠 K8s/Istio 注释占位 | docker-compose.yml |
| 25 | E | 🔵 | 前端 56 个 ts/tsx 约 50 个页面全面对齐假数据 API，UI 完成度虚高 | frontend/src/pages |

---

## 4. 对线索 1-5 的验证结论

1. **LLM 成本失控 — ✅ 成立（细节修正）**。`client.py` 本身有令牌桶+指数退避，且 4xx 不重试（设计没错）；真正的病灶在 **Agent 层**：`base.py` run loop 1s/tick + treasury `on_tick` 每次都发起 LLM 预测 + `llm_analyze` 吞掉一切异常且**不喂熔断器**（base.py:844-853），形成"失败→下一秒再试"的永动机。频率由 `on_tick` 调度决定，非客户端决定。
2. **建表不可复现 — ✅ 成立且比预期更糟**：不仅 alembic 空壳、migrations.py 仅 4 张系统表、init-db.sql 是 5 张旧表，而且全仓库**没有任何 `create_all` 调用**——ORM 34 张表从未用于建表。全新部署会因"静默降级到内存模式"而表面 healthy、实际零持久化。
3. **数据双轨制 — ✅ 成立（量化）**：19/33 Agent 纯内存态（其余多为无状态工具型），涉及 legacy+hr+sales_marketing+operations_strategy 约 250+ 个 operation；重启丢失全部业务单据，仅 RBAC/auth/messaging（≈54 op）幸存。
4. **事件总线 memory 实现 — ✅ 成立**：`/health` 实测 `implementation: "memory"`，33 个订阅者；事件经 event_store 双写 MySQL 但总线投递在内存，多实例部署下事件丢失。
5. **弱种子密码 — ✅ 成立**：`seed.py:764-827` 全部 24 个种子用户为"职位+123"式密码；密码哈希算法本身（bcrypt-12）合格，问题仅在种子策略与未强制首登改密。

---

*报告完 — 详细修复优先级建议：P0 = 线索2（建库链路）+ 线索3（Agent 持久化）+ D-01（薪酬越权）+ B-01（LLM 调度治理）；P1 = Redis 落地、会话持久化、审计覆盖业务写操作；P2 = 其余。*
