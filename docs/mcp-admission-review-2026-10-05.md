# MCP 准入审查材料（工作包二 · 提交委员会备案）

- 日期：2026-10-05
- 关联：`docs/plan-supervisor-skills-streaming-2026-10-04.md` 工作包二 / 裁决 D-B / 实施令第三批前置条件
- 审查对象：① 社区 `office-word-mcp-server`（首批拟准入）② 自研「ERP 只读查询」治理面工具（D-B 指定的替代路径）
- 审查依据：知识库「第三方准入 6 检查」——来源可信 / 权限最小化 / 描述与行为一致 / 版本锁定 / 禁用开关 / 调用审计

---

## 0. 结论摘要

| 对象 | 结论 | 决定性理由 |
|------|------|-----------|
| `office-word-mcp-server`（GongRzhe） | **不予准入**（本批次） | 上游仓库已 archived（CVE 无修复通道）+ 54 工具无条件注册无服务端禁用开关 + 任意路径读写无归一化 + 工具可触发外部进程；桥接层又无鉴权头/超时/版本锁定字段，六检查中 4 项无法闭合 |
| 自研 `erp_readonly_query` + `document_generate_docx` / `document_generate_xlsx` | **已落地并纳入治理面** | 零新增攻击面（不出网、不起进程、产物只进受控目录），表白名单 + 按表 RBAC 二次鉴权 + 写工具补偿幂等，调用全量留痕 |

实施令要求的"MCP 经准入审查 + 只读治理"按 **自研只读工具替代第三方 server** 的路径达成；第三方 server 的引入决定权交回委员会（见 §5 待裁决项）。

## 1. 取证通道与局限（先声明，避免证据被当作断言使用）

本机 `github.com` 的 HTML 页面不可达，取证走 **`api.github.com` / `codeload.github.com` / `pypi.org` / `pypistats.org` / `registry.npmjs.org`**（均返回 200）。源码证据为下载 `refs/heads/main` 归档后本地 grep，每条代码引用另用 `contents/<path>?ref=a3bbbb6d` 逐一验证存在（HTTP 200）。

**未核实项**（不作为结论依据）：依赖 CVE/高危漏洞扫描；是否存在官方发布镜像（ghcr 匿名 401、hub.docker.com 网络不可达）；PyPI sdist 与 tag `v1.1.11` 的逐文件一致性。

## 2. `office-word-mcp-server` 六检查逐项

### 2.1 来源可信 —— 部分通过
- 唯一有实质量级的仓库：2108 stars / 289 forks；同名精确搜索 8 个仓库中其余 7 个为 0 star 派生/镜像。
- **`archived=true`**，仓库只读，2025-12-31 末次提交后停止接受 PR；README **无** archived/deprecated 声明（grep `archiv|deprecat` 0 命中）——使用者极易误判为仍在维护。
- 维护活跃度：近 90 天零提交；watchers(subscribers)=1；开放 issue 53（另 13 个 PR，合计 66）；16 名贡献者中 GongRzhe 占 31/67 commits（46%），实质单主维护（bus factor=1）。
- 许可证：MIT（LICENSE 1064 B，Copyright (c) 2025 GongRzhe；PyPI `info.license` 同文）。**这一项通过。**

### 2.2 权限最小化 —— **不通过**
- 文件读写**无路径校验、无基目录白名单**：`utils/file_utils.py`（全文 85 行）仅有 `check_file_writeable()`（`os.access` / `open(...,'a')`）与 `ensure_docx_extension()`（只追加后缀，不做 basename）；全库 grep `realpath|normpath|allowlist|sanitize|basename` + `startswith` **无任何目录归一化**；`os.getenv` 只读 MCP_TRANSPORT/HOST/PORT/PATH/SSE_PATH，**不存在输出目录限制项**。
  - 后果：`create_document(filename)` 直接用调用方路径；`list_available_documents(directory=".")` 接受任意目录；`add_picture` 用 `os.path.abspath(image_path)` 读任意文件；`create_document_copy()` 用 `shutil.copy2(source_path, dest_path)` 原样拷贝。任意路径读/写与 `../` 穿越均无阻断。
- 可执行外部命令：MCP 工具 `convert_to_pdf` 在 `tools/extended_document_tools.py:132` `subprocess.run(cmd, capture_output=True, timeout=60)` 调 LibreOffice/soffice（L106 还 `os.makedirs(output_dir, exist_ok=True)`），Windows 分支经 `docx2pdf` 调起 MS Word COM。**模型可触发**。
- 文档解析超出必要范围：`core/footnotes.py` 用 `zipfile.ZipFile` + `lxml.etree.fromstring` 直接读写 OOXML part；工具 `get_document_xml` 原样导出整篇文档 XML——内容外泄面与解析复杂度均高于 python-docx 必要路径。

### 2.3 描述与行为一致性 —— **不通过（声明缺失）**
- README **无工具清单表**，仅示例提示语；实际 `register_tools()` **无条件注册 54 个 `@mcp.tool`**（`word_document_server/main.py` L95–L682），无 resource/prompt 导出。也就是说"能力面"只能读源码才知道，声明与行为不同源。
- 网络出口这一项是**正向**的：全库 `*.py` grep `requests|httpx|urllib|socket|urlopen|webhook|telemetry|analytics|posthog|sentry|aiohttp` 仅 1 命中（`setup_mcp.py:403` 把含 `httpx` 的字符串写进 requirements 文本），运行时无出网/遥测调用，无 `eval/exec/pickle.loads/yaml.load/os.system`。
- 但 HTTP/SSE 模式下 server 自身监听 **`0.0.0.0:8000`（默认）+ `path=/mcp` + 无鉴权中间件**（`main.py` L38–L60）。一旦被桥接层暴露，即等于一个无鉴权的文档写入端点。

### 2.4 版本锁定 —— 上游可锁，**桥接层不可锁**
- 上游具备锁定坐标：git tag `v1.1.11`、PyPI `office-word-mcp-server==1.1.11`（2025-12-31T13:25:20Z 上传，累计 14 个版本，首版 2025-03-25）、仓库 `uv.lock`（85820 B）。
- 供应链信号偏弱：`publish.yml` 由 `release:published` 触发，用 `TWINE_USERNAME=__token__` + 长期 `PYPI_API_TOKEN` 上传，**非 OIDC trusted publishing**；1.1.11 的 wheel/sdist `has_sig=False`。依赖声明不可重现：`requirements.txt`（5 行）**零 pin**，`pyproject.toml` 仅下界（`fastmcp>=2.8.1` 等），经 pip/uvx 安装出的环境与 `uv.lock` 不一致。
- **本仓事实**：`mcp_servers` 表（`app/db/models.py:1817-1831`）只有 `name/url/description/last_probe/created_by/updated_by/时间戳`——**没有 transport、command、api_key、版本/摘要锁定列**；`external_tools.py:47/162` 的 `streamable_http_client(url)` **不携带任何鉴权头**；整条链路**无 timeout / retry / 熔断**。故"版本锁定"这一检查在当前桥接层无法落实（属桥接层约束，不是上游缺失——此处对早期表述作事实更正）。

### 2.5 禁用开关 —— **不通过**
- 服务端无工具级开关：`register_tools()` 全无条件注册，代码内无 env 白名单、无 `enabled=False`。平台侧唯一可用的过滤元数据是 `ToolAnnotations` 的 `destructiveHint=True`（L98/L108/L268/L278/L494）与 `readOnlyHint=True`（L118-L158），粒度是"提示"而非"禁用"。
- 平台侧现有收口：`MAX_TOOLS_PER_SERVER=32`（单 server 暴露上限）+ 管理端工具勾选（`agent_customizations` type="mcp" 的已批准白名单）。**但勾选只能选"接入哪些"，无法约束"该工具会写任意路径"**——这正是 2.2 的问题所在。

### 2.6 调用审计 —— 通过（平台侧具备）
- 外部工具注册名 `external_{server}_{tool}`、风险定级 READONLY、`agent_domain="external"`（`external_tools.py:143/179/181`），调用一律经 `AgentGovernor`：RBAC `check_permission` → schema 校验 → 执行 → 审计落链（`governor.py:116-128`）。
- SSRF 三关卡已具备：静态规则 `url_guard.check_url_static` → DNS 解析后二次校验 `resolve_public` → 每次出站重过静态规则（`external_tools.py:155-160`）。
- **一处治理缺口**：`resource="external"` 不在 RBAC `RESOURCES`（`seed.py:81-121`）里，因此除拥有通配权限的角色外无人可调用外部工具。本批已为平台级能力新增 `platform` 资源（见 §3），`external` 是否同样入种子，列为待裁决（§5）。

## 3. 自研替代路径（D-B 指定，本批已落地并实测）

| 工具 | 风险/权限 | 治理约束（实测） |
|------|-----------|------------------|
| `erp_readonly_query` | readonly / `platform:read` | 表白名单 14 项（逐项对应 `app/db/models.py` 的 `__tablename__`，测试断言防漂移）+ SELECT-only 与禁止 token 双重校验 + 强制 LIMIT（默认 200，上限 1000）+ **本批新增：按表二次鉴权 `<域>:read`**（否则 `platform:read` 会等于能读 `payroll_records`） |
| `document_generate_docx` | write_low / `document:create` | 复用既有 python-docx 生成器；产物只进受控目录（文件名白名单净化 + `basename` 剥离）；**D-C 页眉标注由治理面强制注入，调用方自带标注一律丢弃**；补偿=删除产物，已不存在则 no-op |
| `document_generate_xlsx` | write_low / `document:create` | 仅放开 `basic` 模板；行数上限 5000；xlsx 无页眉机制，标注落 `notes` 行；补偿同上 |

实测证据（本批离线冒烟，非估算）：
- 三工具注册成功：`platform` 域 3 个 spec，风险/权限/`idempotent`/`compensation`/`effects_fn` 齐备；
- docx 生成 37674 B、xlsx 5356 B，落 `backend/data/documents/`；文件页眉实读为 `AI 生成草稿 · 生成时间 2026-10-05T04:22:41+08:00 · trace_id NA · 未经审批，不得作为正式交付物`，调用方伪造的"正式交付物"标注未出现；
- 补偿重放：首轮 `{"removed": true}`，次轮 `{"removed": false, "reason": "产物已不存在（补偿幂等：no-op）"}`；
- 出厂 Skill 目录 12 项、`invalid=0`（此前 `docx_report`/`xlsx_report` 引用的工具名全仓 0 注册点，现补齐）。

## 4. 与实施令验收行的对应

| 验收项 | 落点 | 证据 |
|--------|------|------|
| Skill 热加载生效 | 目录指纹（路径+文件名+纳秒 mtime）触发重扫 | `tests/test_skills.py::test_skill_domain_filter_and_hot_reload` |
| 管理端启停 | `agent_customizations` 的 `skill:{name}` 开关；停用即运行时不可见 | `tests/test_skill_versions_f44.py`（启停/版本流）+ 本批新增 `test_resolve_activatable_skill_respects_disable` |
| MCP 经准入审查 | 本文件（§2 六检查逐项 + §1 取证通道与未核实项） | 提交委员会备案 |
| 只读治理 | 第三方 server 不接入；自研只读查询 + 按表 RBAC | `test_readonly_query_denied_without_domain_permission` |
| 审计留痕 | 治理面逐调用审计 + **本批新增技能启停留痕**（`skill.activated` / `skill.activation_denied`） | `orchestration_service._audit_skill_activation` |

## 5. 待委员会裁决（不在本批自行决定）

1. **是否为外部 MCP 新增 stdio transport / 允许在本机拉起第三方 server 进程**。现状仅 streamable-http、无鉴权头、无超时——若不改桥接层，任何第三方 server 都只能以"无鉴权内网 HTTP 端点"形态存在。
2. **`external` 是否入 RBAC `RESOURCES`**。不入则外部工具实际只有通配权限者可调用；入了需要同时定义敏感度（建议只放开 `read`）。
3. **外部 MCP 的最小治理前置**：建议列为准入必要条件——`mcp_servers` 增列 `transport/api_key_ref/version_pin`，调用侧加 timeout+熔断，出站侧按 `ToolAnnotations` 的 `readOnlyHint/destructiveHint` 强制分级。上述三项在实现前，任何"可准入"结论都不成立。
4. `fetch` / `filesystem` 两个第二批候选是否随 §1 一并冻结（它们的默认能力同样是任意路径/任意主机）。

## 6. 复核触发条件

- 委员会批准 §5-1（新增 transport）→ 本文件 §2.4/§2.5 需按新链路重审；
- 出现同类**活跃维护**替代品（如 `ykarapazar/word-mcp-server` 229★、`trisdn/mcp-server-word` 2★，本批仅记录存在性，未做行为审查）→ 需按同一六检查重跑；
- 依赖 CVE 扫描具备离线数据源时 → 补齐 §1 未核实项。
