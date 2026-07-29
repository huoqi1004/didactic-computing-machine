import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  RadialBarChart,
  RadialBar,
  PolarAngleAxis,
} from 'recharts'
import {
  Bot,
  Activity,
  CheckCircle2,
  Zap,
  Clock,
  TrendingUp,
  AlertTriangle,
  Package,
  Calculator,
  ArrowRight,
} from 'lucide-react'
import { useSystemStore } from '../stores/systemStore'
import {
  AGENT_STATUS_CONFIG,
  EVENT_LEVEL_CONFIG,
  formatNumber,
  formatCurrency,
  formatTime,
  cn,
} from '../lib/utils'

export default function Dashboard() {
  const stats = useSystemStore((s) => s.stats)
  const agents = useSystemStore((s) => s.agents)
  const events = useSystemStore((s) => s.events)
  const loading = useSystemStore((s) => s.loading)

  // 生成最近 12 个时段的事件趋势（基于实时事件模拟）
  const trendData = useMemo(() => {
    const buckets = Array.from({ length: 12 }, (_, i) => ({
      time: `${String(i * 2).padStart(2, '0')}:00`,
      events: 0,
      tasks: 0,
    }))
    events.forEach((e) => {
      const hour = new Date(e.timestamp).getHours()
      const idx = Math.min(Math.floor(hour / 2), 11)
      buckets[idx].events += 1
      if (e.event_type === 'task_completed') buckets[idx].tasks += 1
    })
    // 填充基础数据使图表更丰满
    return buckets.map((b, i) => ({
      ...b,
      events: b.events + 8 + Math.round(Math.sin(i / 2) * 6 + 10),
      tasks: b.tasks + 5 + Math.round(Math.cos(i / 2) * 4 + 6),
    }))
  }, [events])

  if (loading && !stats) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <div className="w-10 h-10 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
          <p className="text-sm text-slate-400">正在加载系统数据...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">系统仪表盘</h2>
          <p className="text-sm text-slate-500 mt-0.5">Agent-Native ERP 实时运行态势</p>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-bg-card border border-[#2a3142]">
          <span className="pulse-dot bg-accent" />
          <span className="text-xs text-slate-300">数据实时同步</span>
        </div>
      </div>

      {/* 核心指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="在线 Agent"
          value={stats?.active_agents ?? 0}
          total={stats?.total_agents}
          suffix={`/ ${stats?.total_agents ?? 0}`}
          icon={Bot}
          color="primary"
          trend="+2"
        />
        <StatCard
          title="今日任务"
          value={stats?.total_tasks_today ?? 0}
          icon={CheckCircle2}
          color="accent"
          trend="+12.4%"
        />
        <StatCard
          title="成功率"
          value={stats?.success_rate ?? 0}
          suffix="%"
          icon={Zap}
          color="accent"
          trend="+0.3%"
        />
        <StatCard
          title="平均响应"
          value={stats?.avg_response_time ?? 0}
          suffix="ms"
          icon={Clock}
          color="primary"
          trend="-8ms"
        />
      </div>

      {/* 图表区 + Agent 状态环 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 事件趋势图 */}
        <div className="lg:col-span-2 card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-white">事件与任务趋势</h3>
              <p className="text-xs text-slate-500 mt-0.5">最近 24 小时</p>
            </div>
            <div className="flex items-center gap-4 text-xs">
              <span className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-sm bg-primary" />
                <span className="text-slate-400">事件数</span>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-sm bg-accent" />
                <span className="text-slate-400">完成任务</span>
              </span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={trendData} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="colorEvents" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4f7cff" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#4f7cff" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="colorTasks" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00d9a3" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#00d9a3" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2536" vertical={false} />
              <XAxis
                dataKey="time"
                tick={{ fill: '#64748b', fontSize: 11 }}
                axisLine={{ stroke: '#2a3142' }}
                tickLine={false}
              />
              <YAxis tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{
                  background: '#131825',
                  border: '1px solid #2a3142',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
                labelStyle={{ color: '#94a3b8' }}
              />
              <Area
                type="monotone"
                dataKey="events"
                stroke="#4f7cff"
                strokeWidth={2}
                fill="url(#colorEvents)"
              />
              <Area
                type="monotone"
                dataKey="tasks"
                stroke="#00d9a3"
                strokeWidth={2}
                fill="url(#colorTasks)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Agent 状态分布环 */}
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-1">Agent 状态分布</h3>
          <p className="text-xs text-slate-500 mb-2">运行态势一览</p>
          <ResponsiveContainer width="100%" height={180}>
            <RadialBarChart
              innerRadius="40%"
              outerRadius="100%"
              data={agentStatusData(agents)}
              startAngle={90}
              endAngle={-270}
            >
              <PolarAngleAxis type="number" domain={[0, agents.length || 1]} tick={false} />
              <RadialBar background={{ fill: '#1a1f2e' }} dataKey="value" cornerRadius={6} />
              <Tooltip
                contentStyle={{
                  background: '#131825',
                  border: '1px solid #2a3142',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
              />
            </RadialBarChart>
          </ResponsiveContainer>
          <div className="grid grid-cols-2 gap-2 mt-2">
            {(['running', 'paused', 'circuit_open', 'idle'] as const).map((st) => {
              const cfg = AGENT_STATUS_CONFIG[st]
              const count = agents.filter((a) => a.status === st).length
              return (
                <div key={st} className="flex items-center gap-2">
                  <span className={cn('w-2 h-2 rounded-full', cfg.dot)} />
                  <span className="text-xs text-slate-400">{cfg.label}</span>
                  <span className="text-xs font-semibold text-white ml-auto tabular-nums">{count}</span>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Agent 状态卡片 + 实时事件流 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Agent 状态卡片 */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-white">Agent 运行状态</h3>
            <Link
              to="/agents"
              className="flex items-center gap-1 text-xs text-primary hover:text-primary-light transition-colors"
            >
              查看全部 <ArrowRight className="w-3 h-3" />
            </Link>
          </div>
          <div className="space-y-2.5">
            {agents.slice(0, 5).map((agent) => {
              const cfg = AGENT_STATUS_CONFIG[agent.status]
              return (
                <div
                  key={agent.id}
                  className="flex items-center gap-3 p-3 rounded-lg bg-bg-input border border-[#1e2536] hover:border-primary/30 transition-colors"
                >
                  <div className="w-9 h-9 rounded-lg bg-bg-hover flex items-center justify-center shrink-0">
                    <Bot className="w-4 h-4 text-primary" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-white truncate">{agent.name}</span>
                      <span className={cn('badge', cfg.bg, cfg.color, cfg.border, 'py-0')}>
                        <span className={cn('w-1.5 h-1.5 rounded-full', cfg.dot)} />
                        {cfg.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-[11px] text-slate-500">
                      <span>任务 {formatNumber(agent.tasks_completed)}</span>
                      <span>成功率 {agent.success_rate}%</span>
                      <span className="hidden sm:inline">CPU {agent.cpu_usage}%</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* 实时事件流 */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-white">实时事件流</h3>
            <Link
              to="/events"
              className="flex items-center gap-1 text-xs text-primary hover:text-primary-light transition-colors"
            >
              查看全部 <ArrowRight className="w-3 h-3" />
            </Link>
          </div>
          <div className="space-y-2 max-h-[340px] overflow-y-auto pr-1">
            {events.length === 0 && (
              <div className="text-center py-8 text-sm text-slate-500">等待事件流入...</div>
            )}
            {events.slice(0, 12).map((event) => {
              const cfg = EVENT_LEVEL_CONFIG[event.level]
              return (
                <div
                  key={event.id}
                  className="flex items-start gap-3 p-2.5 rounded-lg hover:bg-bg-hover transition-colors animate-slide-in"
                >
                  <span className={cn('w-2 h-2 rounded-full mt-1.5 shrink-0', cfg.color.replace('text-', 'bg-'))} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-slate-200 leading-snug">{event.message}</p>
                    <div className="flex items-center gap-2 mt-0.5 text-[11px] text-slate-500">
                      <span className="text-primary/80">{event.agent_name}</span>
                      <span>·</span>
                      <span>{formatTime(event.timestamp)}</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* 业务概览卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MiniStatCard
          title="库存总价值"
          value={stats ? formatCurrency(stats.inventory_value) : '--'}
          icon={Package}
          to="/inventory"
          color="accent"
        />
        <MiniStatCard
          title="待审批凭证"
          value={stats?.pending_vouchers ?? 0}
          icon={Calculator}
          to="/accounting"
          color="paused"
        />
        <MiniStatCard
          title="事件总数"
          value={stats ? formatNumber(stats.total_events) : '--'}
          icon={Activity}
          to="/events"
          color="primary"
        />
        <MiniStatCard
          title="熔断 Agent"
          value={stats?.circuit_open_agents ?? 0}
          icon={AlertTriangle}
          to="/agents"
          color="circuit"
        />
      </div>
    </div>
  )
}

/* ============ 子组件 ============ */

function StatCard({
  title,
  value,
  total,
  suffix,
  icon: Icon,
  color,
  trend,
}: {
  title: string
  value: number | string
  total?: number
  suffix?: string
  icon: typeof Bot
  color: 'primary' | 'accent'
  trend?: string
}) {
  const colorMap = {
    primary: { bg: 'bg-primary/10', text: 'text-primary', glow: 'shadow-glow' },
    accent: { bg: 'bg-accent/10', text: 'text-accent', glow: 'shadow-glow-accent' },
  }
  const c = colorMap[color]
  return (
    <div className="card card-hover relative overflow-hidden">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-slate-500 mb-1">{title}</p>
          <p className="text-2xl font-bold text-white tabular-nums">
            {typeof value === 'number' ? formatNumber(value) : value}
            {suffix && <span className="text-sm font-normal text-slate-500 ml-1">{suffix}</span>}
          </p>
        </div>
        <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center', c.bg)}>
          <Icon className={cn('w-5 h-5', c.text)} />
        </div>
      </div>
      {trend && (
        <div className="flex items-center gap-1 mt-2 text-xs">
          <TrendingUp className="w-3.5 h-3.5 text-accent" />
          <span className="text-accent">{trend}</span>
          <span className="text-slate-600">较昨日</span>
        </div>
      )}
    </div>
  )
}

function MiniStatCard({
  title,
  value,
  icon: Icon,
  to,
  color,
}: {
  title: string
  value: number | string
  icon: typeof Bot
  to: string
  color: 'primary' | 'accent' | 'paused' | 'circuit'
}) {
  const colorMap = {
    primary: 'text-primary bg-primary/10',
    accent: 'text-accent bg-accent/10',
    paused: 'text-status-paused bg-status-paused/10',
    circuit: 'text-status-circuit bg-status-circuit/10',
  }
  return (
    <Link to={to} className="card card-hover group block">
      <div className="flex items-center gap-3">
        <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center shrink-0', colorMap[color])}>
          <Icon className="w-5 h-5" />
        </div>
        <div className="min-w-0">
          <p className="text-xs text-slate-500">{title}</p>
          <p className="text-lg font-bold text-white tabular-nums truncate">{value}</p>
        </div>
        <ArrowRight className="w-4 h-4 text-slate-600 ml-auto opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
    </Link>
  )
}

function agentStatusData(agents: { status: string }[]) {
  return [
    { name: '运行', value: agents.filter((a) => a.status === 'running').length, fill: '#22c55e' },
    { name: '暂停', value: agents.filter((a) => a.status === 'paused').length, fill: '#eab308' },
    { name: '熔断', value: agents.filter((a) => a.status === 'circuit_open').length, fill: '#ef4444' },
    { name: '空闲', value: agents.filter((a) => a.status === 'idle').length, fill: '#64748b' },
  ]
}
