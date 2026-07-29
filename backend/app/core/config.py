"""
系统配置管理
基于三域隔离架构的统一配置中心
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from enum import Enum
from typing import Optional


class DomainType(str, Enum):
    """三域隔离架构 - 域类型"""
    AGENT_EXECUTION = "agent_execution"    # Agent执行域
    DATA_KNOWLEDGE = "data_knowledge"      # 数据与知识域
    USER_INTERACTION = "user_interaction"  # 用户交互域


class SecurityLevel(str, Enum):
    """数据安全分级"""
    PUBLIC = "public"        # 公开
    INTERNAL = "internal"    # 内部
    CONFIDENTIAL = "confidential"  # 机密
    TOP_SECRET = "top_secret"      # 绝密


class DBBackend(str, Enum):
    """
    持久化后端类型
    - memory:  简单内存 Dict 实现，开发/测试默认，零依赖
    - mysql:   MySQL + SQLAlchemy 2.0 async，生产环境推荐
    """
    MEMORY = "memory"
    MYSQL = "mysql"


class CacheBackend(str, Enum):
    """
    缓存后端类型
    - memory:  进程内 Dict 缓存，开发/测试默认
    - redis:   Redis 分布式缓存，生产环境推荐
    """
    MEMORY = "memory"
    REDIS = "redis"


class AgentState(str, Enum):
    """Agent生命周期状态"""
    CREATED = "created"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    DEGRADED = "degraded"
    FUSED = "fused"           # 熔断
    TERMINATED = "terminated"
    ERROR = "error"


class Permission(str, Enum):
    """操作权限"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    APPROVE = "approve"
    DELETE = "delete"


class Settings(BaseSettings):
    """全局配置 - 环境变量注入"""

    # 应用配置
    app_name: str = "Agent-Native ERP"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"

    # 三域隔离配置
    current_domain: DomainType = DomainType.AGENT_EXECUTION

    # 数据库配置
    mysql_url: str = "mysql+aiomysql://erp:erp_password@localhost:3306/erp_db?charset=utf8mb4"
    redis_url: str = "redis://localhost:6379/0"

    # 持久化后端开关（memory | mysql）
    # 通过环境变量 ERP_DB_BACKEND 切换；默认 mysql 生产环境
    db_backend: DBBackend = DBBackend.MYSQL
    # 缓存后端开关（memory | redis）
    cache_backend: CacheBackend = CacheBackend.MEMORY

    # 数据库连接池配置
    db_pool_size: int = 10           # 连接池常驻连接数
    db_max_overflow: int = 20        # 连接池允许的溢出连接数
    db_pool_timeout: int = 30        # 获取连接超时（秒）
    db_pool_recycle: int = 1800      # 连接回收周期（秒），避免数据库端主动断开
    db_echo: bool = False            # 是否打印SQL日志（调试用）

    # Redis 配置
    redis_max_connections: int = 20  # Redis 连接池上限
    redis_default_ttl: int = 300     # 默认缓存过期时间（秒）

    # Kafka事件总线
    # 启用开关：默认关闭，启用后使用 KafkaEventBus；未启用或不可用时降级到内存 EventBus
    # 通过环境变量 ERP_KAFKA_ENABLED=true 启用
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_event_topic: str = "erp.events"
    kafka_audit_topic: str = "erp.audit"
    kafka_consumer_group: str = "erp-agent-group"

    # LLM配置 (私有化部署)
    # 兼容OpenAI API格式的私有化LLM（Qwen-72B / DeepSeek / Llama4 等）
    llm_base_url: str = "http://localhost:8000/v1"
    llm_model: str = "Qwen-72B-Chat"
    llm_api_key: str = "sk-placeholder"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    # LLM认知引擎开关：默认关闭，保护现有测试与未配置LLM的环境
    # 设置环境变量 ERP_LLM_ENABLED=true 可启用
    llm_enabled: bool = False
    # LLM不可用时是否降级到规则引擎（true=降级，false=直接报错）
    llm_fallback_to_rules: bool = True
    # LLM请求超时时间（秒）
    llm_timeout: int = 30
    # LLM请求最大重试次数（指数退避：0.5s, 1s, 2s ...）
    llm_max_retries: int = 3
    # LLM速率限制：每秒最大请求数（令牌桶算法，突发容量=rate*1秒）
    llm_rate_limit: int = 10

    # 安全配置
    jwt_secret: str = "change-this-in-production-use-hsm"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    mtls_enabled: bool = True

    # 审计配置
    audit_chain_enabled: bool = True
    audit_hash_algorithm: str = "sha256"

    # 区块链审计配置
    # 默认关闭，启用后使用 Hyperledger Fabric；
    # 不可用时自动降级到内存 SHA-256 哈希链（HashChainFallbackClient）
    blockchain_enabled: bool = False
    blockchain_channel: str = "audit-channel"
    blockchain_chaincode: str = "audit-chaincode"
    blockchain_peer_url: str = "grpcs://peer0.erp.com:7051"
    blockchain_orderer_url: str = "grpcs://orderer.erp.com:7050"
    blockchain_cert_path: str = "/etc/hyperledger/certs"
    blockchain_timeout: int = 10          # 单次 Chaincode 调用超时（秒）
    blockchain_max_retries: int = 3       # 最大重试次数（指数退避：0.5s, 1s, 2s）

    # Agent配置
    agent_health_check_interval: int = 30  # 秒
    agent_slo_response_time_ms: int = 5000
    agent_circuit_breaker_threshold: int = 5  # 连续失败次数
    agent_consensus_threshold: float = 0.67  # 多Agent共识阈值

    # 人在回路配置
    human_approval_timeout: int = 3600  # 人类审批超时（秒）
    auto_fallback_to_human: bool = True

    # 监控配置
    prometheus_port: int = 9090
    metrics_enabled: bool = True

    # 版本化与灰度发布配置
    versioning_enabled: bool = True
    canary_default_traffic: int = 10  # 默认灰度流量10%
    canary_success_threshold: float = 0.95
    canary_error_rate_threshold: float = 0.05
    canary_duration_seconds: int = 3600  # 默认灰度1小时
    digital_twin_enabled: bool = True

    class Config:
        env_file = ".env"
        env_prefix = "ERP_"
        extra = "ignore"  # 忽略.env中的非ERP_前缀变量（如TZ）


# 全局配置单例
settings = Settings()
