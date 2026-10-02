# 代码执行沙箱（S4）书面评估 —— 按评审裁决 D5-B 交付

- **文档日期**：2026-10-03
- **裁决依据**：《可观测与沙箱回滚路线图》v1.1 §8 决策点 D5——"B 有条件通过（维持禁用，出评估预案）"：
  ① 本报告含最小落地设计预案；② 每季度运维例会复审 code-exec 需求；
  ③ 一旦出现真实需求，按 §3 预案**直接立项免二次评审**。
- **本期结论**：**不部署**。infra/sandbox/ 维持预案文档态（本文件即预案正文），`sandbox_enabled` 维持 False。

---

## 1. 现状（2026-10-03 代码核对）

| 项 | 实况 | 依据 |
|----|------|------|
| 已注册的 code-exec 工具 | **0 个**——E2 守卫上线以来拦截数为零 | `tools_v2.py`（code-exec 标签注册即拒） |
| 守卫机制 | 已建且生效：带 `code-exec` 标签的工具在 `sandbox_enabled=False` 时拒绝注册 | `tools_v2.py` register()；`config.py` `sandbox_enabled: bool = False` |
| 容器隔离 | **未部署**：infra/sandbox/ 仅有 README | `infra/sandbox/` |
| 资源边界 | 生产 2核2G/50G；沙箱容器常驻预算约 0.5~1G 内存 | `.workbuddy/memory/2026-09-04.md` |

**结论**：为不存在的需求在 2核2G 上建常驻隔离容器是负 ROI——委员会采纳 B 的前提成立。

## 2. 触发条件（满足任一即按 §3 立项）

1. 出现**让 Agent 编写并执行代码**的真实业务需求（数据分析/报表脚本/规则计算表达式）；
2. 引入会生成并执行代码的外部 Skill/MCP 工具；
3. 合规或甲方明确要求具备代码沙箱能力。

## 3. 最小落地设计预案（立项后按此实施，预估 1 后端周 + 0.5 运维周）

### 3.1 执行模型（一次性容器，无常驻守护）

```
ERP backend ── docker API（经 socket 代理，见 3.3）──> 沙箱容器（一次性）
   │                                                     │
   │  payload：{code, timeout_s, input_files[]}          │ 镜像：python:3.12-slim
   │  <── {stdout, stderr, exit_code, artifacts[]}       │ 无网络 / 只读根 / 非 root / 限额
```

- 每次执行**新建容器**，结束即销毁（`--rm`）：无跨次状态泄露；
- 容器内仅挂载一个 tmpfs 工作目录（读写），产物回传后清空。

### 3.2 容器硬限制（全部非协商项）

| 限制 | 值 | 实现旗标 |
|------|----|----------|
| 网络 | **完全禁用** | `--network none` |
| 文件系统 | 只读根 + tmpfs 工作区（64MB） | `--read-only --tmpfs /work:rw,size=64m` |
| 用户 | 非 root | `--user 65534:65534` |
| CPU/内存 | 0.5 核 / 256MB | `--cpus 0.5 --memory 256m` |
| 进程数 | 64 | `--pids-limit 64` |
| 时长 | 30s（可配 ≤120s） | `--stop-timeout` + 应用侧超时强杀 |
| 能力 | 全部丢弃 | `--cap-drop ALL --security-opt no-new-privileges` |

### 3.3 宿主安全（2C2G 特化）

- **不直接挂载 docker.sock**：以最小代理容器（socket-proxy，只暴露
  `POST /containers/create|start|delete` 白名单路由）转发——沙箱内代码
  即使逃逸也拿不到完整 Docker API；
- 镜像内置国内源（构建期固化），执行期无网络无需拉取；
- 沙箱容器按需创建，空闲期零内存占用——2G 机器可承载（峰值 +256MB）。

### 3.4 治理面接入（复用既有协议，无需新机制）

- 工具以 `@erp_tool(tags=["code-exec"], risk_level=WRITE_HIGH, idempotent=True,
  effects_fn=..., impact_fn=...)` 注册——**每次执行强制人审**（write_high）+
  影响面快照 + 全量审计（治理面既有通道原样生效）；
- 执行产物（stdout 截断/artifacts 清单）进审计 details；产物文件留存
  `operation_logs.details`（受 G2.4 全文开关与过期策略约束）；
- G4 回滚账本：代码执行类天然"只读外部世界"，登记后以"产物清理"为补偿。

### 3.5 上线步骤（立项后执行序）

1. `infra/sandbox/` 落地 Dockerfile + socket-proxy compose（profile: sandbox）；
2. 沙箱执行器模块 `app/core/sandbox/executor.py`（容器生命周期 + 超时 + 产物回收）；
3. 首个 code-exec 业务工具按 3.4 协议注册 + drill 登记；
4. `ERP_SANDBOX_ENABLED=true` 开启守卫放行；
5. UAT：越权网络访问被拒 / 资源超限被杀 / 人审卡展示影响面 / 回滚演练。

### 3.6 资源预估

| 项 | 预估 |
|----|------|
| 磁盘（镜像） | python:3.12-slim ≈ 130MB |
| 内存峰值 | +256MB/次（串行执行，无并发池） |
| 首次执行冷启动 | 镜像已预拉，容器创建 <1s |
| 对既有 P95 影响 | 0（独立通道） |

## 4. 季度复审机制（裁决 D5 附加条件）

- 运维例会（每季度）核对 §2 触发条件清单；任一命中 → 按预案立项，免二次评审；
- 复审结论记录于本文件 §5（追加行，不删改历史）。

| 复审日期 | 触发条件核对 | 结论 | 签署 |
|----------|--------------|------|------|
| 2026-10（交付时） | 零命中（零 code-exec 工具/需求） | 维持禁用 | 乙方 |
| （待填） | | | |
