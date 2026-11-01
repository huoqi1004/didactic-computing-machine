import type { SystemEvent, EventLevel } from '../stores/systemStore'

type EventCallback = (event: SystemEvent) => void
type StatusCallback = (connected: boolean) => void

/* WebSocket 连接地址 */
const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/ws`

/* Mock 事件生成所需的 Agent 信息 */
const MOCK_AGENTS = [
  { id: 'inventory-agent', name: '库存管家' },
  { id: 'accounting-agent', name: '会计专员' },
  { id: 'procurement-agent', name: '采购助手' },
  { id: 'risk-agent', name: '风控哨兵' },
  { id: 'settlement-agent', name: '结算管家' },
  { id: 'data-sync-agent', name: '数据同步' },
  { id: 'audit-agent', name: '审计巡检' },
]

const MOCK_EVENT_TEMPLATES: Array<{
  event_type: string
  level: EventLevel
  messages: string[]
}> = [
  {
    event_type: 'agent_heartbeat',
    level: 'info',
    messages: ['心跳正常，CPU {cpu}%，内存 {mem}MB'],
  },
  {
    event_type: 'task_completed',
    level: 'success',
    messages: ['完成库存盘点任务，核对 {n} 项', '生成会计凭证 {no}，金额 ¥{amt}', '采购比价完成，节省成本 ¥{amt}', '风控扫描完成，检查 {n} 笔交易', '数据同步完成，处理 {n} 条记录'],
  },
  {
    event_type: 'inventory_adjusted',
    level: 'info',
    messages: ['自动入库 SKU-{sku} +{n} 件', '出库 SKU-{sku} -{n} 件', '库存预警：SKU-{sku} 低于安全线'],
  },
  {
    event_type: 'voucher_created',
    level: 'info',
    messages: ['新建凭证 记-{no}，等待审批', '凭证 记-{no} 已自动生成'],
  },
  {
    event_type: 'alert_raised',
    level: 'warning',
    messages: ['检测到库存异常波动', '应收账款逾期提醒：客户 {cust}', '供应商交期延迟预警'],
  },
  {
    event_type: 'circuit_breaker',
    level: 'error',
    messages: ['数据同步熔断器触发，已暂停自动同步', '风控熔断：检测到异常交易模式'],
  },
  {
    event_type: 'agent_status',
    level: 'info',
    messages: ['Agent 状态变更'],
  },
]

function randomInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)]
}

function fillTemplate(template: string): string {
  return template
    .replace('{cpu}', String(randomInt(15, 65)))
    .replace('{mem}', String(randomInt(200, 600)))
    .replace('{n}', String(randomInt(10, 999)))
    .replace('{no}', `20260726-${String(randomInt(1, 99)).padStart(3, '0')}`)
    .replace('{amt}', String(randomInt(1000, 980000)))
    .replace('{sku}', pick(['A001', 'A002', 'B001', 'C002', 'D001', 'E002']))
    .replace('{cust}', pick(['华锐科技', '鼎盛集团', '远景实业', '联信贸易']))
}

function generateMockEvent(): SystemEvent {
  const agent = pick(MOCK_AGENTS)
  const template = pick(MOCK_EVENT_TEMPLATES)
  const message = fillTemplate(pick(template.messages))
  return {
    id: `evt-${Date.now()}-${randomInt(1000, 9999)}`,
    timestamp: new Date().toISOString(),
    agent_id: agent.id,
    agent_name: agent.name,
    event_type: template.event_type,
    level: template.level,
    message,
    metadata:
      template.event_type === 'agent_heartbeat'
        ? { cpu_usage: randomInt(15, 65), memory_usage: randomInt(200, 600) }
        : undefined,
  }
}

class WebSocketService {
  private ws: WebSocket | null = null
  private eventCallbacks: EventCallback[] = []
  private statusCallbacks: StatusCallback[] = []
  private reconnectAttempts = 0
  private maxReconnectAttempts = 6
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private mockTimer: ReturnType<typeof setInterval> | null = null
  private started = false

  /**
   * 建立 WebSocket 连接，并在连接失败时启动 Mock 事件流，保证前端实时演示效果。
   */
  connect(onEvent: EventCallback, onStatus: StatusCallback): void {
    this.eventCallbacks.push(onEvent)
    this.statusCallbacks.push(onStatus)

    if (this.started) return
    this.started = true

    this.doConnect()
  }

  private doConnect(): void {
    try {
      this.ws = new WebSocket(WS_URL)

      this.ws.onopen = () => {
        this.reconnectAttempts = 0
        this.stopMock()
        this.notifyStatus(true)
      }

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data)
          const normalized: SystemEvent = {
            id: data.id || `evt-${Date.now()}`,
            timestamp: data.timestamp || new Date().toISOString(),
            agent_id: data.agent_id || 'system',
            agent_name: data.agent_name || data.agent_id || '系统',
            event_type: data.event_type || 'unknown',
            level: data.level || 'info',
            message: data.message || JSON.stringify(data),
            metadata: data.metadata,
          }
          this.notifyEvent(normalized)
        } catch {
          // 忽略无法解析的消息
        }
      }

      this.ws.onerror = () => {
        // 错误后触发关闭逻辑
        this.ws?.close()
      }

      this.ws.onclose = () => {
        this.notifyStatus(false)
        // 后端不可用 -> 启动 Mock 事件流
        this.startMock()
        // 尝试重连真实 WebSocket
        this.scheduleReconnect()
      }
    } catch {
      this.notifyStatus(false)
      this.startMock()
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectAttempts++
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000)
    this.reconnectTimer = setTimeout(() => this.doConnect(), delay)
  }

  /**
   * Mock 事件流：每 2-5 秒生成一条模拟事件，用于无后端时的实时演示。
   */
  private startMock(): void {
    if (this.mockTimer) return
    this.notifyStatus(false)
    // 立即推送一条，避免初始空白
    this.notifyEvent(generateMockEvent())
    const scheduleNext = () => {
      this.mockTimer = setTimeout(() => {
        this.notifyEvent(generateMockEvent())
        scheduleNext()
      }, randomInt(2000, 5000))
    }
    scheduleNext()
  }

  private stopMock(): void {
    if (this.mockTimer) {
      clearTimeout(this.mockTimer)
      this.mockTimer = null
    }
  }

  private notifyEvent(event: SystemEvent): void {
    this.eventCallbacks.forEach((cb) => cb(event))
  }

  private notifyStatus(connected: boolean): void {
    this.statusCallbacks.forEach((cb) => cb(connected))
  }

  /**
   * 主动断开连接，清理所有资源。
   */
  disconnect(): void {
    this.started = false
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopMock()
    if (this.ws) {
      this.ws.onclose = null
      this.ws.onerror = null
      this.ws.onmessage = null
      this.ws.onopen = null
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close()
      }
      this.ws = null
    }
    this.eventCallbacks = []
    this.statusCallbacks = []
    this.reconnectAttempts = 0
  }

  /**
   * 获取当前连接状态。
   */
  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }
}

export const websocketService = new WebSocketService()
