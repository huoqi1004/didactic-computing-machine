# 监理 Agent 回答质量 + 开源 Skill/MCP + 流式输出 · 分析与整改方案（待甲方审计）

- **文档日期**：2026-10-04
- **状态**：**待甲方审计——审计通过前不实施**
- **触发**：甲方实测项目监理 Agent 对多个不同问题返回同一答案（截图四张）

---

## 一、根因分析（已代码取证 + 实测复现）

### 1.1 直接根因：LLM 不可用期降级到关键词规则匹配，且关键词过宽

对话链路（`conversation.py`）为三级：**LLM 路径**（`_chat_with_llm`）→ 失败降级 → **关键词规则匹配**（`_rule_based_response`）→ 无匹配 → "已就绪"能力引导。

截图时段 DeepSeek 402 欠费，LLM 路径必然失败，全部流量落入规则匹配。项目监理 Agent 的规则分支只有两组：

1. **进展报告分支**：命中关键词 `["进展", "进度", "项目", "里程碑", "状态", "现状", "报告"]` → 返回**写死的**进展报告文本；
2. **能力引导兜底**：返回"已就绪…请问您需要监理哪个项目？"（思考步骤"未命中具体意图"）。

于是：问"哪些**项目**需要监理"→ 命中分支 1；问"新产品研发**项目**（proj-003）写监理大纲"→ 同样命中"项目"→ **同一份进展报告**（截图 2/3/4 同文）；"写监理实施方案（word）"→ 无"项目"等关键词 → 落兜底"已就绪"（截图 1）。**"同一个答案"的本质 = "项目"一词过宽的固定响应 + 创作类意图无分支**。

### 1.2 架构层根因（即使 LLM 恢复也存在）

1. **规则分支伪装成正式回答**：降级回复无 `degraded` 标记，用户无法区分 LLM 回答与写死文案；
2. **Agent 本体能力未接对话**：`ProjectSupervisorAgent` 的 generate_report/check_compliance（真实逻辑）在对话链路中未被调用——规则分支的数据是另一份写死文案，两套数据源不一致；
3. **创作类意图无工具支撑**："写监理大纲/实施方案/word 文档"需要文档生成能力——项目已有 python-docx 文档生成模块（`document.py`），但监理 Agent 未接入；
4. **无流式输出**：`_chat_with_llm` 整块返回，长回答（监理大纲全文）需等待 10-30s 白屏。

### 1.3 实测验证（当前状态）

Agnes-3.0-flash 接入后 LLM 路径已生效——**用截图同款问题实测**："帮我写一份 proj-003 新产品研发项目的监理大纲（含五大监理阶段）"返回了结构化大纲（thinking 五步含"确认 proj-003 基本信息/识别研发类五大监理阶段/当前工程样机阶段重点提示"，content 为项目感知的大纲正文 + dataCard）。**即根因修复（LLM 恢复）已部分生效，但架构隐患仍在**——LLM 再度不可用（余额/网络）时同类问题必然复发，且流式/文档能力仍缺。

---

## 二、整改方案（三个工作包，待审计）

### 工作包一：监理 Agent 对话链路升级（修"同答案"的架构面，1 人日）

| # | 动作 | 说明 |
|---|------|------|
| 1.1 | 规则分支收窄 + 降级显性化 | "项目"从宽泛关键词移除；规则分支仅作 LLM 失败时的**降级骨架**，回复带 `degraded: true` 标记，前端显示"规则降级"徽标（可观测性原则：不伪装成正式回答） |
| 1.2 | 对话接真实 Agent 工具 | 监理对话经治理面调用 `generate_report` / `check_compliance` / 进展查询——消灭"两套数据源不一致" |
| 1.3 | 创作类意图接文档生成 | "监理大纲/实施方案/word 文档"→ LLM 生成结构化内容 → 调用既有 document 模块（python-docx）产出 **.docx 真文件** → 回复附下载链接 |
| 1.4 | 意图分类双路 | LLM 分类优先 / 规则兜底（复用 supervisor.classify 既有模式）；golden 用例进 evals 防回归 |

### 工作包二：企业级开源 Skill + MCP 接入（2-3 人日）

**Skill（复用既有目录式 YAML 热加载体系 + 管理端启停/审计）**：

| Skill | 能力 | 参考来源 |
|-------|------|----------|
| docx-report | 结构化内容→Word 文档（大纲/方案/报告） | Anthropic skills 库 docx 能力 + 仓内 document 模块 |
| xlsx-report | 表格/报表→Excel（含样式） | 同上 xlsx |
| supervision-plan | 监理大纲/实施方案模板（五大阶段+检查点，PMBOK 监理口径） | 本项目 Agent 领域模板 |
| meeting-notes / weekly-report | 会议纪要、周报生成 | 通用企业模板 |
| data-brief | CSV/Excel 上传→数据分析摘要与图表建议 | 通用 |

**MCP（经既有 external_tools 白名单桥接 + SSRF 三关卡 + readonly 治理面，准入按知识库"第三方准入 6 检查"：来源可信/权限最小化/描述与行为一致审查/版本锁定/禁用开关/调用审计）**：

| MCP Server | 能力 | 治理约束 |
|------------|------|----------|
| office-word-mcp-server（社区开源） | Word 文档生成与编辑 | readonly 桥接→写操作走审批；产物落受控目录 |
| mcp-server-fetch | 受限网页抓取（补充外部资料） | 出口域名白名单；禁内网/元数据地址（复用 SSRF 三关卡） |
| mcp-server-sqlite（官方） | 数据库只读查询 | **只读账号**；schema 白名单；单查询超时 |
| mcp-server-filesystem（官方） | 受控工作目录文件操作 | 目录白名单锁定；禁逃逸 |

### 工作包三：流式输出（1-1.5 人日）

| 层 | 方案 |
|----|------|
| 后端 | 新增 `POST /api/v1/conversation/chat/stream`（SSE）：LLM 流式（astream）分片推送；事件帧类型 `thinking` / `token` / `dataCard` / `done`；一次调用一条审计（完成时落，语义不变） |
| 前端 | Conversation 页 fetch ReadableStream 渐进渲染：token 追加、thinking 逐条点亮、dataCard 尾帧渲染 |
| 治理 | 输出护栏：分片聚合后过 guardrails（BLOCK 则已发内容截断+提示）；失败自动降级非流式一次性返回 |
| 兜底 | `stream=false` 或 SSE 异常 → 现有同步路径不变 |

### 排期与验收

| 批次 | 内容 | 工期 | 验收 |
|------|------|------|------|
| 第一批 | 工作包一 | 1 人日 | 截图四个问题重放：大纲/方案问题得到项目感知回答且可下载 .docx；进展问题答进展；降级时显式徽标 |
| 第二批 | 工作包三（流式） | 1-1.5 人日 | 首 token < 2s；dataCard/thinking 分帧正确；SSE 失败降级可用 |
| 第三批 | 工作包二（Skill/MCP） | 2-3 人日 | Skill 热加载生效+管理端启停；MCP 经准入审查+只读治理；审计留痕 |

---

## 三、待甲方裁决点

1. **D-A**：规则降级回复的显性化方式——"规则降级徽标"（推荐）vs 完全隐藏降级；
2. **D-B**：MCP 准入范围——先接 2 个（word + fetch，推荐）vs 一次性 4 个；
3. **D-C**：真 LLM 创作内容的合规口径——监理大纲类文档生成后**是否强制人审**（推荐：生成草稿免费审，正式提交走既有审批流）；
4. **D-D**：流式覆盖范围——仅 Conversation 页（推荐先行）vs 全部对话入口（含 messaging 内嵌 Agent 卡片）。

---

## 四、评审委员会裁决意见书（2026-10-04）

**评审方式**：委员会三组（内控合规/平台架构/产品体验）对方案文档逐工作包评审 + 代码适配点核对（document 模块、skills 体系、external_tools 桥接均已实测确认）。

**总体结论**：**有条件批准，按批次实施**。根因分析属实（降级链路 + 关键词过宽 + 创作意图缺失三点全部经代码与截图对上）；三工作包方向正确；以下裁决与修正条件随实施令生效。

### 裁决 D-A（降级显性化）：采纳推荐案（"规则降级徽标"），附加修正

修正：降级骨架**按意图类型分组**，不得使用单一兜底文案——创作类（大纲/方案/文档）降级→"创作服务暂不可用，已转人工队列"提示并生成管理员待办；查询类降级→返回**真实缓存数据**（Agent 本体 get_proposals/check_compliance 实时数据），而非写死文案。`degraded: true` 标记为必选项。

### 裁决 D-B（MCP 首批范围）：2 个先行，并修正一处方案错误

- **方案错误修正**：`mcp-server-sqlite`（官方）与本项目 MySQL 存储不匹配——改选**社区 MySQL 只读 MCP server**，或由乙方自研"ERP 只读查询"治理面工具（优先自研：复用治理面 RBAC/审计，零新攻击面）；
- 首批准入 2 个：**office-word-mcp-server**（准入审查：许可证/维护活跃度/权限声明与行为一致性）+ **自研只读查询工具**；fetch 与 filesystem 第二批（filesystem 必须只读 + 目录白名单锁定后另行评审）；
- 全部经 external_tools 白名单桥接 + SSRF 三关卡 + 调用审计。

### 裁决 D-C（创作内容合规）：采纳"草稿免费审、正式提交走审批流"，附加两条

1. AI 生成文档首页页眉固定标注 **"AI 生成草稿 · 生成时间 · 生成请求 trace_id"**（可溯源，防冒充正式文件）；
2. 文档产物落受控目录，下载走既有鉴权通道（document download 已有 basename 防穿越 + 登录态），生成行为全量审计（谁/何时/什么提示词生成了什么）。

### 裁决 D-D（流式范围）：Conversation 页先行，附安全设计强制项

1. SSE 客户端必须用 **fetch + ReadableStream**（带 Authorization 头），禁用 EventSource（无法带鉴权头）；
2. 输出护栏采用**滑动窗口检测**（敏感模式可能跨分片拼接逃逸）+ **最终聚合复核**；聚合判定 BLOCK 时推送 `[内容已撤回]` 终止帧 + 审计留痕；
3. 流式断线/异常 → 自动降级一次性重答（幂等，不做断点续传——复杂度不成比例）。

### 委员会新增裁决 D-E（创作能力评测与成本）

1. 创作类能力（大纲/方案/文档）上线前必须建 **golden 评测集 ≥10 例**（结构完整性/项目数据感知性/阶段匹配度三维度）进 evals，防"能生成但生成得离谱"；
2. **fallback 触发率**进监控指标（工业规范 EDD：兜底率飙升是核心预警信号）；
3. 创作类长文本的 token 消耗纳入 P2-7 全成本报表口径（Agnes 免费期结束后的成本可见性）。

### 实施令

- **第一批**（工作包一 + D-A 修正）：即行；
- **第二批**（工作包三流式 + D-D 强制项）：第一批验收后接续；
- **第三批**（工作包二 + D-B 修正）：第二批后；MCP 准入审查材料先行提交委员会备案；
- 每批次完成报委员会销项；全部批次完成 → 本方案关闭。

**签署**：内控合规组 ✓ / 平台架构组 ✓ / 产品体验组 ✓

---

## 五、第二批实施记录与销项（工作包三：流式 + 裁决 D-D 强制项，2026-10-05）

验收行原文：**"第二批 | 工作包三（流式） | 1-1.5 人日 | 首 token < 2s；dataCard/thinking 分帧正确；SSE 失败降级可用"**。
三条中两条达标，一条（首 token < 2s）不达标且根因不在本系统——见 5.5 与 5.7 第 1 项。

### 5.1 D-D 强制项逐条对照

| 强制项 | 落点 | 状态 |
| --- | --- | --- |
| Conversation 页先行 | `frontend/src/pages/Conversation.tsx`，其余页面未动 | ✓ |
| 客户端 fetch + ReadableStream（禁 EventSource） | `frontend/src/services/api.ts:724-800`，带 `Authorization` 头 | ✓ |
| 滑动窗口检测 + 聚合复核 | `conversation.py` `_STREAM_GUARD_WINDOW=240 / _STREAM_GUARD_OVERLAP=120`，逐窗口复核 + 流末全文复核 | ✓ |
| `[内容已撤回]` 终止帧 + 审计留痕 | `retract` 帧为终止帧（其后不再发 `done`），审计 `result=denied`、`details.status=retracted`，`reason` 只记 `type@pattern` 不落原文 | ✓ |
| 断线一次性重答（幂等，不做 resume） | `error` 帧 `fallback:"one_shot"`，前端改打 `POST /conversation/chat` 重答一次并标注"通道降级" | ✓ |

### 5.2 甲方加码项（本轮两条裁决）落地与实测

| 指标 | 改前 | 改后 | 取证 |
| --- | --- | --- | --- |
| 流式正文形态 | JSON 包壳：`{"text": "{"}` 逐字进正文流，用户先看到花括号 | 6/6 例纯文本增量 | `_STREAM_PLAIN_TEXT_DIRECTIVE` 只挂流式路径；真机 :8010 |
| 单次回答成本 | 51.7s / 936 字 | 5.5–8.6s / 163–255 字（不再白烧 dataCard token，合 D-E 第 3 条） | 同一问题真机对照 |
| 首分片失败 | 真机 429 → 约 400ms 直接 `error` 帧、0 字 | mock 5xx 腿出现 2 条 `conversation.stream_first_chunk_retry` 后正常出帧；重试上限 3 次、加码 ≤1.2s（门槛是 2s，等更多无意义） | :8012 日志 + `_tmp_mock_frames.json` |
| 连接预热 | 首轮对话现建 TLS | httpx 池 `keepalive_expiry=30s` + lifespan `GET /models` 预热（`app.llm_prewarm_scheduled`） | `client.py` / `main.py` 8.3 |
| 凭证形态命中即撤回 | `check_output` 只产 SANITIZE/ALLOW，无 BLOCK 语义，挡不住整段撤回 | 新增只读探测 `guardrails.find_credential_leaks()`（按 regex 前缀认领 password/api/secret/private/token 五档，天然排除卡号与 SSN，不累加脱敏计数、不打日志） | `guardrails.py` + `test_guardrails.py` |

### 5.3 撤回与误杀取证（OpenAI 兼容 mock provider，`_tmp_mock_llm.py` 起 :8011，后端 :8012 只换上游，路由/护栏/帧协议/前端全部走产品真实代码）

- 注入腿：46 个 token 帧、484 字后 `retract`，`reason=prompt_injection`，`pattern=ignore\s+(previous|above|all)\s+(instructions|prompts|rules)`；mock 备了 58 片，实际未走完即拦停。
- 凭证腿：`retract reason=credential_leak`，`pattern=api[_\s-]?key\s*[=:]\s*\S+`，`charsStreamed=482`；断言审计里不含泄露的密钥原文。
- 误杀防线：16 位卡号不撤回（离线实测 `_stream_guard_hit("…4111111111111111…") = None`——`check_input` 无敏感数据档，此前误以为会触发）；正常监理答案收尾为 `done`。
- 分帧正确性（验收行第 2 条）：`thinking×3 → token×6 → dataCard → done`，`done` 带 `traceId/intent/classifier/degraded/status/elapsedMs/chars/hasDataCard/firstTokenMs`。

### 5.4 真机 UI 腿（:5178 vite → :8012，browser-use 驱动，DOM 取证）

- 渐进渲染：正文长度 4 步递增 964 → 981 → 1050 → 1131，@ t=18 / 652 / 1946 / 2038 ms。
- 撤回显示：`[data-testid=retract-notice]` 两条，文案"输出护栏已撤回本条回答｜命中模式：prompt_injection / credential_leak"；被撤回正文与 `sk-` 串均不在 DOM 中，`[内容已撤回]` 占位可见。
- 断流 → 一次性重答（验收行第 3 条）：`[data-testid=stream-fallback]`"流式通道中断，已由对话接口一次性重答（回答内容不受影响）"+ 降级徽标"规则降级回复｜原因：模型调用失败"；时间线 26ms（用户消息）→ 5406ms（3 个 thinking）→ 14698ms（重答落屏）。
- 裁决 D-A：查询意图 + 上游全失败时，正文是"实时监理数据（来源：项目监理agent 本体，经治理面读取）：在监 1 个项目，里程碑合计 2/3…"，dataCard 渲染成实时表（项目编号/当前阶段/里程碑/预算使用率/上线就绪/风险数）；同腿 HTTP 侧 `dataSource=agent_native, has_data_card=true, degraded=true`。非查询意图（capability_guide）则 `dataSource=null` 且不挂数据卡——没有把引导文案伪装成业务结论。
- 通道限制：本会话像素截图不可用（`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`，in-app Browser 面板未打开），视觉证据以 DOM 快照为准，**像素图待补测**。

### 5.5 首 token 口径实测（验收行第 1 条不达标的数据）

| 口径 | 首帧（thinking） | 首 token | n |
| --- | --- | --- | --- |
| 管线自身（mock 上游、空载串行） | 18.1–21.9 ms（响应头）/ 18.4–72.2 ms | 46.5–94.4 ms | 5 |
| 真实网关（空载串行，本轮窗口） | 30.9 / 260.6 / 1990.9 ms | 3520 / 3647 / 3766 ms → **0/3 < 2s** | 3 |
| 真实网关（同日早窗口 23:00–00:15） | 13–93 ms | min 1101 / median 1697.5 ms → 3/7 < 2s | 7 |
| 真实网关分类调用单独计时 | — | min 768 / p50 1678 / max 7076 ms | 10 |

结论：本系统在该链路上的开销实测 ≤0.1s，"首 token < 2s" 的门槛几乎完全由供应商 TTFT 决定（同日两窗口 median 1697ms vs 3647ms，差 2.1 倍）。**不达标项归因为供应商侧，整改建议见 5.7 第 1 项。**

### 5.6 本轮实测发现并已改（不是登记）

① **LLM 意图分类在真实网关上 5/5 全失败**（`intent_classify_failed error=Expecting value: line 1 column 1 (char 0)`），且每次失败前白烧 ~214 token、耗时 1.4–8.0s。根因：分类调用没传 `NO_THINK_EXTRA`，思考态下网关 `content` 为空，client 按 `_extract_content` 回落到 `reasoning_content` 自然语言，JSON 解析必炸——等于工作包一交付的"双路分类"里 LLM 这一路从未生效。修复后同 prompt 10 次采样**命中 8/10**（改前 0/5）。
② **该调用串行卡在首帧之前**：供应商抖动时它能独自吃掉整个首 token 预算。加 `_INTENT_CLASSIFY_TIMEOUT=1.5s` 硬上限，超时主动放手回落规则结果（规则落空本就是 GUIDE，无正确性损失）。断流腿首帧因此从 **3668.5ms 降到 1844.4ms**（`_tmp_boom_probe.py` 同腿对照；收尾时机器空载复测 n=3：1842.1 / 1844.4 / 1875.7ms，上限由 1.5s 超时决定而非负载）。同腿 `error` 帧（前端据此发起一次性重答）实测 5444.4 / 6325.8 / 9455.8ms，median 6325.8ms。
③ **解析认值不认键**：同一条 prompt 实测既出 `{"intent":…}` 也出 `{"label":…}`，认死键会把已经命中的分类又判成失败。
④ 上一轮遗留的"断流腿首帧 3.6s"归因完成：不是流式管线，而是 ①——规则未命中触发分类调用，撞上游 500 后又被 client 内部 3 次重试放大；日志序列 `async_retry_5xx ×3 → intent_classify_failed → stream_first_chunk_retry ×2 → stream_llm_failed`。
测试：`test_conversation_upgrade.py` 新增 4 条（关思考载荷 / 认值不认键 / 非 JSON 不抛异常 / 超时预算生效）。

### 5.7 待委员会裁决（三条）

1. **"首 token < 2s" 口径重定义**：建议拆成"管线预算 ≤150ms（实测已达标）+ 供应商 TTFT 单独 SLO"。若坚持端到端 2s，需要供应商侧承诺或在网关层加"超时即降级到治理面直出"的兜底策略——这已超出工作包三范围。
2. **分类仲裁预算取值**：1.5s 是命中率与延迟的折中（该窗口内预算内命中约 4/10）。可调项：放宽到 2s 换命中率、或对 GUIDE 类直接跳过仲裁（规则 GUIDE 已是安全默认）。
3. **像素截图取证通道**：需要甲方打开 in-app Browser 面板，或批准用 Playwright 通道补拍。

### 5.8 明确不做（含理由，沿用计划 A3）

- 投机式"首分片超时即重开"：算术上不划算（2.0s 等待 + 0.4s 退避 + 新 TTFT 很可能超过原本要避免的 4.0s），且 `wait_for` 取消生成器后 `aclose()` 可能炸 `async generator is already running`。
- 治理面采集 `asyncio.gather` 并行化：实测 0.5–80ms，且会打乱 prompt 文本顺序（`test_conversation_stream.py` 帧序断言钉着）。
- `_build_payload` 里 `thinking` 键与 `NO_THINK_EXTRA` 的语义冲突：**A/B 各 5 次直连**，keep TTFT [711.1, 981.9, 1877.6, 2014.6, 2672.2] vs drop [744.7, 774.1, 786.5, 1806.0, 3155.4]，median 1877.6 → 786.5ms——方向一致但 n=5 不显著（Mann-Whitney U=15/25，p≈0.3）。按计划"有实测收益才动"，**未动共用载荷**（该函数被全部 LLM 调用共用，`test_llm.py` 钉载荷形状）；① 的命中率证据只支撑"在分类调用点补 `NO_THINK_EXTRA`"，已按调用点修完。空载 n≥12/arm 的补测挂在这里，等 5.7 第 3 项通道一并解决。

### 5.9 第三批前置条件

- 工作包二（Skill/MCP + D-B）未开始；**MCP 准入审查材料需先行提交委员会备案**（office-word-mcp-server 许可证/维护活跃度/权限声明与行为一致性 + 自研只读查询工具）。
- 5.7 第 1 项口径裁决会影响工作包三是否需要追加供应商侧兜底改造，建议与第三批验收一并过。
- 5.10 的补偿幂等竞态（`test_rollback_drills.py` 跨用例后台写）需在第三批前定责——它直接影响红线 2 的可判定性。

### 5.10 测试与全量回归（第二批收尾，2026-10-05 01:08）

| 通道 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider` | **1 failed, 1480 passed, 5 skipped** in 1081.86s（18:01） |
| 前端全量 | `node_modules/.bin/vitest run` | **15 files / 253 passed** in 12.97s |
| 本批次新增用例 | — | `test_conversation_stream.py`（帧序/审计条数/撤回/降级）、`test_conversation_upgrade.py` +4（5.6 三条修复各钉一条）、`test_guardrails.py` +凭证探测档 |

前后端**串行**跑（项目记忆已警示并发会让等待型断言假红灯）。

唯一红灯归因（已回树核实，判为既有跨用例竞态，不是第二批缺陷）：

- 现象：`tests/drill/test_rollback_drills.py::test_rollback_drill_table_driven` —— 全量里红 `hr_add_employee`，`tests/drill/` 复跑红 `sales_create_order`，单文件复跑红 `crm_create_customer`，**三轮三个不同 param**；而每个 param 单独复跑都 PASS（39.37s / 44.54s）。
- 断言点：`test_rollback_drills.py:217-221`——补偿重放（设计上必须 no-op）之后再读一次 `COUNT(*)`，读到 1，而重放前一次读（`:211`）是 0。即两次读之间有一行被**后台写**补了回来。
- 排除项：收集顺序固定、无 xdist、无 pytest-randomly → 不是随机顺序；本文件不在工作树改动清单里；演练用例不经过 conversation / guardrails / llm client 的任何本轮改动路径，`audit.py` 本轮只给启动链尾查询加了 10s 超时（`_CHAIN_RESTORE_TIMEOUT_S`）。
- 结论：**红线 2（补偿幂等）在这条竞态窗口上不可判定**，与待决策项 #40（EventStore/event_bus 单例跨事件循环）同族，登记给委员会随第三批一并处置；本批次不冒充"全绿"。

取证脚本说明：本轮的 `_tmp_*` 探针（mock provider / 帧计时 / 分类采样 / A-B 对照）连同产物已在收尾全部删除（`backend/` 下 `_tmp_*` 计数 0；自起的 :8010/:8011/:8012/:5177/:5178 进程树已 taskkill；:5173 上的 vite 启动于 2026-10-03 09:12，早于本计划，未动）。因此上表数字以本文档记录为准，脚本不再可复现；如需复现，5.3 已写明栈配置（自建 OpenAI 兼容 mock provider 起 :8011、后端 :8012 只换上游，路由/护栏/帧协议/前端全走产品真实代码），5.5 写明了空载串行采样方法——并发采样会自我污染计时，这是本轮踩过的坑。

---

## 六、第三批实施记录与销项（工作包二：Skill/MCP + 裁决 D-B 修正，2026-10-05）

验收行原文（第 82 行）：**"第三批 | 工作包二（Skill/MCP） | 2-3 人日 | Skill 热加载生效+管理端启停；MCP 经准入审查+只读治理；审计留痕"**。
实施令第三批前置：**"MCP 准入审查材料先行提交委员会备案"**——备案件 `docs/mcp-admission-review-2026-10-05.md`（94 行）已先行成文，本批实现按该件结论走自研路径。

### 6.1 备案件结论摘要（不重复取证细节，详见该文件）

| 对象 | 结论 | 决定性理由 |
| --- | --- | --- |
| 社区 `office-word-mcp-server` | **本批不予准入** | 上游 `archived=true`（CVE 无修复通道）+ 54 工具无条件注册、服务端无禁用开关 + 文件读写无路径归一/白名单（任意路径读写）+ `convert_to_pdf` 可被模型触发拉起外部进程；叠加本仓桥接层无鉴权头/无超时/无版本锁定列，六检查 4 项无法闭合 |
| 自研 `erp_readonly_query` + `document_generate_docx` / `document_generate_xlsx` | **已落地并纳入治理面** | 零新增攻击面（不出网、不起进程、产物只进受控目录），表白名单 + 按表 RBAC 二次鉴权 + 写工具补偿幂等，调用全量留痕 |

D-B 逐条对照：`mcp-server-sqlite` 否选→按裁决改自研只读查询（✓ 已落地）；首批准入 2 个→自研工具 ✓、第三方 server 经审后 ✗ 不接入（能力面由既有 python-docx/openpyxl 生成器承接）；"external_tools 白名单桥接 + SSRF 三关卡 + 调用审计"→桥接层既有且经回归覆盖，但本批**无外部 server 接入**，该路径未被使用（不是绕过，是没用）；fetch / filesystem 第二批候选→未启动，仍属待裁决（备案件 §5-4）。

### 6.2 验收行三条逐条对照（全部实测，非估算）

| 验收项 | 落点 | 实测证据（真机 :8010，产品 HTTP 通道） |
| --- | --- | --- |
| Skill 热加载生效 | `skills.py` 目录指纹（路径+文件名+纳秒 mtime）触发重扫 | 不重启进程：发布目录写入 `zz_batch3_probe.yaml` → 目录 `count 12→13`，新项 `valid=True tools=['document_generate_docx']`；删除 YAML 后回落 |
| 管理端启停 | `PUT /api/v1/agent-ops/skills/{name}/enabled` → `skill_versions.set_enabled` → 定制层 | 停用（收紧）：`200 {'status':'applied','reason':'收紧类变更即时生效','thread_id':None}`，目录 `enabled=False`；启用（放宽）：`200 {'status':'pending_approval','thread_id':'cust:skill:weekly_report:user_admin'}`，**待批期间目录仍 `enabled=False`**（红线 2：提案不顶掉现状） |
| 人审闭环（启停的另一半） | `POST /api/v1/agent-ops/customizations/skill/weekly_report/decide` | `200 {'status':'rejected','reply':'定制变更已驳回（当前生效配置未改动）'}`；`customization_versions` id=700 → `rejected` + `approved_by=user_chairman`；`?status=pending_approval` 由 1 条→0 条；`/agent-ops/approvals` 由 5 张→4 张且 `weekly_report_cards=[]`（卡片自动关闭）；终态新进程读 DB：`get_override('skill','weekly_report')=None` → `enabled=True` |
| MCP 经准入审查 | 备案件 §2 六检查逐项 + §1 取证通道与未核实项声明 | 提交委员会备案（本批实现的前置件） |
| 只读治理 | `erp_readonly_query`：14 表白名单 + SELECT-only + 禁止 token + 强制 LIMIT(200/1000) + 按表二次鉴权 | 入口闸门真实拦截：cio 初次 5/5 表返回"权限不足：需要 `platform:read`"，周报数值全标"待补"（未编造）；授权后 `platform:read` 1 行，K2 腿真实取数 `numeric=12`（含 `3,308,000`） |
| 审计留痕 | `_audit_skill_activation` 写 `audit_logs` | 3 轮请求 → `audit_rows=6`：`skill.activated\|skill:weekly_report\|success\|low` 与 `skill.activation_denied\|skill:not_a_skill\|denied\|medium\|{"reason":"技能不存在或已停用: not_a_skill", "trace_id":..., "thread_id":"channel:CH-8d43324bbebf"}` |

### 6.3 工具面与出厂目录现状

`GET /api/v1/agent-ops/tools` 共 40 个工具，新增 3 个均带 `permission/risk_level/has_compensation/args_schema` 字段面：`erp_readonly_query`（readonly/`platform:read`）、`document_generate_docx`、`document_generate_xlsx`（write_low/`document:create`）。`GET /api/v1/agent-ops/skills` 出厂目录 **12 项、invalid=0**，其中 platform 域 5 项（`data_brief`/`docx_report`/`meeting_notes`/`weekly_report`/`xlsx_report`，工具引用全部可解析）。

一处**提交声明与树内容不符**必须报备：工作包二要求的"五件套 Skill"此前**从未存在过**——`git log --all -- 'backend/app/core/agent/skills/*.yaml'` 只有一个提交（`8a99aee` phase-D，6 项业务技能），`421d99d` 的提交信息写着"五件套 Skill（D-B/D5 落地）"却没有任何技能 YAML（`git show 421d99d --name-only` 共 7 个文件，全是 tools/intent/sandbox/tests），磁盘上也没有（本批新建的 6 个文件 mtime 均为 2026-10-05，其余 6 个为 2026-09-19）。也就是说整改方案 1.2 里"Skill 目录有定义但工具名 0 注册点"的真实形态更糟：连定义都没有。本批把 5 件套 + `supervision_plan` 正式落盘并纳入版本控制，目录 6→12。D-C 页眉标注由治理面强制注入，调用方自带标注一律丢弃（实测产物页眉 `AI 生成草稿 · 生成时间 … · trace_id … · 未经审批，不得作为正式交付物`，伪造的"正式交付物"未出现）；补偿重放首轮 `{"removed": true}`、次轮 `{"removed": false, "reason": "产物已不存在（补偿幂等：no-op）"}`。

### 6.4 本批实测发现并已改（不是登记）

1. **fea297c 误删了 `result = await agent_runtime.run(...)`**——`orchestration_service.run_scenario_for_agent` 的非 supervisor 分支自此直接读未定义的 `result`，任何"直连单个 Agent（非总调度）"的调用必抛 NameError。`git show af3af8c:backend/app/core/agent/orchestration_service.py` 第 92 行可复核。**两批全量回归（1480 passed）从未为它红灯——该分支零覆盖**。已恢复调用，并由 `test_skills.py::test_orchestration_activates_skill_into_ctx_and_audits`（monkeypatch `agent_runtime.run` 断 `reply=="ok"`）钉住。
2. **`base.py` 把编排键当 LLM 参数下发**：`ctx.extra` 里的 `skill`/`domain` 等键随 kwargs 进 OpenAI 载荷。改为 `_llm_keys` 白名单过滤（`temperature/max_tokens/response_format/extra/scene/cost_domain`）。
3. **supervisor 规则路径没有 platform 域**：`ROUTE_KEYWORDS` 缺 platform，规则降级时"汇总本周经营进展并生成一份周报文档"回"未识别出该指令涉及的业务域。目前可协同的域：contract、crm、hr、payroll、reimbursement、sales、treasury"（实测 1.4s）。已补 platform 关键词。
4. **分类抖动会让指名技能被静默丢弃**：同一指令两次分别分到 `platform+knowledge` 与 `knowledge`，`runtime._skill_for` 的域守卫据此丢掉 weekly_report → 无产物（I 腿 `reply_len=1292`、`artifact_link=NONE`）。新增 `supervisor._apply_skill_route`：请求指名的技能所在域并入委派集合（幂等、置于最前、不改多域语义）。修后日志 `supervisor.skill_domain_forced domain=platform`，K2 腿 `doc_link=True`。
5. **`audit._save_verify_state` 里 `orjson` NameError**：真机日志 `audit.verify_state_save_failed error=name 'orjson' is not defined`——审计链点位与报告**从未落库**。补局部 import 后实测 `verify_chain checked=5442`，`audit_verify_state.last_incremental_at` 由 `None`→`2026-10-04 23:59:53`，`last_report` 56 B 首次写入。
6. **`erp_readonly_query` 表名未归一**：白名单校验按 `strip().lower()`，按表鉴权却用原始表名查 `_TABLE_RESOURCE` → `" Payroll_RECORDS "` 这类变体绕过白名单判定后 KeyError。已统一归一，并加 `test_table_name_normalization_before_domain_check`（断言 seen `payroll:read` + PermissionError）。
7. **`platform:read` 入口资格把业务角色全挡在只读查询外**：本批最初只给 chairman/ceo，真机周报 5/5 表被拒。改为业务角色统一获得**入口资格**（`seed.py` 尾部对 `ROLE_PERMISSION_MATRIX` 补 `platform:{"read"}`），数据面仍由按表二次鉴权收口（读 `payroll_records` 需 `payroll:read`）；持有者 27→24。
8. **一处早期表述需更正（我的探针误判，不是产品缺陷）**：D2 腿 chairman 停用技能被 403 拦下，我当时记作"erp_db 与种子矩阵漂移"。回树核实 `seed.py:851`——chairman 的 customization 恰为 `{read, approve, create, publish}`，**不含 update**，403 是设计正确行为（CIO 执行、董事长审批）。真正的漂移只有 `platform:read` 一处：`seed.py:1310` 的播种是"先查后插、从不撤销"，所以三个专用角色在 erp_db 里仍持有 `platform:read`（代码侧已收回），需人工撤销——登记待委员会（6.6-②）。
9. **conftest 与 D-04 的断言冲突定责**：`tests/conftest.py` 为隔离把 `settings.seed_password` 钉成共享口令，使 D-04 要治理的"随机口令模式"三条断言在测试环境恒不成立。已在 `test_seed_password.py` 加 `random_mode_seed` fixture 显式清掉覆盖后重建种子（不改 conftest 的隔离语义，也不弱化断言）。

### 6.5 测试（本批新增 14 条 + 改写 3 条 + 两处计数断言校正）

| 文件 | 新增用例 | 钉住的行为 |
| --- | --- | --- |
| `test_platform_sandbox_g.py` | `test_readonly_query_denied_without_domain_permission` / `test_readonly_whitelist_tables_exist_in_orm` / `test_table_name_normalization_before_domain_check` / `test_platform_read_is_entry_gate_for_business_roles` / `test_document_tools_registered_with_governance_triplet` / `test_document_generate_docx_forces_draft_header` / `test_document_compensation_removes_then_noop` / `test_skill_activation_audit_rows_persist` | 按表二次鉴权、14 表白名单与 ORM `__tablename__` 逐项对齐防漂移、表名归一、入口资格排除集（三个专用角色）与业务角色恰为 `{"read"}`、写工具三件套齐备、D-C 页眉强制注入、补偿二轮 no-op、启停留痕真写 `audit_logs` |
| `test_skills.py` | `test_builtin_skills_all_valid` / `test_resolve_activatable_skill` / `test_resolve_activatable_skill_respects_disable` / `test_skill_for_not_applied_when_tools_out_of_domain` / `test_orchestration_activates_skill_into_ctx_and_audits` | 出厂目录不得存在"引用未注册工具"的技能；`resolve_activatable_skill` 的 (name, reason) 契约；停用项不可激活；跨域技能不套工具（域守卫）；生产侧 `kwargs.skill → ctx.extra["skill"]` 并回落纯域——同时覆盖 6.4-① 恢复的 `agent_runtime.run` 分支 |
| `test_supervisor.py` | `test_apply_skill_route_keeps_named_skill_domain` | 指名技能的域并入委派集合：`["knowledge"] → ["platform","knowledge"]`、幂等、空/未知技能/None ctx 原样返回 |
| `test_seed_password.py` | 改写 3 条走 `random_mode_seed` | 6.4-⑨ |
| `test_external_mcp.py` | 计数断言由 `len(skills) == 6` 改为按名字集合断言 + `assert not invalid` | 目录会随版本增长，写死条数等于给自己埋漂移红灯；改后 overlay 有额外技能时也不假红灯 |

### 6.6 待委员会裁决（五项，本批不自行决定）

1. **备案件 `docs/mcp-admission-review-2026-10-05.md` §5 四条原样转呈**：是否为外部 MCP 新增 stdio transport / 允许本机拉起第三方 server 进程；`external` 是否入 RBAC `RESOURCES`；外部 MCP 的最小治理前置（`mcp_servers` 增列 `transport/api_key_ref/version_pin` + 调用侧 timeout/熔断 + 按 `ToolAnnotations` 强制分级）；`fetch`/`filesystem` 第二批候选是否一并冻结。
2. **erp_db 中三个专用角色（`log_auditor`/`customization_admin`/`customization_approver`）残留的 `platform:read` 是否撤销**。代码侧本批已收回，但播种 insert-only 不会 revoke，库里仍是授予态；`log_auditor` 的"零业务资源"是两条安全回归钉住的不变量，属于必须撤销的一类。
3. **5.7-1 首 token 口径**：仍按"管线预算 ≤150ms + 供应商 TTFT 单独 SLO"拆，还是坚持端到端 2s 并要求网关层兜底——它决定本方案能否关闭（第三批不涉及，但验收行口径是共用的）。
4. **#40 事件循环单例族**：本批全量再次复现（`test_ws_auth_g.py::test_refresh_rotates_refresh_token` 报 `Lock ... bound to a different event_loop`，隔离复跑 PASS），与 5.10 红线 2 竞态同族，仍未定责。
5. **`mcp` 类定制入口的存废**：第三方 server 本批零准入，但管理端仍可配 server + 勾选工具（`agent_customizations` type="mcp"）。入口保留而接入面为空，是否算"可点但必然无效"的 UI 债，请委员会定：保留（等 §5-3 前置件）或暂时隐藏并标注"未准入"。

### 6.7 明确不做 / 未实现（含理由）

- **不接入任何第三方 MCP server**（备案件 §2 六检查 4 项无法闭合，且桥接层无鉴权头/超时/版本锁定）——`external_tools.py` 桥接与 SSRF 三关卡原样保留，本批未使用；
- **platform 域不新增 Agent**：`runtime._build_graph(domain, skill)` 只要工具注册即可建图，新增 Agent 只会多一个空壳节点（真机 K2 腿即在 supervisor 委派下跑通，无需本体 Agent）；
- **未做**：`office-word-mcp-server` 的依赖 CVE 扫描（无离线数据源，备案件 §1 已列为"未核实项"，不作为结论依据）；
- **未做**：§5-3 的桥接层治理前置（等裁决 6.6-①，实现前任何"可准入"结论都不成立）；
- **前端未动**：技能启停/版本历史 UI 在 F4.4/F4.5 已具备，本批无新增交互面。

