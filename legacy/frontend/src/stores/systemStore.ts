import { create } from 'zustand'
import { websocketService } from '../services/websocket'
import { apiService, sendMessageStream } from '../services/api'

/* ===================== 类型定义 ===================== */

export type AgentStatus = 'running' | 'paused' | 'circuit_open' | 'idle'

export interface Agent {
  id: string
  name: string
  type: string
  status: AgentStatus
  last_heartbeat: string
  tasks_completed: number
  tasks_failed: number
  success_rate: number
  cpu_usage: number
  memory_usage: number
  description: string
  capabilities: string[]
  mode: 'autonomous' | 'supervised' | 'manual'
}

export type EventLevel = 'info' | 'warning' | 'error' | 'success'

export interface SystemEvent {
  id: string
  timestamp: string
  agent_id: string
  agent_name: string
  event_type: string
  level: EventLevel
  message: string
  metadata?: Record<string, unknown>
}

export interface SystemStats {
  total_agents: number
  active_agents: number
  paused_agents: number
  circuit_open_agents: number
  total_events: number
  events_per_minute: number
  total_tasks_today: number
  success_rate: number
  avg_response_time: number
  system_uptime: number
  inventory_value: number
  pending_vouchers: number
}

export type InventoryStatus = 'normal' | 'low' | 'out' | 'overstock'

export interface InventoryItem {
  id: string
  sku: string
  name: string
  category: string
  quantity: number
  unit: string
  safety_stock: number
  max_stock: number
  unit_price: number
  warehouse: string
  status: InventoryStatus
}

export interface RestockSuggestion {
  sku: string
  name: string
  current_qty: number
  suggested_qty: number
  reason: string
  estimated_cost: number
}

export type VoucherStatus = 'pending' | 'approved' | 'rejected' | 'posted'

export interface Voucher {
  id: string
  voucher_no: string
  date: string
  summary: string
  debit_account: string
  credit_account: string
  amount: number
  status: VoucherStatus
  created_by: string
  approved_by?: string
  created_at: string
}

export interface AgentIdentity {
  agent_id: string
  agent_name: string
  identity_token: string
  permissions: string[]
  last_auth: string
  auth_status: 'verified' | 'expired' | 'revoked'
  risk_level: 'low' | 'medium' | 'high'
}

export type AuditResult = 'success' | 'denied' | 'error'

export interface AuditLog {
  id: string
  timestamp: string
  agent_id: string
  agent_name: string
  action: string
  resource: string
  ip_address: string
  result: AuditResult
  details: string
}

export interface SecurityStats {
  total_identities: number
  verified_identities: number
  denied_actions_today: number
  high_risk_agents: number
  security_events: number
  last_breach: string | null
}

/* ===================== 对话式操作 类型 ===================== */

export type ChatAgentStatus = 'online' | 'offline' | 'busy'

export interface ChatAgent {
  id: string
  name: string
  type: string
  status: ChatAgentStatus
  icon: string
  description: string
  capabilities: string[]
  quickCommands: string[]
}

export type DataCardType = 'table' | 'stats' | 'list'

export interface DataCardColumn {
  key: string
  label: string
}

export interface DataCardStat {
  label: string
  value: string
  trend?: string
  tone?: 'accent' | 'primary' | 'paused' | 'circuit'
}

export interface DataCardItem {
  label: string
  value: string
}

export interface DataCard {
  type: DataCardType
  title: string
  columns?: DataCardColumn[]
  rows?: Record<string, string | number>[]
  stats?: DataCardStat[]
  items?: DataCardItem[]
}

export type ChatRole = 'user' | 'agent'
export type ChatMessageStatus = 'sending' | 'sent' | 'error'

/** 创作类意图产出的可下载附件（后端落受控目录，经鉴权下载端点取回） */
export interface ChatAttachment {
  name: string
  filename: string
  url: string
  format: string
  size: number
}

export interface ChatMessage {
  id: string
  role: ChatRole
  agentId: string
  agentName: string
  content: string
  timestamp: string
  thinkingProcess?: string[]
  dataCard?: DataCard
  status?: ChatMessageStatus
  /** 降级显性化（裁决 D-A）：后端规则骨架/人工队列回复必须标 true */
  degraded?: boolean
  degradedReason?: string
  intent?: string
  classifier?: string
  /** 数据来源：agent_native=Agent 本体实时数据；unavailable=取不到数 */
  dataSource?: string
  manualQueue?: boolean
  todoId?: string
  traceId?: string
  attachments?: ChatAttachment[]
  /** 流式失败后由非流式端点一次性重答（通道降级，不是模型降级，不挂降级徽标） */
  streamFallback?: boolean
  streamFallbackReason?: string
  /** 输出护栏撤回：正文已截断为终止帧文案（裁决 D-D 第 2 条） */
  retracted?: boolean
  retractReason?: string
}

export interface ConversationResponse {
  content: string
  thinkingProcess: string[]
  dataCard?: DataCard
  degraded?: boolean
  degradedReason?: string
  intent?: string
  classifier?: string
  dataSource?: string
  manualQueue?: boolean
  todoId?: string
  traceId?: string
  attachments?: ChatAttachment[]
}

export interface QuickCommand {
  label: string
  command: string
  icon: string
}

/* ===================== 例外管理 类型 ===================== */

export type ExceptionType = 'approval' | 'alert' | 'anomaly'
export type ExceptionStatus = 'pending' | 'resolved' | 'timeout'
export type ExceptionCategory = 'restock' | 'voucher' | 'purchase' | 'audit' | 'compliance'
export type ExceptionRisk = 'low' | 'medium' | 'high'
export type ExceptionEventLevel = 'info' | 'warning' | 'error' | 'success'
export type ExceptionActionResult = 'success' | 'failed' | 'pending'

export interface ExceptionEvent {
  id: string
  timestamp: string
  agentId: string
  agentName: string
  action: string
  detail: string
  level: ExceptionEventLevel
}

export interface ExceptionAction {
  id: string
  timestamp: string
  operator: string
  action: string
  result: ExceptionActionResult
  remark?: string
}

export interface ExceptionContextField {
  label: string
  value: string
}

export interface ExceptionContext {
  summary: string
  fields: ExceptionContextField[]
  details?: string
}

export interface DecisionStep {
  step: number
  reasoning: string
  evidence?: string
}

export interface DecisionTrace {
  summary: string
  steps: DecisionStep[]
  conclusion: string
  confidence: number
}

export interface ExceptionItem {
  id: string
  type: ExceptionType
  category: ExceptionCategory
  title: string
  sourceAgent: string
  sourceAgentId: string
  riskLevel: ExceptionRisk
  status: ExceptionStatus
  createdAt: string
  description: string
  context: ExceptionContext
  decisionTrace: DecisionTrace
  eventChain: ExceptionEvent[]
  actionHistory: ExceptionAction[]
}

export interface ExceptionStats {
  pending: number
  todayNew: number
  resolved: number
  timeoutWarning: number
}

/* ===================== Store 定义 ===================== */

/* 对话历史 localStorage 存储 Key */
const CONVERSATION_STORAGE_KEY = 'erp_conversation_history'

interface SystemState {
  // 数据
  agents: Agent[]
  events: SystemEvent[]
  stats: SystemStats | null
  inventory: InventoryItem[]
  restockSuggestions: RestockSuggestion[]
  vouchers: Voucher[]
  agentIdentities: AgentIdentity[]
  auditLogs: AuditLog[]
  securityStats: SecurityStats | null

  // 对话式操作
  chatAgents: ChatAgent[]
  conversationMessages: ChatMessage[]
  activeAgentId: string
  conversationLoading: boolean

  // 例外管理
  exceptions: ExceptionItem[]
  exceptionStats: ExceptionStats | null
  exceptionsLoading: boolean
  selectedExceptionId: string | null

  // UI 状态
  wsConnected: boolean
  loading: boolean
  error: string | null
  eventFilter: EventLevel | 'all'

  // Actions
  initWebSocket: () => void
  setWsConnected: (connected: boolean) => void
  addEvent: (event: SystemEvent) => void
  setEventFilter: (filter: EventLevel | 'all') => void
  updateAgent: (agentId: string, updates: Partial<Agent>) => void
  setAgents: (agents: Agent[]) => void
  setStats: (stats: SystemStats) => void
  setInventory: (items: InventoryItem[]) => void
  setVouchers: (vouchers: Voucher[]) => void
  setAuditLogs: (logs: AuditLog[]) => void
  fetchInitialData: () => Promise<void>
  controlAgent: (agentId: string, action: 'pause' | 'resume' | 'takeover') => Promise<void>
  adjustInventory: (sku: string, delta: number, reason: string) => Promise<void>
  approveVoucher: (voucherId: string) => Promise<void>
  rejectVoucher: (voucherId: string) => Promise<void>
  dailySettlement: () => Promise<void>
  updateStatsFromAgents: () => void

  // 对话 Actions
  initChatAgents: () => Promise<void>
  setActiveAgent: (agentId: string) => void
  sendMessage: (content: string) => Promise<void>
  clearConversation: () => void
  loadConversationHistory: (agentId: string) => void

  // 例外 Actions
  fetchExceptions: () => Promise<void>
  resolveException: (
    id: string,
    action: 'approve' | 'reject' | 'transfer',
    remark?: string,
  ) => Promise<void>
  selectException: (id: string | null) => void
}

/**
 * 对话回复（一次性 JSON 或 SSE done 帧）里必须逐字段落到消息上的元数据。
 *
 * 只带已定义字段：done 帧不含 thinkingProcess/dataCard（它们各有自己的帧），
 * 盲撒会把流式期间攒下的内容抹成 undefined。丢 degraded/dataSource 字段
 * 就等于让兜底在界面上冒充正式回答（裁决 D-A）。
 */
function conversationFields(res: ConversationResponse): Partial<ChatMessage> {
  const fields: Partial<ChatMessage> = {
    degraded: res.degraded,
    degradedReason: res.degradedReason,
    intent: res.intent,
    classifier: res.classifier,
    dataSource: res.dataSource,
    manualQueue: res.manualQueue,
    todoId: res.todoId,
    traceId: res.traceId,
    attachments: res.attachments,
    dataCard: res.dataCard,
  }
  return Object.fromEntries(
    Object.entries(fields).filter(([, v]) => v !== undefined),
  ) as Partial<ChatMessage>
}

export const useSystemStore = create<SystemState>((set, get) => ({
  agents: [],
  events: [],
  stats: null,
  inventory: [],
  restockSuggestions: [],
  vouchers: [],
  agentIdentities: [],
  auditLogs: [],
  securityStats: null,

  // 对话式操作初始状态
  chatAgents: [],
  conversationMessages: [],
  activeAgentId: 'inventory-agent',
  conversationLoading: false,

  // 例外管理初始状态
  exceptions: [],
  exceptionStats: null,
  exceptionsLoading: false,
  selectedExceptionId: null,

  wsConnected: false,
  loading: false,
  error: null,
  eventFilter: 'all',

  initWebSocket: () => {
    websocketService.connect(
      (event) => {
        get().addEvent(event)
        // 处理 Agent 状态变更事件
        if (event.event_type === 'agent_status' && event.metadata) {
          get().updateAgent(event.agent_id, event.metadata as Partial<Agent>)
        }
        // 处理心跳事件
        if (event.event_type === 'agent_heartbeat' && event.metadata) {
          get().updateAgent(event.agent_id, {
            last_heartbeat: event.timestamp,
            ...(event.metadata as Partial<Agent>),
          })
        }
      },
      (connected) => {
        get().setWsConnected(connected)
      },
    )
  },

  setWsConnected: (connected) => set({ wsConnected: connected }),

  addEvent: (event) =>
    set((state) => ({
      events: [event, ...state.events].slice(0, 500),
    })),

  setEventFilter: (filter) => set({ eventFilter: filter }),

  updateAgent: (agentId, updates) =>
    set((state) => ({
      agents: state.agents.map((a) => (a.id === agentId ? { ...a, ...updates } : a)),
    })),

  setAgents: (agents) => set({ agents }),
  setStats: (stats) => set({ stats }),
  setInventory: (inventory) => set({ inventory }),
  setVouchers: (vouchers) => set({ vouchers }),
  setAuditLogs: (auditLogs) => set({ auditLogs }),

  updateStatsFromAgents: () => {
    const agents = get().agents
    const running = agents.filter((a) => a.status === 'running').length
    const paused = agents.filter((a) => a.status === 'paused').length
    const circuit = agents.filter((a) => a.status === 'circuit_open').length
    const totalTasks = agents.reduce((sum, a) => sum + a.tasks_completed, 0)
    const totalSuccess = agents.reduce((sum, a) => sum + a.tasks_completed, 0)
    const totalFail = agents.reduce((sum, a) => sum + a.tasks_failed, 0)
    const successRate = totalSuccess + totalFail > 0 ? (totalSuccess / (totalSuccess + totalFail)) * 100 : 100

    set((state) => ({
      stats: state.stats
        ? {
            ...state.stats,
            total_agents: agents.length,
            active_agents: running,
            paused_agents: paused,
            circuit_open_agents: circuit,
            total_tasks_today: totalTasks,
            success_rate: Math.round(successRate * 10) / 10,
          }
        : state.stats,
    }))
  },

  fetchInitialData: async () => {
    set({ loading: true, error: null })
    try {
      const [agents, stats, inventory, restock, vouchers, identities, auditLogs, securityStats] =
        await Promise.all([
          apiService.getAgents(),
          apiService.getStats(),
          apiService.getInventory(),
          apiService.getRestockSuggestions(),
          apiService.getVouchers(),
          apiService.getAgentIdentities(),
          apiService.getAuditLogs(),
          apiService.getSecurityStats(),
        ])
      set({
        agents,
        stats,
        inventory,
        restockSuggestions: restock,
        vouchers,
        agentIdentities: identities,
        auditLogs,
        securityStats,
        loading: false,
      })
    } catch (err) {
      // 后端不可用时静默降级到 Mock 数据，保证前端可独立演示
      set({ loading: false, error: null })
    }
  },

  controlAgent: async (agentId, action) => {
    const statusMap: Record<string, AgentStatus> = {
      pause: 'paused',
      resume: 'running',
      takeover: 'idle',
    }
    const modeMap: Record<string, Agent['mode']> = {
      pause: 'supervised',
      resume: 'autonomous',
      takeover: 'manual',
    }
    try {
      await apiService.controlAgent(agentId, action)
    } catch {
      // 降级：本地更新状态
    }
    get().updateAgent(agentId, {
      status: statusMap[action],
      mode: modeMap[action],
    })
    get().updateStatsFromAgents()

    // 记录操作事件
    const agent = get().agents.find((a) => a.id === agentId)
    const actionText = { pause: '暂停', resume: '恢复', takeover: '接管' }[action]
    get().addEvent({
      id: `evt-${Date.now()}`,
      timestamp: new Date().toISOString(),
      agent_id: agentId,
      agent_name: agent?.name || agentId,
      event_type: 'agent_control',
      level: 'warning',
      message: `人工${actionText}了 Agent「${agent?.name}」`,
    })
  },

  adjustInventory: async (sku, delta, reason) => {
    try {
      await apiService.adjustInventory(sku, delta, reason)
    } catch {
      // 降级处理
    }
    set((state) => ({
      inventory: state.inventory.map((item) => {
        if (item.sku !== sku) return item
        const newQty = Math.max(0, item.quantity + delta)
        let status: InventoryStatus = 'normal'
        if (newQty <= 0) status = 'out'
        else if (newQty < item.safety_stock) status = 'low'
        else if (newQty > item.max_stock) status = 'overstock'
        return { ...item, quantity: newQty, status }
      }),
    }))

    const item = get().inventory.find((i) => i.sku === sku)
    get().addEvent({
      id: `evt-${Date.now()}`,
      timestamp: new Date().toISOString(),
      agent_id: 'inventory-agent',
      agent_name: '库存管家',
      event_type: 'inventory_adjusted',
      level: delta >= 0 ? 'success' : 'info',
      message: `库存调整：${item?.name} (${sku}) ${delta >= 0 ? '+' : ''}${delta}，原因：${reason}`,
    })
  },

  approveVoucher: async (voucherId) => {
    try {
      await apiService.approveVoucher(voucherId)
    } catch {
      // 降级处理
    }
    set((state) => ({
      vouchers: state.vouchers.map((v) =>
        v.id === voucherId ? { ...v, status: 'approved' as VoucherStatus, approved_by: '当前用户' } : v,
      ),
    }))
    const voucher = get().vouchers.find((v) => v.id === voucherId)
    get().addEvent({
      id: `evt-${Date.now()}`,
      timestamp: new Date().toISOString(),
      agent_id: 'accounting-agent',
      agent_name: '会计专员',
      event_type: 'voucher_approved',
      level: 'success',
      message: `凭证 ${voucher?.voucher_no} 已审批通过，金额 ¥${voucher?.amount.toLocaleString()}`,
    })
  },

  rejectVoucher: async (voucherId) => {
    try {
      await apiService.rejectVoucher(voucherId)
    } catch {
      // 降级处理
    }
    set((state) => ({
      vouchers: state.vouchers.map((v) =>
        v.id === voucherId ? { ...v, status: 'rejected' as VoucherStatus } : v,
      ),
    }))
    const voucher = get().vouchers.find((v) => v.id === voucherId)
    get().addEvent({
      id: `evt-${Date.now()}`,
      timestamp: new Date().toISOString(),
      agent_id: 'accounting-agent',
      agent_name: '会计专员',
      event_type: 'voucher_rejected',
      level: 'warning',
      message: `凭证 ${voucher?.voucher_no} 已被驳回`,
    })
  },

  dailySettlement: async () => {
    try {
      await apiService.dailySettlement()
    } catch {
      // 降级处理
    }
    set((state) => ({
      vouchers: state.vouchers.map((v) =>
        v.status === 'approved' ? { ...v, status: 'posted' as VoucherStatus } : v,
      ),
    }))
    get().addEvent({
      id: `evt-${Date.now()}`,
      timestamp: new Date().toISOString(),
      agent_id: 'settlement-agent',
      agent_name: '结算管家',
      event_type: 'daily_settlement',
      level: 'success',
      message: `日清日结完成，已过账 ${get().vouchers.filter((v) => v.status === 'posted').length} 张凭证`,
    })
  },

  /* ===================== 对话式操作 Actions ===================== */

  initChatAgents: async () => {
    const agents = await apiService.getChatAgents()
    set({ chatAgents: agents })
    // 首次加载当前 Agent 的历史
    get().loadConversationHistory(get().activeAgentId)
  },

  setActiveAgent: (agentId) => {
    set({ activeAgentId: agentId })
    get().loadConversationHistory(agentId)
  },

  loadConversationHistory: (agentId) => {
    try {
      const raw = localStorage.getItem(CONVERSATION_STORAGE_KEY)
      const all: Record<string, ChatMessage[]> = raw ? JSON.parse(raw) : {}
      if (all[agentId] && all[agentId].length > 0) {
        set({ conversationMessages: all[agentId] })
      } else {
        // 首次访问时加载预置示例对话
        const presets = apiService.getPresetConversations()
        set({ conversationMessages: presets[agentId] || [] })
      }
    } catch {
      set({ conversationMessages: [] })
    }
  },

  sendMessage: async (content) => {
    const agentId = get().activeAgentId
    const agent = get().chatAgents.find((a) => a.id === agentId)
    const agentName = agent?.name || 'Agent'

    const writeStorage = (msgs: ChatMessage[]) => {
      try {
        const raw = localStorage.getItem(CONVERSATION_STORAGE_KEY)
        const all: Record<string, ChatMessage[]> = raw ? JSON.parse(raw) : {}
        all[agentId] = msgs
        localStorage.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify(all))
      } catch {
        // localStorage 不可用时忽略
      }
    }

    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}-u`,
      role: 'user',
      agentId,
      agentName: '当前用户',
      content,
      timestamp: new Date().toISOString(),
      status: 'sent',
    }
    set({
      conversationMessages: [...get().conversationMessages, userMsg],
      conversationLoading: true,
    })
    writeStorage(get().conversationMessages)

    /* 流式占位消息：token 边到边写进气泡。
       set 每片都做会把渲染压成瓶颈，故 80ms 合并一次；持久化只在终止态做——
       半截回答不该进历史对话。 */
    let current: ChatMessage = {
      id: `msg-${Date.now()}-a`,
      role: 'agent',
      agentId,
      agentName,
      content: '',
      timestamp: new Date().toISOString(),
      status: 'sending',
      thinkingProcess: [],
    }
    const commit = (patch: Partial<ChatMessage>, persist = false) => {
      current = { ...current, ...patch }
      const msgs = get().conversationMessages.map((m) => (m.id === current.id ? current : m))
      set({ conversationMessages: msgs })
      // 用户已切到别的 Agent 时不能再写它的历史（否则拿新会话覆盖旧 Agent 存档）
      if (persist && get().activeAgentId === agentId) writeStorage(msgs)
    }
    set({ conversationMessages: [...get().conversationMessages, current] })

    let streamed = ''
    let flushTimer: ReturnType<typeof setTimeout> | null = null
    const flushTokens = () => {
      if (flushTimer) {
        clearTimeout(flushTimer)
        flushTimer = null
      }
      commit({ content: streamed })
    }

    try {
      const outcome = await sendMessageStream(agentId, content, {
        onThinking: (step) => {
          if (!step) return
          commit({ thinkingProcess: [...(current.thinkingProcess || []), step] })
        },
        onToken: (text) => {
          streamed += text
          if (!flushTimer) flushTimer = setTimeout(flushTokens, 80)
        },
        onDataCard: (card) => commit({ dataCard: card }),
        // done 携带降级标记/人工队列/附件等元数据：丢字段就等于把降级冒充正式回答
        onDone: (meta) => commit(conversationFields(meta)),
        onRetract: () => undefined,
        onError: () => undefined,
      })
      flushTokens()

      if (outcome.kind === 'retracted') {
        // 已推送的分片收不回来，整段截断为终止帧文案并留标记（裁决 D-D 第 2 条）
        commit({ content: outcome.notice, retracted: true, retractReason: outcome.reason,
          status: 'sent' }, true)
        return
      }

      if (outcome.kind !== 'completed') {
        // 断线/异常 → 一次性重答（幂等，不做断点续传）。回答改由非流式端点出具，
        // 这条标记是通道降级，不能冒充模型降级，所以不额外写 degraded。
        const res = await apiService.sendMessage(agentId, content)
        commit({
          ...conversationFields(res),
          thinkingProcess: res.thinkingProcess,
          content: res.content,
          streamFallback: true,
          streamFallbackReason: outcome.reason,
          status: 'sent',
        }, true)
        return
      }

      commit({ status: 'sent' }, true)
    } catch {
      commit({
        content: '抱歉，我暂时无法响应，请稍后重试。',
        status: 'error',
      }, true)
    } finally {
      set({ conversationLoading: false })
    }
  },

  clearConversation: () => {
    const agentId = get().activeAgentId
    try {
      const raw = localStorage.getItem(CONVERSATION_STORAGE_KEY)
      const all: Record<string, ChatMessage[]> = raw ? JSON.parse(raw) : {}
      delete all[agentId]
      localStorage.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify(all))
    } catch {
      // 忽略
    }
    set({ conversationMessages: [] })
  },

  /* ===================== 例外管理 Actions ===================== */

  fetchExceptions: async () => {
    set({ exceptionsLoading: true })
    try {
      const [exceptions, stats] = await Promise.all([
        apiService.getExceptions(),
        apiService.getExceptionStats(),
      ])
      set({ exceptions, exceptionStats: stats, exceptionsLoading: false })
    } catch {
      set({ exceptionsLoading: false })
    }
  },

  resolveException: async (id, action, remark) => {
    const exception = get().exceptions.find((e) => e.id === id)
    if (!exception) return

    try {
      await apiService.resolveException(id, action, remark)
    } catch {
      // 降级：本地更新
    }

    const actionText = { approve: '批准', reject: '拒绝', transfer: '转交' }[action]
    const newAction: ExceptionAction = {
      id: `act-${Date.now()}`,
      timestamp: new Date().toISOString(),
      operator: '当前用户',
      action: actionText,
      result: 'success',
      remark: remark || (action === 'approve' ? '同意处理' : action === 'reject' ? '不予执行' : '已转交相关负责人'),
    }

    set((state) => ({
      exceptions: state.exceptions.map((e) =>
        e.id === id
          ? {
              ...e,
              status: 'resolved' as ExceptionStatus,
              actionHistory: [...e.actionHistory, newAction],
            }
          : e,
      ),
      exceptionStats: state.exceptionStats
        ? {
            ...state.exceptionStats,
            pending: Math.max(0, state.exceptionStats.pending - 1),
            resolved: state.exceptionStats.resolved + 1,
          }
        : state.exceptionStats,
    }))

    get().addEvent({
      id: `evt-${Date.now()}`,
      timestamp: new Date().toISOString(),
      agent_id: exception.sourceAgentId,
      agent_name: exception.sourceAgent,
      event_type: 'exception_resolved',
      level: 'success',
      message: `例外事项「${exception.title}」已被${actionText}（${exception.sourceAgent}）`,
    })
  },

  selectException: (id) => set({ selectedExceptionId: id }),
}))
