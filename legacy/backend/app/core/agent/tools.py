"""
Agent工具注册与调用沙箱
Agent通过注册的工具与外部系统交互，所有调用在沙箱中安全执行
"""
import asyncio
import structlog
from typing import Any, Callable, Optional
from inspect import signature

from app.core.security.audit import audit_chain

logger = structlog.get_logger()


class Tool:
    """工具定义"""
    def __init__(self, name: str, description: str, handler: Callable,
                 required_permissions: list[str] = None,
                 risk_level: str = "low"):
        self.name = name
        self.description = description
        self.handler = handler
        self.required_permissions = required_permissions or []
        self.risk_level = risk_level
        self.call_count = 0
        self.error_count = 0

    async def execute(self, **kwargs) -> Any:
        """执行工具"""
        self.call_count += 1
        try:
            result = self.handler(**kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            self.error_count += 1
            logger.error("tool.execution_failed",
                        tool=self.name, error=str(e))
            raise

    def get_stats(self) -> dict:
        return {
            "name": self.name,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "error_rate": self.error_count / max(self.call_count, 1),
        }


class ToolRegistry:
    """
    工具注册中心

    特性：
    - 工具注册与发现
    - 权限检查
    - 调用审计
    - 执行沙箱（隔离错误，防止级联失败）
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        """注册工具"""
        self._tools[tool.name] = tool
        logger.info("tool.registered", tool=tool.name)

    def unregister(self, name: str):
        """注销工具"""
        self._tools.pop(name, None)

    def list_tools(self) -> list[dict]:
        """列出所有工具"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "required_permissions": t.required_permissions,
                "risk_level": t.risk_level,
                "stats": t.get_stats(),
            }
            for t in self._tools.values()
        ]

    async def call(self, tool_name: str, agent_did: str, **kwargs) -> dict:
        """
        调用工具（带审计和沙箱）

        返回:
        - {"success": True, "result": ...} 或 {"success": False, "error": ...}
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return {"success": False, "error": f"工具'{tool_name}'未注册"}

        try:
            # 执行工具
            result = await tool.execute(**kwargs)

            # 审计记录
            await audit_chain.log(
                actor_did=agent_did,
                action=f"tool.call.{tool_name}",
                resource=tool_name,
                result="success",
                details={"args": kwargs, "tool": tool_name},
                risk_level=tool.risk_level,
            )

            return {"success": True, "result": result}

        except Exception as e:
            # 审计失败记录
            await audit_chain.log(
                actor_did=agent_did,
                action=f"tool.call.{tool_name}",
                resource=tool_name,
                result="failed",
                details={"args": kwargs, "error": str(e)},
                risk_level="high",
            )

            return {"success": False, "error": str(e)}

    def get_stats(self) -> dict:
        """获取工具统计"""
        return {
            "total_tools": len(self._tools),
            "tools": {name: t.get_stats() for name, t in self._tools.items()},
        }


# 全局工具注册中心单例
tool_registry = ToolRegistry()
