import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  Info,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Pause,
  Play,
  Trash2,
  Bot,
  Filter,
} from 'lucide-react'
import { useSystemStore, type EventLevel } from '../stores/systemStore'
import { EVENT_LEVEL_CONFIG, formatTime, formatDateTime, cn } from '../lib/utils'

const LEVEL_ICONS: Record<EventLevel, typeof Info> = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
}

export default function EventStream() {
  const events = useSystemStore((s) => s.events)
  const wsConnected = useSystemStore((s) => s.wsConnected)
  const eventFilter = useSystemStore((s) => s.eventFilter)
  const setEventFilter = useSystemStore((s) => s.setEventFilter)
  const [paused, setPaused] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const listRef = useRef<HTMLDivElement>(null)
  const [localEvents, setLocalEvents] = useState(events)

  // 暂停时冻结事件列表
  useEffect(() => {
    if (!paused) {
      setLocalEvents(events)
    }
  }, [events, paused])

  // 自动滚动
  useEffect(() => {
    if (autoScroll && !paused && listRef.current) {
      listRef.current.scrollTop = 0
    }
  }, [localEvents, autoScroll, paused])

  const filtered = localEvents.filter((e) => eventFilter === 'all' || e.level === eventFilter)

  const levelCounts = {
    all: events.length,
    info: events.filter((e) => e.level === 'info').length,
    success: events.filter((e) => e.level === 'success').length,
    warning: events.filter((e) => e.level === 'warning').length,
    error: events.filter((e) => e.level === 'error').length,
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">事件流</h2>
          <p className="text-sm text-slate-500 mt-0.5">Agent 实时事件追踪 · WebSocket 推送</p>
        </div>
        <div className="flex items-center gap-2">
          {/* 连接状态 */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-bg-card border border-[#2a3142]">
            <span className={cn('pulse-dot', wsConnected ? 'bg-accent' : 'bg-status-paused')} />
            <span className="text-xs text-slate-300">
              {wsConnected ? 'WebSocket 已连接' : 'Mock 事件流（演示模式）'}
            </span>
          </div>
          {/* 暂停/恢复 */}
          <button
            onClick={() => setPaused(!paused)}
            className={cn(
              'btn !py-1.5 text-xs',
              paused ? 'btn-accent' : 'btn-secondary',
            )}
          >
            {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            {paused ? '恢复' : '暂停'}
          </button>
        </div>
      </div>

      {/* 统计条 */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <FilterStat
          label="全部事件"
          count={levelCounts.all}
          active={eventFilter === 'all'}
          onClick={() => setEventFilter('all')}
          color="primary"
        />
        <FilterStat
          label="信息"
          count={levelCounts.info}
          active={eventFilter === 'info'}
          onClick={() => setEventFilter('info')}
          color="info"
        />
        <FilterStat
          label="成功"
          count={levelCounts.success}
          active={eventFilter === 'success'}
          onClick={() => setEventFilter('success')}
          color="success"
        />
        <FilterStat
          label="警告"
          count={levelCounts.warning}
          active={eventFilter === 'warning'}
          onClick={() => setEventFilter('warning')}
          color="warning"
        />
        <FilterStat
          label="错误"
          count={levelCounts.error}
          active={eventFilter === 'error'}
          onClick={() => setEventFilter('error')}
          color="error"
        />
      </div>

      {/* 事件流列表 */}
      <div className="card !p-0 overflow-hidden">
        {/* 列表头 */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#1e2536] bg-bg-hover/50">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            <span className="text-sm font-semibold text-white">实时事件</span>
            <span className="text-xs text-slate-500">（{filtered.length} 条）</span>
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="accent-primary w-3.5 h-3.5"
            />
            自动滚动
          </label>
        </div>

        {/* 事件列表 */}
        <div ref={listRef} className="max-h-[calc(100vh-340px)] min-h-[400px] overflow-y-auto scan-line">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-slate-500">
              <Activity className="w-8 h-8 mb-2 opacity-40" />
              <p className="text-sm">等待事件流入...</p>
            </div>
          ) : (
            <div className="divide-y divide-[#1e2536]">
              {filtered.map((event) => {
                const cfg = EVENT_LEVEL_CONFIG[event.level]
                const Icon = LEVEL_ICONS[event.level]
                return (
                  <div
                    key={event.id}
                    className="flex items-start gap-3 px-5 py-3 hover:bg-bg-hover transition-colors animate-slide-in"
                  >
                    {/* 级别图标 */}
                    <div
                      className={cn(
                        'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5',
                        cfg.bg,
                        cfg.border,
                        'border',
                      )}
                    >
                      <Icon className={cn('w-4 h-4', cfg.color)} />
                    </div>

                    {/* 内容 */}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm text-slate-200 leading-snug">{event.message}</p>
                        <span className="text-[11px] text-slate-600 font-mono shrink-0 mt-0.5">
                          {formatTime(event.timestamp)}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        <span className="flex items-center gap-1 text-[11px] text-primary/80">
                          <Bot className="w-3 h-3" />
                          {event.agent_name}
                        </span>
                        <span className="text-[11px] text-slate-600">·</span>
                        <span className={cn('badge py-0 text-[10px]', cfg.bg, cfg.color, cfg.border)}>
                          {cfg.label}
                        </span>
                        <span className="text-[11px] text-slate-600">·</span>
                        <span className="text-[11px] text-slate-500 font-mono">{event.event_type}</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 底部状态 */}
        <div className="flex items-center justify-between px-5 py-2.5 border-t border-[#1e2536] bg-bg-hover/50 text-[11px] text-slate-500">
          <span>
            {paused ? '已暂停接收新事件' : '实时接收中...'} · 最近更新{' '}
            {filtered[0] ? formatDateTime(filtered[0].timestamp) : '—'}
          </span>
          <span className="flex items-center gap-1.5">
            <Filter className="w-3 h-3" />
            {eventFilter === 'all' ? '显示全部级别' : `仅显示 ${EVENT_LEVEL_CONFIG[eventFilter as EventLevel].label}`}
          </span>
        </div>
      </div>
    </div>
  )
}

function FilterStat({
  label,
  count,
  active,
  onClick,
  color,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
  color: 'primary' | 'info' | 'success' | 'warning' | 'error'
}) {
  const colorMap = {
    primary: { dot: 'bg-primary', text: 'text-primary', activeBg: 'bg-primary/10 border-primary/40' },
    info: { dot: 'bg-blue-500', text: 'text-blue-400', activeBg: 'bg-blue-500/10 border-blue-500/40' },
    success: { dot: 'bg-accent', text: 'text-accent', activeBg: 'bg-accent/10 border-accent/40' },
    warning: { dot: 'bg-status-paused', text: 'text-status-paused', activeBg: 'bg-status-paused/10 border-status-paused/40' },
    error: { dot: 'bg-status-circuit', text: 'text-status-circuit', activeBg: 'bg-status-circuit/10 border-status-circuit/40' },
  }
  const c = colorMap[color]
  return (
    <button
      onClick={onClick}
      className={cn(
        'card !p-3 text-left transition-all border',
        active ? c.activeBg : 'border-[#1e2536] hover:border-[#2a3142]',
      )}
    >
      <div className="flex items-center gap-2">
        <span className={cn('w-2 h-2 rounded-full', c.dot)} />
        <span className="text-xs text-slate-400">{label}</span>
      </div>
      <p className={cn('text-xl font-bold tabular-nums mt-1', active ? c.text : 'text-white')}>{count}</p>
    </button>
  )
}
