import { useState, useEffect, useMemo } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  Bell,
  CheckCircle2,
  Clock,
  Search,
  Filter,
  Check,
  X,
  Eye,
  Forward,
  ChevronDown,
  ChevronRight,
  Package,
  Calculator,
  ShoppingCart,
  ShieldAlert,
  FileSearch,
  XCircle,
  History,
  GitBranch,
  Brain,
  FileText,
  type LucideIcon,
} from 'lucide-react'
import {
  useSystemStore,
  type ExceptionItem,
  type ExceptionType,
  type ExceptionStatus,
  type ExceptionCategory,
} from '../stores/systemStore'
import { RISK_LEVEL_CONFIG, cn, timeAgo, formatDateTime } from '../lib/utils'

/* 例外类型配置 */
const EXCEPTION_TYPE_CONFIG: Record<
  ExceptionType,
  { label: string; color: string; bg: string; icon: LucideIcon }
> = {
  approval: { label: '审批', color: 'text-primary', bg: 'bg-primary/10', icon: CheckCircle2 },
  alert: { label: '告警', color: 'text-status-paused', bg: 'bg-status-paused/10', icon: AlertTriangle },
  anomaly: { label: '异常', color: 'text-status-circuit', bg: 'bg-status-circuit/10', icon: AlertCircle },
}

/* 例外状态配置 */
const EXCEPTION_STATUS_CONFIG: Record<
  ExceptionStatus,
  { label: string; color: string; bg: string; dot: string }
> = {
  pending: { label: '待处理', color: 'text-status-paused', bg: 'bg-status-paused/10', dot: 'bg-status-paused' },
  resolved: { label: '已处理', color: 'text-accent', bg: 'bg-accent/10', dot: 'bg-accent' },
  timeout: { label: '已超时', color: 'text-status-circuit', bg: 'bg-status-circuit/10', dot: 'bg-status-circuit' },
}

/* 例外分类配置 */
const EXCEPTION_CATEGORY_CONFIG: Record<
  ExceptionCategory,
  { label: string; bg: string; icon: LucideIcon }
> = {
  restock: { label: '补货审批', bg: 'bg-primary/10', icon: Package },
  voucher: { label: '凭证审批', bg: 'bg-accent/10', icon: Calculator },
  purchase: { label: '采购审批', bg: 'bg-blue-500/10', icon: ShoppingCart },
  audit: { label: '内审告警', bg: 'bg-status-paused/10', icon: ShieldAlert },
  compliance: { label: '合规违规', bg: 'bg-status-circuit/10', icon: FileSearch },
}

/* 事件级别配置 */
const EVENT_LEVEL_CONFIG: Record<string, { color: string; dot: string }> = {
  info: { color: 'text-blue-400', dot: 'bg-blue-500' },
  success: { color: 'text-accent', dot: 'bg-accent' },
  warning: { color: 'text-status-paused', dot: 'bg-status-paused' },
  error: { color: 'text-status-circuit', dot: 'bg-status-circuit' },
}

export default function Exceptions() {
  const exceptions = useSystemStore((s) => s.exceptions)
  const exceptionStats = useSystemStore((s) => s.exceptionStats)
  const exceptionsLoading = useSystemStore((s) => s.exceptionsLoading)
  const selectedExceptionId = useSystemStore((s) => s.selectedExceptionId)
  const fetchExceptions = useSystemStore((s) => s.fetchExceptions)
  const resolveException = useSystemStore((s) => s.resolveException)
  const selectException = useSystemStore((s) => s.selectException)

  const [search, setSearch] = useState('')
  const [filterType, setFilterType] = useState<ExceptionType | 'all'>('all')
  const [filterStatus, setFilterStatus] = useState<ExceptionStatus | 'all'>('all')
  const [filterAgent, setFilterAgent] = useState<string>('all')
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  /* 初始化加载 */
  useEffect(() => {
    if (exceptions.length === 0) {
      fetchExceptions()
    }
  }, [exceptions.length, fetchExceptions])

  /* 获取所有 Agent 选项（用于筛选） */
  const agentOptions = useMemo(() => {
    const map = new Map<string, string>()
    exceptions.forEach((e) => map.set(e.sourceAgentId, e.sourceAgent))
    return Array.from(map.entries()).map(([id, name]) => ({ id, name }))
  }, [exceptions])

  /* 过滤后的例外列表（按优先级排序） */
  const filtered = useMemo(() => {
    const riskOrder: Record<string, number> = { high: 0, medium: 1, low: 2 }
    return exceptions
      .filter((e) => {
        const matchSearch =
          e.title.toLowerCase().includes(search.toLowerCase()) ||
          e.description.toLowerCase().includes(search.toLowerCase()) ||
          e.id.toLowerCase().includes(search.toLowerCase())
        const matchType = filterType === 'all' || e.type === filterType
        const matchStatus = filterStatus === 'all' || e.status === filterStatus
        const matchAgent = filterAgent === 'all' || e.sourceAgentId === filterAgent
        return matchSearch && matchType && matchStatus && matchAgent
      })
      .sort((a, b) => {
        // 待处理优先
        if (a.status === 'pending' && b.status !== 'pending') return -1
        if (a.status !== 'pending' && b.status === 'pending') return 1
        // 按风险等级排序
        const riskDiff = (riskOrder[a.riskLevel] ?? 9) - (riskOrder[b.riskLevel] ?? 9)
        if (riskDiff !== 0) return riskDiff
        // 按创建时间倒序
        return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
      })
  }, [exceptions, search, filterType, filterStatus, filterAgent])

  const selectedException = useMemo(
    () => exceptions.find((e) => e.id === selectedExceptionId) || null,
    [exceptions, selectedExceptionId],
  )

  const handleAction = async (
    exception: ExceptionItem,
    action: 'approve' | 'reject' | 'transfer',
  ) => {
    setActionLoading(`${exception.id}-${action}`)
    await resolveException(exception.id, action)
    setTimeout(() => setActionLoading(null), 600)
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">例外管理</h2>
          <p className="text-sm text-slate-500 mt-0.5">集中处理需要人工干预的 Agent 决策例外事项</p>
        </div>
        <button
          onClick={() => fetchExceptions()}
          className="btn-secondary !py-1.5 text-xs"
        >
          <History className="w-3.5 h-3.5" />
          刷新
        </button>
      </div>

      {/* 统计卡片 */}
      <ExceptionStats stats={exceptionStats} loading={exceptionsLoading} />

      {/* 筛选栏 */}
      <ExceptionFilters
        search={search}
        setSearch={setSearch}
        filterType={filterType}
        setFilterType={setFilterType}
        filterStatus={filterStatus}
        setFilterStatus={setFilterStatus}
        filterAgent={filterAgent}
        setFilterAgent={setFilterAgent}
        agentOptions={agentOptions}
      />

      {/* 主体：列表 + 详情面板 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 例外列表 */}
        <div className={cn('space-y-3', selectedException ? 'lg:col-span-2' : 'lg:col-span-3')}>
          {exceptionsLoading && exceptions.length === 0 ? (
            <div className="card text-center py-12">
              <div className="w-8 h-8 border-2 border-primary/30 border-t-primary rounded-full animate-spin mx-auto mb-3" />
              <p className="text-sm text-slate-400">加载例外事项...</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="card text-center py-12 text-slate-500">
              <CheckCircle2 className="w-10 h-10 text-accent/40 mx-auto mb-2" />
              <p className="text-sm">暂无匹配的例外事项</p>
            </div>
          ) : (
            filtered.map((exception) => (
              <ExceptionCard
                key={exception.id}
                exception={exception}
                selected={exception.id === selectedExceptionId}
                onSelect={() => selectException(exception.id)}
                onAction={handleAction}
                actionLoading={actionLoading}
              />
            ))
          )}
        </div>

        {/* 详情侧边面板 */}
        {selectedException && (
          <div className="lg:col-span-1">
            <ExceptionDetail
              exception={selectedException}
              onClose={() => selectException(null)}
              onAction={handleAction}
              actionLoading={actionLoading}
            />
          </div>
        )}
      </div>
    </div>
  )
}

/* ============ 子组件 ============ */

function ExceptionStats({
  stats,
  loading,
}: {
  stats: { pending: number; todayNew: number; resolved: number; timeoutWarning: number } | null
  loading: boolean
}) {
  const cards = [
    {
      title: '待处理例外',
      value: stats?.pending ?? 0,
      icon: AlertCircle,
      color: 'text-status-paused',
      bg: 'bg-status-paused/10',
      suffix: '项',
    },
    {
      title: '今日新增',
      value: stats?.todayNew ?? 0,
      icon: Bell,
      color: 'text-primary',
      bg: 'bg-primary/10',
      suffix: '项',
    },
    {
      title: '已处理',
      value: stats?.resolved ?? 0,
      icon: CheckCircle2,
      color: 'text-accent',
      bg: 'bg-accent/10',
      suffix: '项',
    },
    {
      title: '超时预警',
      value: stats?.timeoutWarning ?? 0,
      icon: Clock,
      color: 'text-status-circuit',
      bg: 'bg-status-circuit/10',
      suffix: '项',
    },
  ]

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((card) => (
        <div key={card.title} className="card card-hover relative overflow-hidden">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs text-slate-500 mb-1">{card.title}</p>
              <p className="text-2xl font-bold text-white tabular-nums">
                {loading ? '--' : card.value}
                <span className="text-sm font-normal text-slate-500 ml-1">{card.suffix}</span>
              </p>
            </div>
            <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center', card.bg)}>
              <card.icon className={cn('w-5 h-5', card.color)} />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function ExceptionFilters({
  search,
  setSearch,
  filterType,
  setFilterType,
  filterStatus,
  setFilterStatus,
  filterAgent,
  setFilterAgent,
  agentOptions,
}: {
  search: string
  setSearch: (v: string) => void
  filterType: ExceptionType | 'all'
  setFilterType: (v: ExceptionType | 'all') => void
  filterStatus: ExceptionStatus | 'all'
  setFilterStatus: (v: ExceptionStatus | 'all') => void
  filterAgent: string
  setFilterAgent: (v: string) => void
  agentOptions: { id: string; name: string }[]
}) {
  return (
    <div className="card !p-4 space-y-3">
      {/* 搜索框 */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索例外标题、描述或编号..."
          className="input pl-9"
        />
      </div>

      {/* 筛选标签 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 text-xs text-slate-500 mr-1">
          <Filter className="w-3.5 h-3.5" />
          筛选：
        </span>

        {/* 类型筛选 */}
        <div className="flex items-center gap-1.5">
          {(['all', 'approval', 'alert', 'anomaly'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setFilterType(t)}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs font-medium transition-all',
                filterType === t
                  ? 'bg-primary text-white'
                  : 'bg-bg-input text-slate-400 hover:text-white border border-[#2a3142]',
              )}
            >
              {t === 'all' ? '全部类型' : EXCEPTION_TYPE_CONFIG[t as ExceptionType].label}
            </button>
          ))}
        </div>

        <span className="text-slate-600">|</span>

        {/* 状态筛选 */}
        <div className="flex items-center gap-1.5">
          {(['all', 'pending', 'resolved', 'timeout'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs font-medium transition-all',
                filterStatus === s
                  ? 'bg-accent text-white'
                  : 'bg-bg-input text-slate-400 hover:text-white border border-[#2a3142]',
              )}
            >
              {s === 'all' ? '全部状态' : EXCEPTION_STATUS_CONFIG[s as ExceptionStatus].label}
            </button>
          ))}
        </div>

        <span className="text-slate-600">|</span>

        {/* Agent 筛选 */}
        <select
          value={filterAgent}
          onChange={(e) => setFilterAgent(e.target.value)}
          className="bg-bg-input border border-[#2a3142] rounded-lg px-2.5 py-1 text-xs text-slate-300 focus:outline-none focus:border-primary"
        >
          <option value="all">全部 Agent</option>
          {agentOptions.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}

function ExceptionCard({
  exception,
  selected,
  onSelect,
  onAction,
  actionLoading,
}: {
  exception: ExceptionItem
  selected: boolean
  onSelect: () => void
  onAction: (e: ExceptionItem, action: 'approve' | 'reject' | 'transfer') => void
  actionLoading: string | null
}) {
  const [expanded, setExpanded] = useState(false)
  const typeCfg = EXCEPTION_TYPE_CONFIG[exception.type] ?? Object.values(EXCEPTION_TYPE_CONFIG)[0]
  const statusCfg = EXCEPTION_STATUS_CONFIG[exception.status] ?? Object.values(EXCEPTION_STATUS_CONFIG)[0]
  const catCfg = EXCEPTION_CATEGORY_CONFIG[exception.category] ?? Object.values(EXCEPTION_CATEGORY_CONFIG)[0]
  const riskCfg = RISK_LEVEL_CONFIG[exception.riskLevel] ?? Object.values(RISK_LEVEL_CONFIG)[0]
  const isPending = exception.status === 'pending'
  const isLoading = (action: string) => actionLoading === `${exception.id}-${action}`

  return (
    <div
      className={cn(
        'card card-hover relative overflow-hidden transition-all',
        selected && 'border-primary/50 shadow-glow',
      )}
    >
      {/* 风险等级顶部条 */}
      <div
        className={cn(
          'absolute top-0 left-0 right-0 h-0.5',
          exception.riskLevel === 'high'
            ? 'bg-status-circuit'
            : exception.riskLevel === 'medium'
              ? 'bg-status-paused'
              : 'bg-accent',
        )}
      />

      <div className="flex items-start gap-3">
        {/* 类型图标 */}
        <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center shrink-0', typeCfg.bg)}>
          <typeCfg.icon className={cn('w-5 h-5', typeCfg.color)} />
        </div>

        {/* 内容 */}
        <div className="min-w-0 flex-1">
          {/* 标题行 */}
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={cn('badge py-0', catCfg.bg, 'text-slate-300 border-[#2a3142]')}>
                  <catCfg.icon className="w-3 h-3" />
                  {catCfg.label}
                </span>
                <span className={cn('badge py-0', riskCfg.bg, riskCfg.color, riskCfg.border)}>
                  {riskCfg.label}
                </span>
                <span className={cn('badge py-0', statusCfg.bg, statusCfg.color, 'border-[#2a3142]')}>
                  <span className={cn('w-1.5 h-1.5 rounded-full', statusCfg.dot)} />
                  {statusCfg.label}
                </span>
              </div>
              <h3 className="text-sm font-semibold text-white mt-1.5 leading-snug">{exception.title}</h3>
            </div>
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-slate-500 hover:text-white shrink-0"
            >
              {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
          </div>

          {/* 描述 */}
          <p className={cn('text-xs text-slate-400 mt-1.5 leading-relaxed', !expanded && 'line-clamp-2')}>
            {exception.description}
          </p>

          {/* 元信息 */}
          <div className="flex items-center gap-3 mt-2 text-[11px] text-slate-500">
            <span className="flex items-center gap-1">
              <span className="text-primary/70">{exception.sourceAgent}</span>
            </span>
            <span>·</span>
            <span>{exception.id}</span>
            <span>·</span>
            <span>{timeAgo(exception.createdAt)}</span>
          </div>

          {/* 展开内容：上下文摘要 */}
          {expanded && (
            <div className="mt-3 p-3 rounded-lg bg-bg-input border border-[#1e2536] animate-fade-in">
              <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
                上下文摘要
              </p>
              <p className="text-xs text-slate-300 mb-2">{exception.context.summary}</p>
              <div className="grid grid-cols-2 gap-1.5">
                {exception.context.fields.slice(0, 4).map((f, i) => (
                  <div key={i} className="text-[11px]">
                    <span className="text-slate-500">{f.label}：</span>
                    <span className="text-slate-300">{f.value}</span>
                  </div>
                ))}
              </div>
              {exception.context.details && (
                <p className="text-[11px] text-slate-500 mt-2 leading-relaxed">{exception.context.details}</p>
              )}
            </div>
          )}

          {/* 操作按钮 */}
          {isPending && (
            <div className="flex items-center gap-2 mt-3">
              <button
                onClick={() => onAction(exception, 'approve')}
                disabled={isLoading('approve')}
                className="btn-accent !py-1.5 !px-3 text-xs flex-1"
              >
                {isLoading('approve') ? (
                  <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <Check className="w-3.5 h-3.5" />
                )}
                批准
              </button>
              <button
                onClick={() => onAction(exception, 'reject')}
                disabled={isLoading('reject')}
                className="btn-danger !py-1.5 !px-3 text-xs flex-1"
              >
                {isLoading('reject') ? (
                  <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <X className="w-3.5 h-3.5" />
                )}
                拒绝
              </button>
              <button
                onClick={() => onAction(exception, 'transfer')}
                disabled={isLoading('transfer')}
                className="btn-secondary !py-1.5 !px-3 text-xs"
              >
                {isLoading('transfer') ? (
                  <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <Forward className="w-3.5 h-3.5" />
                )}
                转交
              </button>
              <button
                onClick={onSelect}
                className="btn-ghost !py-1.5 !px-3 text-xs"
              >
                <Eye className="w-3.5 h-3.5" />
                详情
              </button>
            </div>
          )}

          {!isPending && (
            <div className="flex items-center gap-2 mt-3">
              <button onClick={onSelect} className="btn-secondary !py-1.5 !px-3 text-xs">
                <Eye className="w-3.5 h-3.5" />
                查看详情
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ExceptionDetail({
  exception,
  onClose,
  onAction,
  actionLoading,
}: {
  exception: ExceptionItem
  onClose: () => void
  onAction: (e: ExceptionItem, action: 'approve' | 'reject' | 'transfer') => void
  actionLoading: string | null
}) {
  const typeCfg = EXCEPTION_TYPE_CONFIG[exception.type] ?? Object.values(EXCEPTION_TYPE_CONFIG)[0]
  const statusCfg = EXCEPTION_STATUS_CONFIG[exception.status] ?? Object.values(EXCEPTION_STATUS_CONFIG)[0]
  const riskCfg = RISK_LEVEL_CONFIG[exception.riskLevel] ?? Object.values(RISK_LEVEL_CONFIG)[0]
  const isPending = exception.status === 'pending'
  const isLoading = (action: string) => actionLoading === `${exception.id}-${action}`

  return (
    <div className="card !p-0 sticky top-20 overflow-hidden max-h-[calc(100vh-6rem)] flex flex-col">
      {/* 头部 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2536] bg-bg-card/80">
        <div className="flex items-center gap-2 min-w-0">
          <typeCfg.icon className={cn('w-4 h-4 shrink-0', typeCfg.color)} />
          <h3 className="text-sm font-semibold text-white truncate">例外详情</h3>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-white shrink-0">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* 可滚动内容 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 基本信息 */}
        <div>
          <div className="flex items-center gap-2 flex-wrap mb-2">
            <span className={cn('badge py-0', riskCfg.bg, riskCfg.color, riskCfg.border)}>{riskCfg.label}</span>
            <span className={cn('badge py-0', statusCfg.bg, statusCfg.color, 'border-[#2a3142]')}>
              <span className={cn('w-1.5 h-1.5 rounded-full', statusCfg.dot)} />
              {statusCfg.label}
            </span>
          </div>
          <h4 className="text-sm font-semibold text-white leading-snug">{exception.title}</h4>
          <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">{exception.description}</p>
          <div className="flex items-center gap-2 mt-2 text-[11px] text-slate-500">
            <span>{exception.id}</span>
            <span>·</span>
            <span>{formatDateTime(exception.createdAt)}</span>
          </div>
        </div>

        {/* 上下文信息 */}
        <ContextInfo context={exception.context} />

        {/* 决策推理过程 */}
        <DecisionTrace trace={exception.decisionTrace} />

        {/* 事件链 */}
        <EventChain events={exception.eventChain} />

        {/* 操作历史时间线 */}
        <ActionHistory actions={exception.actionHistory} />
      </div>

      {/* 底部操作 */}
      {isPending && (
        <div className="border-t border-[#1e2536] p-3 bg-bg-card/80 flex gap-2">
          <button
            onClick={() => onAction(exception, 'approve')}
            disabled={isLoading('approve') || isLoading('reject') || isLoading('transfer')}
            className="btn-accent !py-2 text-xs flex-1"
          >
            {isLoading('approve') ? (
              <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <Check className="w-3.5 h-3.5" />
            )}
            批准
          </button>
          <button
            onClick={() => onAction(exception, 'reject')}
            disabled={isLoading('approve') || isLoading('reject') || isLoading('transfer')}
            className="btn-danger !py-2 text-xs flex-1"
          >
            {isLoading('reject') ? (
              <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <X className="w-3.5 h-3.5" />
            )}
            拒绝
          </button>
          <button
            onClick={() => onAction(exception, 'transfer')}
            disabled={isLoading('approve') || isLoading('reject') || isLoading('transfer')}
            className="btn-secondary !py-2 text-xs"
          >
            {isLoading('transfer') ? (
              <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <Forward className="w-3.5 h-3.5" />
            )}
            转交
          </button>
        </div>
      )}
    </div>
  )
}

function ContextInfo({ context }: { context: ExceptionItem['context'] }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-2">
        <FileText className="w-3.5 h-3.5 text-primary" />
        <h5 className="text-xs font-semibold text-white">上下文信息</h5>
      </div>
      <div className="rounded-lg bg-bg-input border border-[#1e2536] p-3">
        <p className="text-xs text-slate-300 mb-2">{context.summary}</p>
        <div className="grid grid-cols-1 gap-1.5">
          {context.fields.map((f, i) => (
            <div key={i} className="flex items-center justify-between gap-2 text-xs">
              <span className="text-slate-500 shrink-0">{f.label}</span>
              <span className="text-slate-200 font-medium text-right">{f.value}</span>
            </div>
          ))}
        </div>
        {context.details && (
          <p className="text-[11px] text-slate-500 mt-2 pt-2 border-t border-[#1e2536] leading-relaxed">
            {context.details}
          </p>
        )}
      </div>
    </div>
  )
}

function DecisionTrace({ trace }: { trace: ExceptionItem['decisionTrace'] }) {
  const [expanded, setExpanded] = useState(true)
  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 mb-2 w-full"
      >
        <Brain className="w-3.5 h-3.5 text-accent" />
        <h5 className="text-xs font-semibold text-white">Agent 决策推理</h5>
        <span className="text-[10px] text-slate-500 ml-1">置信度 {trace.confidence}%</span>
        <ChevronDown
          className={cn('w-3.5 h-3.5 text-slate-500 ml-auto transition-transform', expanded && 'rotate-180')}
        />
      </button>
      {expanded && (
        <div className="rounded-lg bg-bg-input border border-[#1e2536] p-3 animate-fade-in">
          <p className="text-xs text-slate-400 mb-3">{trace.summary}</p>
          <div className="space-y-2">
            {trace.steps.map((step) => (
              <div key={step.step} className="flex items-start gap-2">
                <span className="w-5 h-5 rounded-full bg-primary/15 text-primary text-[10px] font-bold flex items-center justify-center shrink-0 mt-0.5">
                  {step.step}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-slate-300 leading-relaxed">{step.reasoning}</p>
                  {step.evidence && (
                    <p className="text-[10px] text-slate-500 font-mono mt-0.5">{step.evidence}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-2 border-t border-[#1e2536]">
            <p className="text-[11px] text-accent font-medium leading-relaxed">{trace.conclusion}</p>
          </div>
          {/* 置信度进度条 */}
          <div className="mt-2 flex items-center gap-2">
            <span className="text-[10px] text-slate-500">置信度</span>
            <div className="flex-1 h-1.5 rounded-full bg-bg-deep overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-accent"
                style={{ width: `${trace.confidence}%` }}
              />
            </div>
            <span className="text-[10px] font-semibold text-accent tabular-nums">{trace.confidence}%</span>
          </div>
        </div>
      )}
    </div>
  )
}

function EventChain({ events }: { events: ExceptionItem['eventChain'] }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-2">
        <GitBranch className="w-3.5 h-3.5 text-blue-400" />
        <h5 className="text-xs font-semibold text-white">相关事件链</h5>
      </div>
      <div className="relative pl-4 space-y-2.5">
        {/* 时间线竖线 */}
        <div className="absolute left-[3px] top-1 bottom-1 w-px bg-[#2a3142]" />
        {events.map((evt) => {
          const cfg = EVENT_LEVEL_CONFIG[evt.level] || EVENT_LEVEL_CONFIG.info
          return (
            <div key={evt.id} className="relative">
              {/* 节点 */}
              <span
                className={cn(
                  'absolute -left-4 top-1 w-2 h-2 rounded-full border-2 border-bg-card',
                  cfg.dot,
                )}
              />
              <div className="rounded-lg bg-bg-input border border-[#1e2536] p-2.5">
                <div className="flex items-center gap-2 mb-1">
                  <span className={cn('text-[10px] font-medium', cfg.color)}>{evt.action}</span>
                  <span className="text-[10px] text-slate-500">{formatDateTime(evt.timestamp)}</span>
                </div>
                <p className="text-[11px] text-slate-300 leading-relaxed">{evt.detail}</p>
                <p className="text-[10px] text-primary/70 mt-0.5">by {evt.agentName}</p>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ActionHistory({ actions }: { actions: ExceptionItem['actionHistory'] }) {
  const resultConfig: Record<string, { label: string; color: string; icon: LucideIcon }> = {
    success: { label: '成功', color: 'text-accent', icon: CheckCircle2 },
    failed: { label: '失败', color: 'text-status-circuit', icon: XCircle },
    pending: { label: '处理中', color: 'text-status-paused', icon: Clock },
  }
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-2">
        <History className="w-3.5 h-3.5 text-slate-400" />
        <h5 className="text-xs font-semibold text-white">操作历史</h5>
      </div>
      <div className="relative pl-4 space-y-2">
        <div className="absolute left-[3px] top-1 bottom-1 w-px bg-[#2a3142]" />
        {actions.map((act) => {
          const cfg = resultConfig[act.result] || resultConfig.pending
          return (
            <div key={act.id} className="relative">
              <span
                className={cn(
                  'absolute -left-4 top-1.5 w-2 h-2 rounded-full border-2 border-bg-card',
                  act.result === 'success' ? 'bg-accent' : act.result === 'failed' ? 'bg-status-circuit' : 'bg-status-paused',
                )}
              />
              <div className="flex items-start gap-2 text-xs">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <cfg.icon className={cn('w-3 h-3', cfg.color)} />
                    <span className="font-medium text-slate-300">{act.operator}</span>
                    <span className="text-slate-500">执行了</span>
                    <span className="font-medium text-slate-200">{act.action}</span>
                  </div>
                  {act.remark && <p className="text-[11px] text-slate-500 mt-0.5">{act.remark}</p>}
                  <p className="text-[10px] text-slate-600 mt-0.5">{formatDateTime(act.timestamp)}</p>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
