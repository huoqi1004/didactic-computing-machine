"""
版本化与灰度发布系统 - 灰度发布管理器
======================================

管理灰度发布的完整生命周期：启动 → 路由 → 评估 → 全量/中止。

核心能力
--------
- 一致性哈希路由：基于请求ID哈希 + 百分比分配，保证同一请求始终路由到同一版本
- 实时指标采集：记录请求数、成功数、错误数、延迟总和
- 自动评估：检查成功率/错误率/持续时间，输出 pass/fail/continue 决策
- 一键全量：灰度通过后自动切换为 ACTIVE 版本
- 安全中止：灰度失败时自动回退到原 ACTIVE 版本

全局单例：canary_manager
"""
from __future__ import annotations

import hashlib
import structlog
from datetime import datetime, timezone

from app.core.versioning.models import CanaryConfig, VersionStatus
from app.core.versioning.registry import version_registry

logger = structlog.get_logger()


class CanaryManager:
    """灰度发布管理器

    Attributes:
        _active_canaries: version_id -> CanaryConfig（正在运行的灰度）
        _traffic_router: agent_name -> {version_id: percentage}（流量分配表）
        _metrics: version_id -> {requests, successes, errors, latency_sum}
    """

    def __init__(self):
        self._active_canaries: dict[str, CanaryConfig] = {}
        self._traffic_router: dict[str, dict] = {}
        self._metrics: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 灰度启动 / 停止
    # ------------------------------------------------------------------

    async def start_canary(self, version_id: str, config: CanaryConfig) -> CanaryConfig:
        """启动灰度发布

        将目标版本状态切换为 CANARY，并配置流量切分。

        Args:
            version_id: 灰度目标版本ID
            config: 灰度配置

        Returns:
            已启动的 CanaryConfig

        Raises:
            ValueError: 版本不存在或已有灰度在运行
        """
        version = version_registry.get_version(version_id)
        if version is None:
            raise ValueError(f"Version not found: {version_id}")

        if version_id in self._active_canaries:
            raise ValueError(f"Canary already running for version: {version_id}")

        # 初始化灰度配置
        config.version_id = version_id
        config.started_at = datetime.now(timezone.utc)
        config.status = "running"

        # 切换版本状态为 CANARY
        version.status = VersionStatus.CANARY

        # 注册灰度
        self._active_canaries[version_id] = config
        version_registry.set_canary_config(version_id, config)

        # 配置流量路由：灰度版本分配 traffic_percentage，原 ACTIVE 版本分配剩余
        agent_name = version.agent_name
        active_version = version_registry.get_active_version(agent_name)
        active_id = active_version.version_id if active_version else None

        routing: dict = {}
        if active_id and active_id != version_id:
            routing[active_id] = 100 - config.traffic_percentage
        routing[version_id] = config.traffic_percentage
        self._traffic_router[agent_name] = routing

        # 初始化指标
        self._metrics[version_id] = {
            "requests": 0,
            "successes": 0,
            "errors": 0,
            "latency_sum": 0.0,
        }

        logger.info(
            "canary.started",
            version_id=version_id,
            agent_name=agent_name,
            traffic_percentage=config.traffic_percentage,
            routing=routing,
        )
        return config

    async def stop_canary(self, version_id: str, reason: str = "manual") -> bool:
        """停止灰度发布

        Args:
            version_id: 灰度版本ID
            reason: 停止原因

        Returns:
            是否停止成功
        """
        config = self._active_canaries.get(version_id)
        if config is None:
            return False

        version = version_registry.get_version(version_id)
        agent_name = version.agent_name if version else None

        # 清理路由
        if agent_name:
            self._traffic_router.pop(agent_name, None)

        # 移除灰度注册
        self._active_canaries.pop(version_id, None)
        version_registry.remove_canary_config(version_id)

        # 若版本仍是 CANARY 状态，回退为 DRAFT
        if version and version.status == VersionStatus.CANARY:
            version.status = VersionStatus.DRAFT

        logger.info(
            "canary.stopped",
            version_id=version_id,
            reason=reason,
        )
        return True

    # ------------------------------------------------------------------
    # 请求路由
    # ------------------------------------------------------------------

    def route_request(self, agent_name: str, request_context: dict) -> str:
        """路由请求到指定版本

        基于请求ID哈希 + 百分比分配，保证同一请求始终路由到同一版本（一致性哈希）。

        Args:
            agent_name: Agent 名称
            request_context: 请求上下文（含 request_id）

        Returns:
            目标版本ID
        """
        routing = self._traffic_router.get(agent_name)
        if not routing:
            # 无灰度，路由到 ACTIVE 版本
            active = version_registry.get_active_version(agent_name)
            return active.version_id if active else ""

        # 一致性哈希：基于 request_id 计算 0-99 的桶
        request_id = request_context.get("request_id", "")
        if not request_id:
            from uuid import uuid4
            request_id = str(uuid4())

        hash_value = int(hashlib.md5(request_id.encode("utf-8")).hexdigest(), 16)
        bucket = hash_value % 100

        # 按百分比分配版本
        cumulative = 0
        selected_version_id = ""
        for version_id, percentage in routing.items():
            cumulative += percentage
            if bucket < cumulative:
                selected_version_id = version_id
                break

        if not selected_version_id:
            # 兜底：取第一个版本
            selected_version_id = next(iter(routing), "")

        return selected_version_id

    # ------------------------------------------------------------------
    # 指标记录
    # ------------------------------------------------------------------

    def record_result(self, version_id: str, success: bool, latency_ms: float) -> None:
        """记录请求结果

        Args:
            version_id: 处理请求的版本ID
            success: 是否成功
            latency_ms: 响应延迟（毫秒）
        """
        metrics = self._metrics.get(version_id)
        if metrics is None:
            metrics = {
                "requests": 0,
                "successes": 0,
                "errors": 0,
                "latency_sum": 0.0,
            }
            self._metrics[version_id] = metrics

        metrics["requests"] += 1
        if success:
            metrics["successes"] += 1
        else:
            metrics["errors"] += 1
        metrics["latency_sum"] += latency_ms

    # ------------------------------------------------------------------
    # 灰度评估
    # ------------------------------------------------------------------

    async def evaluate_canary(self, version_id: str) -> dict:
        """评估灰度结果

        评估逻辑：
        1. 检查成功率是否 >= success_threshold
        2. 检查错误率是否 <= error_rate_threshold
        3. 灰度持续时间是否足够
        4. 自动决策：pass / fail / continue

        Args:
            version_id: 灰度版本ID

        Returns:
            评估结果字典
        """
        config = self._active_canaries.get(version_id)
        if config is None:
            return {
                "version_id": version_id,
                "decision": "not_running",
                "error": "canary not running",
            }

        metrics = self._metrics.get(version_id, {})
        requests = metrics.get("requests", 0)
        successes = metrics.get("successes", 0)
        errors = metrics.get("errors", 0)
        latency_sum = metrics.get("latency_sum", 0.0)

        # 计算指标
        success_rate = (successes / requests) if requests > 0 else 0.0
        error_rate = (errors / requests) if requests > 0 else 0.0
        avg_latency_ms = (latency_sum / requests) if requests > 0 else 0.0

        # 检查持续时间
        now = datetime.now(timezone.utc)
        elapsed_seconds = (
            (now - config.started_at).total_seconds()
            if config.started_at else 0
        )
        duration_sufficient = elapsed_seconds >= config.duration_seconds

        # 决策逻辑
        decision = "continue"
        reasons = []

        # 样本不足时继续观察
        if requests < 10:
            decision = "continue"
            reasons.append(f"insufficient_samples ({requests}/10)")
        else:
            # 错误率超标 → 失败
            if error_rate > config.error_rate_threshold:
                decision = "fail"
                reasons.append(
                    f"error_rate {error_rate:.2%} > threshold {config.error_rate_threshold:.2%}"
                )
            # 成功率达标且错误率达标 → 可能通过
            elif success_rate >= config.success_threshold:
                if duration_sufficient:
                    decision = "pass"
                    reasons.append(
                        f"success_rate {success_rate:.2%} >= threshold "
                        f"{config.success_threshold:.2%}"
                    )
                    reasons.append(
                        f"duration {elapsed_seconds:.0f}s >= "
                        f"{config.duration_seconds}s"
                    )
                else:
                    decision = "continue"
                    reasons.append(
                        f"duration {elapsed_seconds:.0f}s < "
                        f"{config.duration_seconds}s"
                    )
            else:
                decision = "continue"
                reasons.append(
                    f"success_rate {success_rate:.2%} < threshold "
                    f"{config.success_threshold:.2%}"
                )

        result = {
            "version_id": version_id,
            "decision": decision,
            "metrics": {
                "requests": requests,
                "successes": successes,
                "errors": errors,
                "success_rate": success_rate,
                "error_rate": error_rate,
                "avg_latency_ms": avg_latency_ms,
            },
            "thresholds": {
                "success_threshold": config.success_threshold,
                "error_rate_threshold": config.error_rate_threshold,
                "duration_seconds": config.duration_seconds,
            },
            "elapsed_seconds": elapsed_seconds,
            "duration_sufficient": duration_sufficient,
            "reasons": reasons,
        }

        logger.info(
            "canary.evaluated",
            version_id=version_id,
            decision=decision,
            success_rate=success_rate,
            error_rate=error_rate,
        )
        return result

    # ------------------------------------------------------------------
    # 全量切换 / 中止
    # ------------------------------------------------------------------

    async def promote_canary(self, version_id: str) -> bool:
        """灰度通过，全量切换

        将灰度版本升级为 ACTIVE 版本，停止灰度。

        Args:
            version_id: 灰度版本ID

        Returns:
            是否切换成功
        """
        config = self._active_canaries.get(version_id)
        if config is None:
            return False

        # 先停止灰度（清理路由与指标）
        await self.stop_canary(version_id, reason="promoted")

        # 激活版本
        success = version_registry.activate_version(version_id)
        if success:
            config.status = "passed"
            logger.info("canary.promoted", version_id=version_id)
        return success

    async def abort_canary(self, version_id: str, reason: str = "manual") -> bool:
        """中止灰度，回退

        停止灰度并将版本回退到 DRAFT 状态，恢复原 ACTIVE 版本全量流量。

        Args:
            version_id: 灰度版本ID
            reason: 中止原因

        Returns:
            是否中止成功
        """
        config = self._active_canaries.get(version_id)
        if config is None:
            return False

        await self.stop_canary(version_id, reason=f"aborted: {reason}")
        config.status = "aborted"

        logger.info("canary.aborted", version_id=version_id, reason=reason)
        return True

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_canary_status(self, version_id: str) -> dict:
        """获取灰度状态"""
        config = self._active_canaries.get(version_id)
        if config is None:
            # 检查是否曾经运行过（从 registry 获取历史配置）
            historical = version_registry.get_canary_config(version_id)
            if historical:
                return {
                    "version_id": version_id,
                    "status": historical.status,
                    "config": historical.to_dict(),
                    "metrics": self._metrics.get(version_id, {}),
                    "active": False,
                }
            return {
                "version_id": version_id,
                "status": "not_running",
                "active": False,
            }

        version = version_registry.get_version(version_id)
        agent_name = version.agent_name if version else None
        routing = self._traffic_router.get(agent_name, {}) if agent_name else {}

        return {
            "version_id": version_id,
            "status": config.status,
            "active": True,
            "config": config.to_dict(),
            "metrics": self._metrics.get(version_id, {}),
            "routing": routing,
        }

    def get_metrics(self, version_id: str) -> dict:
        """获取灰度指标"""
        return self._metrics.get(version_id, {
            "requests": 0,
            "successes": 0,
            "errors": 0,
            "latency_sum": 0.0,
        })

    def clear(self) -> None:
        """清空所有灰度状态（主要用于测试）"""
        self._active_canaries.clear()
        self._traffic_router.clear()
        self._metrics.clear()


# 全局单例
canary_manager = CanaryManager()
