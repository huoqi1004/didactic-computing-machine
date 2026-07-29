import { useState } from 'react'
import {
  Bot,
  Pause,
  Play,
  Hand,
  Cpu,
  MemoryStick,
  Activity,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Search,
  Shield,
} from 'lucide-react'
import { useSystemStore, type Agent, type AgentStatus } from '../stores/systemStore'
import { AGENT_STATUS_CONFIG, formatNumber, timeAgo, cn } from '../lib/utils'

export default function Agents() {
  const agents = useSystemStore((s) => s.agents)
  const controlAgent = useSystemStore((s) => s.controlAgent)
  const [search, setSearch] = useState('')
  const [filterStatus, setFilterStatus] = useState<AgentStatus | 'all'>('all')
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const filtered = agents.filter((a) => {
    const matchSearch =
      a.name.toLowerCase().includes(search.toLowerCase()) ||
      a.id.toLowerCase().includes(search.toLowerCase()) ||
      a.type.toLowerCase().includes(search.toLowerCase())
    const matchStatus = filterStatus === 'all' || a.status === filterStatus
    return matchSearch && matchStatus
  })

  const handleAction = async (agent: Agent, action: 'pause' | 'resume' | 'takeover') => {
    setActionLoading(`${agent.id}-${action}`)
    await controlAgent(agent.id, action)
    setTimeout(() => setActionLoading(null), 600)
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">Agent 管理</h2>
          <p className="text-sm text-slate-500 mt-0.5">智能体运行状态与人工接管控制</p>
        </div>
        <div className="flex items-center gap-2">
          {(['all', 'running', 'paused', 'circuit_open', 'idle'] as const).map((st) => (
            <button
              key={st}
              onClick={() => setFilterStatus(st)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
                filterStatus === st
                  ? 'bg-primary text-white shadow-glow'
                  : 'bg-bg-card text-slate-400 hover:text-white border border-[#2a3142]',
              )}
            >
              {st === 'all' ? '全部' : AGENT_STATUS_CONFIG[st as AgentStatus].label}
              <span className="ml-1.5 text-[10px] opacity-70">
                {st === 'all' ? agents.length : agents.filter((a) => a.status === st).length}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* 搜索栏 */}
      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索 Agent 名称、ID 或类型..."
          className="input pl-9"
        />
      </div>

      {/* Agent 卡片网格 */}
      {filtered.length === 0 ? (
        <div className="card text-center py-12 text-slate-500">未找到匹配的 Agent</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              actionLoading={actionLoading}
              onAction={handleAction}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function AgentCard({
  agent,
  actionLoading,
  onAction,
}: {
  agent: Agent
  actionLoading: string | null
  onAction: (agent: Agent, action: 'pause' | 'resume' | 'takeover') => void
}) {
  const cfg = AGENT_STATUS_CONFIG[agent.status]
  const isLoading = (action: string) => actionLoading === `${agent.id}-${action}`

  return (
    <div className="card card-hover relative overflow-hidden">
      {/* 顶部状态条 */}
      <div className={cn('absolute top-0 left-0 right-0 h-0.5', cfg.dot)} />

      {/* 头部 */}
      <div className="flex items-start gap-3 mb-4">
        <div
          className={cn(
            'w-11 h-11 rounded-xl flex items-center justify-center shrink-0 relative',
            cfg.bg,
          )}
        >
          <Bot className={cn('w-5 h-5', cfg.color)} />
          {agent.status === 'running' && (
            <span className={cn('absolute -top-0.5 -right-0.5 pulse-dot', cfg.dot)} />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-white truncate">{agent.name}</h3>
            <span className={cn('badge', cfg.bg, cfg.color, cfg.border, 'py-0')}>
              <span className={cn('w-1.5 h-1.5 rounded-full', cfg.dot)} />
              {cfg.label}
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{agent.id}</p>
          <p className="text-xs text-slate-400 mt-1 line-clamp-2">{agent.description}</p>
        </div>
      </div>

      {/* 能力标签 */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        {agent.capabilities.map((cap) => (
          <span
            key={cap}
            className="px-2 py-0.5 rounded text-[11px] bg-bg-input text-slate-400 border border-[#2a3142]"
          >
            {cap}
          </span>
        ))}
      </div>

      {/* 指标 */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <Metric
          icon={CheckCircle2}
          label="完成任务"
          value={formatNumber(agent.tasks_completed)}
          color="text-accent"
        />
        <Metric
          icon={XCircle}
          label="失败任务"
          value={formatNumber(agent.tasks_failed)}
          color="text-status-circuit"
        />
        <Metric
          icon={Activity}
          label="成功率"
          value={`${agent.success_rate}%`}
          color="text-primary"
        />
        <Metric
          icon={Cpu}
          label="CPU"
          value={agent.cpu_usage > 0 ? `${agent.cpu_usage}%` : '—'}
          color="text-blue-400"
        />
      </div>

      {/* 运行模式 + 心跳 */}
      <div className="flex items-center justify-between text-[11px] text-slate-500 mb-4 pb-4 border-b border-[#1e2536]">
        <span className="flex items-center gap-1.5">
          <Shield className="w-3.5 h-3.5" />
          {agent.mode === 'autonomous' ? '自主运行' : agent.mode === 'supervised' ? '监督模式' : '人工接管'}
        </span>
        <span>心跳 {timeAgo(agent.last_heartbeat)}</span>
      </div>

      {/* 操作按钮 */}
      <div className="flex gap-2">
        {agent.status === 'running' ? (
          <button
            onClick={() => onAction(agent, 'pause')}
            disabled={isLoading('pause')}
            className="btn-secondary flex-1 !py-1.5 text-xs"
          >
            {isLoading('pause') ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Pause className="w-3.5 h-3.5" />
            )}
            暂停
          </button>
        ) : (
          <button
            onClick={() => onAction(agent, 'resume')}
            disabled={isLoading('resume')}
            className="btn-accent flex-1 !py-1.5 text-xs"
          >
            {isLoading('resume') ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            恢复
          </button>
        )}
        <button
          onClick={() => onAction(agent, 'takeover')}
          disabled={isLoading('takeover') || agent.mode === 'manual'}
          className="btn-secondary flex-1 !py-1.5 text-xs"
        >
          {isLoading('takeover') ? (
            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Hand className="w-3.5 h-3.5" />
          )}
          接管
        </button>
      </div>
    </div>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: typeof Cpu
  label: string
  value: string
  color: string
}) {
  return (
    <div className="flex items-center gap-2 p-2 rounded-lg bg-bg-input">
      <Icon className={cn('w-4 h-4 shrink-0', color)} />
      <div className="min-w-0">
        <p className="text-[10px] text-slate-500 leading-tight">{label}</p>
        <p className={cn('text-sm font-semibold tabular-nums leading-tight', color)}>{value}</p>
      </div>
    </div>
  )
}
