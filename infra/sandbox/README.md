# 代码执行沙箱（Phase E / E2，默认关闭）

用途：承载"任意代码执行"类 Agent 工具（数据分析/报表计算），与主服务隔离。

安全基线（容器隔离四件套）：
- `--network none`：无网络访问
- 只读根文件系统（`read_only: true`），仅 /tmp 可写（tmpfs 64m）
- 非 root 运行 + 内存/CPU 限额（512M / 0.5 CPU）+ 墙钟超时 30s
- 输出白名单：仅 stdout JSON 回传主服务

启用步骤：
1. `ERP_SANDBOX_ENABLED=true`（后端）
2. `docker compose --profile sandbox up -d erp-sandbox`
3. 沙箱内注册代码执行工具（tag 含 code-exec），主服务经 HTTP 调用沙箱执行端点

**默认关闭**：当前无代码执行类工具注册，所有数据计算走预注册的安全函数。
