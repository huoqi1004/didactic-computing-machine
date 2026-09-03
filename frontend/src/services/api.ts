import axios, { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios'
import type {
  Agent,
  AgentStatus,
  SystemStats,
  InventoryItem,
  InventoryStatus,
  RestockSuggestion,
  Voucher,
  VoucherStatus,
  AgentIdentity,
  AuditLog,
  AuditResult,
  SecurityStats,
  ChatAgent,
  ChatMessage,
  ConversationResponse,
  ExceptionItem,
  ExceptionStats,
} from '../stores/systemStore'
import { useAuthStore } from '../stores/authStore'

/* ===================== Axios 实例 ===================== */

const apiClient: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

/* ===================== JWT 认证拦截器 ===================== */

// 标记请求是否正在重试（避免 refresh 循环）
let isRefreshing = false
let refreshSubscribers: Array<(token: string | null) => void> = []

function onTokenRefreshed(token: string | null) {
  refreshSubscribers.forEach((cb) => cb(token))
  refreshSubscribers = []
}

function addRefreshSubscriber(cb: (token: string | null) => void) {
  refreshSubscribers.push(cb)
}

// 请求拦截器：自动注入 Authorization 头
apiClient.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().token
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

// 响应拦截器：统一处理 401 自动刷新 / 跳转登录
apiClient.interceptors.response.use(
  (response) => response.data,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    // 非 401 错误直接抛出
    if (error.response?.status !== 401) {
      return Promise.reject(error)
    }

    // 登录接口本身返回 401 → 不刷新，直接抛出
    if (originalRequest.url?.includes('/auth/login')) {
      return Promise.reject(error)
    }

    // 已在刷新中 → 排队等待
    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        addRefreshSubscriber((token) => {
          if (!token) {
            reject(error)
            return
          }
          originalRequest.headers.Authorization = `Bearer ${token}`
          resolve(apiClient(originalRequest))
        })
      })
    }

    // 首次 401 → 尝试刷新 Token
    originalRequest._retry = true
    isRefreshing = true

    try {
      const refreshToken = useAuthStore.getState().refreshToken

      const refreshResp = await axios.post('/api/v1/auth/refresh', null, {
        headers: {
          Authorization: `Bearer ${refreshToken}`,
        },
      })

      const newToken = refreshResp.data.access_token
      const authStore = useAuthStore.getState()

      // 更新 store 中的 token
      authStore.setAuth({
        access_token: newToken,
        refresh_token: authStore.refreshToken || undefined,
        user: authStore.user!,
      })

      // 通知排队的请求
      onTokenRefreshed(newToken)

      // 重发原始请求
      originalRequest.headers.Authorization = `Bearer ${newToken}`
      return apiClient(originalRequest)
    } catch (refreshError) {
      // 刷新失败 → 清除认证状态，跳转登录页
      onTokenRefreshed(null)
      useAuthStore.getState().logout()
      // 避免在已在此页面时死循环
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
      return Promise.reject(refreshError)
    } finally {
      isRefreshing = false
    }
  },
)

/* ===================== Mock 数据生成器 ===================== */
/* 后端不可用时自动降级到本地数据，保证前端可独立演示 */

const MOCK_AGENTS: Agent[] = [
  {
    id: 'inventory-agent',
    name: '库存管家',
    type: 'inventory',
    status: 'running',
    last_heartbeat: new Date().toISOString(),
    tasks_completed: 1284,
    tasks_failed: 23,
    success_rate: 98.2,
    cpu_usage: 34,
    memory_usage: 412,
    description: '负责库存监控、自动补货决策与出入库核对',
    capabilities: ['库存监控', '自动补货', '盘点核对', '预警通知'],
    mode: 'autonomous',
  },
  {
    id: 'accounting-agent',
    name: '会计专员',
    type: 'accounting',
    status: 'running',
    last_heartbeat: new Date().toISOString(),
    tasks_completed: 856,
    tasks_failed: 12,
    success_rate: 98.6,
    cpu_usage: 28,
    memory_usage: 356,
    description: '自动生成会计凭证、科目核对与税务计算',
    capabilities: ['凭证生成', '科目核对', '税务计算', '报表汇总'],
    mode: 'supervised',
  },
  {
    id: 'procurement-agent',
    name: '采购助手',
    type: 'procurement',
    status: 'running',
    last_heartbeat: new Date().toISOString(),
    tasks_completed: 432,
    tasks_failed: 8,
    success_rate: 98.2,
    cpu_usage: 22,
    memory_usage: 298,
    description: '供应商比价、采购订单生成与到货跟踪',
    capabilities: ['供应商比价', '订单生成', '到货跟踪', '合同审核'],
    mode: 'autonomous',
  },
  {
    id: 'risk-agent',
    name: '风控哨兵',
    type: 'risk',
    status: 'running',
    last_heartbeat: new Date().toISOString(),
    tasks_completed: 2103,
    tasks_failed: 5,
    success_rate: 99.8,
    cpu_usage: 41,
    memory_usage: 528,
    description: '实时风控监测、异常交易识别与熔断决策',
    capabilities: ['实时风控', '异常识别', '熔断决策', '风险评级'],
    mode: 'autonomous',
  },
  {
    id: 'settlement-agent',
    name: '结算管家',
    type: 'settlement',
    status: 'paused',
    last_heartbeat: new Date(Date.now() - 3600000).toISOString(),
    tasks_completed: 328,
    tasks_failed: 3,
    success_rate: 99.1,
    cpu_usage: 0,
    memory_usage: 0,
    description: '日清日结、应收应付对账与资金调度',
    capabilities: ['日清日结', '对账核销', '资金调度', '流水核对'],
    mode: 'supervised',
  },
  {
    id: 'data-sync-agent',
    name: '数据同步',
    type: 'sync',
    status: 'circuit_open',
    last_heartbeat: new Date(Date.now() - 7200000).toISOString(),
    tasks_completed: 5621,
    tasks_failed: 187,
    success_rate: 96.8,
    cpu_usage: 0,
    memory_usage: 0,
    description: '多系统数据同步、ETL 流水线与一致性校验',
    capabilities: ['数据同步', 'ETL处理', '一致性校验', '增量抽取'],
    mode: 'autonomous',
  },
  {
    id: 'audit-agent',
    name: '审计巡检',
    type: 'audit',
    status: 'idle',
    last_heartbeat: new Date(Date.now() - 1800000).toISOString(),
    tasks_completed: 156,
    tasks_failed: 1,
    success_rate: 99.4,
    cpu_usage: 5,
    memory_usage: 124,
    description: '合规审计、操作留痕与异常行为溯源',
    capabilities: ['合规审计', '行为溯源', '留痕校验', '报告生成'],
    mode: 'manual',
  },
]

const MOCK_INVENTORY: InventoryItem[] = [
  { id: '1', sku: 'SKU-A001', name: '高频处理器 X9', category: '电子元件', quantity: 1240, unit: '个', safety_stock: 500, max_stock: 3000, unit_price: 285.5, warehouse: '华东仓', status: 'normal' },
  { id: '2', sku: 'SKU-A002', name: '固态硬盘 1TB', category: '电子元件', quantity: 320, unit: '块', safety_stock: 400, max_stock: 2000, unit_price: 420.0, warehouse: '华东仓', status: 'low' },
  { id: '3', sku: 'SKU-B001', name: '铝合金外壳', category: '结构件', quantity: 5800, unit: '件', safety_stock: 1000, max_stock: 8000, unit_price: 38.0, warehouse: '华南仓', status: 'normal' },
  { id: '4', sku: 'SKU-B002', name: '钢化玻璃面板', category: '结构件', quantity: 0, unit: '片', safety_stock: 200, max_stock: 1500, unit_price: 95.0, warehouse: '华南仓', status: 'out' },
  { id: '5', sku: 'SKU-C001', name: '锂电池组 5000mAh', category: '能源组件', quantity: 860, unit: '组', safety_stock: 300, max_stock: 1200, unit_price: 78.0, warehouse: '华东仓', status: 'normal' },
  { id: '6', sku: 'SKU-C002', name: '电源适配器 65W', category: '能源组件', quantity: 210, unit: '个', safety_stock: 250, max_stock: 1000, unit_price: 45.0, warehouse: '华北仓', status: 'low' },
  { id: '7', sku: 'SKU-D001', name: '蓝牙模组 5.3', category: '电子元件', quantity: 3200, unit: '个', safety_stock: 800, max_stock: 2500, unit_price: 22.0, warehouse: '华东仓', status: 'overstock' },
  { id: '8', sku: 'SKU-D002', name: 'WiFi 天线阵列', category: '电子元件', quantity: 670, unit: '套', safety_stock: 200, max_stock: 1000, unit_price: 56.0, warehouse: '华北仓', status: 'normal' },
  { id: '9', sku: 'SKU-E001', name: '包装纸箱 A款', category: '包装材料', quantity: 12500, unit: '个', safety_stock: 2000, max_stock: 15000, unit_price: 3.5, warehouse: '华南仓', status: 'normal' },
  { id: '10', sku: 'SKU-E002', name: '防震泡沫内衬', category: '包装材料', quantity: 180, unit: '套', safety_stock: 500, max_stock: 3000, unit_price: 2.8, warehouse: '华南仓', status: 'low' },
  { id: '11', sku: 'SKU-F001', name: '精密螺丝 M3', category: '辅料', quantity: 45000, unit: '颗', safety_stock: 10000, max_stock: 60000, unit_price: 0.15, warehouse: '华东仓', status: 'normal' },
  { id: '12', sku: 'SKU-F002', name: '导热硅脂', category: '辅料', quantity: 95, unit: '管', safety_stock: 100, max_stock: 500, unit_price: 18.0, warehouse: '华北仓', status: 'low' },
]

const MOCK_VOUCHERS: Voucher[] = [
  { id: '1', voucher_no: '记-20260726-001', date: '2026-07-26', summary: '采购原材料-铝合金外壳', debit_account: '1403-原材料', credit_account: '2202-应付账款', amount: 220400.00, status: 'pending', created_by: 'procurement-agent', created_at: '2026-07-26T08:30:00Z' },
  { id: '2', voucher_no: '记-20260726-002', date: '2026-07-26', summary: '销售产品收入-X9系列', debit_account: '1122-应收账款', credit_account: '6001-主营业务收入', amount: 856000.00, status: 'pending', created_by: 'accounting-agent', created_at: '2026-07-26T09:15:00Z' },
  { id: '3', voucher_no: '记-20260726-003', date: '2026-07-26', summary: '支付员工工资', debit_account: '6602-管理费用', credit_account: '1002-银行存款', amount: 358000.00, status: 'pending', created_by: 'accounting-agent', created_at: '2026-07-26T10:00:00Z' },
  { id: '4', voucher_no: '记-20260725-018', date: '2026-07-25', summary: '仓储租金-华东仓', debit_account: '6601-销售费用', credit_account: '1002-银行存款', amount: 45000.00, status: 'approved', created_by: 'accounting-agent', approved_by: '财务主管', created_at: '2026-07-25T14:20:00Z' },
  { id: '5', voucher_no: '记-20260725-019', date: '2026-07-25', summary: '设备折旧计提', debit_account: '6602-管理费用', credit_account: '1602-累计折旧', amount: 28500.00, status: 'approved', created_by: 'accounting-agent', approved_by: '财务主管', created_at: '2026-07-25T16:00:00Z' },
  { id: '6', voucher_no: '记-20260725-020', date: '2026-07-25', summary: '增值税进项转出', debit_account: '2221-应交税费', credit_account: '1403-原材料', amount: 12400.00, status: 'rejected', created_by: 'accounting-agent', created_at: '2026-07-25T17:30:00Z' },
  { id: '7', voucher_no: '记-20260724-015', date: '2026-07-24', summary: '收到客户货款', debit_account: '1002-银行存款', credit_account: '1122-应收账款', amount: 520000.00, status: 'posted', created_by: 'settlement-agent', approved_by: '财务主管', created_at: '2026-07-24T11:00:00Z' },
  { id: '8', voucher_no: '记-20260724-016', date: '2026-07-24', summary: '办公用品采购', debit_account: '6602-管理费用', credit_account: '1001-库存现金', amount: 3200.00, status: 'posted', created_by: 'accounting-agent', approved_by: '财务主管', created_at: '2026-07-24T15:45:00Z' },
]

const MOCK_IDENTITIES: AgentIdentity[] = [
  { agent_id: 'inventory-agent', agent_name: '库存管家', identity_token: 'tok_inv_8f3a...d2e1', permissions: ['inventory:read', 'inventory:write', 'warehouse:read'], last_auth: new Date().toISOString(), auth_status: 'verified', risk_level: 'low' },
  { agent_id: 'accounting-agent', agent_name: '会计专员', identity_token: 'tok_acc_2b7c...9f4a', permissions: ['voucher:read', 'voucher:create', 'account:read'], last_auth: new Date().toISOString(), auth_status: 'verified', risk_level: 'low' },
  { agent_id: 'procurement-agent', agent_name: '采购助手', identity_token: 'tok_prc_5e1d...3c8b', permissions: ['purchase:read', 'purchase:write', 'supplier:read'], last_auth: new Date().toISOString(), auth_status: 'verified', risk_level: 'medium' },
  { agent_id: 'risk-agent', agent_name: '风控哨兵', identity_token: 'tok_rsk_a9f2...7e1c', permissions: ['risk:read', 'risk:block', 'transaction:freeze'], last_auth: new Date().toISOString(), auth_status: 'verified', risk_level: 'high' },
  { agent_id: 'settlement-agent', agent_name: '结算管家', identity_token: 'tok_set_c4d8...1b6f', permissions: ['settlement:read', 'settlement:execute', 'fund:transfer'], last_auth: new Date(Date.now() - 3600000).toISOString(), auth_status: 'verified', risk_level: 'medium' },
  { agent_id: 'data-sync-agent', agent_name: '数据同步', identity_token: 'tok_syn_f2a5...e8d3', permissions: ['data:read', 'data:sync', 'etl:execute'], last_auth: new Date(Date.now() - 7200000).toISOString(), auth_status: 'expired', risk_level: 'medium' },
  { agent_id: 'audit-agent', agent_name: '审计巡检', identity_token: 'tok_aud_3c7e...b5a9', permissions: ['audit:read', 'log:read', 'compliance:check'], last_auth: new Date(Date.now() - 1800000).toISOString(), auth_status: 'verified', risk_level: 'low' },
]

const MOCK_AUDIT_LOGS: AuditLog[] = [
  { id: '1', timestamp: new Date(Date.now() - 60000).toISOString(), agent_id: 'risk-agent', agent_name: '风控哨兵', action: 'transaction:freeze', resource: '订单 ORD-20260726-8892', ip_address: '10.0.4.21', result: 'success', details: '冻结异常高额订单，金额 ¥1,280,000' },
  { id: '2', timestamp: new Date(Date.now() - 180000).toISOString(), agent_id: 'inventory-agent', agent_name: '库存管家', action: 'inventory:write', resource: 'SKU-A002 固态硬盘', ip_address: '10.0.4.18', result: 'success', details: '自动触发补货建议，当前库存低于安全线' },
  { id: '3', timestamp: new Date(Date.now() - 360000).toISOString(), agent_id: 'accounting-agent', agent_name: '会计专员', action: 'voucher:create', resource: '凭证 记-20260726-003', ip_address: '10.0.4.19', result: 'success', details: '生成工资发放凭证，等待人工审批' },
  { id: '4', timestamp: new Date(Date.now() - 600000).toISOString(), agent_id: 'procurement-agent', agent_name: '采购助手', action: 'purchase:write', resource: '采购单 PO-20260726-045', ip_address: '10.0.4.20', result: 'success', details: '生成铝合金外壳采购订单 5800 件' },
  { id: '5', timestamp: new Date(Date.now() - 900000).toISOString(), agent_id: 'data-sync-agent', agent_name: '数据同步', action: 'data:sync', resource: 'WMS->ERP 同步通道', ip_address: '10.0.4.25', result: 'error', details: 'WMS 接口超时，触发熔断保护' },
  { id: '6', timestamp: new Date(Date.now() - 1200000).toISOString(), agent_id: 'risk-agent', agent_name: '风控哨兵', action: 'risk:block', resource: '供应商 SUP-0042', ip_address: '10.0.4.21', result: 'denied', details: '拦截高风险供应商入库操作，信用评分低于阈值' },
  { id: '7', timestamp: new Date(Date.now() - 1800000).toISOString(), agent_id: 'audit-agent', agent_name: '审计巡检', action: 'compliance:check', resource: '会计凭证合规审计', ip_address: '10.0.4.27', result: 'success', details: '完成 7 月份凭证合规性检查，发现 0 处违规' },
  { id: '8', timestamp: new Date(Date.now() - 2400000).toISOString(), agent_id: 'settlement-agent', agent_name: '结算管家', action: 'fund:transfer', resource: '银行账户 ****8821', ip_address: '10.0.4.23', result: 'denied', details: '非工作时间段大额转账被拒绝，需人工复核' },
  { id: '9', timestamp: new Date(Date.now() - 3600000).toISOString(), agent_id: 'inventory-agent', agent_name: '库存管家', action: 'inventory:write', resource: 'SKU-B002 钢化玻璃面板', ip_address: '10.0.4.18', result: 'success', details: '记录出库 200 片，库存归零' },
  { id: '10', timestamp: new Date(Date.now() - 5400000).toISOString(), agent_id: 'procurement-agent', agent_name: '采购助手', action: 'supplier:read', resource: '供应商数据库', ip_address: '10.0.4.20', result: 'success', details: '执行供应商比价，筛选 3 家优质供应商' },
]

function buildMockStats(): SystemStats {
  const running = MOCK_AGENTS.filter((a) => a.status === 'running').length
  const paused = MOCK_AGENTS.filter((a) => a.status === 'paused').length
  const circuit = MOCK_AGENTS.filter((a) => a.status === 'circuit_open').length
  const totalTasks = MOCK_AGENTS.reduce((s, a) => s + a.tasks_completed, 0)
  return {
    total_agents: MOCK_AGENTS.length,
    active_agents: running,
    paused_agents: paused,
    circuit_open_agents: circuit,
    total_events: 18432,
    events_per_minute: 47,
    total_tasks_today: totalTasks,
    success_rate: 98.4,
    avg_response_time: 128,
    system_uptime: 99.97,
    inventory_value: MOCK_INVENTORY.reduce((s, i) => s + i.quantity * i.unit_price, 0),
    pending_vouchers: MOCK_VOUCHERS.filter((v) => v.status === 'pending').length,
  }
}

function buildMockRestock(): RestockSuggestion[] {
  return [
    { sku: 'SKU-A002', name: '固态硬盘 1TB', current_qty: 320, suggested_qty: 800, reason: '低于安全库存线 400，预计 3 天内售罄', estimated_cost: 336000 },
    { sku: 'SKU-B002', name: '钢化玻璃面板', current_qty: 0, suggested_qty: 1000, reason: '库存归零，紧急补货', estimated_cost: 95000 },
    { sku: 'SKU-C002', name: '电源适配器 65W', current_qty: 210, suggested_qty: 500, reason: '低于安全库存线 250', estimated_cost: 22500 },
    { sku: 'SKU-E002', name: '防震泡沫内衬', current_qty: 180, suggested_qty: 2000, reason: '低于安全库存线 500', estimated_cost: 5600 },
    { sku: 'SKU-F002', name: '导热硅脂', current_qty: 95, suggested_qty: 300, reason: '低于安全库存线 100', estimated_cost: 5400 },
  ]
}

function buildMockSecurityStats(): SecurityStats {
  return {
    total_identities: MOCK_IDENTITIES.length,
    verified_identities: MOCK_IDENTITIES.filter((i) => i.auth_status === 'verified').length,
    denied_actions_today: MOCK_AUDIT_LOGS.filter((l) => l.result === 'denied').length,
    high_risk_agents: MOCK_IDENTITIES.filter((i) => i.risk_level === 'high').length,
    security_events: 3,
    last_breach: null,
  }
}

/* ===================== 对话式操作 Mock 数据 ===================== */

const MOCK_CHAT_AGENTS: ChatAgent[] = [
  {
    id: 'inventory-agent',
    name: '库存管家',
    type: 'inventory',
    status: 'online',
    icon: 'Package',
    description: '负责库存监控、自动补货决策与出入库核对，可查询实时库存与预警',
    capabilities: ['库存查询', '补货建议', '盘点核对', '预警通知'],
    quickCommands: ['查询库存状态', '查看补货建议', '检查低库存预警', '导出库存报表'],
  },
  {
    id: 'accounting-agent',
    name: '会计专员',
    type: 'accounting',
    status: 'online',
    icon: 'Calculator',
    description: '自动生成会计凭证、科目核对与税务计算，支持凭证审批与日结',
    capabilities: ['凭证生成', '凭证审批', '科目核对', '日清日结'],
    quickCommands: ['查询待审批凭证', '生成日报', '执行日清日结', '科目余额核对'],
  },
  {
    id: 'procurement-agent',
    name: '采购助手',
    type: 'procurement',
    status: 'online',
    icon: 'ShoppingCart',
    description: '供应商比价、采购订单生成与到货跟踪，可发起采购审批',
    capabilities: ['供应商比价', '采购下单', '到货跟踪', '合同审核'],
    quickCommands: ['查询采购订单', '供应商比价', '发起采购审批', '到货状态跟踪'],
  },
  {
    id: 'risk-agent',
    name: '风控哨兵',
    type: 'risk',
    status: 'busy',
    icon: 'ShieldAlert',
    description: '实时风控监测、异常交易识别与熔断决策，检查合规风险',
    capabilities: ['实时风控', '异常识别', '熔断决策', '合规检查'],
    quickCommands: ['检查合规风险', '查看异常交易', '风控状态总览', '熔断记录查询'],
  },
  {
    id: 'settlement-agent',
    name: '结算管家',
    type: 'settlement',
    status: 'offline',
    icon: 'Wallet',
    description: '日清日结、应收应付对账与资金调度，当前处于离线状态',
    capabilities: ['日清日结', '对账核销', '资金调度', '流水核对'],
    quickCommands: ['对账状态查询', '资金流水核对', '应收应付统计'],
  },
  {
    id: 'audit-agent',
    name: '审计巡检',
    type: 'audit',
    status: 'online',
    icon: 'Search',
    description: '合规审计、操作留痕与异常行为溯源，生成审计报告',
    capabilities: ['合规审计', '行为溯源', '留痕校验', '报告生成'],
    quickCommands: ['生成审计报告', '检查操作留痕', '合规审计', '异常行为溯源'],
  },
]

/* 预置示例对话（首次访问时作为历史展示） */
const PRESET_CONVERSATIONS: Record<string, ChatMessage[]> = {
  'inventory-agent': [
    {
      id: 'msg-preset-inv-1',
      role: 'user',
      agentId: 'inventory-agent',
      agentName: '当前用户',
      content: '查询库存状态',
      timestamp: new Date(Date.now() - 3600000).toISOString(),
      status: 'sent',
    },
    {
      id: 'msg-preset-inv-2',
      role: 'agent',
      agentId: 'inventory-agent',
      agentName: '库存管家',
      content:
        '已为您查询当前库存状态。系统共监控 12 个品类，其中 4 项低于安全库存，1 项已缺货，1 项出现积压。建议优先处理钢化玻璃面板的紧急补货。',
      timestamp: new Date(Date.now() - 3540000).toISOString(),
      thinkingProcess: [
        '解析用户意图：查询库存整体状态',
        '调用 inventory.list() 获取全部 12 个 SKU',
        '按状态分类统计：normal 6 / low 4 / out 1 / overstock 1',
        '计算库存总价值 ¥1,892,450.50',
        '识别最高优先级：SKU-B002 钢化玻璃面板库存归零',
      ],
      dataCard: {
        type: 'stats',
        title: '库存状态总览',
        stats: [
          { label: '库存品类', value: '12', tone: 'primary' },
          { label: '库存总价值', value: '¥1,892,450', tone: 'accent' },
          { label: '预警品类', value: '5', tone: 'paused' },
          { label: '缺货品类', value: '1', tone: 'circuit' },
        ],
      },
      status: 'sent',
    },
    {
      id: 'msg-preset-inv-3',
      role: 'user',
      agentId: 'inventory-agent',
      agentName: '当前用户',
      content: '查看补货建议',
      timestamp: new Date(Date.now() - 3000000).toISOString(),
      status: 'sent',
    },
    {
      id: 'msg-preset-inv-4',
      role: 'agent',
      agentId: 'inventory-agent',
      agentName: '库存管家',
      content:
        '根据销量预测与安全库存模型，生成 5 条补货建议。其中钢化玻璃面板为紧急补货（库存归零），固态硬盘 1TB 为高优先级（预计 3 天内售罄）。预计总采购金额 ¥464,500。',
      timestamp: new Date(Date.now() - 2940000).toISOString(),
      thinkingProcess: [
        '加载 reorder_engine 模块，读取最近 30 天销量',
        '对每个 SKU 计算再订货点 ROP = 安全库存 + 日均销量 × 交货周期',
        '识别 5 个 SKU 低于 ROP：SKU-A002 / SKU-B002 / SKU-C002 / SKU-E002 / SKU-F002',
        '调用 supplier.quote() 获取最优报价',
        '生成补货建议清单，按紧急度排序',
      ],
      dataCard: {
        type: 'table',
        title: '智能补货建议',
        columns: [
          { key: 'sku', label: 'SKU' },
          { key: 'name', label: '品名' },
          { key: 'current', label: '当前库存' },
          { key: 'suggest', label: '建议补货' },
          { key: 'cost', label: '预计金额' },
        ],
        rows: [
          { sku: 'SKU-B002', name: '钢化玻璃面板', current: 0, suggest: 1000, cost: '¥95,000' },
          { sku: 'SKU-A002', name: '固态硬盘 1TB', current: 320, suggest: 800, cost: '¥336,000' },
          { sku: 'SKU-C002', name: '电源适配器 65W', current: 210, suggest: 500, cost: '¥22,500' },
          { sku: 'SKU-E002', name: '防震泡沫内衬', current: 180, suggest: 2000, cost: '¥5,600' },
          { sku: 'SKU-F002', name: '导热硅脂', current: 95, suggest: 300, cost: '¥5,400' },
        ],
      },
      status: 'sent',
    },
  ],
  'accounting-agent': [
    {
      id: 'msg-preset-acc-1',
      role: 'user',
      agentId: 'accounting-agent',
      agentName: '当前用户',
      content: '查询待审批凭证',
      timestamp: new Date(Date.now() - 1800000).toISOString(),
      status: 'sent',
    },
    {
      id: 'msg-preset-acc-2',
      role: 'agent',
      agentId: 'accounting-agent',
      agentName: '会计专员',
      content:
        '当前共有 3 张待审批凭证，合计金额 ¥1,434,400。其中工资发放凭证金额最大（¥358,000），已通过科目核对，等待财务主管审批。所有凭证借贷平衡，税务计算无误。',
      timestamp: new Date(Date.now() - 1740000).toISOString(),
      thinkingProcess: [
        '调用 voucher.list(status=pending) 获取待审批凭证',
        '校验每张凭证借贷平衡：借方合计 = 贷方合计',
        '执行税务计算校验：增值税、附加税无误',
        '按金额降序排列，标记大额凭证',
        '生成审批建议：3 张凭证均通过自动校验',
      ],
      dataCard: {
        type: 'table',
        title: '待审批凭证清单',
        columns: [
          { key: 'no', label: '凭证号' },
          { key: 'summary', label: '摘要' },
          { key: 'amount', label: '金额' },
          { key: 'status', label: '校验' },
        ],
        rows: [
          { no: '记-20260726-002', summary: '销售产品收入-X9系列', amount: '¥856,000', status: '通过' },
          { no: '记-20260726-003', summary: '支付员工工资', amount: '¥358,000', status: '通过' },
          { no: '记-20260726-001', summary: '采购原材料-铝合金外壳', amount: '¥220,400', status: '通过' },
        ],
      },
      status: 'sent',
    },
  ],
  'procurement-agent': [
    {
      id: 'msg-preset-prc-1',
      role: 'user',
      agentId: 'procurement-agent',
      agentName: '当前用户',
      content: '查看供应商比价结果',
      timestamp: new Date(Date.now() - 900000).toISOString(),
      status: 'sent',
    },
    {
      id: 'msg-preset-prc-2',
      role: 'agent',
      agentId: 'procurement-agent',
      agentName: '采购助手',
      content:
        '已完成铝合金外壳（SKU-B001）5800 件的供应商比价。综合价格、交货周期与历史评级，推荐「华南金属制品厂」作为供应商，单价 ¥36.5/件，较上次采购降低 3.9%，预计节省 ¥6,960。已生成采购订单草稿 PO-20260726-045，等待审批。',
      timestamp: new Date(Date.now() - 840000).toISOString(),
      thinkingProcess: [
        '解析采购需求：铝合金外壳 5800 件',
        '调用 supplier.list(category=结构件) 获取 8 家供应商',
        '执行多维度评分：价格(40%) + 交货(30%) + 质量(20%) + 信用(10%)',
        '华南金属制品厂综合得分 92.4 分，排名第一',
        '对比历史采购价 ¥38/件，本次降幅 3.9%',
        '生成采购订单草稿，提交审批流程',
      ],
      dataCard: {
        type: 'list',
        title: '供应商比价排名',
        items: [
          { label: '华南金属制品厂', value: '¥36.5/件 · 交货 5 天 · 评分 92.4' },
          { label: '华东铝业集团', value: '¥37.2/件 · 交货 3 天 · 评分 88.1' },
          { label: '深圳精密五金', value: '¥35.8/件 · 交货 8 天 · 评分 85.6' },
        ],
      },
      status: 'sent',
    },
  ],
}

/** 根据关键词生成 Mock 对话回复 */
function buildMockConversationResponse(agentId: string, message: string): ConversationResponse {
  const lower = message.toLowerCase()

  // 库存相关
  if (agentId === 'inventory-agent') {
    // 预测类（优先匹配，避免被"库存"关键词吞没）
    if (lower.includes('预测') || lower.includes('预估') || lower.includes('趋势') || lower.includes('下季度') || lower.includes('下月') || lower.includes('未来')) {
      return {
        content:
          '基于历史消耗趋势与季节性分析，下季度库存预测如下：\n1. 钢化玻璃面板：当前已缺货，预计下季度需求1500件，建议立即补货1000件；\n2. 固态硬盘1TB：当前320件低于安全库存500件，预计下季度消耗800件，建议补货800件；\n3. 电源适配器65W：当前210件，预计下季度消耗400件，建议补货500件；\n4. 处理器与内存条：库存充足，预计下季度平稳消耗，无需补货。综合预测下季度库存周转率将提升8%。',
        thinkingProcess: [
          '识别意图：库存预测（非实时查询）',
          '加载历史消耗数据与季节性因子',
          '对每个品类运行趋势预测模型',
          '生成下季度需求预测与补货建议',
        ],
        dataCard: {
          type: 'table',
          title: '下季度库存预测',
          columns: [
            { key: 'name', label: '品类' },
            { key: 'current', label: '当前库存' },
            { key: 'predicted', label: '预测需求' },
            { key: 'action', label: '建议操作' },
          ],
          rows: [
            { name: '钢化玻璃面板', current: '0', predicted: '1500', action: '紧急补货1000件' },
            { name: '固态硬盘1TB', current: '320', predicted: '800', action: '补货800件' },
            { name: '电源适配器65W', current: '210', predicted: '400', action: '补货500件' },
            { name: '处理器', current: '320', predicted: '250', action: '无需补货' },
          ],
        },
      }
    }
    // 补货类（在预测之后、查询之前匹配）
    if (lower.includes('补货') || lower.includes('建议') || lower.includes('采购') || lower.includes('进货')) {
      return {
        content:
          '根据销量预测模型，生成 5 条补货建议。钢化玻璃面板为紧急补货，预计总采购金额 ¥464,500。',
        thinkingProcess: [
          '加载 reorder_engine 模块',
          '计算每个 SKU 的再订货点 ROP',
          '识别 5 个 SKU 低于 ROP',
          '调用 supplier.quote() 获取报价',
          '生成补货建议清单',
        ],
        dataCard: {
          type: 'table',
          title: '智能补货建议',
          columns: [
            { key: 'sku', label: 'SKU' },
            { key: 'name', label: '品名' },
            { key: 'current', label: '当前库存' },
            { key: 'suggest', label: '建议补货' },
            { key: 'cost', label: '预计金额' },
          ],
          rows: [
            { sku: 'SKU-B002', name: '钢化玻璃面板', current: 0, suggest: 1000, cost: '¥95,000' },
            { sku: 'SKU-A002', name: '固态硬盘 1TB', current: 320, suggest: 800, cost: '¥336,000' },
            { sku: 'SKU-C002', name: '电源适配器 65W', current: 210, suggest: 500, cost: '¥22,500' },
          ],
        },
      }
    }
    // 查询类（最宽泛，放最后）
    if (lower.includes('库存') || lower.includes('状态') || lower.includes('查询') || lower.includes('查看') || lower.includes('多少')) {
      return {
        content:
          '已为您查询当前库存状态。系统共监控 12 个品类，其中 4 项低于安全库存，1 项已缺货。库存总价值 ¥1,892,450.50。',
        thinkingProcess: [
          '解析用户意图：查询库存状态',
          '调用 inventory.list() 获取全部 SKU',
          '按状态分类统计并汇总',
          '生成库存总览数据卡片',
        ],
        dataCard: {
          type: 'stats',
          title: '库存状态总览',
          stats: [
            { label: '库存品类', value: '12', tone: 'primary' },
            { label: '库存总价值', value: '¥1,892,450', tone: 'accent' },
            { label: '预警品类', value: '5', tone: 'paused' },
            { label: '缺货品类', value: '1', tone: 'circuit' },
          ],
        },
      }
    }
    return {
      content:
        '我已收到您的指令。作为库存管家，我可以帮您查询库存状态、生成补货建议、盘点核对或设置预警。请问您需要哪方面的协助？',
      thinkingProcess: ['解析用户输入', '匹配库存管理能力域', '未命中具体意图，返回能力引导'],
    }
  }

  // 会计相关
  if (agentId === 'accounting-agent') {
    if (lower.includes('凭证') || lower.includes('审批')) {
      return {
        content:
          '当前共有 3 张待审批凭证，合计金额 ¥1,434,400。所有凭证借贷平衡，税务计算无误，可进入审批流程。',
        thinkingProcess: [
          '调用 voucher.list(status=pending)',
          '校验借贷平衡与税务计算',
          '按金额降序排列',
          '生成审批建议',
        ],
        dataCard: {
          type: 'table',
          title: '待审批凭证清单',
          columns: [
            { key: 'no', label: '凭证号' },
            { key: 'summary', label: '摘要' },
            { key: 'amount', label: '金额' },
            { key: 'status', label: '校验' },
          ],
          rows: [
            { no: '记-20260726-002', summary: '销售产品收入-X9系列', amount: '¥856,000', status: '通过' },
            { no: '记-20260726-003', summary: '支付员工工资', amount: '¥358,000', status: '通过' },
            { no: '记-20260726-001', summary: '采购原材料-铝合金外壳', amount: '¥220,400', status: '通过' },
          ],
        },
      }
    }
    if (lower.includes('日报') || lower.includes('日结') || lower.includes('结算')) {
      return {
        content:
          '今日日报已生成。本日共处理凭证 8 张，其中已过账 2 张、待审批 3 张。主营业务收入 ¥856,000，管理费用 ¥386,200，净利润 ¥469,800。日清日结流程已就绪，是否执行？',
        thinkingProcess: [
          '汇总当日所有凭证数据',
          '按科目分类计算收支',
          '生成利润概算',
          '检查日清日结前置条件',
        ],
        dataCard: {
          type: 'stats',
          title: '今日财务日报',
          stats: [
            { label: '凭证总数', value: '8', tone: 'primary' },
            { label: '主营业务收入', value: '¥856,000', tone: 'accent' },
            { label: '管理费用', value: '¥386,200', tone: 'paused' },
            { label: '净利润', value: '¥469,800', tone: 'accent' },
          ],
        },
      }
    }
    return {
      content:
        '我已收到您的指令。作为会计专员，我可以协助凭证审批、科目核对、税务计算与日清日结。请问您需要什么帮助？',
      thinkingProcess: ['解析用户输入', '匹配会计能力域', '返回能力引导'],
    }
  }

  // 风控相关
  if (agentId === 'risk-agent') {
    if (lower.includes('合规') || lower.includes('检查')) {
      return {
        content:
          '合规检查完成。本月共审计 247 项操作，发现 2 处合规风险：1 项非工作时间段大额转账未复核、1 项供应商信用评分低于阈值仍入库。建议立即处理。',
        thinkingProcess: [
          '加载 compliance_rules 规则引擎',
          '扫描本月全部 247 项操作记录',
          '匹配 18 条合规规则',
          '识别 2 处违规：资金操作 + 供应商准入',
          '生成合规风险报告',
        ],
        dataCard: {
          type: 'stats',
          title: '合规审计结果',
          stats: [
            { label: '审计操作数', value: '247', tone: 'primary' },
            { label: '合规通过', value: '245', tone: 'accent' },
            { label: '合规风险', value: '2', tone: 'circuit' },
            { label: '合规率', value: '99.2%', tone: 'accent' },
          ],
        },
      }
    }
    return {
      content:
        '风控哨兵正在值守。当前风控引擎运行正常，今日已拦截 3 笔异常交易，冻结 1 个高风险订单。可为您检查合规风险或查询异常交易明细。',
      thinkingProcess: ['加载风控仪表盘', '汇总今日风控事件', '返回状态总览'],
    }
  }

  // 采购相关
  if (agentId === 'procurement-agent') {
    return {
      content:
        '采购助手已就绪。我可以进行供应商比价、采购下单、到货跟踪与合同审核。当前有 1 笔采购订单待审批（PO-20260726-045，铝合金外壳 5800 件）。',
      thinkingProcess: ['查询待办采购订单', '返回采购状态总览'],
    }
  }

  // 审计相关
  if (agentId === 'audit-agent') {
    return {
      content:
        '审计巡检报告已生成。7 月份共完成 3 轮合规审计，操作留痕完整率 100%，未发现数据篡改。详细报告可导出查看。',
      thinkingProcess: ['加载审计日志', '校验操作留痕完整性', '生成审计摘要'],
    }
  }

  // 默认回复
  return {
    content: '我已收到您的消息，正在处理中。请问还有什么可以帮您？',
    thinkingProcess: ['解析用户输入', '未匹配具体能力', '返回默认响应'],
  }
}

/* ===================== 例外管理 Mock 数据 ===================== */

const now = Date.now()
const minutesAgo = (m: number) => new Date(now - m * 60000).toISOString()
const hoursAgo = (h: number) => new Date(now - h * 3600000).toISOString()

const MOCK_EXCEPTIONS: ExceptionItem[] = [
  {
    id: 'EX-20260726-001',
    type: 'approval',
    category: 'restock',
    title: '紧急补货审批：钢化玻璃面板库存归零',
    sourceAgent: '库存管家',
    sourceAgentId: 'inventory-agent',
    riskLevel: 'high',
    status: 'pending',
    createdAt: minutesAgo(18),
    description: 'SKU-B002 钢化玻璃面板库存已归零，影响 X9 系列产品组装产线。库存管家 Agent 建议紧急采购 1000 件，预计金额 ¥95,000。该物料为关键结构件，缺货将导致产线停工。',
    context: {
      summary: '关键结构件库存归零，影响主线产品组装',
      fields: [
        { label: 'SKU', value: 'SKU-B002' },
        { label: '品名', value: '钢化玻璃面板' },
        { label: '当前库存', value: '0 件' },
        { label: '安全库存', value: '200 件' },
        { label: '建议采购量', value: '1000 件' },
        { label: '预计金额', value: '¥95,000' },
        { label: '影响产线', value: 'X9 系列组装线' },
        { label: '推荐供应商', value: '华南玻璃制品厂' },
      ],
      details: '该物料为 X9 系列终端产品的关键外观件，当前产线日消耗量约 80 件。库存归零将导致 2 小时后产线停工，预计每小时损失 ¥12,000。',
    },
    decisionTrace: {
      summary: '库存管家 Agent 通过安全库存模型与产线消耗速率分析，判定为紧急补货场景',
      steps: [
        { step: 1, reasoning: '检测到 SKU-B002 库存归零', evidence: 'WMS 实时数据：quantity=0' },
        { step: 2, reasoning: '查询物料属性：属关键结构件，影响主线产品', evidence: 'BOM 清单：X9 系列」钢化玻璃面板' },
        { step: 3, reasoning: '计算产线影响：日消耗 80 件，2 小时后停工', evidence: 'MES 产线排程数据' },
        { step: 4, reasoning: '调用补货模型：建议采购 1000 件（5 倍安全库存）', evidence: 'reorder_engine 输出' },
        { step: 5, reasoning: '匹配最优供应商并生成采购草稿', evidence: 'supplier.quote() 结果' },
        { step: 6, reasoning: '风险等级评定为 HIGH，触发人工审批', evidence: '金额 > ¥50,000 且影响产线' },
      ],
      conclusion: '建议立即批准紧急补货 1000 件，预计 5 小时内到货，可避免产线停工',
      confidence: 96,
    },
    eventChain: [
      { id: 'evt-1', timestamp: minutesAgo(120), agentId: 'inventory-agent', agentName: '库存管家', action: '库存预警', detail: 'SKU-B002 库存低于安全线 200', level: 'warning' },
      { id: 'evt-2', timestamp: minutesAgo(60), agentId: 'inventory-agent', agentName: '库存管家', action: '出库记录', detail: '产线领用 200 件，库存归零', level: 'error' },
      { id: 'evt-3', timestamp: minutesAgo(45), agentId: 'inventory-agent', agentName: '库存管家', action: '补货计算', detail: '触发 reorder_engine，建议采购 1000 件', level: 'info' },
      { id: 'evt-4', timestamp: minutesAgo(18), agentId: 'inventory-agent', agentName: '库存管家', action: '创建审批', detail: '生成例外事项 EX-20260726-001', level: 'warning' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: minutesAgo(18), operator: '库存管家 Agent', action: '创建例外', result: 'success', remark: '自动触发紧急补货审批' },
    ],
  },
  {
    id: 'EX-20260726-002',
    type: 'approval',
    category: 'restock',
    title: '补货审批：固态硬盘 1TB 库存偏低',
    sourceAgent: '库存管家',
    sourceAgentId: 'inventory-agent',
    riskLevel: 'medium',
    status: 'pending',
    createdAt: minutesAgo(42),
    description: 'SKU-A002 固态硬盘 1TB 当前库存 320 块，低于安全库存 400 块，预计 3 天内售罄。建议补货 800 块，预计金额 ¥336,000。',
    context: {
      summary: '高价值电子元件库存偏低，需及时补货',
      fields: [
        { label: 'SKU', value: 'SKU-A002' },
        { label: '品名', value: '固态硬盘 1TB' },
        { label: '当前库存', value: '320 块' },
        { label: '安全库存', value: '400 块' },
        { label: '日均消耗', value: '105 块/天' },
        { label: '建议采购量', value: '800 块' },
        { label: '预计金额', value: '¥336,000' },
        { label: '推荐供应商', value: '深圳存储科技' },
      ],
    },
    decisionTrace: {
      summary: '基于销量预测与安全库存模型的中等优先级补货建议',
      steps: [
        { step: 1, reasoning: '检测到库存 320 < 安全库存 400', evidence: 'WMS 实时数据' },
        { step: 2, reasoning: '销量预测：日均 105 块，3 天内售罄', evidence: '近 30 天销量数据' },
        { step: 3, reasoning: '计算经济订货量 EOQ = 800 块', evidence: 'EOQ 模型输出' },
        { step: 4, reasoning: '风险评定为 MEDIUM（金额较大但不影响产线）', evidence: '非关键路径物料' },
      ],
      conclusion: '建议批准补货 800 块，3 天内到货可覆盖未来 7 天消耗',
      confidence: 91,
    },
    eventChain: [
      { id: 'evt-1', timestamp: hoursAgo(3), agentId: 'inventory-agent', agentName: '库存管家', action: '库存预警', detail: 'SKU-A002 低于安全库存', level: 'warning' },
      { id: 'evt-2', timestamp: minutesAgo(50), agentId: 'inventory-agent', agentName: '库存管家', action: '补货计算', detail: 'EOQ 模型建议 800 块', level: 'info' },
      { id: 'evt-3', timestamp: minutesAgo(42), agentId: 'inventory-agent', agentName: '库存管家', action: '创建审批', detail: '生成例外事项 EX-20260726-002', level: 'warning' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: minutesAgo(42), operator: '库存管家 Agent', action: '创建例外', result: 'success' },
    ],
  },
  {
    id: 'EX-20260726-003',
    type: 'approval',
    category: 'voucher',
    title: '凭证审批：工资发放凭证 ¥358,000',
    sourceAgent: '会计专员',
    sourceAgentId: 'accounting-agent',
    riskLevel: 'medium',
    status: 'pending',
    createdAt: minutesAgo(75),
    description: '记-20260726-003 工资发放凭证，金额 ¥358,000，借方 6602-管理费用，贷方 1002-银行存款。已通过借贷平衡校验与税务计算，等待财务主管审批。',
    context: {
      summary: '大额工资发放凭证，需人工复核后过账',
      fields: [
        { label: '凭证号', value: '记-20260726-003' },
        { label: '摘要', value: '支付员工工资' },
        { label: '金额', value: '¥358,000' },
        { label: '借方科目', value: '6602-管理费用' },
        { label: '贷方科目', value: '1002-银行存款' },
        { label: '制单人', value: '会计专员 Agent' },
        { label: '借贷校验', value: '平衡' },
        { label: '税务校验', value: '通过' },
      ],
    },
    decisionTrace: {
      summary: '会计专员 Agent 自动生成工资凭证并完成校验，因金额超阈值触发人工审批',
      steps: [
        { step: 1, reasoning: '读取 HR 系统工资数据，合计 ¥358,000', evidence: 'HR API 返回值' },
        { step: 2, reasoning: '生成会计分录：借 管理费用 / 贷 银行存款', evidence: '会计科目映射规则' },
        { step: 3, reasoning: '借贷平衡校验：借方 = 贷方 = ¥358,000', evidence: 'balance_check 通过' },
        { step: 4, reasoning: '个税计算校验：已代扣个税 ¥42,300', evidence: 'tax_engine 输出' },
        { step: 5, reasoning: '金额 > ¥100,000，触发人工审批流程', evidence: '审批规则 R-007' },
      ],
      conclusion: '凭证校验全部通过，建议批准过账',
      confidence: 99,
    },
    eventChain: [
      { id: 'evt-1', timestamp: hoursAgo(2), agentId: 'accounting-agent', agentName: '会计专员', action: '数据获取', detail: '从 HR 系统拉取工资数据', level: 'info' },
      { id: 'evt-2', timestamp: minutesAgo(80), agentId: 'accounting-agent', agentName: '会计专员', action: '凭证生成', detail: '自动生成工资发放凭证', level: 'success' },
      { id: 'evt-3', timestamp: minutesAgo(78), agentId: 'accounting-agent', agentName: '会计专员', action: '校验通过', detail: '借贷平衡 + 税务计算无误', level: 'success' },
      { id: 'evt-4', timestamp: minutesAgo(75), agentId: 'accounting-agent', agentName: '会计专员', action: '创建审批', detail: '生成例外事项 EX-20260726-003', level: 'warning' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: minutesAgo(75), operator: '会计专员 Agent', action: '创建例外', result: 'success' },
    ],
  },
  {
    id: 'EX-20260726-004',
    type: 'approval',
    category: 'purchase',
    title: '采购审批：铝合金外壳 5800 件采购订单',
    sourceAgent: '采购助手',
    sourceAgentId: 'procurement-agent',
    riskLevel: 'medium',
    status: 'pending',
    createdAt: minutesAgo(95),
    description: '采购订单 PO-20260726-045，向华南金属制品厂采购铝合金外壳 5800 件，单价 ¥36.5，总金额 ¥211,700。已通过供应商比价，较历史采购价降低 3.9%。',
    context: {
      summary: '常规物料采购订单，已完成供应商比价',
      fields: [
        { label: '订单号', value: 'PO-20260726-045' },
        { label: '供应商', value: '华南金属制品厂' },
        { label: '品名', value: '铝合金外壳（SKU-B001）' },
        { label: '数量', value: '5800 件' },
        { label: '单价', value: '¥36.5/件' },
        { label: '总金额', value: '¥211,700' },
        { label: '交货周期', value: '5 个工作日' },
        { label: '供应商评分', value: '92.4 / 100' },
      ],
    },
    decisionTrace: {
      summary: '采购助手 Agent 通过多维度评分模型选定供应商，因金额超阈值触发审批',
      steps: [
        { step: 1, reasoning: '接收库存管家补货需求：铝合金外壳 5800 件', evidence: '跨 Agent 消息' },
        { step: 2, reasoning: '查询合格供应商名录 8 家', evidence: 'supplier.list()' },
        { step: 3, reasoning: '多维度评分：价格40% + 交货30% + 质量20% + 信用10%', evidence: 'scoring_model' },
        { step: 4, reasoning: '华南金属制品厂得分 92.4，单价 ¥36.5', evidence: '比价结果' },
        { step: 5, reasoning: '对比历史价 ¥38，降幅 3.9%，节省 ¥6,960', evidence: '历史采购记录' },
        { step: 6, reasoning: '金额 > ¥200,000，触发人工审批', evidence: '采购审批规则' },
      ],
      conclusion: '推荐批准，供应商资质良好且价格优于历史',
      confidence: 94,
    },
    eventChain: [
      { id: 'evt-1', timestamp: hoursAgo(2), agentId: 'procurement-agent', agentName: '采购助手', action: '需求接收', detail: '接收库存管家补货需求', level: 'info' },
      { id: 'evt-2', timestamp: minutesAgo(100), agentId: 'procurement-agent', agentName: '采购助手', action: '供应商比价', detail: '评估 8 家供应商', level: 'info' },
      { id: 'evt-3', timestamp: minutesAgo(96), agentId: 'procurement-agent', agentName: '采购助手', action: '订单生成', detail: '创建采购订单草稿', level: 'success' },
      { id: 'evt-4', timestamp: minutesAgo(95), agentId: 'procurement-agent', agentName: '采购助手', action: '创建审批', detail: '生成例外事项 EX-20260726-004', level: 'warning' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: minutesAgo(95), operator: '采购助手 Agent', action: '创建例外', result: 'success' },
    ],
  },
  {
    id: 'EX-20260726-005',
    type: 'approval',
    category: 'restock',
    title: '补货审批：电源适配器 65W 库存偏低',
    sourceAgent: '库存管家',
    sourceAgentId: 'inventory-agent',
    riskLevel: 'low',
    status: 'pending',
    createdAt: hoursAgo(2),
    description: 'SKU-C002 电源适配器 65W 当前库存 210 个，低于安全库存 250 个。建议补货 500 个，预计金额 ¥22,500。该物料为常规辅料，风险较低。',
    context: {
      summary: '常规辅料库存偏低，低风险补货',
      fields: [
        { label: 'SKU', value: 'SKU-C002' },
        { label: '品名', value: '电源适配器 65W' },
        { label: '当前库存', value: '210 个' },
        { label: '安全库存', value: '250 个' },
        { label: '建议采购量', value: '500 个' },
        { label: '预计金额', value: '¥22,500' },
        { label: '推荐供应商', value: '深圳电源科技' },
        { label: '交货周期', value: '3 个工作日' },
      ],
    },
    decisionTrace: {
      summary: '常规辅料低风险补货，库存略低于安全线',
      steps: [
        { step: 1, reasoning: '库存 210 < 安全线 250', evidence: 'WMS 数据' },
        { step: 2, reasoning: 'EOQ 计算：建议采购 500 个', evidence: 'reorder_engine' },
        { step: 3, reasoning: '风险评定 LOW（金额小、非关键物料）', evidence: '物料分级 B 类' },
      ],
      conclusion: '建议批准补货 500 个',
      confidence: 88,
    },
    eventChain: [
      { id: 'evt-1', timestamp: hoursAgo(3), agentId: 'inventory-agent', agentName: '库存管家', action: '库存预警', detail: 'SKU-C002 低于安全库存', level: 'warning' },
      { id: 'evt-2', timestamp: hoursAgo(2), agentId: 'inventory-agent', agentName: '库存管家', action: '创建审批', detail: '生成例外事项 EX-20260726-005', level: 'info' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: hoursAgo(2), operator: '库存管家 Agent', action: '创建例外', result: 'success' },
    ],
  },
  {
    id: 'EX-20260726-006',
    type: 'alert',
    category: 'audit',
    title: '内审告警：非工作时间段大额转账被拦截',
    sourceAgent: '风控哨兵',
    sourceAgentId: 'risk-agent',
    riskLevel: 'high',
    status: 'pending',
    createdAt: minutesAgo(55),
    description: '结算管家 Agent 于 23:42 尝试向账户 ****8821 转账 ¥1,280,000，被风控哨兵拦截。该操作发生在非工作时间段且金额超阈值，风控规则判定为高风险，需人工复核。',
    context: {
      summary: '非工作时间大额转账触发风控熔断',
      fields: [
        { label: '操作时间', value: '2026-07-25 23:42:18' },
        { label: '操作 Agent', value: '结算管家' },
        { label: '目标账户', value: '****8821' },
        { label: '转账金额', value: '¥1,280,000' },
        { label: '风控规则', value: 'R-021 非工作时间大额转账' },
        { label: '处理结果', value: '已拦截' },
        { label: '账户归属', value: '供应商 SUP-0042' },
      ],
      details: '该供应商信用评分 58 分（低于阈值 70），且转账发生在非工作时间段。风控引擎综合判定风险等级 HIGH，执行熔断拦截。',
    },
    decisionTrace: {
      summary: '风控哨兵 Agent 通过多维度风控规则判定该转账为高风险操作',
      steps: [
        { step: 1, reasoning: '监测到转账请求：¥1,280,000', evidence: '资金流监控' },
        { step: 2, reasoning: '时间维度检查：23:42 属非工作时间段', evidence: '业务日历 09:00-18:00' },
        { step: 3, reasoning: '金额维度检查：超过大额阈值 ¥500,000', evidence: '风控规则 R-021' },
        { step: 4, reasoning: '账户维度检查：目标账户归属 SUP-0042 信用分 58', evidence: '供应商信用库' },
        { step: 5, reasoning: '综合风险评分 92/100，判定 HIGH', evidence: 'risk_score_model' },
        { step: 6, reasoning: '执行熔断拦截，冻结操作并上报', evidence: 'circuit_breaker 触发' },
      ],
      conclusion: '建议人工复核该转账合法性，确认是否为误操作或异常行为',
      confidence: 97,
    },
    eventChain: [
      { id: 'evt-1', timestamp: minutesAgo(57), agentId: 'settlement-agent', agentName: '结算管家', action: '发起转账', detail: '向 ****8821 转账 ¥1,280,000', level: 'warning' },
      { id: 'evt-2', timestamp: minutesAgo(56), agentId: 'risk-agent', agentName: '风控哨兵', action: '风控检测', detail: '命中规则 R-021', level: 'error' },
      { id: 'evt-3', timestamp: minutesAgo(55), agentId: 'risk-agent', agentName: '风控哨兵', action: '熔断拦截', detail: '冻结转账操作', level: 'error' },
      { id: 'evt-4', timestamp: minutesAgo(55), agentId: 'risk-agent', agentName: '风控哨兵', action: '创建告警', detail: '生成例外事项 EX-20260726-006', level: 'warning' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: minutesAgo(55), operator: '风控哨兵 Agent', action: '创建告警', result: 'success', remark: '自动拦截并上报' },
    ],
  },
  {
    id: 'EX-20260726-007',
    type: 'approval',
    category: 'voucher',
    title: '凭证审批：销售收款凭证 ¥856,000',
    sourceAgent: '会计专员',
    sourceAgentId: 'accounting-agent',
    riskLevel: 'medium',
    status: 'pending',
    createdAt: minutesAgo(120),
    description: '记-20260726-002 销售产品收入凭证，金额 ¥856,000，借方 1122-应收账款，贷方 6001-主营业务收入。本月最大单笔收入凭证，已通过校验。',
    context: {
      summary: '大额销售收款凭证，本月最大单笔收入',
      fields: [
        { label: '凭证号', value: '记-20260726-002' },
        { label: '摘要', value: '销售产品收入-X9系列' },
        { label: '金额', value: '¥856,000' },
        { label: '借方科目', value: '1122-应收账款' },
        { label: '贷方科目', value: '6001-主营业务收入' },
        { label: '客户', value: '深圳智联科技' },
        { label: '借贷校验', value: '平衡' },
        { label: '发票校验', value: '已开具' },
      ],
    },
    decisionTrace: {
      summary: '会计专员 Agent 根据销售订单自动生成收入凭证',
      steps: [
        { step: 1, reasoning: '接收销售确认信号，订单金额 ¥856,000', evidence: '销售系统 webhook' },
        { step: 2, reasoning: '生成收入分录：借 应收账款 / 贷 主营业务收入', evidence: '收入确认规则' },
        { step: 3, reasoning: '增值税计算：销项税 ¥99,026.55', evidence: 'tax_engine' },
        { step: 4, reasoning: '借贷平衡校验通过', evidence: 'balance_check' },
        { step: 5, reasoning: '金额 > ¥500,000，触发人工审批', evidence: '审批规则 R-003' },
      ],
      conclusion: '凭证校验通过，建议批准并过账',
      confidence: 98,
    },
    eventChain: [
      { id: 'evt-1', timestamp: hoursAgo(3), agentId: 'accounting-agent', agentName: '会计专员', action: '订单确认', detail: '接收销售确认信号', level: 'info' },
      { id: 'evt-2', timestamp: minutesAgo(125), agentId: 'accounting-agent', agentName: '会计专员', action: '凭证生成', detail: '生成收入凭证', level: 'success' },
      { id: 'evt-3', timestamp: minutesAgo(120), agentId: 'accounting-agent', agentName: '会计专员', action: '创建审批', detail: '生成例外事项 EX-20260726-007', level: 'warning' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: minutesAgo(120), operator: '会计专员 Agent', action: '创建例外', result: 'success' },
    ],
  },
  {
    id: 'EX-20260726-008',
    type: 'anomaly',
    category: 'compliance',
    title: '合规违规：高风险供应商未走准入审批入库',
    sourceAgent: '审计巡检',
    sourceAgentId: 'audit-agent',
    riskLevel: 'high',
    status: 'pending',
    createdAt: minutesAgo(35),
    description: '审计巡检发现供应商 SUP-0042（信用评分 58）的物料于 2026-07-25 未经准入审批直接入库。该供应商信用评分低于阈值 70，按合规要求应先完成准入审批。风控哨兵已拦截后续操作。',
    context: {
      summary: '供应商准入流程违规，高风险供应商未审批先入库',
      fields: [
        { label: '供应商编号', value: 'SUP-0042' },
        { label: '供应商名称', value: '广州快速五金' },
        { label: '信用评分', value: '58 / 100（低于阈值 70）' },
        { label: '违规操作', value: '未审批先入库' },
        { label: '入库物料', value: 'SKU-D002 WiFi 天线阵列 200 套' },
        { label: '入库时间', value: '2026-07-25 16:30' },
        { label: '违规规则', value: 'CR-014 供应商准入合规' },
        { label: '当前状态', value: '后续操作已冻结' },
      ],
      details: '根据《供应商管理办法》第 12 条，信用评分低于 70 的供应商必须完成准入审批后方可入库。本次操作跳过审批流程，存在合规风险。',
    },
    decisionTrace: {
      summary: '审计巡检 Agent 在例行合规扫描中发现供应商准入流程违规',
      steps: [
        { step: 1, reasoning: '执行每日合规扫描，检查 247 项操作', evidence: 'compliance_scanner' },
        { step: 2, reasoning: '匹配规则 CR-014：供应商准入合规', evidence: '合规规则引擎' },
        { step: 3, reasoning: '发现 SUP-0042 信用分 58 < 阈值 70', evidence: '供应商信用库' },
        { step: 4, reasoning: '核查入库记录：2026-07-25 16:30 已入库', evidence: 'WMS 入库日志' },
        { step: 5, reasoning: '核查审批记录：未找到准入审批单', evidence: '审批系统查询' },
        { step: 6, reasoning: '判定为合规违规，通知风控哨兵冻结后续操作', evidence: '跨 Agent 协同' },
      ],
      conclusion: '建议立即启动供应商准入审批，并追查违规入库的责任方',
      confidence: 95,
    },
    eventChain: [
      { id: 'evt-1', timestamp: hoursAgo(28), agentId: 'data-sync-agent', agentName: '数据同步', action: '入库同步', detail: 'SUP-0042 物料入库 200 套', level: 'info' },
      { id: 'evt-2', timestamp: minutesAgo(40), agentId: 'audit-agent', agentName: '审计巡检', action: '合规扫描', detail: '命中规则 CR-014', level: 'warning' },
      { id: 'evt-3', timestamp: minutesAgo(36), agentId: 'audit-agent', agentName: '审计巡检', action: '风险通知', detail: '通知风控哨兵冻结操作', level: 'error' },
      { id: 'evt-4', timestamp: minutesAgo(35), agentId: 'audit-agent', agentName: '审计巡检', action: '创建告警', detail: '生成例外事项 EX-20260726-008', level: 'error' },
    ],
    actionHistory: [
      { id: 'act-1', timestamp: minutesAgo(35), operator: '审计巡检 Agent', action: '创建告警', result: 'success', remark: '自动冻结并上报' },
    ],
  },
]

function buildMockExceptionStats(): ExceptionStats {
  return {
    pending: MOCK_EXCEPTIONS.filter((e) => e.status === 'pending').length,
    todayNew: MOCK_EXCEPTIONS.length,
    resolved: MOCK_EXCEPTIONS.filter((e) => e.status === 'resolved').length,
    timeoutWarning: 2,
  }
}

/* ===================== 后端响应 → 前端模型 映射器 ===================== */

/** 后端 Agent get_status() → 前端 Agent */
function mapAgent(raw: any): Agent {
  const stateMap: Record<string, AgentStatus> = {
    running: 'running', created: 'idle', initializing: 'idle',
    paused: 'paused', degraded: 'paused', fused: 'circuit_open',
    terminated: 'idle', error: 'circuit_open',
  }
  return {
    id: raw.agent_id || raw.id || '',
    name: raw.name || '',
    type: raw.domain || raw.type || 'general',
    status: stateMap[raw.state] || (raw.status as AgentStatus) || 'idle',
    last_heartbeat: raw.last_health_check || new Date().toISOString(),
    tasks_completed: raw.stats?.success_count ?? raw.tasks_completed ?? 0,
    tasks_failed: raw.stats?.error_count ?? raw.tasks_failed ?? 0,
    success_rate: raw.stats?.success_count
      ? Math.round((raw.stats.success_count / (raw.stats.success_count + (raw.stats?.error_count || 1))) * 1000) / 10
      : raw.success_rate ?? 100,
    cpu_usage: raw.cpu_usage ?? 0,
    memory_usage: raw.memory_usage ?? 0,
    description: raw.description || `${raw.name} (${raw.domain})`,
    capabilities: raw.capabilities || [],
    mode: raw.human_override ? 'manual' : 'autonomous',
  }
}

/** 后端 inventory status → 前端 InventoryItem */
function mapInventoryItem(raw: any): InventoryItem {
  const qty = raw.quantity ?? 0
  const safety = raw.safety_stock ?? 0
  const reorder = raw.reorder_point ?? 0
  let status: InventoryStatus = 'normal'
  if (qty <= 0) status = 'out'
  else if (qty <= reorder) status = 'low'
  return {
    id: raw.product_id || raw.id || '',
    sku: raw.product_id || raw.sku || '',
    name: raw.product_name || raw.name || '',
    category: raw.category || '未分类',
    quantity: qty,
    unit: raw.unit || '个',
    safety_stock: safety,
    max_stock: raw.max_stock || safety * 4,
    unit_price: raw.unit_cost || raw.unit_price || 0,
    warehouse: raw.warehouse || 'default',
    status,
  }
}

/** 后端 proposal → 前端 RestockSuggestion */
function mapProposal(raw: any): RestockSuggestion {
  return {
    sku: raw.product_id || raw.sku || '',
    name: raw.product_name || raw.name || '',
    current_qty: raw.current_quantity ?? raw.current_qty ?? 0,
    suggested_qty: raw.reorder_quantity ?? raw.reorder_qty ?? 0,
    reason: raw.confidence != null ? `置信度 ${(raw.confidence * 100).toFixed(0)}%` : '自动补货建议',
    estimated_cost: raw.total_cost ?? raw.estimated_cost ?? 0,
  }
}

/** 后端 voucher → 前端 Voucher */
function mapVoucher(raw: any): Voucher {
  const entries = raw.entries || []
  const debit = entries.find((e: any) => e.direction === 'debit') || entries[0] || {}
  const credit = entries.find((e: any) => e.direction === 'credit') || entries[1] || {}
  return {
    id: raw.voucher_id || raw.id || '',
    voucher_no: raw.voucher_no || raw.voucher_id || '',
    date: (raw.created_at || new Date().toISOString()).slice(0, 10),
    summary: raw.summary || raw.description || '',
    debit_account: debit.account || raw.debit_account || '',
    credit_account: credit.account || raw.credit_account || '',
    amount: debit.amount || raw.amount || 0,
    status: (raw.status as VoucherStatus) || 'pending',
    created_by: raw.created_by || 'accounting-agent',
    approved_by: raw.approved_by,
    created_at: raw.created_at || new Date().toISOString(),
  }
}

/** 后端 security_clearance → 前端 risk_level 映射 */
const CLEARANCE_TO_RISK: Record<string, 'low' | 'medium' | 'high'> = {
  public: 'low',
  internal: 'low',
  confidential: 'medium',
  top_secret: 'high',
}

/** 后端 identity → 前端 AgentIdentity */
function mapIdentity(raw: any): AgentIdentity {
  const clearance = raw.security_clearance || raw.risk_level || 'internal'
  return {
    agent_id: raw.agent_id || raw.did || '',
    agent_name: raw.name || raw.agent_name || '',
    identity_token: (raw.did ? raw.did.slice(0, 20) + '...' : raw.identity_token) || '',
    permissions: raw.capabilities || raw.permissions || [],
    last_auth: raw.last_active || raw.registered_at || raw.last_auth || new Date().toISOString(),
    auth_status:
      raw.auth_status ||
      (raw.status === 'revoked' || raw.revoked
        ? 'revoked'
        : raw.status === 'expired'
          ? 'expired'
          : 'verified'),
    risk_level: CLEARANCE_TO_RISK[clearance] || 'low',
  }
}

/** 后端 audit entry → 前端 AuditLog */
function mapAuditLog(raw: any): AuditLog {
  const details = raw.details || {}
  const detailStr = typeof details === 'string' ? details : ''

  // 从 details 中提取人类可读的 Agent 名称
  const agentName =
    details?.name ||
    raw.agent_name ||
    (raw.actor_did && raw.actor_did !== 'system'
      ? raw.actor_did.split(':')[2] || raw.actor_did.split(':').pop() || 'system'
      : 'system')

  // 根据 action 类型生成可读的详情描述
  let humanDetails = detailStr
  if (typeof details === 'object' && Object.keys(details).length > 0) {
    const action = raw.action || ''
    if (action === 'system.start') {
      humanDetails = `系统启动，版本 ${details.version || '1.0.0'}，加载 ${details.agents || 0} 个 Agent`
    } else if (action === 'agent.start') {
      humanDetails = `Agent「${details.name || agentName}」启动（域: ${details.domain || 'unknown'}）`
    } else if (action === 'agent.stop' || action === 'agent.shutdown') {
      humanDetails = `Agent「${details.name || agentName}」停止运行`
    } else if (details.message) {
      humanDetails = details.message
    } else {
      humanDetails = JSON.stringify(details)
    }
  }

  return {
    id: raw.entry_id || raw.id || '',
    timestamp: raw.timestamp || new Date().toISOString(),
    agent_id: raw.actor_did || raw.agent_id || '',
    agent_name: agentName,
    action: raw.action || '',
    resource: raw.resource || '',
    ip_address: details?.ip || raw.ip_address || '10.0.4.1',
    result: (raw.result as AuditResult) || 'success',
    details: humanDetails,
  }
}

/** 后端 system stats → 前端 SystemStats */
function mapSystemStats(raw: any): SystemStats {
  const orch = raw.orchestrator || {}
  const eb = raw.event_bus || {}
  const agents = orch.state_distribution || {}
  return {
    total_agents: orch.total_agents ?? 0,
    active_agents: agents.running ?? 0,
    paused_agents: agents.paused ?? 0,
    circuit_open_agents: agents.fused ?? agents.error ?? 0,
    total_events: eb.published ?? 0,
    events_per_minute: Math.round((eb.published ?? 0) / 60),
    total_tasks_today: Object.values(agents).reduce((s: number, v: any) => s + v, 0),
    success_rate: 98.4,
    avg_response_time: 128,
    system_uptime: 99.97,
    inventory_value: 0,
    pending_vouchers: 0,
  }
}

/** 后端 security stats → 前端 SecurityStats */
function mapSecurityStats(raw: any): SecurityStats {
  const did = raw.did_registry || {}
  const authz = raw.authorization || {}
  const audit = raw.audit_chain || {}
  return {
    total_identities: did.total_identities ?? did.total ?? 0,
    verified_identities: did.active_identities ?? did.active ?? 0,
    denied_actions_today: authz.denied_count ?? authz.denied ?? 0,
    high_risk_agents: 0,
    security_events: audit.total_entries ?? 0,
    last_breach: null,
  }
}

/* ===================== API 服务 ===================== */

async function withFallback<T>(request: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await request()
  } catch {
    return fallback
  }
}

export const apiService = {
  /* ----- Agent 相关 ----- */
  getAgents: (): Promise<Agent[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/agents')
      const list = (res as any)?.agents ?? res ?? []
      return (Array.isArray(list) ? list : []).map(mapAgent)
    }, MOCK_AGENTS),

  getStats: (): Promise<SystemStats> =>
    withFallback(async () => {
      const res = await apiClient.get('/system/stats')
      return mapSystemStats(res as any)
    }, buildMockStats()),

  controlAgent: (agentId: string, action: string): Promise<void> =>
    withFallback(async () => {
      await apiClient.post(`/agents/${agentId}/${action}`)
    }, undefined as unknown as void),

  /* ----- 库存相关 ----- */
  getInventory: (): Promise<InventoryItem[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/inventory')
      const list = (res as any)?.inventory ?? res ?? []
      return (Array.isArray(list) ? list : []).map(mapInventoryItem)
    }, MOCK_INVENTORY),

  getRestockSuggestions: (): Promise<RestockSuggestion[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/inventory/proposals')
      const list = (res as any)?.proposals ?? res ?? []
      return (Array.isArray(list) ? list : []).map(mapProposal)
    }, buildMockRestock()),

  adjustInventory: (sku: string, delta: number, reason: string): Promise<void> =>
    withFallback(async () => {
      await apiClient.post('/inventory/adjust', {
        product_id: sku,
        product_name: sku,
        quantity: delta,
        reason,
      })
    }, undefined as unknown as void),

  /* ----- 会计相关 ----- */
  getVouchers: (): Promise<Voucher[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/accounting/vouchers')
      const list = (res as any)?.vouchers ?? res ?? []
      return (Array.isArray(list) ? list : []).map(mapVoucher)
    }, MOCK_VOUCHERS),

  approveVoucher: (voucherId: string): Promise<void> =>
    withFallback(async () => {
      await apiClient.post('/accounting/vouchers/approve', {
        voucher_id: voucherId,
        approved: true,
      })
    }, undefined as unknown as void),

  rejectVoucher: (voucherId: string): Promise<void> =>
    withFallback(async () => {
      await apiClient.post('/accounting/vouchers/approve', {
        voucher_id: voucherId,
        approved: false,
      })
    }, undefined as unknown as void),

  dailySettlement: (): Promise<void> =>
    withFallback(async () => {
      await apiClient.get('/accounting/settlement')
    }, undefined as unknown as void),

  /* ----- 安全审计相关 ----- */
  getAgentIdentities: (): Promise<AgentIdentity[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/security/identities')
      const list = (res as any)?.identities ?? res ?? []
      return (Array.isArray(list) ? list : []).map(mapIdentity)
    }, MOCK_IDENTITIES),

  getAuditLogs: (): Promise<AuditLog[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/security/audit')
      const list = (res as any)?.entries ?? res ?? []
      return (Array.isArray(list) ? list : []).map(mapAuditLog)
    }, MOCK_AUDIT_LOGS),

  getSecurityStats: (): Promise<SecurityStats> =>
    withFallback(async () => {
      const res = await apiClient.get('/security/stats')
      return mapSecurityStats(res as any)
    }, buildMockSecurityStats()),

  /* ----- 对话式操作相关 ----- */

  getChatAgents: (): Promise<ChatAgent[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/conversation/agents')
      const list = (res as any)?.agents ?? res ?? []
      return Array.isArray(list) && list.length > 0 ? list : MOCK_CHAT_AGENTS
    }, MOCK_CHAT_AGENTS),

  sendMessage: (agentId: string, message: string): Promise<ConversationResponse> =>
    withFallback(async () => {
      const res = await apiClient.post('/conversation/chat', {
        agent_id: agentId,
        message,
      })
      return res as unknown as ConversationResponse
    }, buildMockConversationResponse(agentId, message)),

  getPresetConversations: (): Record<string, ChatMessage[]> => PRESET_CONVERSATIONS,

  /* ----- 例外管理相关 ----- */

  getExceptions: (): Promise<ExceptionItem[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/exceptions')
      const list = (res as any)?.exceptions ?? res ?? []
      return Array.isArray(list) && list.length > 0 ? list : MOCK_EXCEPTIONS
    }, MOCK_EXCEPTIONS),

  getExceptionStats: (): Promise<ExceptionStats> =>
    withFallback(async () => {
      const res = await apiClient.get('/exceptions/stats')
      return (res as any) ?? buildMockExceptionStats()
    }, buildMockExceptionStats()),

  resolveException: (
    id: string,
    action: 'approve' | 'reject' | 'transfer',
    remark?: string,
  ): Promise<void> =>
    withFallback(async () => {
      await apiClient.post(`/exceptions/${id}/resolve`, { action, remark })
    }, undefined as unknown as void),

  /* ----- 发票管理相关 ----- */
  getInvoices: (params?: { status?: string; invoice_type?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.invoice_type) query.set('invoice_type', params.invoice_type)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/invoice/list?${query.toString()}`)
      return (res as any)?.invoices ?? []
    }, []),

  getInvoiceStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/invoice/stats')
      return (res as any) ?? {}
    }, {} as any),

  issueInvoice: (data: any): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/invoice/issue', data)
      return res
    }, {} as any),

  verifyInvoice: (invoiceId: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post(`/invoice/${invoiceId}/verify`)
      return res
    }, {} as any),

  cancelInvoice: (invoiceId: string, reason?: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post(`/invoice/${invoiceId}/cancel`, undefined, {
        params: { reason: reason || '' },
      })
      return res
    }, {} as any),

  /* ----- 报销管理相关 ----- */
  getReimbursements: (params?: { status?: string; employee_id?: string; project_id?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.employee_id) query.set('employee_id', params.employee_id)
      if (params?.project_id) query.set('project_id', params.project_id)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/reimbursement/list?${query.toString()}`)
      return (res as any)?.reimbursements ?? []
    }, []),

  getPendingReimbursements: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/reimbursement/pending')
      return (res as any)?.reimbursements ?? []
    }, []),

  getReimbursementStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/reimbursement/stats')
      return (res as any) ?? {}
    }, {} as any),

  submitReimbursement: (data: any): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/reimbursement/submit', data)
      return res
    }, {} as any),

  verifyReimbursement: (reimbId: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post(`/reimbursement/${reimbId}/verify`)
      return res
    }, {} as any),

  approveReimbursement: (reimbId: string, approved: boolean, remark?: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/reimbursement/approve', {
        reimbursement_id: reimbId,
        approved,
        remark: remark || '',
      })
      return res
    }, {} as any),

  payReimbursement: (reimbId: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post(`/reimbursement/${reimbId}/pay`)
      return res
    }, {} as any),

  registerProject: (projectId: string, name: string, budget: number): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/reimbursement/project/register', {
        project_id: projectId,
        name,
        budget,
      })
      return res
    }, {} as any),

  registerEmployee: (employeeId: string, name: string, department: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/reimbursement/employee/register', {
        employee_id: employeeId,
        name,
        department,
      })
      return res
    }, {} as any),

  /* ----- 报税综合看板 ----- */
  getTaxDashboard: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/tax/dashboard')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- 文档生成相关 ----- */
  generateDocument: (params: {
    agent_id: string
    format: 'docx' | 'xlsx' | 'pptx' | 'pdf'
    template_type: string
    filename: string
    data: Record<string, unknown>
    title?: string
  }): Promise<{ success: boolean; file_path: string; filename: string; format: string; file_size: number; generated_at: string }> =>
    withFallback(async () => {
      const res = await apiClient.post('/document/generate', params)
      return res as any
    }, { success: false, file_path: '', filename: '', format: '', file_size: 0, generated_at: '' }),

  downloadDocument: (filename: string): Promise<Blob> =>
    withFallback(async () => {
      const res = await apiClient.get(`/document/download/${filename}`, { responseType: 'blob' })
      return res as unknown as Blob
    }, new Blob()),

  getDocumentTemplates: (): Promise<Array<{ agent_id: string; templates: Array<{ template: string; format: string; name: string }> }>> =>
    withFallback(async () => {
      const res = await apiClient.get('/document/templates')
      const rawTemplates = (res as any)?.templates ?? []

      // 后端可能返回两种格式：
      // 1. 扁平列表: [{agent_id, template, format, name}, ...]
      // 2. 分组列表: [{agent_id, templates: [{template, format, name}, ...]}, ...]
      // 统一转换为分组格式，供前端组件使用
      if (Array.isArray(rawTemplates) && rawTemplates.length > 0) {
        // 检测是否已经是分组格式（第一个元素有 templates 数组）
        if (rawTemplates[0].templates && Array.isArray(rawTemplates[0].templates)) {
          return rawTemplates as Array<{ agent_id: string; templates: Array<{ template: string; format: string; name: string }> }>
        }

        // 扁平列表 → 按 agent_id 分组
        const grouped: Record<string, Array<{ template: string; format: string; name: string }>> = {}
        for (const item of rawTemplates) {
          const aid = item.agent_id || 'unknown'
          if (!grouped[aid]) grouped[aid] = []
          grouped[aid].push({
            template: item.template,
            format: item.format,
            name: item.name,
          })
        }
        return Object.entries(grouped).map(([agent_id, templates]) => ({ agent_id, templates }))
      }

      return []
    }, []),

  getAgentTemplates: (agentId: string): Promise<Array<{ template: string; format: string; name: string }>> =>
    withFallback(async () => {
      const res = await apiClient.get(`/document/agents/${agentId}/templates`)
      return (res as any)?.templates ?? []
    }, []),

  /* ----- 人力资源管理相关 ----- */
  getEmployees: (params?: { department?: string; status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.department) query.set('department', params.department)
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/hr/employees?${query.toString()}`)
      return (res as any)?.employees ?? []
    }, []),

  getEmployee: (employeeId: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get(`/hr/employees/${employeeId}`)
      return (res as any) ?? {}
    }, {} as any),

  createEmployee: (data: any): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/hr/employees', data)
      return res
    }, {} as any),

  updateEmployee: (employeeId: string, data: any): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.put(`/hr/employees/${employeeId}`, data)
      return res
    }, {} as any),

  getHRStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/hr/stats')
      return (res as any) ?? {}
    }, {} as any),

  getAttendance: (params?: { employee_id?: string; start_date?: string; end_date?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.employee_id) query.set('employee_id', params.employee_id)
      if (params?.start_date) query.set('start_date', params.start_date)
      if (params?.end_date) query.set('end_date', params.end_date)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/hr/attendance?${query.toString()}`)
      return (res as any)?.records ?? []
    }, []),

  getOrgChart: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/hr/org-chart')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- 薪酬管理相关 ----- */
  getPayrollRecords: (params?: { employee_id?: string; period?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.employee_id) query.set('employee_id', params.employee_id)
      if (params?.period) query.set('period', params.period)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/payroll/records?${query.toString()}`)
      return (res as any)?.records ?? []
    }, []),

  getPayrollStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/payroll/stats')
      return (res as any) ?? {}
    }, {} as any),

  batchCalculatePayroll: (params: { employee_ids: string[]; period: string }): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/payroll/batch-calculate', params)
      return res
    }, {} as any),

  /* ----- CRM客户管理相关 ----- */
  getCustomers: (params?: { tier?: string; status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.tier) query.set('tier', params.tier)
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/crm/customers?${query.toString()}`)
      return (res as any)?.customers ?? []
    }, []),

  getCustomer: (customerId: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get(`/crm/customers/${customerId}`)
      return (res as any) ?? {}
    }, {} as any),

  createCustomer: (data: any): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/crm/customers', data)
      return res
    }, {} as any),

  updateCustomer: (customerId: string, data: any): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.put(`/crm/customers/${customerId}`, data)
      return res
    }, {} as any),

  getOpportunities: (params?: { stage?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.stage) query.set('stage', params.stage)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/crm/opportunities?${query.toString()}`)
      return (res as any)?.opportunities ?? []
    }, []),

  getCRMStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/crm/stats')
      return (res as any) ?? {}
    }, {} as any),

  getCustomerSegments: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/crm/segments')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- 销售管理相关 ----- */
  getSalesOrders: (params?: { status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/sales/orders?${query.toString()}`)
      return (res as any)?.orders ?? []
    }, []),

  getSalesStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/sales/stats')
      return (res as any) ?? {}
    }, {} as any),

  getSalesForecast: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/sales/forecast')
      return (res as any) ?? {}
    }, {} as any),

  getSalesTargetProgress: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/sales/target-progress')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- 预算管理相关 ----- */
  getBudgets: (params?: { department?: string; period?: string; status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.department) query.set('department', params.department)
      if (params?.period) query.set('period', params.period)
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/budget?${query.toString()}`)
      return (res as any)?.budgets ?? []
    }, []),

  getBudgetStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/budget/stats')
      return (res as any) ?? {}
    }, {} as any),

  getBudgetVariances: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/budget/variances')
      return (res as any)?.variances ?? []
    }, []),

  /* ----- 资产管理相关 ----- */
  getAssets: (params?: { category?: string; status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.category) query.set('category', params.category)
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/assets?${query.toString()}`)
      return (res as any)?.assets ?? []
    }, []),

  getAssetStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/assets/stats')
      return (res as any) ?? {}
    }, {} as any),

  getAssetDeprecationRecords: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/assets/depreciation/records')
      return (res as any)?.records ?? []
    }, []),

  /* ----- 合同管理相关 ----- */
  getContracts: (params?: { status?: string; type?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.type) query.set('type', params.type)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/contracts?${query.toString()}`)
      return (res as any)?.contracts ?? []
    }, []),

  getContractStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/contracts/stats')
      return (res as any) ?? {}
    }, {} as any),

  getExpiringContracts: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/contracts/expiring')
      return (res as any)?.contracts ?? []
    }, []),

  /* ----- 项目管理相关 ----- */
  getProjects: (params?: { status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/projects?${query.toString()}`)
      return (res as any)?.projects ?? []
    }, []),

  getProjectStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/projects/stats')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- 营销活动相关 ----- */
  getMarketingCampaigns: (params?: { status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/marketing/campaigns?${query.toString()}`)
      return (res as any)?.campaigns ?? []
    }, []),

  getMarketingStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/marketing/stats')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- 招聘管理相关 ----- */
  getRecruitmentPositions: (params?: { status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/recruitment/positions?${query.toString()}`)
      return (res as any)?.positions ?? []
    }, []),

  getRecruitmentStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/recruitment/stats')
      return (res as any) ?? {}
    }, {} as any),

  getCandidates: (params?: { position_id?: string; status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.position_id) query.set('position_id', params.position_id)
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/recruitment/candidates?${query.toString()}`)
      return (res as any)?.candidates ?? []
    }, []),

  /* ----- 风险管理相关 ----- */
  getRisks: (params?: { category?: string; level?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.category) query.set('category', params.category)
      if (params?.level) query.set('level', params.level)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/risk?${query.toString()}`)
      return (res as any)?.risks ?? []
    }, []),

  getRiskStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/risk/stats')
      return (res as any) ?? {}
    }, {} as any),

  getRiskAlerts: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/risk/alerts')
      return (res as any)?.alerts ?? []
    }, []),

  /* ----- 质量管理相关 ----- */
  getQualityInspections: (params?: { status?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.status) query.set('status', params.status)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/quality/inspections?${query.toString()}`)
      return (res as any)?.inspections ?? []
    }, []),

  getQualityStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/quality/stats')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- BI报表相关 ----- */
  getBIReports: (params?: { type?: string; limit?: number }): Promise<any[]> =>
    withFallback(async () => {
      const query = new URLSearchParams()
      if (params?.type) query.set('type', params.type)
      if (params?.limit) query.set('limit', String(params.limit))
      const res = await apiClient.get(`/bi/reports?${query.toString()}`)
      return (res as any)?.reports ?? []
    }, []),

  getBIStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/bi/stats')
      return (res as any) ?? {}
    }, {} as any),

  getBIKPIs: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/bi/kpis')
      return (res as any)?.kpis ?? []
    }, []),

  /* ----- ESG管理相关 ----- */
  getESGIndicators: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/esg/indicators')
      return (res as any)?.indicators ?? []
    }, []),

  getESGStats: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/esg/stats')
      return (res as any) ?? {}
    }, {} as any),

  getESGScore: (): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.get('/esg/score')
      return (res as any) ?? {}
    }, {} as any),

  /* ----- 内部通讯相关 ----- */
  getChatChannels: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/messaging/channels')
      return (res as any)?.channels ?? []
    }, []),

  getChannelMessages: (channelId: string, limit = 50, beforeId?: number): Promise<any[]> =>
    withFallback(async () => {
      let url = `/messaging/channels/${channelId}/messages?limit=${limit}`
      if (beforeId) url += `&before_id=${beforeId}`
      const res = await apiClient.get(url)
      return (res as any)?.messages ?? []
    }, []),

  sendChannelMessage: (channelId: string, content: string, messageType = 'text'): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post(`/messaging/channels/${channelId}/messages`, {
        content,
        message_type: messageType,
      })
      return res as any
    }, null),

  createChatChannel: (type: string, name: string, memberIds: string[]): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post('/messaging/channels', {
        channel_type: type,
        name,
        member_ids: memberIds,
      })
      return res as any
    }, null),

  markChannelRead: (channelId: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post(`/messaging/channels/${channelId}/read`)
      return res as any
    }, null),

  getChannelMembers: (channelId: string): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get(`/messaging/channels/${channelId}/members`)
      return (res as any)?.members ?? []
    }, []),

  getChatUsers: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/messaging/users')
      return (res as any)?.users ?? []
    }, []),

  getMessagingAgents: (): Promise<any[]> =>
    withFallback(async () => {
      const res = await apiClient.get('/messaging/agents')
      return (res as any)?.agents ?? []
    }, []),

  uploadChatFile: (channelId: string, file: File): Promise<any> =>
    withFallback(async () => {
      const formData = new FormData()
      formData.append('file', file)
      const res = await apiClient.post(`/messaging/channels/${channelId}/files`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 60000,
      })
      return res as any
    }, null),

  downloadChatFile: async (fileId: string): Promise<Blob> => {
    try {
      const res = await apiClient.get(`/messaging/files/${fileId}`, { responseType: 'blob' })
      return res as unknown as Blob
    } catch {
      return new Blob()
    }
  },

  sendAgentCommand: (channelId: string, agentId: string, command: string): Promise<any> =>
    withFallback(async () => {
      const res = await apiClient.post(`/messaging/channels/${channelId}/agent-command`, {
        agent_id: agentId,
        command,
      })
      return res as any
    }, null),
}

export { apiClient }
