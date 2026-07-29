"""
配置管理模块
CIO管理员系统的系统配置中心，支持分类管理、版本控制、热更新

特性：
- 系统配置CRUD（按分类组织）
- 配置版本控制（变更历史）
- 配置导入导出
- 环境变量管理
- 配置校验（类型检查、范围检查）
- 配置热更新（无需重启）
"""
import json
import structlog
from datetime import datetime, timezone
from typing import Optional, Any
from uuid import uuid4

logger = structlog.get_logger()


class ConfigManager:
    """
    配置管理器

    职责：
    - 系统配置的CRUD操作（按分类组织）
    - 配置变更版本控制与历史追溯
    - 配置批量导入导出
    - 环境变量管理
    - 配置值校验（类型检查、范围检查）
    - 配置热更新通知
    """

    # 配置校验规则定义
    VALIDATION_RULES: dict[str, dict] = {
        "max_agents": {
            "type": "int", "min": 1, "max": 1000,
            "description": "最大Agent数量",
        },
        "agent_health_check_interval": {
            "type": "int", "min": 5, "max": 3600,
            "description": "Agent健康检查间隔（秒）",
        },
        "agent_circuit_breaker_threshold": {
            "type": "int", "min": 1, "max": 100,
            "description": "Agent熔断器阈值",
        },
        "agent_consensus_threshold": {
            "type": "float", "min": 0.0, "max": 1.0,
            "description": "Agent共识阈值",
        },
        "llm_enabled": {
            "type": "bool",
            "description": "是否启用LLM",
        },
        "llm_model": {
            "type": "str",
            "description": "LLM模型名称",
        },
        "db_pool_size": {
            "type": "int", "min": 1, "max": 100,
            "description": "数据库连接池大小",
        },
        "redis_default_ttl": {
            "type": "int", "min": 1, "max": 86400,
            "description": "Redis默认TTL（秒）",
        },
        "debug": {
            "type": "bool",
            "description": "调试模式",
        },
        "session_timeout": {
            "type": "int", "min": 60, "max": 86400,
            "description": "会话超时时间（秒）",
        },
        "password_min_length": {
            "type": "int", "min": 6, "max": 128,
            "description": "密码最小长度",
        },
        "api_rate_limit": {
            "type": "int", "min": 1, "max": 100000,
            "description": "API限流（请求/分钟）",
        },
    }

    def __init__(self):
        # 配置存储：{category: {key: config_entry}}
        self._configs: dict[str, dict[str, dict]] = {}
        # 配置变更历史：{(category, key): [history_records]}
        self._config_history: dict[tuple[str, str], list[dict]] = {}
        # 环境变量存储
        self._env_vars: dict[str, str] = {}
        # 热更新订阅者：{key: [callback_functions]}
        self._hot_reload_callbacks: dict[str, list] = {}
        self._seed_data()

    def _seed_data(self) -> None:
        """加载示例数据"""
        now = datetime.now(timezone.utc)
        # general 分类
        general_configs = {
            "app_name": {
                "value": "Agent-Native ERP",
                "type": "str",
                "description": "应用名称",
                "updated_by": "system",
                "updated_at": now.isoformat(),
            },
            "debug": {
                "value": False,
                "type": "bool",
                "description": "调试模式",
                "updated_by": "system",
                "updated_at": now.isoformat(),
            },
            "session_timeout": {
                "value": 3600,
                "type": "int",
                "description": "会话超时时间（秒）",
                "updated_by": "admin",
                "updated_at": now.isoformat(),
            },
        }
        # agent 分类
        agent_configs = {
            "max_agents": {
                "value": 50,
                "type": "int",
                "description": "最大Agent数量",
                "updated_by": "system",
                "updated_at": now.isoformat(),
            },
            "agent_health_check_interval": {
                "value": 30,
                "type": "int",
                "description": "Agent健康检查间隔（秒）",
                "updated_by": "system",
                "updated_at": now.isoformat(),
            },
            "agent_consensus_threshold": {
                "value": 0.67,
                "type": "float",
                "description": "Agent共识阈值",
                "updated_by": "admin",
                "updated_at": now.isoformat(),
            },
            "llm_enabled": {
                "value": True,
                "type": "bool",
                "description": "是否启用LLM",
                "updated_by": "admin",
                "updated_at": now.isoformat(),
            },
        }
        # security 分类
        security_configs = {
            "password_min_length": {
                "value": 12,
                "type": "int",
                "description": "密码最小长度",
                "updated_by": "admin",
                "updated_at": now.isoformat(),
            },
            "api_rate_limit": {
                "value": 1000,
                "type": "int",
                "description": "API限流（请求/分钟）",
                "updated_by": "admin",
                "updated_at": now.isoformat(),
            },
        }
        self._configs = {
            "general": general_configs,
            "agent": agent_configs,
            "security": security_configs,
        }
        # 记录初始历史
        for category, configs in self._configs.items():
            for key, entry in configs.items():
                self._config_history[(category, key)] = [{
                    "history_id": str(uuid4()),
                    "key": key,
                    "category": category,
                    "value": entry["value"],
                    "updated_by": entry["updated_by"],
                    "timestamp": entry["updated_at"],
                    "action": "initial",
                }]
        # 环境变量示例
        self._env_vars = {
            "ERP_ENV": "production",
            "LOG_LEVEL": "INFO",
            "TZ": "Asia/Shanghai",
        }
        logger.info("config.seeded",
                    categories=len(self._configs),
                    total_keys=sum(len(c) for c in self._configs.values()))

    def get_config(
        self,
        category: Optional[str] = None,
        key: Optional[str] = None,
    ) -> dict:
        """
        获取系统配置

        参数:
            category: 可选，配置分类（如general/agent/security）
            key: 可选，配置键名

        Returns:
            dict: 配置数据。若指定category和key则返回单条配置；
                  若只指定category则返回该分类下所有配置；
                  若都不指定则返回全部分类配置。
        """
        if category and key:
            cat_configs = self._configs.get(category, {})
            entry = cat_configs.get(key)
            if not entry:
                return {"error": "config_not_found",
                        "category": category, "key": key}
            return {"category": category, "key": key, **entry}
        elif category:
            return {
                "category": category,
                "configs": dict(self._configs.get(category, {})),
            }
        else:
            return {
                category: dict(configs)
                for category, configs in self._configs.items()
            }

    def set_config(
        self,
        key: str,
        value: Any,
        category: str = "general",
        updated_by: str = "system",
    ) -> dict:
        """
        设置系统配置（新建或更新）

        参数:
            key: 配置键名
            value: 配置值
            category: 配置分类，默认"general"
            updated_by: 更新者标识

        Returns:
            dict: 设置结果，包含新配置信息和是否触发热更新
        """
        # 校验配置
        if not self.validate_config(key, value):
            return {"error": "validation_failed", "key": key, "value": value}

        now = datetime.now(timezone.utc)
        is_update = False
        old_value = None

        if category not in self._configs:
            self._configs[category] = {}

        cat_configs = self._configs[category]
        if key in cat_configs:
            is_update = True
            old_value = cat_configs[key]["value"]

        # 推断类型
        config_type = type(value).__name__
        if isinstance(value, bool):
            config_type = "bool"
        elif isinstance(value, int):
            config_type = "int"
        elif isinstance(value, float):
            config_type = "float"
        elif isinstance(value, str):
            config_type = "str"

        entry = {
            "value": value,
            "type": config_type,
            "description": cat_configs.get(key, {}).get("description", ""),
            "updated_by": updated_by,
            "updated_at": now.isoformat(),
        }
        cat_configs[key] = entry

        # 记录历史
        history_key = (category, key)
        if history_key not in self._config_history:
            self._config_history[history_key] = []
        self._config_history[history_key].append({
            "history_id": str(uuid4()),
            "key": key,
            "category": category,
            "value": value,
            "old_value": old_value,
            "updated_by": updated_by,
            "timestamp": now.isoformat(),
            "action": "update" if is_update else "create",
        })

        # 热更新通知
        hot_reloaded = False
        if is_update and old_value != value:
            hot_reloaded = self._notify_hot_reload(category, key, value, old_value)

        logger.info("config.set",
                    category=category, key=key,
                    action="update" if is_update else "create",
                    hot_reloaded=hot_reloaded)
        return {
            "status": "success",
            "category": category,
            "key": key,
            "config": entry,
            "hot_reloaded": hot_reloaded,
        }

    def delete_config(self, key: str, category: str = "general") -> dict:
        """
        删除系统配置

        参数:
            key: 配置键名
            category: 配置分类，默认"general"

        Returns:
            dict: 删除结果
        """
        cat_configs = self._configs.get(category, {})
        if key not in cat_configs:
            return {"error": "config_not_found",
                    "category": category, "key": key}

        deleted_entry = cat_configs.pop(key)
        now = datetime.now(timezone.utc)

        # 记录历史
        history_key = (category, key)
        if history_key not in self._config_history:
            self._config_history[history_key] = []
        self._config_history[history_key].append({
            "history_id": str(uuid4()),
            "key": key,
            "category": category,
            "value": None,
            "old_value": deleted_entry["value"],
            "updated_by": "system",
            "timestamp": now.isoformat(),
            "action": "delete",
        })

        logger.info("config.deleted", category=category, key=key)
        return {
            "status": "success",
            "category": category,
            "key": key,
            "deleted_value": deleted_entry["value"],
        }

    def get_config_history(
        self, key: str, category: str = "general"
    ) -> list[dict]:
        """
        获取配置的变更历史

        参数:
            key: 配置键名
            category: 配置分类，默认"general"

        Returns:
            list[dict]: 变更历史列表，按时间正序排列
        """
        history = self._config_history.get((category, key), [])
        logger.info("config.history_queried",
                    category=category, key=key, count=len(history))
        return list(history)

    def export_config(self) -> dict:
        """
        导出全部系统配置

        Returns:
            dict: 包含所有分类配置的字典，可用于备份或迁移
        """
        export_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
            "configs": {},
            "env_vars": dict(self._env_vars),
        }
        for category, configs in self._configs.items():
            export_data["configs"][category] = {
                key: entry["value"]
                for key, entry in configs.items()
            }
        logger.info("config.exported",
                    categories=len(export_data["configs"]))
        return export_data

    def import_config(
        self, config_data: dict, imported_by: str = "system"
    ) -> dict:
        """
        导入系统配置

        参数:
            config_data: 配置数据字典（格式同export_config输出）
            imported_by: 导入操作者标识

        Returns:
            dict: 导入结果，包含成功/失败计数
        """
        configs = config_data.get("configs", {})
        env_vars = config_data.get("env_vars", {})
        success_count = 0
        failure_count = 0
        errors = []

        for category, items in configs.items():
            if not isinstance(items, dict):
                continue
            for key, value in items.items():
                result = self.set_config(key, value, category, imported_by)
                if result.get("status") == "success":
                    success_count += 1
                else:
                    failure_count += 1
                    errors.append({
                        "category": category,
                        "key": key,
                        "error": result.get("error", "unknown"),
                    })

        # 导入环境变量
        if env_vars:
            self._env_vars.update(env_vars)

        logger.info("config.imported",
                    success=success_count, failure=failure_count,
                    imported_by=imported_by)
        return {
            "status": "success",
            "imported_count": success_count,
            "failed_count": failure_count,
            "errors": errors,
        }

    def validate_config(self, key: str, value: Any) -> bool:
        """
        校验配置值（类型检查、范围检查）

        参数:
            key: 配置键名
            value: 待校验的配置值

        Returns:
            bool: 校验通过返回True，否则返回False
        """
        rule = self.VALIDATION_RULES.get(key)
        if not rule:
            # 无校验规则的配置项默认通过
            return True

        expected_type = rule["type"]
        # 类型检查
        if expected_type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                logger.warning("config.validation_type_failed",
                              key=key, expected="int", got=type(value).__name__)
                return False
            if "min" in rule and value < rule["min"]:
                return False
            if "max" in rule and value > rule["max"]:
                return False
        elif expected_type == "float":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                logger.warning("config.validation_type_failed",
                              key=key, expected="float", got=type(value).__name__)
                return False
            if "min" in rule and value < rule["min"]:
                return False
            if "max" in rule and value > rule["max"]:
                return False
        elif expected_type == "bool":
            if not isinstance(value, bool):
                logger.warning("config.validation_type_failed",
                              key=key, expected="bool", got=type(value).__name__)
                return False
        elif expected_type == "str":
            if not isinstance(value, str):
                logger.warning("config.validation_type_failed",
                              key=key, expected="str", got=type(value).__name__)
                return False
        return True

    def get_categories(self) -> list[str]:
        """
        获取所有配置分类

        Returns:
            list[str]: 配置分类名称列表
        """
        return list(self._configs.keys())

    def _notify_hot_reload(
        self,
        category: str,
        key: str,
        new_value: Any,
        old_value: Any,
    ) -> bool:
        """
        通知热更新订阅者

        参数:
            category: 配置分类
            key: 配置键名
            new_value: 新值
            old_value: 旧值

        Returns:
            bool: 是否有订阅者被通知
        """
        full_key = f"{category}.{key}"
        callbacks = self._hot_reload_callbacks.get(full_key, [])
        for callback in callbacks:
            try:
                callback(new_value, old_value)
            except Exception as e:
                logger.error("config.hot_reload_callback_failed",
                            key=full_key, error=str(e))
        return len(callbacks) > 0

    def subscribe_hot_reload(
        self, key: str, callback, category: str = "general"
    ) -> dict:
        """
        订阅配置热更新

        参数:
            key: 配置键名
            callback: 回调函数，签名 callback(new_value, old_value)
            category: 配置分类，默认"general"

        Returns:
            dict: 订阅结果
        """
        full_key = f"{category}.{key}"
        if full_key not in self._hot_reload_callbacks:
            self._hot_reload_callbacks[full_key] = []
        self._hot_reload_callbacks[full_key].append(callback)
        logger.info("config.hot_reload_subscribed", key=full_key)
        return {"status": "success", "key": full_key}

    def get_env_vars(self) -> dict:
        """
        获取所有环境变量

        Returns:
            dict: 环境变量字典
        """
        return dict(self._env_vars)

    def set_env_var(self, name: str, value: str) -> dict:
        """
        设置环境变量

        参数:
            name: 环境变量名
            value: 环境变量值

        Returns:
            dict: 设置结果
        """
        self._env_vars[name] = value
        logger.info("config.env_var_set", name=name)
        return {"status": "success", "name": name, "value": value}
