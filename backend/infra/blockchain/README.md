# Hyperledger Fabric 审计链部署指南

本目录包含 Agent-Native ERP 审计链对接 Hyperledger Fabric 所需的全部配置文件、Chaincode 源码与部署脚本。

## 目录结构

```
infra/blockchain/
├── README.md                     # 本文件
├── network-config.yaml           # Fabric 网络拓扑配置
├── connection-profile.yaml       # 客户端连接配置
├── deploy-chaincode.sh           # Chaincode 部署脚本
└── chaincode/
    ├── audit_chaincode.go        # 智能合约源码（Go，参考实现）
    └── go.mod                    # Go 模块定义
```

## 前置条件

部署前请确认以下环境已就绪：

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Docker | 20.10+ | 运行 Fabric 容器 |
| Docker Compose | 2.0+ | 编排 Fabric 网络 |
| Go | 1.21+ | 编译 Chaincode |
| Fabric Samples | v2.5+ | 提供 cryptogen / configtxgen / peer 等二进制工具 |
| fabric-sdk-py | 0.7+ | Python SDK（可选，应用层使用） |

### 安装 Fabric 二进制工具

```bash
# 下载 Fabric 二进制与 Docker 镜像
curl -sSL https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/install-fabric.sh | bash -s -- binary docker

# 验证安装
cryptogen version
configtxgen --version
peer version
```

## 网络启动步骤

### 1. 生成加密材料（证书）

```bash
# 使用 cryptogen 生成组织证书
cryptogen generate --config=crypto-config.yaml --output=crypto-config
```

预期输出：在 `crypto-config/` 目录下生成 `ordererOrganizations/` 和 `peerOrganizations/`。

### 2. 生成创世区块与通道配置

```bash
# 生成创世区块
configtxgen -profile OrdererGenesis \
    -channelID system-channel \
    -outputBlock ./channel-artifacts/genesis.block

# 生成通道配置交易
configtxgen -profile AuditChannel \
    -channelID audit-channel \
    -outputCreateChannelTx ./channel-artifacts/audit-channel.tx
```

### 3. 启动 Fabric 网络

```bash
# 使用 docker-compose 启动 orderer + peer + couchdb + ca
docker-compose -f docker-compose-erp.yaml up -d

# 验证容器状态
docker ps | grep -E "orderer|peer|couchdb|ca"
```

### 4. 创建并加入通道

```bash
# 设置 peer 环境变量
export CORE_PEER_LOCALMSPID=ERPMSP
export CORE_PEER_ADDRESS=peer0.erp.com:7051
export CORE_PEER_MSPCONFIGPATH=/etc/hyperledger/certs/ERPMSP/users/Admin@erp.com/msp
export CORE_PEER_TLS_ROOTCERT_FILE=/etc/hyperledger/certs/peer-tls-cert.pem

# 创建通道
peer channel create \
    -o orderer.erp.com:7050 \
    -c audit-channel \
    -f ./channel-artifacts/audit-channel.tx \
    --tls --cafile /etc/hyperledger/certs/orderer-tls-cert.pem

# 加入通道
peer channel join -b ./audit-channel.block
```

## Chaincode 部署步骤

### 1. 打包并安装

```bash
# 设置环境变量（覆盖脚本默认值）
export CHAINCODE_NAME=audit-chaincode
export CHAINCODE_VERSION=1.0.0
export CHAINCODE_SEQUENCE=1
export CHAINCODE_PATH=./chaincode

# 执行部署脚本
./deploy-chaincode.sh
```

脚本会自动完成：
1. 打包 chaincode（`peer lifecycle chaincode package`）
2. 安装到 peer0（`peer lifecycle chaincode install`）
3. 组织 ERPMSP 审批（`peer lifecycle chaincode approveformyorg`）
4. 提交到通道 audit-channel（`peer lifecycle chaincode commit`）
5. 验证部署（`peer lifecycle chaincode querycommitted`）

### 2. 手动验证 Chaincode

```bash
# 调用 GetChainStats（应返回空统计）
peer chaincode invoke \
    -o orderer.erp.com:7050 --tls \
    --cafile /etc/hyperledger/certs/orderer-tls-cert.pem \
    -C audit-channel -n audit-chaincode \
    --peerAddresses peer0.erp.com:7051 \
    --tlsRootCertFiles /etc/hyperledger/certs/peer-tls-cert.pem \
    -c '{"function":"GetChainStats","Args":[]}'

# 调用 CreateAuditEntry
peer chaincode invoke \
    -o orderer.erp.com:7050 --tls \
    --cafile /etc/hyperledger/certs/orderer-tls-cert.pem \
    -C audit-channel -n audit-chaincode \
    --peerAddresses peer0.erp.com:7051 \
    --tlsRootCertFiles /etc/hyperledger/certs/peer-tls-cert.pem \
    -c '{"function":"CreateAuditEntry","Args":["{\"entry_id\":\"test-001\",\"actor_did\":\"did:erp:test\",\"action\":\"test\",\"resource\":\"test\",\"result\":\"success\",\"details\":\"{}\",\"risk_level\":\"low\",\"timestamp\":\"2026-01-01T00:00:00Z\"}"]}'
```

## 集成验证方法

### 1. 启用区块链配置

在 `.env` 中设置：

```bash
ERP_BLOCKCHAIN_ENABLED=true
ERP_BLOCKCHAIN_CHANNEL=audit-channel
ERP_BLOCKCHAIN_CHAINCODE=audit-chaincode
ERP_BLOCKCHAIN_PEER_URL=grpcs://peer0.erp.com:7051
ERP_BLOCKCHAIN_ORDERER_URL=grpcs://orderer.erp.com:7050
ERP_BLOCKCHAIN_CERT_PATH=/etc/hyperledger/certs
ERP_BLOCKCHAIN_TIMEOUT=10
ERP_BLOCKCHAIN_MAX_RETRIES=3
```

### 2. 安装 Python SDK

```bash
pip install fabric-sdk-py
```

### 3. 启动后端服务

```bash
cd backend
uvicorn app.main:app --reload
```

启动日志应显示：

```
app.audit_chain_blockchain_ready implementation=FabricAuditClient blockchain_enabled=True
```

### 4. 调用 API 验证

```bash
# 查看区块链统计
curl http://localhost:8000/api/v1/security/blockchain/stats

# 记录一条审计日志（会自动提交到区块链）
curl -X POST http://localhost:8000/api/v1/inventory/adjust \
    -H "Content-Type: application/json" \
    -d '{"product_id":"P001","product_name":"测试商品","quantity":100}'

# 在区块链上验证该记录
curl http://localhost:8000/api/v1/security/blockchain/verify/<entry_id>

# 从区块链查询审计记录
curl "http://localhost:8000/api/v1/security/blockchain/query?limit=10"
```

### 5. 降级验证

关闭 Fabric 网络后重启后端，应自动降级到哈希链模式：

```
app.audit_chain_blockchain_unavailable  # 或 degraded
audit_chain.blockchain_initialized implementation=HashChainFallbackClient
```

API 仍可正常调用，`/security/blockchain/stats` 返回 `backend=hash_chain_fallback`。

## 哈希链算法一致性

Python 端与 Go Chaincode 使用相同的哈希算法，保证跨层可验证性：

```
content = entry_id + actor_did + action + resource + result +
          timestamp + risk_level + sorted_json(details)
if prev_hash:
    content += prev_hash
entry_hash = sha256(content)
```

- Python 实现：`app/core/security/audit.py` 中的 `AuditEntry.compute_hash()`
- Go 实现：`infra/blockchain/chaincode/audit_chaincode.go` 中的 `computeEntryHash()`
- 降级实现：`app/core/blockchain/fabric_client.py` 中的 `_compute_entry_hash()`

## 故障排查

### Q: Fabric SDK 导入失败

```
blockchain.fabric_sdk_unavailable_using_fallback hint="pip install fabric-sdk-py"
```

**A**: 未安装 `fabric-sdk-py`，系统已自动降级到哈希链。如需启用 Fabric，执行 `pip install fabric-sdk-py`。

### Q: 连接 Peer 超时

```
blockchain.fabric_unavailable_degraded error="Fabric 网络连接失败"
```

**A**: 检查：
1. Peer 容器是否运行：`docker ps | grep peer`
2. 证书路径是否正确：`ERP_BLOCKCHAIN_CERT_PATH`
3. TLS 证书是否匹配：`peer-tls-cert.pem`
4. 网络是否可达：`openssl s_client -connect peer0.erp.com:7051`

### Q: Chaincode 调用返回错误

```
audit.blockchain_submit_failed error="Chaincode CreateAuditEntry 调用失败"
```

**A**: 检查：
1. Chaincode 是否已提交：`peer lifecycle chaincode querycommitted -C audit-channel`
2. 背书策略是否满足：单组织只需 ERPMSP 任一成员
3. 查看 Peer 容器日志：`docker logs peer0.erp.com`

## 参考资源

- [Hyperledger Fabric 官方文档](https://hyperledger-fabric.readthedocs.io/)
- [fabric-sdk-py GitHub](https://github.com/hyperledger/fabric-sdk-py)
- [fabric-contract-api-go](https://github.com/hyperledger/fabric-contract-api-go)
- [Fabric 部署示例](https://github.com/hyperledger/fabric-samples)
