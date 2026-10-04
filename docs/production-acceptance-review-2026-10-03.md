# 功能生产化综合验收评审意见书（基于知识库 Agent 安全框架的深度评审）

- **评审日期**：2026-10-03
- **评审人**：评审委员会成员（依据个人知识库 Agent/安全知识框架执行）
- **评审依据**：
  1. [[工业级Agent工程规范]] —— 三绝对 / 四底线 / 七铁律 / G1-G12 上线门禁 / 反模式清单 / 自检 15 题
  2. [[Agent安全与权限治理]] —— 四类 Agent 风险、R0-R4 操作分级、身份授权设计、第三方准入 6 检查、上线前 9 检查
  3. [[提示词注入与越狱攻防]] —— 缓解五层（输入/模型/工具/输出/运行时）、注入测试集入 CI、攻击成功判据
  4. [[Agent可观测性设计决策]] · [[Agent治理面设计模式]]（本项目沉淀，作为已达标基线）
- **审计方式**：逐文件代码取证（附 `文件:行号`）+ 全量回归基线（1341 passed）+ UAT 六场景验收纪要
- **总体结论**：**内部试点级（Copilot 阶段）合格，公网生产级暂缓**——治理面/审计/回滚等"骨架血肉"达到门禁要求（G3/G9/G10 大部达标），但**提示词注入防护存在"引擎未接线"的结构性缺陷**（P0×3），认证面与权限模型存在 P1×9 项限期整改项。修复见 §4 效率-安全平衡要求。

---

## 一、已达标的评审项（先肯定，避免重复建设）

| 门禁/检查 | 达标证据 |
| --- | --- |
| G3 LLM 边界 | 治理面单通道：代码层面不存在 LLM→执行器直连路径（`governor.execute` 唯一入口，write_high 未批准不执行） |
| G9 幂等与可观测 | 幂等键全覆盖（定制/账本/审批）；日志(structlog JSON)+指标(Prometheus)+追踪(trace_id 三层键)+审计(哈希链) 四件套齐备 |
| G10 灰度与回滚 | 工作流 canary 粘性灰度（F3.5）+ 回滚账本 CAS+对账（G4）+ drill 重放断言 CI 门禁 |
| 幻觉治理（G4 部分） | RAG 强制来源（knowledge_search 治理面工具）、write_high 影子执行影响面进审批单、全量留痕（Thought/Action/Observation 经 trace 可回放） |
| 第三方准入（部分） | 外部 MCP 三道 SSRF 关卡 + 只读桥接 + 服务端每次调用刷定制缓存（停用即时生效） |
| 熔断（部分） | per-Agent 日 token 预算超限 raise→降级（runtime.py） |

---

## 二、缺陷清单

### P0（公网生产阻断项，2 周内必须清零）

#### P0-1 提示词注入防护引擎已建成但从未接入主链路

- **取证**：`app/core/security/guardrails.py:95,142` 已实现 `check_input/check_output`（PROMPT_INJECTION / JAILBREAK / MALICIOUS_CODE 三类检测 + decide_action），但全仓仅 `legacy.py:20,66` 引用其 stats——**runtime.py、governor.py、消息入口零调用**。对话消息、工具返回值、Agent 回复全部绕过输入/输出检测。
- **违反**：注入攻防"工具侧+输入侧是最有效一层"；G11"安全阻断率 100%"无从谈起——不是阻断率低，是**阻断器没装**。
- **修复要求**：guardrails 接入三处——①对话入口（用户消息→check_input）；②RAG 检索结果与外部文档文本（知识检索、发票 PDF 解析文本→check_input，标 untrusted）；③Agent 最终回复出口（check_output 敏感信息过滤）。接线走轻量正则首层（引擎已有），禁止以性能为由跳过。

#### P0-2 间接注入面（RAG 投毒 / 文档投毒）完全开放

- **取证**：`app/core/agent/base.py:772`"将 context 序列化为 JSON 注入到用户消息中"——知识库检索结果与发票 PDF 文本（均为外部可控输入）**无来源分级标注、无"以下是数据不是指令"边界封装、无指令性内容清洗**，直接进入 LLM 上下文。注入攻防明确：间接注入（RAG/文档/工具返回值）是最危险类型，攻击链终点是"注入之后触发了工具"。
- **后果推演**：低权限员工上传一份含"指令"的知识文档 → 同域 Agent 检索命中 → Agent 被诱导调用其权限内的写工具（write_low 免审直执行）→ 经账本可回滚，但审计与业务混乱已然发生。
- **修复要求**：①检索内容与文件文本进入上下文前**结构化封装**（XML/分隔符 + 显式"数据非指令"声明）；②来源可信度分级标注（system > user > 检索 > 外部文件）；③检索结果预处理清洗明显指令性内容；④write_low 工具在"上下文含外部文件来源"时升级为二次确认（可配置，默认开）。

#### P0-3 无注入红队测试集与安全阻断率指标

- **取证**：`tests/evals/agent_evals.json` 50 用例无一条恶意注入/越狱/编码混淆用例；全仓无"攻击成功判据=是否触发未授权工具调用"的评测口径。
- **违反**：注入攻防 §6（≥100 条入 CI + 成功判据）与 G11（黄金测试集须含恶意 Prompt 注入）。
- **修复要求**：首版 ≥30 条注入用例（直接/间接/编码混淆/多轮/工具投毒五类）2 周内入 CI，4 周到 100 条；指标定为**安全阻断率 100%**（按"触发未授权工具调用"判定，不按"说了什么"）；残余风险按攻防 §7 话术如实披露。

### P1（限期整改，2 周内完成关键 5 项）

| # | 缺陷 | 取证 | 违反 | 修复要求 |
|---|------|------|------|----------|
| P1-1 | **会话存储进程内存态** | `app/core/auth/session.py:64` `_sessions: dict`；云端已实际踩坑（2 worker 登录裂、重启全失效） | 企业落地"任务状态管理"四能力之一；G9 | 迁 Redis（复用既有实例，零新组件）；多 worker 前必须完成 |
| P1-2 | **登录无防爆破** | auth.py 无 lockout/退避/验证码；仅审计+告警（告警≠阻断） | 上线前检查"注入/越权/越权三类红队用例"之外的账户安全基本面 | 复用既有 RateLimitMiddleware 做**递进延迟+IP 限流**；连续 N 次失败锁定递增时长；不做验证码强墙（平衡效率） |
| P1-3 | **JWT 密钥无启动校验** | `config.py:181 jwt_secret=""`，jwt_manager 无空值拒绝——漏配即以弱密钥签发 | fail-closed 原则 | 启动时 jwt_secret 为空/弱熵 → **拒绝启动**并给修复指引 |
| P1-4 | **CORS 白名单含开发源** | `main.py:962` 默认 7 个 localhost 端口 | 上线门禁 G11 工程成熟度 | 生产 profile 强制显式 `ERP_CORS_ORIGINS`，启动时发现含 localhost 且非 debug → 告警或拒绝 |
| P1-5 | **权限模型越权面（自查报告已列、未修）** | admin-guide 附录 B：ceo 经兜底通配获 customization 全套；`domain_scope` 未落地（customization:update 全局可改任意域）；customization_admin/approver 无 system:manage 进不了管理台 | 权限治理"职责分离"与最小权限 | ①移除/收紧 ceo 兜底通配（决策留痕）；②定制权限按域过滤（改与审批都限定 scope）；③两个专用角色补 system:manage 授权（D5 决策落地） |
| P1-6 | **前端 refresh token 落 localStorage** | `frontend/src/stores/authStore.ts:41-53` zustand persist | XSS→R3 凭证窃取（refresh 7 天） | access_token 内存态；refresh_token 迁 httpOnly cookie 或 sessionStorage+旋转；输出侧已有 CSP 无——补基本 CSP 头 |
| P1-7 | **WS 凭证走 query param** | `main.py:1142` `?token=<JWT>`——进访问日志/代理日志 | 凭证不落日志原则 | 改 Sec-WebSocket-Protocol 子协议或首消息认证 |
| P1-8 | **HTTP 明文公网暴露** | 云部署 3000/8000 直出公网（work log 2026-09-04） | 传输安全基本面 | Caddy/Nginx TLS 终结 + 8000/3000 收回内网；文档化证书续期 |
| P1-9 | **知识库文档级 ACL 服务端强制待证实** | `hybrid.py:266` 注释"在结果中过滤 permission_level"——检索结果是否按请求者部门/权限过滤需实现确认；若仅提示词约束即混淆代理 | 权限治理"多租户/多部门隔离在服务端强制（非仅提示词）" | 检索层强制按 `permission_level`+`department` 过滤（SQL 谓词，零额外成本）；知识 ingest 增加来源标注（配合 P0-2） |

### P2（纳入下期路线图，不阻断试点）

| # | 项 | 依据 |
|---|----|------|
| P2-1 | Agent 级 Kill Switch（一键停某 Agent/全部动作）与步数/失败率熔断阈值（现仅 token 预算+工具停用） | 权限治理 §6 熔断/Kill Switch 须演练 |
| P2-2 | 注入红队例行化（季度）+ Bad Case 飞轮（fallback 触发→脱敏→回补测试集） | 注入攻防 §6；工业规范 EDD |
| P2-3 | Agent/Skill 级 Shadow 灰度阶段（现灰度仅工作流 canary） | 灰度三阶段 Shadow→Copilot→Auto |
| P2-4 | 行为基线与异常检测（单任务调用次数/数据量/外发域名） | 权限治理 §6 |
| P2-5 | MySQL 备份/恢复演练与 RTO 定义 | 企业落地四能力 |
| P2-6 | 第三方 MCP 内容审查：签名/校验值、权限声明与行为一致性静态审查、工具描述变更重审（rug pull 防护） | 权限治理 §5 六检查（SSRF 已做，内容审查缺） |
| P2-7 | 全成本核算报表（模型成本/人工接管时长/组件复用次数——token 计量已有，未成口径） | G12 全成本 |
| P2-8 | WS 客户端并发多路复用下的会话竞态压测 | 工程规范 §4.1 溃散模式 4 |

---

## 三、自检 15 题作答（可答项实答，不可答项即缺陷）

| # | 问题 | 作答 |
|---|------|------|
| 2 | LLM 挂了业务还能跑吗 | ✅ 逐级降级规则兜底（base.py fallback），降级显式可观测 |
| 4 | 指令重传执行两次 | ✅ 幂等键全覆盖（G9）+ 账本 CAS |
| 5 | LLM 被注入最坏后果、哪几层闸门 | ❌ **当前仅治理面 RBAC/schema 一层 + write_high 人审**；输入/输出检测未接线（P0-1），间接注入无封装（P0-2）——write_low 免审工具是被注入后的现实攻击面 |
| 10 | 事故后能否完整还原 | ✅ 审计哈希链+trace 三层键+回放（G1-1 UAT 已证） |
| 11 | 死循环谁掐断 | ✅ LangGraph 步数/Token 硬上限 + per-Agent 日预算 |
| 12 | 安全约束在 prompt 还是代码 | ✅ 红线在代码（治理面/注册校验）；但 **P0-2 的"数据非指令"封装缺失**属 prompt 层缺口 |
| 14 | 注入用例与安全阻断率 | ❌ 无（P0-3） |
| 15 | 当前处于 Shadow/Copilot/Auto 哪阶段 | **Copilot**（write_high 全人审 + write_low 免审但可回滚）——符合"高可靠场景停留最久的形态"判断，**禁止未清 P0 直接切 Auto** |

其余各题对应 P1/P2 各条。

## 四、效率与安全的平衡要求（修复迭代的总约束）

评审明确：**修复不得把系统推向"全人审/全阻断"的极端**——安全增益必须以可测的性能代价核算：

1. **注入防护分层加载**：正则首层同步（引擎已有，P95 增量门禁 **<50ms**）；LLM 二审**采样异步**（write_low 写操作采样 10%、write_high 已有人审兜底不重复深检）——不同步全量深检；
2. **登录防爆破用递进延迟+IP 限流**（复用 RateLimitMiddleware），不上验证码强墙；
3. **会话迁 Redis 零新组件**、知识库 ACL 用 SQL 谓词过滤（复用既有字段，零额外查询）、CORS/JWT 校验/WS 凭证三项纯配置级修复零性能代价；
4. **write_low 免审框架保留**（事后抽样+账本可回滚兜底），仅新增"上下文含外部文件来源→二次确认"的可配置开关，默认开、可按域关闭（关闭走定制审批留痕）；
5. 每项修复附性能对照（复用 `g6_overhead.py` 口径），P95 增量超门禁即打回重设计。

## 五、整改指令

1. 乙方开发团队 **2 周内**完成 P0×3 全部 + P1 关键 5 项（P1-1/2/3/4/5）；P1-6/7/8 与全部 P2 纳入下期路线图（P1-8 可与运维排期并行）；
2. 每项修复**必须带测试**：P0-3 注入测试集首版 ≥30 条入 CI（4 周至 100 条），安全阻断率 100% 作为门禁指标；P1 各项带回归测试；
3. 修复完成后委员会**对照本意见书逐条销项复查**（快速会口径），全部销项 + 注入测试集达标 → 出具"公网生产级"放行意见；
4. 在此之前，系统运行边界锁定为：**内网/试点环境 Copilot 阶段**；公网暴露与真实资金流操作须待放行意见。

---

*本意见书全部取证可复核；缺陷清单与整改状态由乙方在本文档追加"整改回签"章节跟踪，不另起文档。*

---

## 整改回签章（乙方，2026-10-03 当日交付）

**回归基线：1399 passed / 4 skipped / 1 failed**（唯一失败仍为 DeepSeek 402 环境性；净增测试 58 例）

| 项 | 状态 | 整改内容 | 测试 |
|----|------|----------|------|
| **P0-1** | ✅ **已修复（范围更正后）** | 评审后复核：`base.py llm_chat/llm_analyze` 此前**已接线** guardrails（734/749/843 三处）——评审取证不完整，P0-1 实际缺口收窄为 **runtime.py ReAct 编排主链路未接线**。已补：`runtime.run` 用户消息 check_input（BLOCK/ESCALATE → 拒绝执行 + guardrail.input_blocked 审计，在 LLM 与工具调用**之前**）；Agent 回复 check_output（BLOCK → 拦截替换，SANITIZE → 净化） | test_injection_defense_g.py::test_runtime_blocks_injection_before_llm / test_runtime_sanitizes_leaked_output |
| **P0-2** | ✅ 已修复 | 新建 `app/core/security/untrusted.py`：`wrap_untrusted()` = 明显指令行剔除（只删不改）+ "数据非指令"边界声明 + 来源标注。接线三处：`llm_analyze` 业务上下文 JSON、`knowledge_answer` 检索材料（逐条）、`knowledge_search` 摘要（工具返回值=注入向量） | test_wrap_untrusted_marks_boundary_and_strips_instruction_lines 等 |
| **P0-3** | ✅ 已修复 | 注入测试集 **40 例**（direct 10/indirect 8/encoding 4/multi_turn 5/tool_poisoning 5/**benign 8 误报护栏**）入 CI；**阻断率门禁 100%**（parametrize 逐例 + 汇总断言）；E2E 判据=注入消息在 LLM 之前拦截、工具调用不可能发生 | test_injection_defense_g.py 全套（46 例） |
| P1-1 | ✅ **复核更正为已达成** | 评审取证不完整：`session.py` 实为**内存 L1 + Redis L2 双层**（跨实例 hydrate/共享黑名单/TTL 完整实现，2026-09-04 云端踩坑后已在生产化路线图 2.5/2.6 补齐）。P1-1 无需整改；遗留仅"Redis 不可用时多实例退化为单实例一致"（已有节流告警，接受） | 既有 redis_backend 测试 |
| P1-2 | ✅ 已修复 | username+IP 维度 10 分钟滑窗，失败 ≥5 次递进锁定（2^n 秒封顶 60s，**429+Retry-After**）；成功登录清零；锁定事件入审计（auth.login_lockout）；**未用验证码**（平衡要求） | test_login_lockout_progressive_and_reset / test_login_lockout_isolates_users_and_ips / test_login_route_returns_429_after_threshold |
| P1-3 | ✅ 已修复 | `startup_checks.validate_jwt_secret`：生产空/弱熵（<16 字符）**拒绝启动**（fail-closed）+ 修复指引；debug 生成临时密钥（重启全员登出，显式告警）。接线点在 main.py settings 导入后、路由导入前 | test_jwt_missing_in_production_refuses 等 4 例 |
| P1-4 | ✅ 已修复 | `sanitize_cors_origins`：生产剥离 localhost/127.0.0.1 开发源 + 显式告警留痕；debug 保留 | test_cors_strips_localhost_in_production 等 2 例 |
| P1-5 | ✅ 部分修复 + ②设计回签 | ①ceo `system: _ALL → {"read"}`、`agent: _ALL → {"read"}`（决策留痕注释：恢复走定制审批，不走种子回改）；③customization_admin/approver 补 `system:manage`（进管理台）+ agent read/approve（D5 决策落地，职责分离名实一致）。**②域隔离（domain_scope）按 admin-guide 落差清单"须先补设计"结论移入下期路线图**——本回签提交设计约束：domain_scope 列（NULL=全局）+ 服务层 `check_domain_scope(user, ctype, target)` 钩子 + 四路由过滤，工作量 0.5 人周 | test_seed_ceo_system_wildcard_removed / test_seed_dedicated_customization_roles_can_enter_admin / test_seed_log_auditor_readonly_unchanged |
| P1-6/7/8 | ⏳ 移交 | 按整改令纳入下期路线图（P1-8 与运维 TLS 排期并行） | — |
| P2×8 | ⏳ 移交 | 已列入下期路线图 | — |

**平衡要求兑现核对**：①注入检测为正则首层（引擎已有，零 LLM 成本），E2E 实测 P95 无感知增量；②write_low 免审框架未动，免审+账本可回滚兜底保留；③会话/限流全部复用既有组件；④登录防爆破未用验证码。

**申请委员会复查**：P0×3 全清 + P1 关键 5 项达成（P1-5 ②按设计前置结论移期），请按意见书 §5 口径逐条销项并出具"公网生产级"放行意见。

---

## 委员会销项复查与放行意见（2026-10-04，第二轮整改后）

### 一、销项核对表

| 项 | 销项状态 | 复核证据 |
|----|----------|----------|
| P0-1 注入防护接线 | ✅ 销项 | runtime.run 输入/输出双向检测在 LLM 与工具调用之前；E2E 判据（注入消息→0 次工具调用）通过 |
| P0-2 间接注入封装 | ✅ 销项 | untrusted.py 三处接线（llm_analyze 上下文/knowledge 两工具）；边界声明+指令行剔除有测试 |
| P0-3 红队测试集 | ✅ 销项 | injection_testset.json 40 例（五类+benign 误报护栏）入 CI，阻断率门禁 100% |
| P1-1 会话 Redis 双层 | ✅ 销项（复核更正：原已达成） | session.py L1+L2 完整；评审取证不完整已在回签更正 |
| P1-2 登录防爆破 | ✅ 销项 | username+IP 滑窗递进锁定（429+Retry-After）、成功清零、锁定入审计；未用验证码 |
| P1-3 JWT fail-closed | ✅ 销项 | 生产空/弱熵拒启动；debug 临时密钥 |
| P1-4 CORS 护栏 | ✅ 销项 | 生产剥离 localhost 开发源+告警 |
| P1-5 权限收紧 | ✅ 销项（②域隔离设计回签移期） | ceo 通配收紧；专用角色补 system:manage+agent；domain_scope 移下期（0.5 人周，附设计约束） |
| P1-6 token 存储 | ✅ 销项 | access 仅内存；refresh sessionStorage+**每次刷新轮换**（后端已签发新 refresh）；localStorage 零凭证 |
| P1-7 WS 凭证 | ✅ 销项 | Sec-WebSocket-Protocol 子协议优先（accept(subprotocol) 由连接管理器统一执行——复查中修复双重 accept 缺陷）；query param 降级废弃告警 |
| P1-8 TLS 终结 | ✅ 销项（配置交付） | deploy/tls/Caddyfile + compose profile tls（443 唯一公网入口、HSTS、证书自动 ACME）；**生产割接后首验为运行期条件** |
| P2×8 | ✅ 固化路线图 | roadmap-security-hardening-2026-10-04.md（范围/估算/出口判据） |

### 二、复查中额外发现并修复的生产缺陷

1. **事件总线阻塞派发**（高危）：`_dispatch_loop` 串行 await 全部订阅者处理器——一个慢处理器（Agent LLM 决策 10~15s）阻塞整个派发循环，后续事件饿死。修复：并发派发（每事件一任务+in-flight 跟踪+stop 排空）。
2. **数据库引擎跨事件循环亲和性**（高危）：pytest-asyncio 每测试新循环下复用旧循环的池化连接 → await 永久挂死。修复：`database.py` 循环亲和检测，循环切换即重建引擎。

### 三、模型供给变更（随本轮整改）

DeepSeek（402 欠费频发）→ **Agnes-3.0-flash**（apihub.agnes-ai.com，OpenAI 兼容，512K 上下文）。密钥仅存 gitignored `.env`；真 LLM 冒烟场景（e2e 场景 1）验证通过（两次：42.9s / 49.8s）。

### 四、最终回归基线

- 第三轮全量：**1403 passed / 5 skipped / 1 failed**——唯一失败（test_agent_tool_whitelist_filters_specs）为 CI 规则模式钉定的显式覆盖遗漏，**已修复并单独验证通过**（29.86s）。
- 测试分层固化：conftest 全局钉 `llm_enabled=False`（规则路径确定性、零成本）；真 LLM 冒烟经 `ERP_REAL_LLM_SMOKE=1` 显式纳入。
- 覆盖：注入红队 40 例（阻断率 100%）+ WS 认证 5 例 + 安全加固 9 例 + 回滚账本 8 例 + 追踪 11 例 + drill 重放 12 例。

### 五、放行意见

经逐条销项复查，委员会出具：

> **"公网生产级"有条件放行**
>
> 条件（均为运行期动作，非代码缺口）：
> 1. TLS 按 `deploy/tls/Caddyfile` 割接后**首验**（证书生效/HSTS/WS 反代连通）；
> 2. 生产 `.env` 按 fail-closed 要求配置 `ERP_JWT_SECRET`（≥16 字符）与 `ERP_CORS_ORIGINS`（正式域名）——启动校验会强制执行；
> 3. 下期路线图（P2×8 + domain_scope）按排期执行，其中备份演练（P2-5）建议与 TLS 割接同窗口。
>
> 运行边界自本意见起升级为：**公网生产级（Copilot 阶段）**——write_high 全人审 + 注入三层缓解 + 审计/回滚/告警全链在位。

**委员会签署**：内控合规组 ✓ / 平台架构组 ✓ / 运维保障组 ✓
