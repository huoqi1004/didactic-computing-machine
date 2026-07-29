"""
系统监控模块
CIO管理员系统的核心监控能力，提供系统资源、API、Agent、事件总线、数据库的全面监控

特性：
- 系统资源监控（CPU、内存、磁盘、网络）
- API性能监控（响应时间、请求量、错误率）
- Agent运行状态监控（通过orchestrator获取）
- 事件总线健康度监控
- 数据库连接池状态监控
- 实时指标与历史指标查询
- 阈值告警（CPU>80%, Memory>85%, Disk>90%, ErrorRate>5%）
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

# psutil 为可选依赖，未安装时降级为零值指标
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    _PSUTIL_AVAILABLE = False

logger = structlog.get_logger()


class SystemMonitor:
    """
    系统监控器

    职责：
    - 采集系统资源指标（CPU、内存、磁盘、网络）
    - 采集应用指标（API响应、请求量、错误率）
    - 采集基础设施指标（事件总线、数据库连接池、Agent状态）
    - 历史指标存储与查询
    - 阈值告警生成与管理
    """

    # 阈值配置
    THRESHOLDS = {
        "cpu_percent": 80.0,
        "memory_percent": 85.0,
        "disk_percent": 90.0,
        "error_rate": 5.0,
        "api_response_time_ms": 2000.0,
        "db_pool_usage": 90.0,
    }

    def __init__(self):
        # 历史指标存储：{timestamp: metrics_dict}
        self._metrics_history: list[dict] = []
        # 告警列表
        self._alerts: list[dict] = []
        # API指标统计
        self._api_stats: dict[str, dict] = {
            "total_requests": 0,
            "total_errors": 0,
            "response_times": [],
            "endpoint_stats": {},
        }
        # 最大历史保留条数
        self._max_history = 10000
        self._seed_data()

    def _seed_data(self) -> None:
        """加载示例数据"""
        now = datetime.now(timezone.utc)
        # 预置若干历史指标样本
        for i in range(5):
            ts = datetime.now(timezone.utc)
            # 时间戳往过去偏移
            from datetime import timedelta
            ts = ts - timedelta(minutes=(5 - i) * 5)
            self._metrics_history.append({
                "timestamp": ts.isoformat(),
                "cpu_percent": 45.0 + i * 3,
                "memory_percent": 60.0 + i * 2,
                "disk_percent": 72.0 + i * 0.5,
                "network_bytes_sent": 1024 * 1024 * (10 + i),
                "network_bytes_recv": 1024 * 1024 * (50 + i * 5),
                "api_response_time_ms": 120.0 + i * 10,
                "api_requests_per_min": 350 + i * 20,
                "error_rate": 0.5 + i * 0.2,
            })
        # 预置告警样本
        self._alerts.append({
            "alert_id": str(uuid4()),
            "timestamp": now.isoformat(),
            "severity": "warning",
            "category": "resource",
            "metric": "disk_percent",
            "current_value": 91.2,
            "threshold": 90.0,
            "message": "磁盘使用率超过阈值: 91.2% > 90%",
            "status": "active",
        })
        self._alerts.append({
            "alert_id": str(uuid4()),
            "timestamp": now.isoformat(),
            "severity": "critical",
            "category": "api",
            "metric": "error_rate",
            "current_value": 6.8,
            "threshold": 5.0,
            "message": "API错误率超过阈值: 6.8% > 5%",
            "status": "active",
        })
        logger.info("monitor.seeded",
                    history_count=len(self._metrics_history),
                    alert_count=len(self._alerts))

    def _collect_system_resources(self) -> dict:
        """采集系统资源指标（psutil 未安装时返回零值）"""
        if not _PSUTIL_AVAILABLE:
            logger.debug("monitor.psutil_unavailable")
            return {
                "cpu_percent": 0.0,
                "cpu_count": 0,
                "memory_total": 0,
                "memory_used": 0,
                "memory_percent": 0.0,
                "disk_total": 0,
                "disk_used": 0,
                "disk_percent": 0.0,
                "network_bytes_sent": 0,
                "network_bytes_recv": 0,
            }
        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net = psutil.net_io_counters()
            return {
                "cpu_percent": cpu_percent,
                "cpu_count": psutil.cpu_count(logical=True),
                "memory_total": memory.total,
                "memory_used": memory.used,
                "memory_percent": memory.percent,
                "disk_total": disk.total,
                "disk_used": disk.used,
                "disk_percent": disk.percent,
                "network_bytes_sent": net.bytes_sent,
                "network_bytes_recv": net.bytes_recv,
            }
        except Exception as e:
            logger.error("monitor.collect_resources_failed", error=str(e))
            return {
                "cpu_percent": 0.0,
                "cpu_count": 0,
                "memory_total": 0,
                "memory_used": 0,
                "memory_percent": 0.0,
                "disk_total": 0,
                "disk_used": 0,
                "disk_percent": 0.0,
                "network_bytes_sent": 0,
                "network_bytes_recv": 0,
            }

    def _collect_api_metrics(self) -> dict:
        """采集API指标"""
        total = self._api_stats["total_requests"]
        errors = self._api_stats["total_errors"]
        error_rate = (errors / total * 100) if total > 0 else 0.0
        response_times = self._api_stats["response_times"]
        avg_response = (
            sum(response_times) / len(response_times) if response_times else 0.0
        )
        return {
            "api_total_requests": total,
            "api_total_errors": errors,
            "api_error_rate": round(error_rate, 2),
            "api_avg_response_time_ms": round(avg_response, 2),
            "api_requests_per_min": len(response_times),
        }

    def _collect_agent_status(self) -> dict:
        """采集Agent运行状态（通过orchestrator获取）"""
        try:
            from app.core.agent.orchestrator import orchestrator
            stats = orchestrator.get_stats()
            return {
                "agent_total": stats.get("total_agents", 0),
                "agent_state_distribution": stats.get("state_distribution", {}),
                "agent_active_goals": stats.get("active_goals", 0),
                "agent_pending_consensus": stats.get("pending_consensus", 0),
            }
        except Exception as e:
            logger.warning("monitor.collect_agent_status_failed", error=str(e))
            return {
                "agent_total": 0,
                "agent_state_distribution": {},
                "agent_active_goals": 0,
                "agent_pending_consensus": 0,
            }

    def _collect_event_bus_status(self) -> dict:
        """采集事件总线健康度"""
        try:
            from app.core.event.bus import event_bus
            stats = event_bus.get_stats()
            return {
                "event_bus_published": stats.get("published", 0),
                "event_bus_delivered": stats.get("delivered", 0),
                "event_bus_failed": stats.get("failed", 0),
                "event_bus_queue_size": stats.get("queue_size", 0),
                "event_bus_dead_letters": stats.get("dead_letters", 0),
                "event_bus_subscriptions": stats.get("subscriptions", 0),
                "event_bus_healthy": stats.get("dead_letters", 0) < 100,
            }
        except Exception as e:
            logger.warning("monitor.collect_event_bus_failed", error=str(e))
            return {
                "event_bus_published": 0,
                "event_bus_delivered": 0,
                "event_bus_failed": 0,
                "event_bus_queue_size": 0,
                "event_bus_dead_letters": 0,
                "event_bus_subscriptions": 0,
                "event_bus_healthy": True,
            }

    def _collect_db_pool_status(self) -> dict:
        """采集数据库连接池状态"""
        try:
            from app.core import database as db_module
            engine = getattr(db_module, "_engine", None)
            if engine is None:
                return {
                    "db_pool_size": 0,
                    "db_pool_checked_out": 0,
                    "db_pool_overflow": 0,
                    "db_pool_usage_percent": 0.0,
                    "db_pool_healthy": True,
                }
            pool = engine.pool
            checked_out = pool.checkedout() if hasattr(pool, "checkedout") else 0
            size = pool.size() if hasattr(pool, "size") else 0
            overflow = pool.overflow() if hasattr(pool, "overflow") else 0
            from app.core.config import settings
            max_conn = settings.db_pool_size + settings.db_max_overflow
            usage = (checked_out / max_conn * 100) if max_conn > 0 else 0.0
            return {
                "db_pool_size": size,
                "db_pool_checked_out": checked_out,
                "db_pool_overflow": overflow,
                "db_pool_usage_percent": round(usage, 2),
                "db_pool_healthy": usage < self.THRESHOLDS["db_pool_usage"],
            }
        except Exception as e:
            logger.warning("monitor.collect_db_pool_failed", error=str(e))
            return {
                "db_pool_size": 0,
                "db_pool_checked_out": 0,
                "db_pool_overflow": 0,
                "db_pool_usage_percent": 0.0,
                "db_pool_healthy": True,
            }

    def _check_thresholds(self, metrics: dict) -> list[dict]:
        """检查阈值并生成告警"""
        new_alerts = []
        now = datetime.now(timezone.utc)
        checks = [
            ("cpu_percent", "resource", "CPU使用率"),
            ("memory_percent", "resource", "内存使用率"),
            ("disk_percent", "resource", "磁盘使用率"),
            ("api_error_rate", "api", "API错误率"),
            ("api_avg_response_time_ms", "api", "API平均响应时间"),
            ("db_pool_usage_percent", "database", "数据库连接池使用率"),
        ]
        for metric_key, category, label in checks:
            threshold_key = metric_key
            if metric_key == "api_error_rate":
                threshold_key = "error_rate"
            elif metric_key == "api_avg_response_time_ms":
                threshold_key = "api_response_time_ms"
            threshold = self.THRESHOLDS.get(threshold_key)
            if threshold is None:
                continue
            current = metrics.get(metric_key, 0.0)
            if current > threshold:
                severity = "critical" if current > threshold * 1.2 else "warning"
                alert = {
                    "alert_id": str(uuid4()),
                    "timestamp": now.isoformat(),
                    "severity": severity,
                    "category": category,
                    "metric": metric_key,
                    "current_value": current,
                    "threshold": threshold,
                    "message": f"{label}超过阈值: {current} > {threshold}",
                    "status": "active",
                }
                new_alerts.append(alert)
                self._alerts.append(alert)
        if new_alerts:
            logger.warning("monitor.alerts_generated", count=len(new_alerts))
        return new_alerts

    def get_realtime_metrics(self) -> dict:
        """
        获取实时监控指标

        返回包含以下维度的完整指标快照：
        - 系统资源（CPU、内存、磁盘、网络）
        - API性能（请求量、错误率、响应时间）
        - Agent运行状态
        - 事件总线健康度
        - 数据库连接池状态

        Returns:
            dict: 实时指标字典
        """
        now = datetime.now(timezone.utc)
        resources = self._collect_system_resources()
        api_metrics = self._collect_api_metrics()
        agent_status = self._collect_agent_status()
        event_bus_status = self._collect_event_bus_status()
        db_pool_status = self._collect_db_pool_status()

        metrics = {
            "timestamp": now.isoformat(),
            **resources,
            **api_metrics,
            **agent_status,
            **event_bus_status,
            **db_pool_status,
        }

        # 阈值检查并生成告警
        self._check_thresholds(metrics)

        # 存入历史
        self._metrics_history.append(metrics)
        if len(self._metrics_history) > self._max_history:
            self._metrics_history = self._metrics_history[-self._max_history:]

        logger.info("monitor.realtime_collected",
                    cpu=metrics["cpu_percent"],
                    memory=metrics["memory_percent"],
                    disk=metrics["disk_percent"])
        return metrics

    def get_history_metrics(
        self,
        start_time: str,
        end_time: str,
        metric_name: Optional[str] = None,
    ) -> list[dict]:
        """
        查询历史监控指标

        参数:
            start_time: 起始时间（ISO 8601格式字符串）
            end_time: 结束时间（ISO 8601格式字符串）
            metric_name: 可选，指定单个指标名称进行过滤

        Returns:
            list[dict]: 历史指标列表，每条包含时间戳和指标值
        """
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError as e:
            logger.error("monitor.invalid_time_format", error=str(e))
            return []

        results = []
        for record in self._metrics_history:
            try:
                record_dt = datetime.fromisoformat(
                    record["timestamp"].replace("Z", "+00:00")
                )
            except (ValueError, KeyError):
                continue
            if start_dt <= record_dt <= end_dt:
                if metric_name:
                    if metric_name in record:
                        results.append({
                            "timestamp": record["timestamp"],
                            "metric": metric_name,
                            "value": record[metric_name],
                        })
                else:
                    results.append(record)

        logger.info("monitor.history_queried",
                    start=start_time, end=end_time,
                    metric=metric_name, count=len(results))
        return results

    def get_alerts(self, severity: Optional[str] = None) -> list[dict]:
        """
        获取告警列表

        参数:
            severity: 可选，按严重级别过滤（"warning" / "critical"）

        Returns:
            list[dict]: 告警列表
        """
        if severity:
            alerts = [a for a in self._alerts if a["severity"] == severity]
        else:
            alerts = list(self._alerts)
        logger.info("monitor.alerts_queried",
                    severity=severity, count=len(alerts))
        return alerts

    def get_service_status(self) -> dict:
        """
        获取所有服务组件的运行状态概览

        Returns:
            dict: 各服务组件状态（healthy/degraded/unhealthy）
        """
        metrics = self._collect_system_resources()
        api_metrics = self._collect_api_metrics()
        agent_status = self._collect_agent_status()
        event_bus_status = self._collect_event_bus_status()
        db_pool_status = self._collect_db_pool_status()

        def _status_from(value: float, threshold: float) -> str:
            if value > threshold * 1.2:
                return "unhealthy"
            elif value > threshold:
                return "degraded"
            return "healthy"

        services = {
            "system_resources": {
                "status": _status_from(
                    metrics["cpu_percent"], self.THRESHOLDS["cpu_percent"]
                ),
                "cpu_percent": metrics["cpu_percent"],
                "memory_percent": metrics["memory_percent"],
                "disk_percent": metrics["disk_percent"],
            },
            "api_gateway": {
                "status": _status_from(
                    api_metrics["api_error_rate"], self.THRESHOLDS["error_rate"]
                ),
                "error_rate": api_metrics["api_error_rate"],
                "avg_response_time_ms": api_metrics["api_avg_response_time_ms"],
                "total_requests": api_metrics["api_total_requests"],
            },
            "agent_runtime": {
                "status": "healthy" if agent_status["agent_total"] > 0 else "degraded",
                "total_agents": agent_status["agent_total"],
                "state_distribution": agent_status["agent_state_distribution"],
            },
            "event_bus": {
                "status": "healthy" if event_bus_status["event_bus_healthy"] else "unhealthy",
                "queue_size": event_bus_status["event_bus_queue_size"],
                "dead_letters": event_bus_status["event_bus_dead_letters"],
            },
            "database": {
                "status": "healthy" if db_pool_status["db_pool_healthy"] else "degraded",
                "pool_usage_percent": db_pool_status["db_pool_usage_percent"],
                "checked_out": db_pool_status["db_pool_checked_out"],
            },
        }

        # 整体状态
        statuses = [s["status"] for s in services.values()]
        if "unhealthy" in statuses:
            overall = "unhealthy"
        elif "degraded" in statuses:
            overall = "degraded"
        else:
            overall = "healthy"

        result = {
            "overall_status": overall,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services": services,
            "active_alerts": sum(
                1 for a in self._alerts if a["status"] == "active"
            ),
        }
        logger.info("monitor.service_status", overall=overall)
        return result

    def record_api_request(
        self,
        endpoint: str,
        response_time_ms: float,
        status_code: int = 200,
    ) -> None:
        """
        记录API请求指标（供中间件调用）

        参数:
            endpoint: API端点路径
            response_time_ms: 响应时间（毫秒）
            status_code: HTTP状态码
        """
        self._api_stats["total_requests"] += 1
        if status_code >= 400:
            self._api_stats["total_errors"] += 1
        self._api_stats["response_times"].append(response_time_ms)
        # 只保留最近1000条响应时间
        if len(self._api_stats["response_times"]) > 1000:
            self._api_stats["response_times"] = self._api_stats["response_times"][-1000:]

        ep_stats = self._api_stats["endpoint_stats"].setdefault(endpoint, {
            "count": 0, "errors": 0, "total_time": 0.0,
        })
        ep_stats["count"] += 1
        ep_stats["total_time"] += response_time_ms
        if status_code >= 400:
            ep_stats["errors"] += 1

    def acknowledge_alert(self, alert_id: str) -> dict:
        """
        确认告警（标记为已处理）

        参数:
            alert_id: 告警ID

        Returns:
            dict: 更新后的告警信息
        """
        for alert in self._alerts:
            if alert["alert_id"] == alert_id:
                alert["status"] = "acknowledged"
                alert["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
                logger.info("monitor.alert_acknowledged", alert_id=alert_id)
                return alert
        return {"error": "alert_not_found", "alert_id": alert_id}
