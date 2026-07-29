"""
区块链审计层 - Hyperledger Fabric 集成与降级兼容

模块导出：
- :class:`BlockchainAuditClient`         抽象接口
- :class:`FabricAuditClient`             Fabric 实现（生产环境）
- :class:`HashChainFallbackClient`       内存哈希链降级实现
- :func:`get_blockchain_client`          全局单例工厂（自动选择实现）
- :func:`reset_blockchain_client`        重置单例（测试用）
- 异常类：见 :mod:`app.core.blockchain.exceptions`

典型用法::

    from app.core.blockchain import get_blockchain_client

    client = await get_blockchain_client()
    tx_id = await client.submit_audit_entry(entry_dict)
    verified = await client.verify_entry_integrity(entry_dict)

降级策略详见 :mod:`app.core.blockchain.fabric_client` 模块文档。
"""
from app.core.blockchain.exceptions import (
    BlockchainError,
    BlockchainUnavailableError,
    BlockchainTimeoutError,
    BlockchainValidationError,
    ChaincodeInvokeError,
)
from app.core.blockchain.fabric_client import (
    BlockchainAuditClient,
    FabricAuditClient,
    HashChainFallbackClient,
    get_blockchain_client,
    reset_blockchain_client,
)

__all__ = [
    # 抽象接口与实现
    "BlockchainAuditClient",
    "FabricAuditClient",
    "HashChainFallbackClient",
    # 单例工厂
    "get_blockchain_client",
    "reset_blockchain_client",
    # 异常
    "BlockchainError",
    "BlockchainUnavailableError",
    "BlockchainTimeoutError",
    "BlockchainValidationError",
    "ChaincodeInvokeError",
]
