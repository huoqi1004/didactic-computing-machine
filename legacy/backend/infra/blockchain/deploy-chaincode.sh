#!/usr/bin/env bash
# ============================================================
# deploy-chaincode.sh - 部署 audit-chaincode 到 Hyperledger Fabric
# ============================================================
# 用法：
#   ./deploy-chaincode.sh
#
# 前置条件：
#   1. Fabric 网络已启动（orderer + peer0 已运行）
#   2. 通道 audit-channel 已创建
#   3. peer CLI 已安装并配置好环境变量（CORE_PEER_*）
#   4. 当前用户对 ${FABRIC_CFG_PATH} 有读写权限
#
# 本脚本执行以下步骤：
#   1. 打包 chaincode（peer lifecycle chaincode package）
#   2. 安装到 peer0（peer lifecycle chaincode install）
#   3. 组织审批（peer lifecycle chaincode approveformyorg）
#   4. 提交到通道（peer lifecycle chaincode commit）
#   5. 验证部署（peer lifecycle chaincode querycommitted）
# ============================================================

set -euo pipefail

# ------------------------------------------------------------
# 配置（可通过环境变量覆盖）
# ------------------------------------------------------------
CHAINCODE_NAME="${CHAINCODE_NAME:-audit-chaincode}"
CHAINCODE_VERSION="${CHAINCODE_VERSION:-1.0.0}"
CHAINCODE_SEQUENCE="${CHAINCODE_SEQUENCE:-1}"
CHANNEL_NAME="${CHANNEL_NAME:-audit-channel}"
CHAINCODE_PATH="${CHAINCODE_PATH:-./chaincode}"
CHAINCODE_LANG="${CHAINCODE_LANG:-golang}"

# Fabric 二进制路径（默认假设在 PATH 中）
PEER_BIN="${PEER_BIN:-peer}"
ORDERER_ADDR="${ORDERER_ADDR:-orderer.erp.com:7050}"
ORDERER_TLS_ROOTCERT="${ORDERER_TLS_ROOTCERT:-/etc/hyperledger/certs/orderer-tls-cert.pem}"

# Peer 配置（通过环境变量 CORE_PEER_* 传入）
: "${CORE_PEER_LOCALMSPID:?需要设置 CORE_PEER_LOCALMSPID=ERPMSP}"
: "${CORE_PEER_ADDRESS:?需要设置 CORE_PEER_ADDRESS=peer0.erp.com:7051}"
: "${CORE_PEER_MSPCONFIGPATH:?需要设置 CORE_PEER_MSPCONFIGPATH}"
: "${CORE_PEER_TLS_ROOTCERT_FILE:?需要设置 CORE_PEER_TLS_ROOTCERT_FILE}"

echo "============================================================"
echo "  部署 Chaincode: ${CHAINCODE_NAME}@${CHAINCODE_VERSION}"
echo "  通道: ${CHANNEL_NAME}"
echo "  语言: ${CHAINCODE_LANG}"
echo "  Peer: ${CORE_PEER_ADDRESS}"
echo "  MSP:  ${CORE_PEER_LOCALMSPID}"
echo "============================================================"

# ------------------------------------------------------------
# 步骤 1：打包 chaincode
# ------------------------------------------------------------
echo ""
echo "[1/5] 打包 chaincode..."
PACKAGE_FILE="/tmp/${CHAINCODE_NAME}.tar.gz"

${PEER_BIN} lifecycle chaincode package "${PACKAGE_FILE}" \
    --path "${CHAINCODE_PATH}" \
    --lang "${CHAINCODE_LANG}" \
    --label "${CHAINCODE_NAME}_${CHAINCODE_VERSION}"

echo "  -> 已打包: ${PACKAGE_FILE}"

# ------------------------------------------------------------
# 步骤 2：安装到 peer
# ------------------------------------------------------------
echo ""
echo "[2/5] 安装 chaincode 到 peer..."
${PEER_BIN} lifecycle chaincode install "${PACKAGE_FILE}"
echo "  -> 安装完成"

# 查询已安装的 chaincode 包ID
PACKAGE_ID=$(${PEER_BIN} lifecycle chaincode queryinstalled \
    | awk -v label="${CHAINCODE_NAME}_${CHAINCODE_VERSION}" \
        '$0 ~ label {print $3}' | sed 's/,//')

if [ -z "${PACKAGE_ID}" ]; then
    echo "  [ERROR] 未找到已安装的 chaincode 包ID"
    exit 1
fi
echo "  -> Package ID: ${PACKAGE_ID}"

# ------------------------------------------------------------
# 步骤 3：组织审批
# ------------------------------------------------------------
echo ""
echo "[3/5] 组织 ${CORE_PEER_LOCALMSPID} 审批 chaincode..."
${PEER_BIN} lifecycle chaincode approveformyorg \
    --orderer "${ORDERER_ADDR}" \
    --ordererTLSHostnameOverride orderer.erp.com \
    --channelID "${CHANNEL_NAME}" \
    --name "${CHAINCODE_NAME}" \
    --version "${CHAINCODE_VERSION}" \
    --package-id "${PACKAGE_ID}" \
    --sequence "${CHAINCODE_SEQUENCE}" \
    --tls \
    --cafile "${ORDERER_TLS_ROOTCERT}"

echo "  -> 审批完成"

# ------------------------------------------------------------
# 步骤 4：提交到通道
# ------------------------------------------------------------
echo ""
echo "[4/5] 提交 chaincode 到通道 ${CHANNEL_NAME}..."
${PEER_BIN} lifecycle chaincode commit \
    --orderer "${ORDERER_ADDR}" \
    --ordererTLSHostnameOverride orderer.erp.com \
    --channelID "${CHANNEL_NAME}" \
    --name "${CHAINCODE_NAME}" \
    --version "${CHAINCODE_VERSION}" \
    --sequence "${CHAINCODE_SEQUENCE}" \
    --tls \
    --cafile "${ORDERER_TLS_ROOTCERT}" \
    --peerAddresses "${CORE_PEER_ADDRESS}" \
    --tlsRootCertFiles "${CORE_PEER_TLS_ROOTCERT_FILE}"

echo "  -> 提交完成"

# ------------------------------------------------------------
# 步骤 5：验证部署
# ------------------------------------------------------------
echo ""
echo "[5/5] 验证部署..."
${PEER_BIN} lifecycle chaincode querycommitted \
    --channelID "${CHANNEL_NAME}" \
    --name "${CHAINCODE_NAME}"

echo ""
echo "============================================================"
echo "  Chaincode ${CHAINCODE_NAME}@${CHAINCODE_VERSION} 部署成功！"
echo "============================================================"
echo ""
echo "调用示例（初始化）："
echo "  peer chaincode invoke \\"
echo "    -o ${ORDERER_ADDR} --tls --cafile ${ORDERER_TLS_ROOTCERT} \\"
echo "    -C ${CHANNEL_NAME} -n ${CHAINCODE_NAME} \\"
echo "    --peerAddresses ${CORE_PEER_ADDRESS} \\"
echo "    --tlsRootCertFiles ${CORE_PEER_TLS_ROOTCERT_FILE} \\"
echo "    -c '{\"function\":\"GetChainStats\",\"Args\":[]}'"
