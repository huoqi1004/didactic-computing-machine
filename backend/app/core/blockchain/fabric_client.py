"""
区块链审计客户端 - Hyperledger Fabric 集成层

设计目标：
1. 提供统一的 :class:`BlockchainAuditClient` 抽象接口，业务层无需感知底层实现
2. 实现 :class:`FabricAuditClient` - Hyperledger Fabric 生产实现（fabric-sdk-py）
3. 实现 :class:`HashChainFallbackClient` - 内存 SHA-256 哈希链降级实现
4. 通过 :func:`get_blockchain_client` 全局单例，根据配置自动选择实现并支持降级

降级策略：
- ``settings.blockchain_enabled == False`` → 直接使用 HashChainFallbackClient
- ``fabric-sdk-py`` 包未安装 → 自动降级
- Fabric 节点连接失败 / 初始化失败 → 自动降级
- 运行期重试耗尽 → 单次操作失败，记录警告，``AuditChain`` 仍保留本地哈希链
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import structlog

from app.core.config import settings
from app.core.blockchain.exceptions import (
    BlockchainError,
    BlockchainUnavailableError,
    BlockchainTimeoutError,
    BlockchainValidationError,
    ChaincodeInvokeError,
)

logger = structlog.get_logger()

# fabric-sdk-py 是可选依赖：仅在生产环境部署 Fabric 网络时才需要安装
# 若导入失败，将在 get_blockchain_client() 中自动降级到 HashChainFallbackClient
_FABRIC_SDK_AVAILABLE = False
try:  # pragma: no cover - 依赖外部库，单元测试环境通常无此包
    from hfc.fabric import Client as _FabricSDKClient  # type: ignore
    _FABRIC_SDK_AVAILABLE = True
except Exception:  # noqa: BLE001
    _FabricSDKClient = None  # type: ignore


# ===================================================================
# 抽象接口
# ===================================================================

class BlockchainAuditClient(ABC):
    """
    区块链审计客户端抽象接口。

    所有方法均为协程（async），保证 IO 操作不阻塞事件循环。
    具体实现必须遵循以下契约：
    - ``submit_audit_entry`` 成功返回交易ID（字符串），失败抛出 ``BlockchainError`` 子类
    - ``query_audit_entry`` 未找到记录时返回空 dict（不抛异常）
    - ``verify_entry_integrity`` 返回布尔值，校验失败返回 ``False``
    - ``get_chain_height`` / ``get_chain_stats`` 失败时降级返回合理默认值
    """

    @abstractmethod
    async def submit_audit_entry(self, entry: dict) -> str:
        """提交审计记录到区块链，返回交易ID"""

    @abstractmethod
    async def query_audit_entry(self, tx_id: str) -> dict:
        """按交易ID（或 entry_id）查询审计记录"""

    @abstractmethod
    async def query_audit_range(self, start_time: Any, end_time: Any) -> list[dict]:
        """按时间范围查询审计记录"""

    @abstractmethod
    async def verify_entry_integrity(self, entry: dict) -> bool:
        """验证记录是否被篡改"""

    @abstractmethod
    async def get_chain_height(self) -> int:
        """获取链高度（区块数）"""

    @abstractmethod
    async def get_chain_stats(self) -> dict:
        """获取链统计信息"""

    # -----------------------------------------------------------------
    # 辅助：实现标识（用于日志和测试断言）
    # -----------------------------------------------------------------
    @property
    def implementation_name(self) -> str:
        """返回实现标识，如 'fabric' / 'hash_chain_fallback'"""
        return self.__class__.__name__


# ===================================================================
# 工具函数
# ===================================================================

def _compute_entry_hash(entry: dict, prev_hash: Optional[str] = None) -> str:
    """
    计算 audit entry 的 SHA-256 哈希。

    与 ``app.core.security.audit.AuditEntry.compute_hash`` 保持一致的算法，
    保证哈希链在 Fabric / 降级 / 本地缓存三层之间的可验证性。
    """
    import orjson
    content = (
        f"{entry.get('entry_id', '')}"
        f"{entry.get('actor_did', '')}"
        f"{entry.get('action', '')}"
        f"{entry.get('resource', '')}"
        f"{entry.get('result', '')}"
        f"{entry.get('timestamp', '')}"
        f"{entry.get('risk_level', '')}"
    )
    content += orjson.dumps(
        entry.get("details", {}), option=orjson.OPT_SORT_KEYS
    ).decode()
    if prev_hash:
        content += prev_hash
    return hashlib.sha256(content.encode()).hexdigest()


# ===================================================================
# HashChainFallbackClient - 内存 SHA-256 哈希链降级实现
# ===================================================================

class HashChainFallbackClient(BlockchainAuditClient):
    """
    内存 SHA-256 哈希链降级客户端。

    用途：
    - ``blockchain_enabled = False`` 时的默认后端
    - Fabric SDK 缺失或网络不可用时的自动降级后端
    - 单元测试环境（无需启动 Fabric 网络）

    特性：
    - 所有操作在内存中完成，零外部依赖
    - 维护独立的哈希链（``_last_hash``），与 ``AuditChain`` 本地缓存互补
    - ``submit_audit_entry`` 返回合成的 "tx_id"（``hc:`` 前缀 + uuid），便于与 Fabric tx_id 区分
    - ``verify_entry_integrity`` 通过重算哈希并校验 prev_hash 指针实现
    """

    def __init__(self):
        # 已提交的审计记录：tx_id -> entry_dict
        self._entries_by_tx: dict[str, dict] = {}
        # entry_id -> tx_id 反向索引（query_audit_entry 支持按 entry_id 查询）
        self._tx_by_entry_id: dict[str, str] = {}
        # 上一条记录的哈希（哈希链指针）
        self._last_hash: Optional[str] = None

    async def submit_audit_entry(self, entry: dict) -> str:
        """提交审计记录到内存哈希链，返回合成的交易ID"""
        # 拷贝避免外部修改影响内部状态
        stored = dict(entry)
        # 若调用方未提供 entry_id / timestamp，则补全
        stored.setdefault("entry_id", str(uuid4()))
        if "timestamp" not in stored or not stored["timestamp"]:
            stored["timestamp"] = datetime.now(timezone.utc).isoformat()

        # 计算并写入哈希链
        prev_hash = self._last_hash
        entry_hash = _compute_entry_hash(stored, prev_hash)
        stored["prev_hash"] = prev_hash
        stored["entry_hash"] = entry_hash
        self._last_hash = entry_hash

        # 生成合成交易ID
        tx_id = f"hc:{uuid4().hex}"
        stored["tx_id"] = tx_id
        self._entries_by_tx[tx_id] = stored
        self._tx_by_entry_id[stored["entry_id"]] = tx_id

        logger.debug(
            "blockchain.fallback.submit",
            tx_id=tx_id,
            entry_id=stored["entry_id"],
        )
        return tx_id

    async def query_audit_entry(self, tx_id: str) -> dict:
        """按交易ID或entry_id查询审计记录"""
        # 优先按 tx_id 查询
        if tx_id in self._entries_by_tx:
            return dict(self._entries_by_tx[tx_id])
        # 兼容按 entry_id 查询
        real_tx = self._tx_by_entry_id.get(tx_id)
        if real_tx and real_tx in self._entries_by_tx:
            return dict(self._entries_by_tx[real_tx])
        return {}

    async def query_audit_range(self, start_time: Any, end_time: Any) -> list[dict]:
        """按时间范围查询审计记录"""
        results: list[dict] = []
        for entry in self._entries_by_tx.values():
            ts_str = entry.get("timestamp", "")
            if not ts_str:
                continue
            # 时间过滤：支持字符串 ISO8601 或 datetime 对象
            # 空字符串 / None 视为该侧无边界限制
            try:
                if isinstance(start_time, str) and start_time and ts_str < start_time:
                    continue
                if isinstance(end_time, str) and end_time and ts_str > end_time:
                    continue
                if isinstance(start_time, datetime) and ts_str < start_time.isoformat():
                    continue
                if isinstance(end_time, datetime) and ts_str > end_time.isoformat():
                    continue
            except Exception:  # noqa: BLE001
                # 时间比较失败时跳过过滤，保证返回结果
                pass
            results.append(dict(entry))
        # 按 timestamp 升序
        results.sort(key=lambda e: e.get("timestamp", ""))
        return results

    async def verify_entry_integrity(self, entry: dict) -> bool:
        """验证记录是否被篡改"""
        if not entry:
            return False
        # 优先用 tx_id 找到链上原记录
        tx_id = entry.get("tx_id")
        original = None
        if tx_id and tx_id in self._entries_by_tx:
            original = self._entries_by_tx[tx_id]
        else:
            entry_id = entry.get("entry_id")
            if entry_id and entry_id in self._tx_by_entry_id:
                original = self._entries_by_tx[self._tx_by_entry_id[entry_id]]

        if original is None:
            # 链上无记录：以传入 entry 自身的 prev_hash + entry_hash 自校验
            expected = _compute_entry_hash(entry, entry.get("prev_hash"))
            return entry.get("entry_hash") == expected

        # 校验关键字段未被篡改
        return (
            original.get("entry_hash") == entry.get("entry_hash")
            and original.get("prev_hash") == entry.get("prev_hash")
            and original.get("actor_did") == entry.get("actor_did")
            and original.get("action") == entry.get("action")
        )

    async def get_chain_height(self) -> int:
        """
        获取链高度（降级语义：返回已记录的条目数）。

        与 Fabric 的区块高度语义不同，但作为降级指标足够使用。
        """
        return len(self._entries_by_tx)

    async def get_chain_stats(self) -> dict:
        """获取链统计信息"""
        risk_dist: dict[str, int] = {}
        for e in self._entries_by_tx.values():
            rl = e.get("risk_level", "unknown")
            risk_dist[rl] = risk_dist.get(rl, 0) + 1
        return {
            "backend": "hash_chain_fallback",
            "implementation": self.implementation_name,
            "total_entries": len(self._entries_by_tx),
            "chain_height": len(self._entries_by_tx),
            "integrity_valid": True,  # 内存链写入即正确
            "risk_distribution": risk_dist,
            "fabric_available": False,
            "blockchain_enabled": settings.blockchain_enabled,
        }

    # -----------------------------------------------------------------
    # 测试辅助：清空状态
    # -----------------------------------------------------------------
    def _reset(self) -> None:
        """清空内部状态（仅用于测试夹具）"""
        self._entries_by_tx.clear()
        self._tx_by_entry_id.clear()
        self._last_hash = None


# ===================================================================
# FabricAuditClient - Hyperledger Fabric 实现
# ===================================================================

class FabricAuditClient(BlockchainAuditClient):
    """
    Hyperledger Fabric 审计客户端实现。

    使用 fabric-sdk-py 连接 Fabric 网络，调用 ``audit-chaincode`` 智能合约。
    配置项从 :mod:`app.core.config` 读取：
    - ``blockchain_channel``       通道名
    - ``blockchain_chaincode``     Chaincode 名
    - ``blockchain_peer_url``      Peer 节点地址
    - ``blockchain_orderer_url``   Orderer 节点地址
    - ``blockchain_cert_path``     证书目录
    - ``blockchain_timeout``       单次操作超时（秒）
    - ``blockchain_max_retries``   最大重试次数（指数退避）

    重试机制：
    - 仅对临时性失败（超时、连接错误）重试
    - 业务异常（ChaincodeInvokeError）不重试
    - 退避间隔：0.5s, 1s, 2s ... （指数增长）
    """

    def __init__(self):
        self._channel = settings.blockchain_channel
        self._chaincode = settings.blockchain_chaincode
        self._peer_url = settings.blockchain_peer_url
        self._orderer_url = settings.blockchain_orderer_url
        self._cert_path = settings.blockchain_cert_path
        self._timeout = settings.blockchain_timeout
        self._max_retries = max(1, settings.blockchain_max_retries)
        # fabric-sdk-py 客户端句柄（懒加载）
        self._sdk_client: Any = None
        self._initialized = False
        self._init_error: Optional[str] = None

    # -----------------------------------------------------------------
    # 初始化与连接
    # -----------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        """
        懒加载初始化 Fabric SDK 客户端。

        失败时抛出 :class:`BlockchainUnavailableError`，
        由 :func:`get_blockchain_client` 捕获后降级。
        """
        if self._initialized:
            if self._sdk_client is None:
                raise BlockchainUnavailableError(
                    "Fabric SDK 初始化失败",
                    detail={"init_error": self._init_error},
                )
            return

        self._initialized = True

        if not _FABRIC_SDK_AVAILABLE:
            self._init_error = "fabric-sdk-py not installed"
            raise BlockchainUnavailableError(
                "fabric-sdk-py 未安装",
                detail={"hint": "pip install fabric-sdk-py 或启用降级模式"},
            )

        try:  # pragma: no cover - 需要真实 Fabric 网络
            self._sdk_client = _FabricSDKClient()  # type: ignore[call-arg]
            # 加载连接配置（connection-profile.yaml）
            # 实际实现会读取 cert_path 下的证书并建立 gRPC 连接
            await asyncio.to_thread(
                self._sdk_client.init_with_config,
                self._build_connection_profile(),
            )
            logger.info(
                "blockchain.fabric.connected",
                channel=self._channel,
                chaincode=self._chaincode,
                peer=self._peer_url,
            )
        except BlockchainUnavailableError:
            raise
        except Exception as e:  # pragma: no cover - 需要真实 Fabric 网络
            self._init_error = str(e)
            self._sdk_client = None
            raise BlockchainUnavailableError(
                "Fabric 网络连接失败",
                detail={
                    "peer_url": self._peer_url,
                    "orderer_url": self._orderer_url,
                    "error": str(e),
                },
            )

    def _build_connection_profile(self) -> dict:
        """
        根据 settings 构建 connection profile（dict 形式）。

        实际生产环境应从 ``connection-profile.yaml`` 加载；
        此处提供编程式构造，便于配置注入和测试 mock。
        """
        return {
            "name": "erp-audit-network",
            "version": "1.0.0",
            "client": {
                "organization": "ERPOrg",
                "credentialStore": {
                    "path": f"{self._cert_path}/credentials",
                },
            },
            "organizations": {
                "ERPOrg": {
                    "mspid": "ERPMSP",
                    "peers": ["peer0.erp.com"],
                    "certificateAuthorities": ["ca.erp.com"],
                },
            },
            "peers": {
                "peer0.erp.com": {
                    "url": self._peer_url,
                    "tlsCACerts": {"path": f"{self._cert_path}/peer-tls-cert.pem"},
                },
            },
            "orderers": {
                "orderer.erp.com": {
                    "url": self._orderer_url,
                    "tlsCACerts": {"path": f"{self._cert_path}/orderer-tls-cert.pem"},
                },
            },
            "channels": {
                self._channel: {
                    "orderers": ["orderer.erp.com"],
                    "peers": {
                        "peer0.erp.com": {
                            "endorsingPeer": True,
                            "chaincodeQuery": True,
                            "ledgerQuery": True,
                            "eventSource": True,
                        },
                    },
                },
            },
        }

    # -----------------------------------------------------------------
    # 重试机制
    # -----------------------------------------------------------------

    async def _invoke_with_retry(
        self,
        fn_name: str,
        args: list,
        *,
        is_query: bool = False,
    ) -> Any:
        """
        调用 Chaincode 函数，带重试机制。

        - ``is_query=True`` 时调用 ``query_chaincode``（只读，不排序）
        - ``is_query=False`` 时调用 ``invoke_chaincode``（写入，需背书+排序）

        重试策略：指数退避（0.5s, 1s, 2s ...），最多 ``max_retries`` 次。
        """
        await self._ensure_initialized()

        last_error: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._do_invoke(fn_name, args, is_query=is_query),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError as e:
                last_error = BlockchainTimeoutError(
                    f"Chaincode {fn_name} 调用超时",
                    detail={
                        "fn": fn_name,
                        "attempt": attempt,
                        "max_retries": self._max_retries,
                        "timeout": self._timeout,
                    },
                )
                logger.warning(
                    "blockchain.fabric.timeout",
                    fn=fn_name,
                    attempt=attempt,
                    timeout=self._timeout,
                )
            except BlockchainUnavailableError:
                # 连接级失败直接抛出，由上层降级
                raise
            except ChaincodeInvokeError as e:
                # 业务异常不重试
                raise
            except Exception as e:  # noqa: BLE001
                last_error = ChaincodeInvokeError(
                    f"Chaincode {fn_name} 调用失败: {e}",
                    detail={
                        "fn": fn_name,
                        "attempt": attempt,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
                logger.warning(
                    "blockchain.fabric.invoke_failed",
                    fn=fn_name,
                    attempt=attempt,
                    error=str(e),
                )

            # 指数退避：0.5, 1, 2, 4 ...
            if attempt < self._max_retries:
                backoff = 0.5 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        # 重试耗尽
        if isinstance(last_error, BlockchainTimeoutError):
            raise last_error
        raise ChaincodeInvokeError(
            f"Chaincode {fn_name} 重试 {self._max_retries} 次后仍失败",
            detail={
                "fn": fn_name,
                "max_retries": self._max_retries,
                "last_error": str(last_error) if last_error else "unknown",
            },
        )

    async def _do_invoke(
        self, fn_name: str, args: list, *, is_query: bool = False
    ) -> Any:
        """
        实际调用 fabric-sdk-py 的方法。

        分离此方法的目的：
        1. 便于在测试中 mock（patch _do_invoke 即可隔离 Fabric SDK）
        2. ``_invoke_with_retry`` 包装重试逻辑，``_do_invoke`` 只负责一次调用
        """
        # pragma: no cover - 需要真实 Fabric 网络，测试通过 mock 验证
        if self._sdk_client is None:  # pragma: no cover
            raise BlockchainUnavailableError("Fabric SDK 客户端未初始化")

        import orjson
        channel = self._sdk_client.get_channel(self._channel)
        chaincode = channel.chaincode(self._chaincode)

        # 序列化参数（Chaincode 接收 []string）
        str_args = [
            a if isinstance(a, str) else orjson.dumps(a).decode()
            for a in args
        ]

        if is_query:
            response = await asyncio.to_thread(
                chaincode.query, fn_name, *str_args
            )
        else:
            response = await asyncio.to_thread(
                chaincode.invoke, fn_name, *str_args
            )
        return response

    # -----------------------------------------------------------------
    # BlockchainAuditClient 接口实现
    # -----------------------------------------------------------------

    async def submit_audit_entry(self, entry: dict) -> str:
        """
        提交审计记录到 Fabric 区块链。

        调用 Chaincode 的 ``CreateAuditEntry`` 函数。
        返回 Fabric 交易ID（64 字符十六进制字符串）。
        """
        response = await self._invoke_with_retry(
            "CreateAuditEntry",
            [entry],
            is_query=False,
        )
        # Chaincode 返回 {"tx_id": "...", "entry_id": "...", "entry_hash": "..."}
        if isinstance(response, dict):
            tx_id = response.get("tx_id") or response.get("TxID") or ""
        elif isinstance(response, str):
            tx_id = response
        else:
            tx_id = str(response)

        if not tx_id:
            raise ChaincodeInvokeError(
                "Chaincode CreateAuditEntry 未返回交易ID",
                detail={"response": str(response)},
            )

        logger.info(
            "blockchain.fabric.submitted",
            tx_id=tx_id,
            entry_id=entry.get("entry_id"),
        )
        return tx_id

    async def query_audit_entry(self, tx_id: str) -> dict:
        """调用 Chaincode 的 ``QueryAuditEntry`` 函数查询记录"""
        response = await self._invoke_with_retry(
            "QueryAuditEntry",
            [tx_id],
            is_query=True,
        )
        if not response:
            return {}
        if isinstance(response, dict):
            return response
        # Chaincode 返回 JSON 字符串
        try:
            import orjson
            return orjson.loads(response) if isinstance(response, (bytes, str)) else {}
        except Exception:  # noqa: BLE001
            return {}

    async def query_audit_range(self, start_time: Any, end_time: Any) -> list[dict]:
        """调用 Chaincode 的 ``QueryAuditRange`` 函数范围查询"""
        start_str = (
            start_time.isoformat() if isinstance(start_time, datetime)
            else str(start_time)
        )
        end_str = (
            end_time.isoformat() if isinstance(end_time, datetime)
            else str(end_time)
        )
        response = await self._invoke_with_retry(
            "QueryAuditRange",
            [start_str, end_str],
            is_query=True,
        )
        if not response:
            return []
        if isinstance(response, list):
            return response
        try:
            import orjson
            data = orjson.loads(response) if isinstance(response, (bytes, str)) else []
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    async def verify_entry_integrity(self, entry: dict) -> bool:
        """
        调用 Chaincode 的 ``VerifyIntegrity`` 函数验证记录。

        Chaincode 会重新计算 entry_hash 并与链上存储的对比。
        """
        try:
            response = await self._invoke_with_retry(
                "VerifyIntegrity",
                [entry.get("entry_id") or entry.get("tx_id", "")],
                is_query=True,
            )
        except ChaincodeInvokeError as e:
            logger.warning(
                "blockchain.fabric.verify_failed",
                entry_id=entry.get("entry_id"),
                error=str(e),
            )
            return False

        if isinstance(response, bool):
            return response
        if isinstance(response, dict):
            return bool(response.get("valid", response.get("Valid", False)))
        if isinstance(response, (bytes, str)):
            try:
                import orjson
                data = orjson.loads(response)
                return bool(data.get("valid", False))
            except Exception:  # noqa: BLE001
                return False
        return False

    async def get_chain_height(self) -> int:
        """调用 Chaincode 的 ``GetChainHeight`` 函数获取链高度"""
        try:
            response = await self._invoke_with_retry(
                "GetChainHeight",
                [],
                is_query=True,
            )
        except (ChaincodeInvokeError, BlockchainTimeoutError) as e:
            logger.warning("blockchain.fabric.height_failed", error=str(e))
            return 0

        if isinstance(response, int):
            return response
        if isinstance(response, dict):
            return int(response.get("height", response.get("Height", 0)))
        try:
            return int(response)
        except (TypeError, ValueError):
            return 0

    async def get_chain_stats(self) -> dict:
        """调用 Chaincode 的 ``GetChainStats`` 函数获取统计"""
        try:
            response = await self._invoke_with_retry(
                "GetChainStats",
                [],
                is_query=True,
            )
        except (ChaincodeInvokeError, BlockchainTimeoutError) as e:
            logger.warning("blockchain.fabric.stats_failed", error=str(e))
            return {
                "backend": "fabric",
                "implementation": self.implementation_name,
                "available": False,
                "error": str(e),
                "fabric_available": False,
                "blockchain_enabled": settings.blockchain_enabled,
            }

        if isinstance(response, dict):
            stats = response
        elif isinstance(response, (bytes, str)):
            try:
                import orjson
                stats = orjson.loads(response)
            except Exception:  # noqa: BLE001
                stats = {}
        else:
            stats = {}

        stats.setdefault("backend", "fabric")
        stats.setdefault("implementation", self.implementation_name)
        stats.setdefault("fabric_available", True)
        stats.setdefault("blockchain_enabled", settings.blockchain_enabled)
        stats.setdefault("channel", self._channel)
        stats.setdefault("chaincode", self._chaincode)
        return stats


# ===================================================================
# 全局单例管理
# ===================================================================

# 全局客户端单例（懒加载）
_global_client: Optional[BlockchainAuditClient] = None
# 是否已尝试初始化（避免每次调用都重试 Fabric 连接）
_init_attempted: bool = False


def reset_blockchain_client() -> None:
    """
    重置全局单例（仅用于测试夹具）。

    在测试间清空全局客户端状态，保证测试隔离。
    """
    global _global_client, _init_attempted
    _global_client = None
    _init_attempted = False


async def get_blockchain_client() -> BlockchainAuditClient:
    """
    获取区块链审计客户端全局单例。

    选择逻辑：
    1. ``settings.blockchain_enabled == False`` → 返回 HashChainFallbackClient
    2. ``fabric-sdk-py`` 未安装 → 返回 HashChainFallbackClient（记录警告）
    3. 尝试初始化 FabricAuditClient：
       - 成功 → 返回 FabricAuditClient
       - 失败 → 返回 HashChainFallbackClient（记录警告）

    懒加载：首次调用时初始化，后续调用直接返回缓存实例。
    """
    global _global_client, _init_attempted

    if _global_client is not None:
        return _global_client

    if _init_attempted:
        # 之前尝试失败，直接返回降级客户端
        _global_client = HashChainFallbackClient()
        return _global_client

    _init_attempted = True

    # 1. 配置开关：未启用区块链 → 降级
    if not settings.blockchain_enabled:
        logger.info("blockchain.disabled_using_fallback", reason="blockchain_enabled=False")
        _global_client = HashChainFallbackClient()
        return _global_client

    # 2. Fabric SDK 不可用 → 降级
    if not _FABRIC_SDK_AVAILABLE:
        logger.warning(
            "blockchain.fabric_sdk_unavailable_using_fallback",
            hint="pip install fabric-sdk-py",
        )
        _global_client = HashChainFallbackClient()
        return _global_client

    # 3. 尝试初始化 Fabric 客户端
    try:
        client = FabricAuditClient()
        await client._ensure_initialized()
        logger.info("blockchain.fabric_ready", channel=settings.blockchain_channel)
        _global_client = client
        return _global_client
    except BlockchainUnavailableError as e:
        logger.warning(
            "blockchain.fabric_unavailable_degraded",
            error=str(e),
            detail=e.detail,
        )
        _global_client = HashChainFallbackClient()
        return _global_client
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "blockchain.fabric_init_failed_degraded",
            error=str(e),
            error_type=type(e).__name__,
        )
        _global_client = HashChainFallbackClient()
        return _global_client
