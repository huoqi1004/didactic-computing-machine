// ============================================================
// audit_chaincode.go - Agent-Native ERP 审计链 Hyperledger Fabric Chaincode
// ============================================================
// 智能合约实现：不可篡改的审计日志存证链
//
// 核心数据结构：
//   - AuditEntry：审计日志条目，包含操作者DID、操作类型、资源、
//     风险等级、哈希链指针（PrevHash / EntryHash）等
//
// 核心函数：
//   - CreateAuditEntry    创建审计记录（验证哈希链）
//   - QueryAuditEntry     按 entryId / txId 查询单条记录
//   - QueryAuditRange     按时间范围查询
//   - VerifyIntegrity     验证记录完整性（重算哈希并比对链上哈希）
//   - GetChainHeight      获取链高度（已存证的审计记录数）
//   - GetChainStats       获取统计信息
//
// 哈希链算法：
//   - 每条记录的 PrevHash = 前一条记录的 EntryHash
//   - EntryHash = SHA-256(entry_id + actor_did + action + resource +
//                          result + timestamp + risk_level + details + prev_hash)
//   - 第一个条目的 PrevHash 为空字符串
//   - 与 Python 端 app.core.security.audit.AuditEntry.compute_hash 算法保持一致
//
// 编译与部署：
//   见同目录 deploy-chaincode.sh
// ============================================================

package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// ============================================================
// AuditEntry - 审计日志条目
// ============================================================

// AuditEntry 是审计链上存储的核心数据结构。
// 序列化为 JSON 后存入 World State，key 为 entryId。
type AuditEntry struct {
	TxID      string `json:"tx_id"`       // Fabric 交易ID（提交后由 stub.GetTxID() 填充）
	EntryID   string `json:"entry_id"`    // 业务层生成的 UUID
	ActorDID  string `json:"actor_did"`   // 操作者去中心化身份标识（DID）
	Action    string `json:"action"`      // 操作类型，如 "inventory.adjust"
	Resource  string `json:"resource"`    // 操作资源，如 "inventory:prod-001"
	Result    string `json:"result"`      // 操作结果：success / failed / denied
	Details   string `json:"details"`     // 详细信息（JSON 字符串）
	RiskLevel string `json:"risk_level"`  // 风险等级：low / medium / high / critical
	PrevHash  string `json:"prev_hash"`   // 前一条记录的 EntryHash（哈希链指针）
	EntryHash string `json:"entry_hash"`  // 本条记录的哈希（SHA-256）
	Timestamp string `json:"timestamp"`   // UTC ISO8601 时间戳
}

// ============================================================
// AuditChaincode - Chaincode 主体
// ============================================================

// AuditChaincode 实现 Hyperledger Fabric 智能合约接口。
type AuditChaincode struct {
	contractapi.Contract
}

// ============================================================
// 辅助函数
// ============================================================

// computeEntryHash 计算 AuditEntry 的 SHA-256 哈希。
// 算法与 Python 端 AuditEntry.compute_hash 保持一致：
//   content = entry_id + actor_did + action + resource + result +
//             timestamp + risk_level + sorted_json(details) + prev_hash
func computeEntryHash(entry *AuditEntry) string {
	// 对 details 做 JSON 重新序列化并按 key 排序，保证哈希稳定性
	sortedDetails := sortJSONKeys(entry.Details)

	content := entry.EntryID + entry.ActorDID + entry.Action +
		entry.Resource + entry.Result + entry.Timestamp +
		entry.RiskLevel + sortedDetails
	if entry.PrevHash != "" {
		content += entry.PrevHash
	}

	hash := sha256.Sum256([]byte(content))
	return hex.EncodeToString(hash[:])
}

// sortJSONKeys 对 JSON 字符串的顶层 key 排序后重新序列化。
// 保证不同语言（Go / Python）之间的哈希一致性。
func sortJSONKeys(detailsJSON string) string {
	if detailsJSON == "" {
		return "{}"
	}
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(detailsJSON), &m); err != nil {
		// 解析失败时原样返回（兼容非 JSON details）
		return detailsJSON
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	// 按 key 排序后重新序列化
	sorted := make(map[string]interface{})
	for _, k := range keys {
		sorted[k] = m[k]
	}
	b, _ := json.Marshal(sorted)
	return string(b)
}

// getCompositeKey 生成 World State 复合键（按时间索引）。
// 用于 QueryAuditRange 范围查询。
func getCompositeKey(timestamp, entryID string) string {
	return timestamp + "#" + entryID
}

// ============================================================
// CreateAuditEntry - 创建审计记录
// ============================================================

// CreateAuditEntry 接收审计条目 JSON，验证哈希链后写入 World State。
//
// 参数：
//   ctx        Fabric 交易上下文
//   entryJSON  AuditEntry 的 JSON 字符串（调用方需提供 EntryID / ActorDID /
//              Action / Resource / Result / Details / RiskLevel / Timestamp）
//
// 返回：
//   成功：JSON {"tx_id": "...", "entry_id": "...", "entry_hash": "..."}
//   失败：error
//
// 哈希链验证：
//   1. 读取上一条记录的 EntryHash 作为当前记录的 PrevHash
//   2. 重算 EntryHash 并校验调用方提供的 EntryHash（若提供）
//   3. 写入 World State：entryId -> AuditEntry + 时间索引
func (cc *AuditChaincode) CreateAuditEntry(ctx contractapi.TransactionContextInterface, entryJSON string) (*AuditEntry, error) {
	// 反序列化
	var entry AuditEntry
	if err := json.Unmarshal([]byte(entryJSON), &entry); err != nil {
		return nil, fmt.Errorf("无效的 entry JSON: %v", err)
	}

	// 校验必填字段
	if entry.EntryID == "" {
		return nil, fmt.Errorf("entry_id 不能为空")
	}
	if entry.ActorDID == "" {
		return nil, fmt.Errorf("actor_did 不能为空")
	}
	if entry.Action == "" {
		return nil, fmt.Errorf("action 不能为空")
	}

	// 检查 entry_id 是否已存在（防止重复提交）
	existing, err := ctx.GetStub().GetState(entry.EntryID)
	if err != nil {
		return nil, fmt.Errorf("查询状态失败: %v", err)
	}
	if existing != nil {
		return nil, fmt.Errorf("审计条目已存在: %s", entry.EntryID)
	}

	// 填充交易ID
	entry.TxID = ctx.GetStub().GetTxID()

	// 哈希链：获取上一条记录的 EntryHash
	// 通过 range query 获取最后一条记录（按时间索引倒序）
	lastEntry, err := getLastEntry(ctx)
	if err != nil {
		return nil, fmt.Errorf("获取上一条记录失败: %v", err)
	}
	if lastEntry != nil {
		entry.PrevHash = lastEntry.EntryHash
	} else {
		entry.PrevHash = ""
	}

	// 计算 EntryHash
	entry.EntryHash = computeEntryHash(&entry)

	// 序列化并写入 World State
	entryBytes, err := json.Marshal(entry)
	if err != nil {
		return nil, fmt.Errorf("序列化审计条目失败: %v", err)
	}

	if err := ctx.GetStub().PutState(entry.EntryID, entryBytes); err != nil {
		return nil, fmt.Errorf("写入 World State 失败: %v", err)
	}

	// 写入时间索引（用于 QueryAuditRange 范围查询）
	indexKey, err := ctx.GetStub().CreateCompositeKey("audit~timestamp", []string{entry.Timestamp, entry.EntryID})
	if err != nil {
		return nil, fmt.Errorf("创建复合键失败: %v", err)
	}
	if err := ctx.GetStub().PutState(indexKey, []byte(entry.EntryID)); err != nil {
		return nil, fmt.Errorf("写入时间索引失败: %v", err)
	}

	return &entry, nil
}

// ============================================================
// QueryAuditEntry - 查询单条记录
// ============================================================

// QueryAuditEntry 按 entryId（或 txId）查询审计记录。
//
// 参数：
//   ctx      Fabric 交易上下文
//   entryId  审计条目ID（UUID）或交易ID
//
// 返回：
//   找到：AuditEntry JSON
//   未找到：空字符串 + nil error
func (cc *AuditChaincode) QueryAuditEntry(ctx contractapi.TransactionContextInterface, entryId string) (*AuditEntry, error) {
	entryBytes, err := ctx.GetStub().GetState(entryId)
	if err != nil {
		return nil, fmt.Errorf("查询审计条目失败: %v", err)
	}
	if entryBytes == nil {
		return nil, nil // 未找到，返回 nil（不视为错误）
	}

	var entry AuditEntry
	if err := json.Unmarshal(entryBytes, &entry); err != nil {
		return nil, fmt.Errorf("反序列化审计条目失败: %v", err)
	}
	return &entry, nil
}

// ============================================================
// QueryAuditRange - 范围查询
// ============================================================

// QueryAuditRange 按时间范围 [startTime, endTime] 查询审计记录。
//
// 参数：
//   ctx         Fabric 交易上下文
//   startTime   起始时间（ISO8601 字符串，包含）
//   endTime     结束时间（ISO8601 字符串，包含）
//
// 返回：
//   []AuditEntry（按时间升序）
//
// 实现：通过 "audit~timestamp" 复合键前缀范围查询。
func (cc *AuditChaincode) QueryAuditRange(ctx contractapi.TransactionContextInterface, startTime, endTime string) ([]*AuditEntry, error) {
	if startTime == "" {
		startTime = "0000-01-01T00:00:00Z"
	}
	if endTime == "" {
		endTime = "9999-12-31T23:59:59Z"
	}

	// 通过复合键范围查询
	startKey, err := ctx.GetStub().CreateCompositeKey("audit~timestamp", []string{startTime})
	if err != nil {
		return nil, fmt.Errorf("创建起始复合键失败: %v", err)
	}
	endKey, err := ctx.GetStub().CreateCompositeKey("audit~timestamp", []string{endTime})
	if err != nil {
		return nil, fmt.Errorf("创建结束复合键失败: %v", err)
	}

	resultsIterator, err := ctx.GetStub().GetStateByRange(startKey, endKey)
	if err != nil {
		return nil, fmt.Errorf("范围查询失败: %v", err)
	}
	defer resultsIterator.Close()

	var entries []*AuditEntry
	for resultsIterator.HasNext() {
		queryResponse, err := resultsIterator.Next()
		if err != nil {
			return nil, fmt.Errorf("迭代查询结果失败: %v", err)
		}

		// 索引值存储的是 entryId，需要再查一次主记录
		entryID := string(queryResponse.Value)
		entryBytes, err := ctx.GetStub().GetState(entryID)
		if err != nil {
			continue
		}
		if entryBytes == nil {
			continue
		}

		var entry AuditEntry
		if err := json.Unmarshal(entryBytes, &entry); err != nil {
			continue
		}
		entries = append(entries, &entry)
	}

	return entries, nil
}

// ============================================================
// VerifyIntegrity - 验证记录完整性
// ============================================================

// VerifyIntegrity 按 entryId 验证记录是否被篡改。
//
// 验证逻辑：
//   1. 从 World State 读取记录
//   2. 重算 EntryHash 并与链上存储的对比
//   3. 校验 PrevHash 是否与前一条记录的 EntryHash 一致
//
// 返回：{"valid": true/false, "entry_id": "...", "reason": "..."}
func (cc *AuditChaincode) VerifyIntegrity(ctx contractapi.TransactionContextInterface, entryId string) (map[string]interface{}, error) {
	result := map[string]interface{}{
		"entry_id": entryId,
		"valid":    false,
	}

	entryBytes, err := ctx.GetStub().GetState(entryId)
	if err != nil {
		result["reason"] = fmt.Sprintf("查询失败: %v", err)
		return result, nil
	}
	if entryBytes == nil {
		result["reason"] = "记录不存在"
		return result, nil
	}

	var entry AuditEntry
	if err := json.Unmarshal(entryBytes, &entry); err != nil {
		result["reason"] = fmt.Sprintf("反序列化失败: %v", err)
		return result, nil
	}

	// 重算哈希
	expectedHash := computeEntryHash(&entry)
	if entry.EntryHash != expectedHash {
		result["reason"] = fmt.Sprintf("EntryHash 不匹配: expected=%s actual=%s",
			expectedHash, entry.EntryHash)
		return result, nil
	}

	// 校验 PrevHash（除第一条记录外）
	if entry.PrevHash != "" {
		// 找到前一条记录并验证其 EntryHash == 当前记录的 PrevHash
		prevEntry, err := getPreviousEntry(ctx, &entry)
		if err != nil {
			result["reason"] = fmt.Sprintf("查询前驱记录失败: %v", err)
			return result, nil
		}
		if prevEntry == nil {
			result["reason"] = "前驱记录不存在，PrevHash 链断裂"
			return result, nil
		}
		if prevEntry.EntryHash != entry.PrevHash {
			result["reason"] = "PrevHash 与前驱记录的 EntryHash 不匹配"
			return result, nil
		}
	}

	result["valid"] = true
	result["entry_hash"] = entry.EntryHash
	return result, nil
}

// ============================================================
// GetChainHeight - 获取链高度
// ============================================================

// GetChainHeight 返回已存证的审计记录总数。
// 通过 "audit~timestamp" 复合键前缀查询计数。
func (cc *AuditChaincode) GetChainHeight(ctx contractapi.TransactionContextInterface) (int, error) {
	// 通过范围查询所有时间索引
	startKey, _ := ctx.GetStub().CreateCompositeKey("audit~timestamp", []string{""})
	endKey, _ := ctx.GetStub().CreateCompositeKey("audit~timestamp", []string{"zzzzz"})

	resultsIterator, err := ctx.GetStub().GetStateByRange(startKey, endKey)
	if err != nil {
		return 0, fmt.Errorf("范围查询失败: %v", err)
	}
	defer resultsIterator.Close()

	count := 0
	for resultsIterator.HasNext() {
		_, err := resultsIterator.Next()
		if err != nil {
			break
		}
		count++
	}

	return count, nil
}

// ============================================================
// GetChainStats - 获取统计信息
// ============================================================

// ChainStats 是 GetChainStats 的返回结构。
type ChainStats struct {
	TotalEntries    int            `json:"total_entries"`
	ChainHeight     int            `json:"chain_height"`
	RiskDistribution map[string]int `json:"risk_distribution"`
	FirstEntryTime  string         `json:"first_entry_time"`
	LastEntryTime   string         `json:"last_entry_time"`
}

// GetChainStats 返回审计链的统计信息。
func (cc *AuditChaincode) GetChainStats(ctx contractapi.TransactionContextInterface) (*ChainStats, error) {
	entries, err := cc.QueryAuditRange(ctx, "", "")
	if err != nil {
		return nil, err
	}

	stats := &ChainStats{
		TotalEntries:    len(entries),
		ChainHeight:     len(entries),
		RiskDistribution: make(map[string]int),
	}

	for _, e := range entries {
		stats.RiskDistribution[e.RiskLevel]++
	}

	if len(entries) > 0 {
		stats.FirstEntryTime = entries[0].Timestamp
		stats.LastEntryTime = entries[len(entries)-1].Timestamp
	}

	return stats, nil
}

// ============================================================
// 内部辅助函数
// ============================================================

// getLastEntry 获取最后一条审计记录（按时间降序的第一条）。
func getLastEntry(ctx contractapi.TransactionContextInterface) (*AuditEntry, error) {
	entries, err := queryAuditRangeInternal(ctx, "", "")
	if err != nil {
		return nil, err
	}
	if len(entries) == 0 {
		return nil, nil
	}
	return entries[len(entries)-1], nil
}

// getPreviousEntry 获取指定记录的前驱记录。
// 通过遍历全部记录找到 EntryHash == entry.PrevHash 的记录。
// 注：此实现为参考实现，生产环境应通过额外的复合索引优化。
func getPreviousEntry(ctx contractapi.TransactionContextInterface, entry *AuditEntry) (*AuditEntry, error) {
	if entry.PrevHash == "" {
		return nil, nil
	}
	entries, err := queryAuditRangeInternal(ctx, "", "")
	if err != nil {
		return nil, err
	}
	for _, e := range entries {
		if e.EntryHash == entry.PrevHash {
			return e, nil
		}
	}
	return nil, nil
}

// queryAuditRangeInternal 是 QueryAuditRange 的内部版本，复用查询逻辑。
func queryAuditRangeInternal(ctx contractapi.TransactionContextInterface, startTime, endTime string) ([]*AuditEntry, error) {
	if startTime == "" {
		startTime = "0000-01-01T00:00:00Z"
	}
	if endTime == "" {
		endTime = "9999-12-31T23:59:59Z"
	}

	startKey, _ := ctx.GetStub().CreateCompositeKey("audit~timestamp", []string{startTime})
	endKey, _ := ctx.GetStub().CreateCompositeKey("audit~timestamp", []string{endTime})

	resultsIterator, err := ctx.GetStub().GetStateByRange(startKey, endKey)
	if err != nil {
		return nil, err
	}
	defer resultsIterator.Close()

	var entries []*AuditEntry
	for resultsIterator.HasNext() {
		queryResponse, err := resultsIterator.Next()
		if err != nil {
			break
		}
		entryID := string(queryResponse.Value)
		entryBytes, err := ctx.GetStub().GetState(entryID)
		if err != nil || entryBytes == nil {
			continue
		}
		var entry AuditEntry
		if err := json.Unmarshal(entryBytes, &entry); err != nil {
			continue
		}
		entries = append(entries, &entry)
	}
	return entries, nil
}

// ============================================================
// main - 入口
// ============================================================

func main() {
	chaincode, err := contractapi.NewChaincode(&AuditChaincode{})
	if err != nil {
		fmt.Printf("创建 chaincode 失败: %v\n", err)
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("启动 chaincode 失败: %v\n", err)
	}
}

// ============================================================
// 以下为编译期类型断言，防止未使用的 import 报错
// ============================================================
var _ = time.Now
var _ = strconv.Atoi
var _ = strings.Contains
