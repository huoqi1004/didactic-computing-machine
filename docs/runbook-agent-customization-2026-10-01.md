# Agent 定制层运维手册（Phase F5.5 / 交付物之一）

- **文档日期**：2026-10-01
- **适用**：Agent 定制化平台路线图（roadmap-agent-customization-2026-09-19.md）Phase F1~F5 全部交付后的运行期
- **配套**：《管理员操作手册》（admin-guide-agent-customization-2026-10-01.md）、部署基线（deployment-production.md）
- **取证口径**：本手册每个数字都指向一次真实测量（CI 用例或真机演练），未测的显式标注「待补测」

---

## 1. 定制层的运行时模型（先理解再排障）

三层合成：**内置默认（代码常量）→ 定制覆盖（MySQL）→ 运行时合成视图**。
运行时的读路径**不同步查库**，读的是进程内缓存 `_cache`（`backend/app/core/agent/customization.py`）。

| 机制 | 实现位置 | 实测值 |
|------|----------|--------|
| 版本信号 Key | `customization.py:36` `CUSTOMIZATION_VERSION_KEY` | `erp:cust:version`（Redis） |
| 漂移检测节流 | `customization.py` `ensure_fresh()` | **5 秒**（同进程 5s 内的重复调用直接返回） |
| 同进程写入串行化 | `_write_lock(ctype, target)` | 按 (类型, 目标) 粒度；**不跨进程** |
| 响应 SLO | `app/core/config.py:223` `agent_slo_response_time_ms` | **5000 ms** |

关键不变量（由 CI 用例 `tests/test_hot_reload_concurrency_f53.py` 守住）：

1. 写入后本进程立即可见自己的值（缓存与库不出现「写成功但读旧值」）；
2. 并发写同一 target 不再产生「库新缓存旧」的永久分歧（50 路并发断言 `cache == db`）；
3. 重载失败**绝不清空缓存**——旧值继续服务，这是降级语义的地基。

多实例收敛实测（两个 uvicorn 进程共用同一 MySQL/Redis，`tests/perf/multireplica_f53.py`）：
跨实例收敛 **4/4**，延迟 `[0.36, 5.252, 5.221, 5.23]` 秒——即「立刻撞上版本号」或「等满 5 秒节流窗口」。

---

## 2. 三个观测面（告警就配在这里）

### 2.1 HTTP：降级状态端点

```
GET /api/v1/agent-ops/customizations/degradation      # 权限点 customization:read
```

响应字段（真机演练实测样例，坏库实例）：

```json
{"degraded": true, "mode": "defaults", "serving": "built_in_defaults",
 "reason": "reload:OperationalError: ... (1044, \"Access denied ... \")",
 "since": "2026-10-01T05:25:32+00:00", "degraded_seconds": 20.6,
 "degrade_events": 1, "reload_failures": 2,
 "loaded_from_db": false, "cache_entries": 0, "cache_version": 694}
```

- `loaded_from_db` 语义是**「至少成功读到过一次定制层」**，不是「试过读库」。
- `degrade_events`（事故次数）与 `reload_failures`（失败重试次数）**必须分开看**：
  5 秒一轮的重试会把失败计数刷高，但告警只应响一次——`degrade_events=1 / reload_failures=2` 就是正确形态。

### 2.2 Prometheus：`erp_customization_degraded`

| 状态 | 序列值 | 实测来源 |
|------|--------|----------|
| 正常（从未降级） | `erp_customization_degraded 0.0` | 真机演练 `_tmp_f54_drill3.json`（修复前该序列**不存在**，见 §6.4） |
| 降级中 | `1.0` | 同上，坏库实例 |
| 恢复 | `0.0` | `_clear_degraded()` 跳变时写 |

抓取路径 `/metrics` 有一处**已核实的重复注册**：`app/main.py:957-958` 用 `make_asgi_app()` 挂 Mount，
`app/main.py:968` Instrumentator 又 `expose(endpoint="/metrics")`。Starlette 按注册顺序匹配，**Mount 胜出**；
两者共用 prometheus_client 默认注册表，所以 `http_requests_total` 仍能在同一响应里看到（实测 present=True）。
运维只需知道：**URL 只有一个 `/metrics`，不要按两个端口分别配抓取任务。**

### 2.3 日志：事件名与跳变语义

| 事件名 | 级别 | 触发时机 |
|--------|------|----------|
| `customization.degraded` | ERROR | **仅在正常→降级的跳变**打一条（带 mode/serving/cache_entries/degrade_events/reload_failures） |
| `customization.degraded_recovered` | INFO | 降级→恢复跳变 |
| `customization.cache_reloaded` | INFO | 每次成功重载（带 entries 数） |

实测：坏库实例运行 21 秒、期间持续重试，全日志中 `customization.degraded` **恰好出现 1 次**
（`grep -c` = 1）。这是刻意设计——每 5 秒一条 ERROR 会把真正的故障埋进噪音里。
排障时请查 `customization.cache_reloaded` 是否停更，而不是等新的 degraded 行。

---

## 3. 两种降级模式：含义完全不同，别混

| mode | serving | 判据 | 运维动作 |
|------|---------|------|----------|
| `defaults` | `built_in_defaults` | 从未成功读到过定制层（`_load_ok=False` 且缓存空） | **所有自定义阈值/人设/工作流都没在生效**，Agent 按代码出厂值跑；审批路由矩阵也回退出厂规则 |
| `stale_cache` | `stale_cache` | 至少成功读过一次，之后读库失败 | 在吃最后一次成功的旧值；**行为可能与库里已发布的配置不一致**，需在对账后确认 |

判据是 `bool(self._cache)` 与 `_load_ok`，**不是** `_loaded_from_db`（后者是节流闸门）。
混淆这两者会在演练里把「冷启动就失败」误报成「在吃旧缓存」——F5.4 真机演练第一轮就抓到了这个错报，
已修（见 §6.4）。日志里看到 `mode=stale_cache` 却 `cache_entries=0` 属异常形态，说明还有回归，请立即上报。

停用（enabled=false）与「没配过」也是两件事：
- `get_override()` 返回**原始条目**（含 `enabled`），技能/MCP/管理页靠它显示启停态；
- `get_effective_override()` 遇到停用返回 `None`，阈值/域参数/风险覆盖/runtime 用它。
所以停用一个定制后，运行时立刻不再采纳该覆盖，但生效视图里仍能看到「已停用」这条记录。

---

## 4. 故障处置 SOP

### 4.1 MySQL 不可达（定制层读失败）

现象：`/api/v1/health` 仍 200；`/degradation` 报 `degraded=true`；读接口正常返回内置默认。
**服务不会因此下线**——这是 Phase F5 退出标准第 5 条「0 不可用」的实测结论（坏库实例 health=200、
生效视图 HTTP 200 且 0 条）。

1. 先看 `mode` 判型（§3）；
2. 确认库侧原因（`reason` 带异常类型与原文）；
3. **不要重启实例来「刷新」**：重启只会把 `stale_cache` 变成 `defaults`（内存缓存随之丢失），
   在库还没恢复时是净损失；
4. 库恢复后无需人工干预：下一轮 `ensure_fresh()` 成功即自动 `_clear_degraded()`，
   日志出 `customization.degraded_recovered`，gauge 回 0。

### 4.2 降级期间的写入（管理端保存定制）返回 500

实测：坏库实例上 `PUT /api/v1/agent-ops/customizations/agent/<target>` → **HTTP 500**，
日志 `request.failed error_type=OperationalError`。

这是**失败安全**而非数据缺陷：定制变更必须与 `customization_versions` 快照、审计哈希链同库落盘，
库不可写时接受变更就等于破坏红线 3（变更 100% 审计）。所以绝不静默丢弃、也绝不假装成功。
但 500 + 「内部服务器错误」对操作者不友好，**已列为待决策项**（§6.2，是否显式 503 + 指引文案）。
处置：告知管理员「定制层当前不可写，请先恢复数据库」，不要反复重试。

### 4.3 Redis 不可达

版本信号读不到时，进程继续用本地缓存，不会因为观测面抖动而阻断业务读。
代价是**多实例收敛失效**（其他实例感知不到变更），恢复后第一轮节流窗口（≈5 秒）内自动追平。

### 4.4 定制表损坏 / 想整体回出厂（对应路线图风险表「定制表可清空回出厂」）

回退粒度从细到粗：

1. **单项回滚**：`POST /api/v1/agent-ops/customizations/{ctype}/{target}/rollback`
   （回滚到任意历史版本或出厂默认，权限 `customization:update`）；
2. **整体回出厂**：清空 `agent_customizations` 生效表 → 全部按内置默认运行。
   真实动作已由 CI 用例守住：`tests/test_customization_degradation_f54.py::test_clearing_customization_table_returns_to_factory_defaults`。

**`customization_versions` 与审计哈希链不许删**——那是红线 3 的留痕面。
真机演练脚本 `tests/perf/drill_f54_degradation.py` 的 `purge_drill_rows()` 就是按这个原则写的：
删生效行（实测 `agent_customizations_deleted=1`），**保留版本行**（演练后残留 4 条 drill/perf 版本行，
经核对属预期留痕，非脏数据）。

### 4.5 多实例之间的残余陈旧（已知机理，未复现）

同 target 并发写由**进程内**锁串行，跨进程不互斥。实测：双实例各 40 路并发写同一 target，
1 秒时 A 仍持有自己的最后写（0.105/0.119）而库里是 0.207/0.219，20~30 秒内两侧追平，
`persistently_stale={}`（无永久分歧）。残余风险是「最后 bump 版本号的实例不再重载」这一理论窗口，
两个样本未复现。**兜底方案（最大陈旧期强制重载）已列为待决策**（§6.2），未擅自实现。
临时人工兜底：对目标实例发一次任意定制写（或 `DEL erp:cust:version` 后等待一轮）即可强制重载。

---

## 5. 限流与容量：429 有两个来源，别只查一处

| 层 | 规则 | 代码位置 |
|----|------|----------|
| IP 全局 | 120 次/分钟 | `app/api/middleware/rate_limit.py:189-190` |
| IP 写操作（POST/PUT/DELETE） | 60 次/分钟 | 同上 `:181-182` |
| IP 认证（登录/刷新） | 10 次/分钟 | 同上 `:154-155` |
| 每用户 LLM 对话 | 10 次/分钟 | 同上 `:160-164`（`config.py:138 llm_rate_limit=10`） |
| 每用户文档生成 | 10 次/分钟 | 同上 `:169-173` |
| 租户配额 | basic 60 / standard 600 / enterprise 3000 次/分钟 | `app/core/tenant/tenant.py:72-77` |

压测口径（F5.3 实测结论）：**429 必须从失败率与 p95 里摘出来单独计数**，否则限流噪音会伪装成性能拐点。

两处信任边界风险（已查明、待决策，见 §6.2）：
- `_get_client_ip` **无条件采信 `X-Forwarded-For`** → 客户端可伪造该头绕过 IP 限流；
  仅当后端确实位于会覆写该头的可信代理之后才安全。
- 租户档位 `standard=600/min`（≈10 RPS）就是**容量天花板**，与并发无关——压测打不满往往是配额先撞。

---

## 6. 已知缺陷、待决策与未实现（三类分开）

### 6.1 已查明并修复（本轮 F5.4 真机演练抓到）

1. **降级模式说谎**：冷启动就读库失败的实例，把「试过读」当成「读到过」，
   报 `mode=stale_cache` + `loaded_from_db=true` 却 `cache_entries=0`。
   修：新增 `_load_ok` 与缓存非空共同判型，失败路径不清空缓存。
2. **停用不生效**：`enabled=false` 后运行时读取器（`runtime._agent_cfg`、`get_threshold`、
   `get_domain_param`、`get_tool_risk_override`、`resolve_approve_roles`）仍照用该覆盖。
   修：新增 `get_effective_override()`，上述 5 处改走它；`get_override()` 保留原始条目供启停态展示。
3. **降级指标序列在正常态不存在**：`erp_customization_degraded` 只在跳变时才 `import` 指标模块，
   健康进程 `/metrics` 里「无数据」，与「监控未接」无法区分。
   修：把指标 import 上提到 `customization.py` 模块级；实测健康实例现在稳定输出 `0.0`。
4. **报销 Agent 启动日志必崩**（顺带修，与定制层无关但污染演练日志判读）：
   `app/agents/reimbursement/agent.py:107` 使用裸 `logger` 而该模块从未定义它（兄弟模块
   contract.py:29 / sales.py:16 / hr.py:19 都有 `logger = structlog.get_logger()`），
   于是 `hydrate_from_db()` 每次加载完数据都在日志行抛 `NameError`，被 `main.py:604` 吞成
   `app.agent_hydration_failed error_type=NameError`；同文件 `self._logger` 在全仓无定义（潜在 AttributeError）。
   修：补模块级 logger，并把 `self._logger.error` 改为 `logger.error`。
   全仓扫描确认这是**唯一**一个「用裸 logger 却没定义」的 agent 模块。

以下 3 条由 **F5.6 UAT（自助动线演练）**抓到，本轮已修：

5. **gate 白名单只在注释里承诺、没有落地**：`apply_change` 的 target 校验只有
   `tool` / `mcp` / `skill` 三支，gate 类型的键随便填都能写进生效层，
   而运行期 `get_threshold` 只认 `THRESHOLD_DEFAULTS` 三键——
   管理端显示「已覆盖」、工作流仍按出厂线跑（**死键**，排查时最费时间的一类）。
   阈值 `value` 也没有形态校验：字符串、负数会被 `get_threshold` 静默回退成内置默认，
   保存返回 200 却什么都没改（假成功）。
   修：`GATE_TARGETS = THRESHOLD_DEFAULTS ∪ {approval_route_matrix}` 白名单 + 非负数字校验，
   越界/非法一律 `CustomizationError` → HTTP 400（矩阵与阈值同挂 gate，白名单不能漏矩阵）。
6. **阈值上调绕过人工审批（红线 2）**：`_classify_change` 的 gate 分支从 `new["value"]` 取新值，
   而调用方传的是 `{"overrides": {...}, "enabled": ...}`——新值恒缺省，
   等于「拿旧值和旧值比」，任何上调都被判成收紧、**即时落库生效**。
   修：改从 `new["overrides"]["value"]` 取，且两侧都按**生效值**比较
   （停用一份比出厂线更紧的覆盖，生效值是升回默认 → 方向属放宽）。
   真机实测：上调 `agent_daily_token_budget` 2,000,000→3,000,000 返回
   `pending_approval`，生效层始终不出现该 gate 条目，驳回后现场无残留。
7. **子分区保存后，父级待审批队列不刷新**（前端）：`ApprovalRouteMatrix` / `ThresholdOverrides`
   的 `load()` 只刷自己那份数据，父级 `pendingApprovals` 停在保存前的计数，
   管理员会以为「提交没进队列」而重复点。修：新增 `onChanged` 回调，父级把 `load` 传下来。

回归：`pytest tests/test_customization_degradation_f54.py tests/test_hot_reload_concurrency_f53.py -q`
→ **17 passed / 41.18s**（本机 `.venv`，2026-10-01）；
gate 阈值改造后复跑
`pytest tests/test_customization.py tests/test_customization_degradation_f54.py tests/test_agent_customization_f2.py tests/test_hot_reload_concurrency_f53.py -q`
→ **38 passed / 84.62s**（2026-10-01，同一 `.venv`）。

### 6.2 待决策（需甲方/运维负责人拍板，未擅自实现）

| # | 事项 | 现状与影响 |
|---|------|-----------|
| D1 | 降级期写入 500 是否改显式 **503 + 指引文案** | 语义上安全，操作者不可读（§4.2） |
| D2 | 多实例「最后 bump 者不再重载」残余陈旧窗口的兜底 | 建议加「最大陈旧期强制重载」；实测未复现（§4.5） |
| D3 | `_get_client_ip` 采信 XFF 的信任边界 | 可伪造绕过 IP 限流；需确认部署拓扑（§5） |
| D4 | 租户配额档位（standard 600/min）是否为容量承诺 | 是压测天花板，需业务侧确认（§5） |
| D5 | `customization_admin` / `customization_approver` **进不了管理台** | 详见操作手册 §2 与本文 §6.3；涉及权限授予，属安全边界变更，未擅自改种子 |
| D6 | 管理台两个看板**用的是两套时钟**（审计=本地墙钟，评测=UTC） | 详见 §6.4 第 5 腿取证；影响事故时间对齐，改哪一侧是口径决策，未擅自统一 |

### 6.3 未实现 / 待补测（不谎报）

- LLM happy-path 压测：本轮 p95 8026.1/4181.2 ms 的超标样本全部来自欠费降级路径
  （日志 `Insufficient Balance` 1334 次），**Key 充值后需同命令重跑**；
- `warm 失败腿`（成功读过再失败）目前只有 CI 证据，真机注入需要改 DDL，已放弃并如实标注；
- locust 本体未跑（`backend/.venv` 无 locust 依赖），压测由自研 `tests/perf/loadtest_f53.py` 完成。

### 6.4 UAT-F5-1 自助改造演练：分腿结论（不把五条腿合并成一句「通过」）

判据（路线图附录 A）：**全程不接触代码/文件/服务器**；操作步骤 ≤10 步/项；全程审计可查；
异常操作（非法 DSL）被校验拒绝并给出可读原因。演练按甲方 2026-10-01 决定改用探针资产，
**不碰报销域线上单据**。以下每条都是浏览器真机取证。

| 腿 | 结论 | 取证 |
|----|------|------|
| ① 调整风控阈值 | **通过**（5–6 步） | 定制中心 →「风控阈值」→ 改值 → 保存。上调 `agent_daily_token_budget` 2,000,000→3,000,000 返回 `pending_approval`；驳回后「当前生效配置未改动」，生效层实测零残留（`GET /customizations` 只有既存 `tool:treasury_fund_transfer_request`） |
| ② 改造条件分支 | **通过**（8–9 步） | 「工作流设计」→ 选 `procure_to_pay` → 载入出厂模板 → 高级 JSON 编辑 → 应用 JSON → 校验。校验回显 `校验未通过（2 项）`，两条可读原因：`condition 节点 triage 分支 0 缺少条件算子（equals/not_equals/in/gt/gte/lt/lte/exists）`、`condition 节点 triage 缺少 default（失败安全）`。**再点「保存草稿」**：后端 400 同一串可读原因，草稿表实测零写入（`GET /workflow-definitions?name=procure_to_pay` → `{"definitions":[]}`）——即「校验通过才会落草稿」不靠前端自觉 |
| ③ 编辑 Skill 指令 | **沿用 F4.6 真机结论**，本轮不重复 | 见本文/操作手册 §4.4（含两个已修坑：目录不刷新、导出漏出厂技能） |
| ④ 三笔不同金额单据验证 | **未做（阻塞，非代码问题）** | 需要可污染的 UAT 租户 + LLM Key 充值；且与「不碰报销域线上资产」的决定直接冲突。见 §6.3 第 1 条 |
| ⑤ 查看审计与评测报告 | **通过** | 定制审计：`共 19 条 · 待批 0 · 已生效 18`，F5.4 探针 `agent:drill_f54_probe_agent` 的「旧值→新值 / 提出人 / 审批人」逐条可查。评测趋势：从看板点「触发评测」（域选 `hr`，**刻意避开 D-4 的薪酬 `payroll`**）→ run_id `eval-2f8748559b` → `PASS · 7 passed, 44 deselected in 82.00s`，刷新后入表：来源=手动、通过率 100%、时长 82.00s、触发人 user_admin |

**⑤ 顺带抓到 D6（未修，见 §6.2）**：同一次评测在「评测趋势」里显示为
`2026-10-01 07:53:36`，而它实际发生在本地 15:53——差整整 8 小时。机理已查明：
`app/db/models.py:75-77` 的 `_utc_now()` **名不副实，返回的是 `datetime.now()`（本地墙钟、naive）**，
全库 `created_at/updated_at` 都走它，所以「定制审计」显示本地时间；
而 `app/core/agent/eval_runner.py:31` 用的是**真 UTC**（带 tzinfo），
两个看板的前端都只做 `slice(0,19).replace('T',' ')`、把时区后缀直接切掉，于是并排呈现两套时钟。
对齐事故时间线时这会误导排查者，但**改哪一侧是口径决策**：
把 `_utc_now()` 改成真 UTC 会让**全部历史时间戳的含义**整体平移 8 小时（且要和 MySQL
`NOW()` 写入的行比对），只改前端则留下「库里存本地时间」的长期坑——故只登记，不擅改。

**步数口径**：①②⑤ 每腿都 ≤10 步，满足附录 A；②的 9 步里有 3 步（高级 JSON 编辑 / 应用 JSON / 载入出厂模板）
是「编辑既有节点」之外的 JSON 直改路径，用节点编辑器逐个点会更少。

---

## 7. 复跑取证（可复制命令）

```bash
cd backend
# 0) 健康实例（不要用 PATH 上的 python，它没有 structlog）
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010

# 1) 坏库实例：指向不存在的 schema 即模拟 MySQL 不可达，零 DDL、不动生产库
ERP_MYSQL_URL="mysql+aiomysql://erp:erp_password@127.0.0.1:3306/erp_f54_no_such_db?charset=utf8mb4" \
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8012

# 2) 降级演练（8 条判据全绿才算通过；自动清理自建生效行，保留审计版本行）
./.venv/Scripts/python.exe tests/perf/drill_f54_degradation.py --out _tmp_f54_drill.json

# 3) 多实例收敛压测
./.venv/Scripts/python.exe tests/perf/multireplica_f53.py --a 8010 --b 8011 --rounds 4 --writers 40
```

**进程管理注意（本机踩过的坑）**：`.venv/Scripts/python.exe` 是 shim，会 re-exec 到系统 Python，
**一个服务实际是父子两个进程**。清理时必须 `taskkill /F /T /PID <监听PID>`，
且杀之前先探测用户自己的 :8000 是否健康——按单个 PID 杀会误伤。

演练脚本自建的行以 `drill_f54_` / `perf_f53_` 前缀命名，收尾核对：
`SELECT COUNT(*) FROM agent_customizations WHERE target LIKE 'drill\_%' OR target LIKE 'perf\_%'` → **0**。

同理核对 gate 探针（本轮 F5.6 UAT 用的是 `agent_daily_token_budget`，不碰报销免审线）：
`SELECT type,target FROM agent_customizations WHERE type='gate'` → **空**（生效层不该留 gate 行；
留了就说明有放宽提案被误批）。

### 7.1 全量回归实测（2026-10-01，本机 `.venv`）

| 范围 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `pytest tests/ -q` | **1 failed, 1302 passed, 4 skipped / 548.84s** |
| 后端改动前基线 | 同上（13:53 那次） | 2 failed, 1299 passed, 4 skipped / 635.15s |
| 前端全量 | `npx vitest run` | **13 files / 224 tests passed / 3.99s** |
| 前端构建 | `npm run build` | built in 8.12s |
| 定制层定向 | 见 §6.1 尾部 | 38 passed / 84.62s |

两条**必须一起读**的说明，否则数字会被误当成回归：

1. 唯一失败 `tests/test_e2e_flows.py::test_库存补货全流程…`（`test_e2e_flows.py:186`
   `assert 0 >= 1`）与基线**同一处、同一断言**，根因是 LLM Key 欠费（同窗口日志
   402 `Insufficient Balance`）导致补货 Agent 没发出 `REPLENISHMENT_PROPOSED` 事件——
   属 §6.3 第 1 条的外部依赖，**不是定制层回归**。
2. `tests/test_migration.py::TestMigrationManager::test_compliance_report` 在基线里 FAIL、
   本轮全量里 PASS、**单独复跑又 FAIL（11.20s）**。它是顺序/时序依赖的脆断言：
   `test_migration.py:553` 断言 `last_activity["action"] in ("read","write")`，
   而紧邻两次 `log_access` 在同一微秒内落库（测试注释自己写了这个风险）。
   与定制层无关，但会反复污染「全量是否绿」的判读，建议后续改成按集合断言。

**操作口径提醒（本轮踩到）**：不要把后端 pytest 和前端 vitest **并发**跑。
本轮第一次前端全量在争用下出现 1 个假失败（`WorkflowDesigner.test.tsx` 校验回显用例，
1562ms vs 隔离复跑 285ms，单独跑 17 passed）——机器负载会把这类等待型断言打成红灯。
