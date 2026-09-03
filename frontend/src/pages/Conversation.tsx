import { useState, useRef, useEffect, useMemo } from 'react'
import {
  Package,
  Calculator,
  ShoppingCart,
  ShieldAlert,
  Wallet,
  Search,
  Send,
  Trash2,
  ChevronDown,
  ChevronRight,
  Brain,
  User,
  AlertCircle,
  Table2,
  BarChart3,
  ListOrdered,
  type LucideIcon,
} from 'lucide-react'
import { useSystemStore, type ChatAgent, type ChatMessage, type DataCard as DataCardType } from '../stores/systemStore'
import { cn, formatTime } from '../lib/utils'

/* Agent 图标映射 */
const AGENT_ICON_MAP: Record<string, LucideIcon> = {
  Package,
  Calculator,
  ShoppingCart,
  ShieldAlert,
  Wallet,
  Search,
}

/* Agent 状态配置 */
const AGENT_STATUS_MAP: Record<
  string,
  { label: string; dot: string; text: string; bg: string }
> = {
  online: { label: '在线', dot: 'bg-status-running', text: 'text-status-running', bg: 'bg-status-running/10' },
  busy: { label: '忙碌', dot: 'bg-status-paused', text: 'text-status-paused', bg: 'bg-status-paused/10' },
  offline: { label: '离线', dot: 'bg-status-idle', text: 'text-status-idle', bg: 'bg-status-idle/10' },
}

/* 数据卡片色调映射 */
const TONE_COLOR_MAP: Record<string, string> = {
  accent: 'text-accent',
  primary: 'text-primary',
  paused: 'text-status-paused',
  circuit: 'text-status-circuit',
}

export default function Conversation() {
  const chatAgents = useSystemStore((s) => s.chatAgents)
  const activeAgentId = useSystemStore((s) => s.activeAgentId)
  const conversationMessages = useSystemStore((s) => s.conversationMessages)
  const conversationLoading = useSystemStore((s) => s.conversationLoading)
  const initChatAgents = useSystemStore((s) => s.initChatAgents)
  const setActiveAgent = useSystemStore((s) => s.setActiveAgent)
  const sendMessage = useSystemStore((s) => s.sendMessage)
  const clearConversation = useSystemStore((s) => s.clearConversation)

  const [input, setInput] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const activeAgent = useMemo(
    () => chatAgents.find((a) => a.id === activeAgentId),
    [chatAgents, activeAgentId],
  )

  /* 初始化 Agent 列表 */
  useEffect(() => {
    if (chatAgents.length === 0) {
      initChatAgents()
    }
  }, [chatAgents.length, initChatAgents])

  /* 自动滚动到底部 */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversationMessages, conversationLoading])

  /* 自适应输入框高度 */
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`
    }
  }, [input])

  const handleSend = () => {
    const text = input.trim()
    if (!text || conversationLoading) return
    if (activeAgent?.status === 'offline') return
    setInput('')
    sendMessage(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleQuickCommand = (cmd: string) => {
    if (conversationLoading || activeAgent?.status === 'offline') return
    sendMessage(cmd)
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4 animate-fade-in">
      {/* ========== 左侧 Agent 侧边栏 ========== */}
      <aside
        className={cn(
          'fixed lg:relative z-40 lg:z-auto top-0 left-0 bottom-0 lg:top-auto lg:left-auto lg:bottom-auto',
          'w-72 shrink-0 flex flex-col',
          'bg-bg-card/95 lg:bg-bg-card/60 backdrop-blur-md border border-[#1e2536] rounded-card',
          'transition-transform duration-300',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
        )}
      >
        {/* 侧边栏标题 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2536]">
          <h3 className="text-sm font-semibold text-white">选择对话 Agent</h3>
          <button
            onClick={() => setSidebarOpen(false)}
            className="lg:hidden text-slate-400 hover:text-white"
          >
            <ChevronDown className="w-4 h-4" />
          </button>
        </div>

        {/* Agent 列表 */}
        <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
          {chatAgents.length === 0 && (
            <div className="text-center py-8 text-xs text-slate-500">加载 Agent 列表...</div>
          )}
          {chatAgents.map((agent) => (
            <AgentListItem
              key={agent.id || Math.random()}
              agent={agent}
              active={agent.id === activeAgentId}
              onClick={() => {
                setActiveAgent(agent.id)
                setSidebarOpen(false)
              }}
            />
          ))}
        </div>

        {/* 快捷指令区 */}
        {activeAgent && (
          <div className="p-3 border-t border-[#1e2536]">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
              快捷指令
            </p>
            <div className="flex flex-wrap gap-1.5">
              {(activeAgent.quickCommands || []).map((cmd) => (
                <button
                  key={cmd}
                  onClick={() => handleQuickCommand(cmd)}
                  disabled={conversationLoading || activeAgent.status === 'offline'}
                  className="px-2.5 py-1 rounded-full text-[11px] font-medium bg-bg-input text-slate-300 border border-[#2a3142] hover:border-primary/40 hover:text-primary transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {cmd}
                </button>
              ))}
            </div>
          </div>
        )}
      </aside>

      {/* 移动端遮罩 */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ========== 右侧对话区域 ========== */}
      <div className="flex-1 flex flex-col min-w-0 bg-bg-card/60 backdrop-blur-md border border-[#1e2536] rounded-card overflow-hidden">
        {/* 顶部 Agent 状态栏 */}
        {activeAgent && (
          <div className="flex items-center gap-3 px-4 py-3 border-b border-[#1e2536] bg-bg-card/80">
            <button
              onClick={() => setSidebarOpen(true)}
              className="lg:hidden text-slate-400 hover:text-white"
            >
              <ChevronRight className="w-5 h-5 rotate-180" />
            </button>
            <AgentAvatar agent={activeAgent} size="md" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white truncate">{activeAgent.name}</h3>
                <AgentStatusBadge status={activeAgent.status} />
              </div>
              <p className="text-xs text-slate-500 truncate">{activeAgent.description}</p>
            </div>
            <button
              onClick={clearConversation}
              className="btn-ghost !px-2 !py-1.5 text-xs"
              title="清空当前对话"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">清空</span>
            </button>
          </div>
        )}

        {/* 消息区域 */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {conversationMessages.length === 0 && !conversationLoading && (
            <EmptyState />
          )}
          {conversationMessages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} agentIcon={activeAgent?.icon} />
          ))}
          {conversationLoading && <TypingIndicator agentName={activeAgent?.name || 'Agent'} />}
          <div ref={messagesEndRef} />
        </div>

        {/* 底部输入栏 */}
        <div className="border-t border-[#1e2536] bg-bg-card/80 p-3">
          {activeAgent?.status === 'offline' && (
            <div className="flex items-center gap-2 mb-2 px-3 py-1.5 rounded-lg bg-status-idle/10 border border-status-idle/30">
              <AlertCircle className="w-3.5 h-3.5 text-status-idle" />
              <span className="text-xs text-status-idle">该 Agent 当前处于离线状态，无法对话</span>
            </div>
          )}
          <div className="flex items-end gap-2">
            <div className="flex-1 relative">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  activeAgent?.status === 'offline'
                    ? 'Agent 离线，无法发送消息...'
                    : `向 ${activeAgent?.name || 'Agent'} 发送消息，回车发送，Shift+回车换行...`
                }
                disabled={activeAgent?.status === 'offline'}
                rows={1}
                className="w-full resize-none bg-bg-input border border-[#2a3142] rounded-xl px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ maxHeight: '120px' }}
              />
            </div>
            <button
              onClick={handleSend}
              disabled={!input.trim() || conversationLoading || activeAgent?.status === 'offline'}
              className="btn-primary !rounded-xl !px-4 !py-3 shrink-0"
            >
              <Send className="w-4 h-4" />
              <span className="hidden sm:inline">发送</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ============ 子组件 ============ */

function AgentAvatar({ agent, size = 'sm' }: { agent: ChatAgent; size?: 'sm' | 'md' }) {
  const Icon = (agent?.icon && AGENT_ICON_MAP[agent.icon]) || Package
  const sizeClass = size === 'md' ? 'w-9 h-9' : 'w-8 h-8'
  const iconClass = size === 'md' ? 'w-5 h-5' : 'w-4 h-4'
  return (
    <div
      className={cn(
        'rounded-lg flex items-center justify-center shrink-0 bg-gradient-primary shadow-glow',
        sizeClass,
      )}
    >
      <Icon className={cn('text-white', iconClass)} />
    </div>
  )
}

function AgentStatusBadge({ status }: { status: string }) {
  const cfg = AGENT_STATUS_MAP[status] || AGENT_STATUS_MAP.offline
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium border border-[#2a3142]', cfg.bg, cfg.text)}>
      <span className={cn('w-1.5 h-1.5 rounded-full', cfg.dot)} />
      {cfg.label}
    </span>
  )
}

function AgentListItem({
  agent,
  active,
  onClick,
}: {
  agent: ChatAgent
  active: boolean
  onClick: () => void
}) {
  const cfg = AGENT_STATUS_MAP[agent.status] || AGENT_STATUS_MAP.offline
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-3 p-2.5 rounded-lg text-left transition-all duration-200 border',
        active
          ? 'bg-primary/15 border-primary/30 shadow-glow'
          : 'bg-transparent border-transparent hover:bg-bg-hover',
      )}
    >
      <div className="relative shrink-0">
        <AgentAvatar agent={agent} size="sm" />
        <span
          className={cn(
            'absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-bg-card',
            cfg.dot,
          )}
        />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={cn('text-sm font-medium truncate', active ? 'text-white' : 'text-slate-300')}>
            {agent.name}
          </span>
        </div>
        <p className="text-[11px] text-slate-500 truncate">{agent.description}</p>
      </div>
    </button>
  )
}

function MessageBubble({
  message,
  agentIcon,
}: {
  message: ChatMessage
  agentIcon?: string
}) {
  if (!message || !message.role) {
    return null
  }
  const isUser = message.role === 'user'
  const Icon = isUser ? User : AGENT_ICON_MAP[agentIcon || 'Package'] || Package

  return (
    <div className={cn('flex gap-3 animate-slide-in', isUser ? 'flex-row-reverse' : 'flex-row')}>
      {/* 头像 */}
      <div
        className={cn(
          'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5',
          isUser ? 'bg-bg-hover' : 'bg-gradient-primary shadow-glow',
        )}
      >
        <Icon className={cn('w-4 h-4', isUser ? 'text-slate-400' : 'text-white')} />
      </div>

      {/* 消息内容 */}
      <div className={cn('max-w-[80%] space-y-2', isUser ? 'items-end' : 'items-start')}>
        {/* 消息头 */}
        <div className={cn('flex items-center gap-2 text-[11px] text-slate-500', isUser ? 'flex-row-reverse' : 'flex-row')}>
          <span className="font-medium text-slate-400">{message.agentName}</span>
          <span>{formatTime(message.timestamp)}</span>
          {message.status === 'error' && (
            <span className="flex items-center gap-1 text-status-circuit">
              <AlertCircle className="w-3 h-3" />
              发送失败
            </span>
          )}
        </div>

        {/* 气泡 */}
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
            isUser
              ? 'bg-primary text-white rounded-tr-md'
              : 'bg-bg-input text-slate-200 border border-[#2a3142] rounded-tl-md',
          )}
        >
          {message.content}
        </div>

        {/* 思考过程折叠面板 */}
        {!isUser && message.thinkingProcess && Array.isArray(message.thinkingProcess) && message.thinkingProcess.length > 0 && (
          <ThinkingProcess steps={message.thinkingProcess} />
        )}

        {/* 数据卡片 */}
        {!isUser && message.dataCard && <DataCard card={message.dataCard} />}
      </div>
    </div>
  )
}

function ThinkingProcess({ steps }: { steps: string[] }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="rounded-lg border border-[#1e2536] bg-bg-deep/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs text-slate-400 hover:text-slate-300 transition-colors"
      >
        <Brain className="w-3.5 h-3.5 text-primary" />
        <span className="font-medium">Agent 思考过程</span>
        <span className="text-slate-600">({steps.length} 步)</span>
        <ChevronDown
          className={cn('w-3.5 h-3.5 ml-auto transition-transform', expanded && 'rotate-180')}
        />
      </button>
      {expanded && (
        <div className="px-3 pb-3 pt-1 space-y-1.5">
          {steps.map((step, i) => (
            <div key={i} className="flex items-start gap-2 text-[11px] text-slate-500 font-mono">
              <span className="text-primary/60 shrink-0">{String(i + 1).padStart(2, '0')}</span>
              <span className="leading-relaxed">{step}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function DataCard({ card }: { card: DataCardType }) {
  if (!card || !card.type) {
    return null
  }
  const Icon = card.type === 'table' ? Table2 : card.type === 'stats' ? BarChart3 : ListOrdered

  return (
    <div className="rounded-lg border border-[#2a3142] bg-bg-card/80 overflow-hidden">
      {/* 卡片标题 */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[#1e2536] bg-bg-hover/50">
        <Icon className="w-3.5 h-3.5 text-accent" />
        <span className="text-xs font-semibold text-white">{card.title}</span>
      </div>

      {/* 统计型卡片 */}
      {card.type === 'stats' && card.stats && (
        <div className="grid grid-cols-2 gap-2 p-3">
          {card.stats.map((stat, i) => (
            <div key={i} className="p-2.5 rounded-lg bg-bg-input border border-[#1e2536]">
              <p className="text-[11px] text-slate-500">{stat.label}</p>
              <p className={cn('text-base font-bold tabular-nums mt-0.5', TONE_COLOR_MAP[stat.tone || 'accent'] || 'text-white')}>
                {stat.value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* 表格型卡片 */}
      {card.type === 'table' && card.columns && card.rows && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[#1e2536]">
                {card.columns.map((col) => (
                  <th
                    key={col.key}
                    className="text-left font-semibold text-slate-400 uppercase tracking-wider px-3 py-2 whitespace-nowrap"
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {card.rows.map((row, i) => (
                <tr key={i} className="border-b border-[#1e2536] last:border-0 hover:bg-bg-hover/50 transition-colors">
                  {card.columns!.map((col) => (
                    <td key={col.key} className="px-3 py-2 text-slate-300 whitespace-nowrap">
                      {String(row[col.key] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 列表型卡片 */}
      {card.type === 'list' && card.items && (
        <div className="p-3 space-y-2">
          {card.items.map((item, i) => (
            <div
              key={i}
              className="flex items-center justify-between gap-3 p-2 rounded-lg bg-bg-input border border-[#1e2536]"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold bg-primary/15 text-primary shrink-0">
                  {i + 1}
                </span>
                <span className="text-xs font-medium text-slate-200 truncate">{item.label}</span>
              </div>
              <span className="text-[11px] text-slate-400 shrink-0">{item.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function TypingIndicator({ agentName }: { agentName: string }) {
  return (
    <div className="flex gap-3 animate-slide-in">
      <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5 bg-gradient-primary shadow-glow">
        <Package className="w-4 h-4 text-white" />
      </div>
      <div className="max-w-[80%]">
        <div className="flex items-center gap-2 text-[11px] text-slate-500 mb-1">
          <span className="font-medium text-slate-400">{agentName}</span>
          <span>正在思考...</span>
        </div>
        <div className="rounded-2xl rounded-tl-md px-4 py-3 bg-bg-input border border-[#2a3142] inline-flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-primary/60 animate-bounce" style={{ animationDelay: '0ms' }} />
          <span className="w-2 h-2 rounded-full bg-primary/60 animate-bounce" style={{ animationDelay: '150ms' }} />
          <span className="w-2 h-2 rounded-full bg-primary/60 animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center py-16">
      <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
        <Brain className="w-8 h-8 text-primary" />
      </div>
      <h3 className="text-base font-semibold text-white mb-1">开始与 Agent 对话</h3>
      <p className="text-sm text-slate-500 max-w-sm">
        选择左侧的 Agent，通过自然语言下达指令或提问。Agent 会展示思考过程并返回结构化数据。
      </p>
      <div className="flex flex-wrap gap-2 mt-4 justify-center">
        {['查询库存状态', '生成日报', '检查合规'].map((cmd) => (
          <span
            key={cmd}
            className="px-3 py-1 rounded-full text-xs bg-bg-input text-slate-400 border border-[#2a3142]"
          >
            {cmd}
          </span>
        ))}
      </div>
    </div>
  )
}
