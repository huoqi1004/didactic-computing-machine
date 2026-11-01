"""
代码执行沙箱执行器（D5 评估预案 §3 落地）
==========================================

在受限容器（compose profile: sandbox 的 erp-sandbox，硬限制见
docker-compose.yml：无网络/只读根/非root/512MB/0.5核/pids 64）内
一次性执行用户代码，回传 stdout/stderr/exit_code。

三重闸门（铁律 06：安全机制拉满，任一不满足即拒绝）：

1. ``settings.sandbox_enabled`` 显式开启（默认 False）；
2. 容器运行时可达（docker exec 探测，超时 fail-closed）；
3. 代码体积/执行时长硬上限。

设计为"库"而非服务：调用方（未来 code-exec 工具，需 write_high 注册）
自行决定审计与审批——本模块不做治理决策。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.core.config import settings

logger = structlog.get_logger()

_SANDBOX_CONTAINER = "erp-sandbox"
_MAX_CODE_CHARS = 20000
_MAX_TIMEOUT_S = 30


@dataclass
class SandboxResult:
    """沙箱执行结果"""
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


class SandboxExecutor:
    """受限容器代码执行器（默认禁用；docker exec 通道）。"""

    def __init__(self, container: str = _SANDBOX_CONTAINER):
        self.container = container

    async def run(self, code: str, timeout_s: int = 10) -> SandboxResult:
        """在沙箱容器内执行 Python 代码（一次性进程，结果回传）。"""
        started = time.monotonic()

        # 闸门 1：总开关（默认禁用，E2 守卫语义延续）
        if not settings.sandbox_enabled:
            return SandboxResult(ok=False, error="沙箱未启用（ERP_SANDBOX_ENABLED=false）")

        # 闸门 2：体积硬上限
        if not code or not code.strip():
            return SandboxResult(ok=False, error="空代码")
        if len(code) > _MAX_CODE_CHARS:
            return SandboxResult(ok=False, error=f"代码超限（{len(code)} > {_MAX_CODE_CHARS} 字符）")
        timeout_s = min(max(timeout_s, 1), _MAX_TIMEOUT_S)

        # 闸门 3：容器通道（docker exec python - —— stdin 传码，超时强杀）
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", self.container, "python", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(code.encode()), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return SandboxResult(ok=False, error=f"执行超时（>{timeout_s}s）",
                                 duration_ms=(time.monotonic() - started) * 1000)

        duration = (time.monotonic() - started) * 1000
        result = SandboxResult(
            ok=proc.returncode == 0,
            stdout=stdout.decode(errors="replace")[:8000],
            stderr=stderr.decode(errors="replace")[:8000],
            exit_code=proc.returncode,
            duration_ms=round(duration, 2),
            meta={"container": self.container, "timeout_s": timeout_s},
        )
        logger.info("sandbox.executed", ok=result.ok, exit_code=result.exit_code,
                    duration_ms=result.duration_ms, chars=len(code))
        return result


# 进程内单例
sandbox_executor = SandboxExecutor()
