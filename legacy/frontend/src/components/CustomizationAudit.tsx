/**
 * 定制审计视图（Phase F5.1）
 * ==========================
 * 数据源：GET /agent-ops/customizations/versions（type/target/status/limit 过滤）
 *
 * 红线 3 的可见面：每条定制变更都要能回答"谁、何时、改了什么、旧值→新值、谁批的"。
 * 只读视图——批准/驳回仍在 Agent 定制中心的审批队列，撤销变更也在那里做，
 * 这里不复制第二套写操作入口（两处审批会互相绕过幂等键）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, ScrollText } from 'lucide-react'
import { apiService } from '../services/api'
import { cn } from '../lib/utils'

interface Snapshot {
  enabled?: boolean
  overrides?: Record<string, unknown> | null
}

export interface AuditRow {
  id: number
  type: string
  target: string
  environment?: string | null
  status: string
  comment?: string | null
  changed_by?: string | null
  approved_by?: string | null
  created_at?: string | null
  old_summary?: Snapshot | null
  new_summary?: Snapshot | null
}

export type DiffLine = { key: string; from: string; to: string }

const TYPES = ['tool', 'gate', 'agent', 'workflow', 'skill', 'mcp']
const STATUSES = ['active', 'pending_approval', 'approved_executed', 'rejected',
                  'rolled_back', 'superseded', 'removed']

/** 值 → 单行文本（对象走 JSON，长度截断留给展开面板看全文）。 */
export function showValue(v: unknown): string {
  if (v === undefined) return '—'
  if (v === null) return 'null'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/**
 * 变更前后快照 → 逐键差异。启停轴（enabled）单独成行：
 * 它是"停用即时、启用须批"的判据，藏在 overrides 里会被读漏。
 */
export function diffSummary(oldS?: Snapshot | null,
                            newS?: Snapshot | null): DiffLine[] {
  const o = oldS?.overrides ?? {}
  const n = newS?.overrides ?? {}
  const keys = [...new Set([...Object.keys(o || {}), ...Object.keys(n || {})])].sort()
  const lines: DiffLine[] = []
  if ((oldS?.enabled ?? true) !== (newS?.enabled ?? true)) {
    lines.push({
      key: '启停',
      from: (oldS?.enabled ?? true) ? '启用' : '停用',
      to: (newS?.enabled ?? true) ? '启用' : '停用',
    })
  }
  for (const k of keys) {
    const from = showValue(o?.[k])
    const to = showValue(n?.[k])
    if (from !== to) lines.push({ key: k, from, to })
  }
  return lines
}

/** 状态 → 语气色。待批必须显眼，避免被读成"已经生效"。 */
export function statusTone(status: string): 'ok' | 'warn' | 'bad' | 'mute' | 'info' {
  if (status === 'pending_approval') return 'warn'
  if (status === 'rejected') return 'bad'
  if (status === 'rolled_back' || status === 'superseded' || status === 'removed') return 'info'
  if (status === 'active' || status === 'approved_executed') return 'ok'
  return 'mute'
}

/** 这条记录是否"没有生效内容的提案行"（待批/已驳回），列宽与文案要分开处理。 */
export function isProposal(status: string): boolean {
  return status === 'pending_approval' || status === 'rejected'
}

function Tag({ tone, children }: { tone: ReturnType<typeof statusTone>; children: React.ReactNode }) {
  const cls = {
    ok: 'bg-status-ok/10 text-status-ok border-status-ok/30',
    warn: 'bg-status-warn/10 text-status-warn border-status-warn/30',
    bad: 'bg-status-circuit/10 text-status-circuit border-status-circuit/30',
    mute: 'bg-bg-input text-slate-400 border-[#2a3142]',
    info: 'bg-primary/10 text-primary border-primary/30',
  }[tone]
  return <span className={cn('px-1.5 py-0.5 rounded border text-[10px] whitespace-nowrap', cls)}>{children}</span>
}

export function CustomizationAudit() {
  const [rows, setRows] = useState<AuditRow[]>([])
  const [type, setType] = useState('')
  const [target, setTarget] = useState('')
  const [status, setStatus] = useState('')
  const [limit, setLimit] = useState(50)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      const res = await apiService.getCustomizationVersions(
        type || undefined, target.trim() || undefined, limit, status || undefined)
      setRows(Array.isArray(res?.versions) ? res.versions : [])
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '审计记录加载失败')
      setRows([])
    } finally {
      setBusy(false)
    }
  }, [type, target, status, limit])

  useEffect(() => { load() }, [load])

  const stat = useMemo(() => {
    const pending = rows.filter((r) => r.status === 'pending_approval').length
    const approved = rows.filter((r) => r.status === 'approved_executed'
      || r.status === 'active').length
    return { pending, approved }
  }, [rows])

  const selCls = 'bg-bg-input border border-[#2a3142] rounded-lg px-2 py-1.5 text-[11px] text-slate-200'

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <ScrollText className="w-4 h-4 text-primary" />
          <h2 className="text-sm font-semibold text-slate-100">定制审计</h2>
          <span className="text-[11px] text-slate-500">
            谁 / 何时 / 改了什么（旧值→新值）/ 谁批的
          </span>
        </div>
        <button onClick={load} disabled={busy}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-bg-input border border-[#2a3142] text-slate-200 disabled:opacity-40">
          <RefreshCw className={cn('w-3 h-3', busy && 'animate-spin')} /> 刷新
        </button>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select className={selCls} data-testid="audit-type" value={type}
          onChange={(e) => setType(e.target.value)}>
          <option value="">全部类型</option>
          {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <input className={cn(selCls, 'w-44')} data-testid="audit-target" value={target}
          placeholder="目标标识符（精确匹配）"
          onChange={(e) => setTarget(e.target.value)} />
        <select className={selCls} data-testid="audit-status" value={status}
          onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className={selCls} value={String(limit)}
          onChange={(e) => setLimit(Number(e.target.value))}>
          {[50, 100, 200].map((n) => <option key={n} value={String(n)}>最近 {n} 条</option>)}
        </select>
        <span className="text-[11px] text-slate-500">
          共 {rows.length} 条 · 待批 {stat.pending} · 已生效 {stat.approved}
        </span>
      </div>

      {error && <div className="text-[11px] text-status-circuit">{error}</div>}

      <div className="rounded-xl border border-[#1e2536] overflow-hidden">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="bg-bg-input/60 text-slate-500 text-left">
              <th className="px-3 py-2 font-normal">时间</th>
              <th className="px-3 py-2 font-normal">定制对象</th>
              <th className="px-3 py-2 font-normal">变更（旧值 → 新值）</th>
              <th className="px-3 py-2 font-normal">状态</th>
              <th className="px-3 py-2 font-normal">提出人</th>
              <th className="px-3 py-2 font-normal">审批人</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !busy && (
              <tr><td colSpan={6} className="px-3 py-4 text-center text-slate-500">
                {error ? '查询失败，未取到记录' : '没有符合条件的变更记录'}
              </td></tr>
            )}
            {rows.map((r) => {
              const lines = diffSummary(r.old_summary, r.new_summary)
              const open = openId === r.id
              return (
                <FragmentRow key={r.id} row={r} lines={lines} open={open}
                  onToggle={() => setOpenId(open ? null : r.id)} />
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-slate-600">
        待批提案行（pending_approval）是放宽类变更的审批中快照，尚未改变运行态；
        批准/驳回请回「Agent 定制中心」的审批队列处理。
      </p>
    </div>
  )
}

function FragmentRow({ row, lines, open, onToggle }: {
  row: AuditRow
  lines: DiffLine[]
  open: boolean
  onToggle: () => void
}) {
  return (
    <>
      <tr className="border-t border-[#1e2536] hover:bg-bg-hover/40"
        onClick={onToggle}>
        <td className="px-3 py-2 font-mono text-slate-400 whitespace-nowrap">
          {row.created_at ? row.created_at.slice(0, 19).replace('T', ' ') : '—'}
        </td>
        <td className="px-3 py-2 text-slate-200 whitespace-nowrap">
          {row.type}:{row.target}
        </td>
        <td className="px-3 py-2 text-slate-400">
          {lines.length === 0
            ? <span className="text-slate-600">{isProposal(row.status) ? '提案内容见详情' : '无键值变化'}</span>
            : lines.slice(0, open ? lines.length : 2).map((l) => (
              <div key={l.key} className="truncate">
                <span className="text-slate-300">{l.key}</span>{' '}
                <span className="text-slate-500">{l.from}</span> → <span>{l.to}</span>
              </div>
            ))}
          {lines.length > 2 && !open && (
            <span className="text-slate-600"> 等 {lines.length} 项</span>
          )}
        </td>
        <td className="px-3 py-2"><Tag tone={statusTone(row.status)}>{row.status}</Tag></td>
        <td className="px-3 py-2 text-slate-300 whitespace-nowrap">{row.changed_by || '—'}</td>
        <td className="px-3 py-2 whitespace-nowrap">
          {row.approved_by
            ? <span className="text-slate-300">{row.approved_by}</span>
            : <span className={cn(isProposal(row.status) ? 'text-yellow-400' : 'text-slate-600')}>
                {isProposal(row.status) ? '待批' : '系统/即时'}
              </span>}
        </td>
      </tr>
      {open && (
        <tr className="border-t border-[#1e2536] bg-bg-input/30">
          <td colSpan={6} className="px-3 py-2.5 space-y-1.5">
            <div className="text-[10px] text-slate-500">
              环境：{row.environment || 'prod'} · 记录号 #{row.id}
            </div>
            {row.comment && (
              <div className="text-[11px] text-slate-300">备注：{row.comment}</div>
            )}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
              <pre data-testid="audit-old" className="max-h-48 overflow-auto rounded bg-bg-deep/60 p-2 text-[10px] text-slate-400 whitespace-pre-wrap">
                变更前：{JSON.stringify(row.old_summary ?? null, null, 2)}
              </pre>
              <pre data-testid="audit-new" className="max-h-48 overflow-auto rounded bg-bg-deep/60 p-2 text-[10px] text-slate-200 whitespace-pre-wrap">
                变更后：{JSON.stringify(row.new_summary ?? null, null, 2)}
              </pre>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
