"""
区块链审计层异常定义

异常层次结构：
- BlockchainError                   （基类，非公开）
    - BlockchainUnavailableError    区块链网络不可用（连接失败 / SDK 缺失）
    - BlockchainTimeoutError        交易提交或查询超时
    - BlockchainValidationError     记录完整性校验失败（链上哈希不匹配）
    - ChaincodeInvokeError          Chaincode 调用失败（业务异常 / 背书失败）

设计原则：
- 所有异常均携带 ``detail`` 上下文，便于结构化日志输出
- ``BlockchainUnavailableError`` 是触发降级（fallback）的关键信号
- 调用方应优先捕获具体异常，最后兜底捕获 ``BlockchainError``
"""
from typing import Optional


class BlockchainError(Exception):
    """区块链审计层异常基类（不应直接抛出）"""

    def __init__(self, message: str = "", detail: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message} detail={self.detail}"
        return self.message


class BlockchainUnavailableError(BlockchainError):
    """
    区块链网络不可用。

    触发场景：
    - fabric-sdk-py 未安装
    - Peer / Orderer 节点连接失败
    - 证书路径错误或权限不足
    - 通道未创建或 Chaincode 未部署

    当此异常被 ``get_blockchain_client()`` 捕获时，
    将自动降级到 :class:`HashChainFallbackClient`。
    """


class BlockchainTimeoutError(BlockchainError):
    """
    区块链操作超时。

    触发场景：
    - ``submit_audit_entry`` 提交交易超过 ``blockchain_timeout`` 秒
    - ``query_audit_entry`` 查询无响应
    - 重试机制耗尽仍未拿到结果
    """


class BlockchainValidationError(BlockchainError):
    """
    区块链记录完整性校验失败。

    触发场景：
    - ``verify_entry_integrity`` 在链上查到的 ``EntryHash`` 与本地计算的不一致
    - ``PrevHash`` 链指针断裂
    - 记录被检测到篡改
    """


class ChaincodeInvokeError(BlockchainError):
    """
    Chaincode 调用失败。

    触发场景：
    - Chaincode 函数不存在或参数格式错误
    - 背书策略未满足（Endorsement failure）
    - 排序服务拒绝交易
    - Chaincode 运行时抛出业务异常
    """
