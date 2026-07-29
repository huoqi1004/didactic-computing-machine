"""
数据库持久化层
================

本包提供 Agent-Native ERP 系统的持久化能力：

- ``models``  : SQLAlchemy 2.0 ORM 模型（事件溯源、快照、审计、凭证、库存）
- ``database``: 异步引擎、会话工厂与 FastAPI 依赖注入
- ``cache``   : Redis 缓存层（带内存降级）

设计原则
--------
1. **默认零依赖**：未安装 asyncpg / redis 时仍可导入本包，
   仅在实际切换到 postgres / redis 后端时才尝试连接。
2. **内存降级**：所有后端均提供 memory 实现，保证开发与测试零依赖启动。
3. **异步优先**：所有数据库操作使用 async/await。
"""
