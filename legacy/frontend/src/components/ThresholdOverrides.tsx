/**
 * gate 阈值管理端入口（路线图 F5.6 UAT-F5-1 补口）
 * ================================================
 * 数据源：
 *   GET  /agent-ops/customizations/meta          阈值清单（键/显示名/出厂默认，与后端白名单同源）
 *   GET  /agent-ops/customizations               当前生效覆盖（type=gate, target=<阈值键>）
 *   PUT  /agent-ops/customizations/gate/<key>    {overrides:{value}, enabled}
 * 语义（后端 _classify_change 已锁，红线 2）：
 *   生效值**上调** = 审批面变松 = 放宽 → 转人工审批，批准前不参与运行；
 *   **下调** = 收紧 → 即时生效；
 *   停用一份比出厂线更紧的覆盖 = 生效值升回默认 = 方向同样是放宽，也须人审。
 * 键名一律来自 /meta：前端不硬编码阈值清单，避免"界面能改、后端拒绝"的两处口径。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Gauge, RefreshCw, Save, X } from 'lucide-react'
import { apiService } from '../services/api'
import Can from './Can'
import { cn } from '../lib/utils'

export interface ThresholdMeta {
  key: string
  label: string
  builtin_default: number
}

export interface ThresholdEntry {
  enabled: boolean
  value?: number
}

/** 卡片渲染态：出厂默认 / 当前生效 / 输入框目标值 / 由目标值推出的方向 */
interface Row {
  meta: ThresholdMeta
  entry: ThresholdEntry
  current: number
  parsed: number | null
  to: number
  changed: boolean
  direction: 'relax' | 'tighten'
}

const inputCls =
  'w-full text-[12px] px-2 py-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-200 ' +
  'focus:outline-none focus:border-primary/60'

/** 千分位：只处理整数部分，避免 toLocaleString 的 ICU 差异进入测试断言 */
export function fmtNum(n: number): string {
  const [int, dec] = String(n).split('.')
  const withSep = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return dec ? `${withSep}.${dec}` : withSep
}

/**
 * 覆盖条目 → {enabled, value}：value 只认有限数字（后端 get_threshold 读不懂就静默回退）。
 * 停用的条目本身仍要显示出来，否则管理员会把"已停用"看成"没配过"。
 */
export function readThreshold(
  entry: { enabled: boolean; overrides?: Record<string, unknown> } | undefined,
): ThresholdEntry {
  if (!entry) return { enabled: true }
  const raw = (entry.overrides || {}).value
  const value = typeof raw === 'number' && Number.isFinite(raw) ? raw : undefined
  return { enabled: Boolean(entry.enabled), value }
}

export function effectiveValue(entry: ThresholdEntry, builtin: number): number {
  if (!entry.enabled || entry.value === undefined) return builtin
  return entry.value
}

/** 变更方向：上调=放宽（转人审），下调或持平=收紧（即时） */
export function classifyDirection(from: number, to: number): 'relax' | 'tighten' {
  return to > from ? 'relax' : 'tighten'
}

/** 输入框里的值 → 后端可接受的 value（与 apply_change 的校验同口径：非负有限数字） */
export function parseThresholdValue(raw: string): number | null {
  const t = raw.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) && n >= 0 ? n : null
}

export function ThresholdOverrides({
  pendingCount = 0,
  onChanged,
}: {
  pendingCount?: number
  /** 父级待审批队列刷新回调（真机踩到：子分区保存后父级队列停在保存前的计数上） */
  onChanged?: () => void | Promise<void>
}) {
  const [metas, setMetas] = useState<ThresholdMeta[]>([])
  const [entries, setEntries] = useState<Record<string, ThresholdEntry>>({})
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [metaRes, custRes] = await Promise.all([
        apiService.getCustomizationMeta(),
        apiService.getAgentCustomizations(),
      ])
      const list: ThresholdMeta[] = Array.isArray(metaRes?.thresholds) ? metaRes.thresholds : []
      setMetas(list)
      const custs = Array.isArray(custRes?.customizations) ? custRes.customizations : []
      const next: Record<string, ThresholdEntry> = {}
      for (const m of list) {
        const entry = custs.find((c: any) => c.type === 'gate' && c.target === m.key)
        next[m.key] = readThreshold(entry)
      }
      setEntries(next)
      setDrafts((prev) => {
        const out: Record<string, string> = {}
        for (const m of list) out[m.key] = prev[m.key] ?? String(next[m.key]?.value ?? '')
        return out
      })
    } catch (e: any) {
      setError(e?.response?.data?.detail || '阈值数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const rows = useMemo<Row[]>(() => metas.map((m) => {
    const entry = entries[m.key] || { enabled: true }
    const current = effectiveValue(entry, m.builtin_default)
    const parsed = parseThresholdValue(drafts[m.key] ?? '')
    const to = parsed === null ? current : parsed
    return {
      meta: m, entry, current, parsed, to,
      changed: to !== current,
      direction: classifyDirection(current, to),
    }
  }), [metas, entries, drafts])

  const save = async (row: Row) => {
    const { meta, current, to, direction } = row
    if (!row.changed) return
    setBusy(meta.key)
    setError('')
    setMessage('')
    try {
      const res = await apiService.putAgentCustomization(
        'gate', meta.key,
        {
          overrides: { value: to },
          enabled: true,
          comment: `管理端阈值调整：${meta.label} ${fmtNum(current)}→${fmtNum(to)}`,
        },
      )
      if (res?.status === 'pending_approval') {
        setMessage(`${meta.label}：上调属放宽类变更，已提交人工审批，批准前仍按 ${fmtNum(current)} 跑`)
      } else if (res?.status === 'applied') {
        setMessage(`${meta.label}：${direction === 'tighten' ? '收紧' : '变更'}已即时生效（${fmtNum(to)}）`)
      } else if (res?.status === 'duplicate') {
        setMessage(`${meta.label}：与上一笔请求相同，后端已忽略（幂等键防重放）`)
      } else {
        setMessage(`${meta.label}：返回状态 ${res?.status || '未知'}`)
      }
      await load()
      await onChanged?.()
    } catch (e: any) {
      setError(e?.response?.data?.detail || `${meta.label} 保存失败`)
    } finally {
      setBusy(null)
    }
  }

  /** 停用 = 撤销覆盖回落出厂线；比出厂线更紧时方向是放宽，后端会转人审 */
  const disable = async (row: Row) => {
    const { meta, entry } = row
    if (!entry.value && entry.enabled) return
    setBusy(meta.key)
    setError('')
    setMessage('')
    try {
      const res = await apiService.putAgentCustomization(
        'gate', meta.key,
        {
          overrides: { value: entry.value ?? meta.builtin_default },
          enabled: false,
          comment: `管理端停用阈值覆盖：${meta.label}（回退出厂线 ${fmtNum(meta.builtin_default)}）`,
        },
      )
      setMessage(res?.status === 'pending_approval'
        ? `${meta.label}：停用后生效值升回出厂线 ${fmtNum(meta.builtin_default)}，属放宽，已转人工审批`
        : `${meta.label}：已停用覆盖，按出厂线 ${fmtNum(meta.builtin_default)} 跑`)
      await load()
      await onChanged?.()
    } catch (e: any) {
      setError(e?.response?.data?.detail || `${meta.label} 停用失败`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Gauge className="w-4 h-4 text-accent shrink-0" />
          <h3 className="text-sm font-semibold text-white">
            风控阈值
            <span className="ml-2 text-xs font-normal text-slate-500">
              gate 类型的可配白名单键 · 上调=放宽需审批 · 下调=收紧即时
            </span>
          </h3>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {pendingCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-400 border border-yellow-500/30">
              {pendingCount} 条阈值变更待审批
            </span>
          )}
          <button onClick={load} disabled={loading}
            className="text-xs text-slate-400 hover:text-white flex items-center gap-1">
            <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
            刷新
          </button>
        </div>
      </div>

      {message && (
        <div className="p-2.5 rounded-lg bg-accent/10 border border-accent/30 text-xs text-accent">
          {message}
        </div>
      )}
      {error && (
        <div className="p-2.5 rounded-lg bg-status-circuit/10 border border-status-circuit/30 text-xs text-status-circuit">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {rows.map((row) => {
          const { meta, entry, current, parsed, to, changed, direction } = row
          const invalid = parsed === null && (drafts[meta.key] ?? '').trim() !== ''
          const state = !entry.value && entry.enabled
            ? { text: '未覆盖（按出厂线）', cls: 'border-slate-600 text-slate-400' }
            : !entry.enabled
              ? { text: '已停用（回退出厂线）', cls: 'border-yellow-500/30 text-yellow-400 bg-yellow-500/10' }
              : { text: '已覆盖', cls: 'border-primary/40 text-primary bg-primary/10' }
          return (
            <div key={meta.key}
              className="rounded-xl border border-[#2a3142] bg-bg-card/80 p-3 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs text-white truncate">{meta.label}</p>
                  <code className="text-[10px] text-slate-500 break-all">{meta.key}</code>
                </div>
                <span className={cn('text-[10px] px-1.5 py-0.5 rounded border shrink-0', state.cls)}>
                  {state.text}
                </span>
              </div>

              <div className="text-[11px] text-slate-500 flex items-center gap-3 flex-wrap">
                <span>出厂默认 <b className="text-slate-300 font-mono">{fmtNum(meta.builtin_default)}</b></span>
                <span>当前生效 <b className="text-slate-200 font-mono">{fmtNum(current)}</b></span>
                {entry.value !== undefined && (
                  <span>覆盖值 <b className="text-slate-300 font-mono">{fmtNum(entry.value)}</b></span>
                )}
              </div>

              <Can resource="customization" action="update"
                fallback={<p className="text-[10px] text-slate-600">需 customization:update 权限</p>}>
                <div className="flex items-center gap-2">
                  <input
                    type="number" min={0} value={drafts[meta.key] ?? ''}
                    placeholder={`出厂默认 ${meta.builtin_default}`}
                    disabled={busy === meta.key}
                    onChange={(e) => setDrafts((prev) => ({ ...prev, [meta.key]: e.target.value }))}
                    className={cn(inputCls, 'max-w-[180px]', invalid && 'border-status-circuit/60')}
                  />
                  <button onClick={() => save(row)} disabled={!changed || invalid || busy === meta.key}
                    className="text-[11px] px-2.5 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-40 flex items-center gap-1 shrink-0">
                    <Save className="w-3 h-3" /> 保存
                  </button>
                  {entry.enabled && entry.value !== undefined && (
                    <button onClick={() => disable(row)} disabled={busy === meta.key}
                      className="text-[11px] px-2 py-1.5 rounded-lg border border-slate-600 text-slate-400 hover:text-white flex items-center gap-1 shrink-0">
                      <X className="w-3 h-3" /> 停用覆盖
                    </button>
                  )}
                </div>
                <p className="text-[10px] text-slate-600">
                  {invalid
                    ? <span className="text-status-circuit">阈值必须是非负数字</span>
                    : changed
                      ? (direction === 'relax'
                        ? `将 ${fmtNum(current)} 上调为 ${fmtNum(to)}：放宽类变更，需人工审批后生效`
                        : `将 ${fmtNum(current)} 下调为 ${fmtNum(to)}：收紧，保存即生效`)
                      : `留空或填回 ${fmtNum(current)} 表示不改`}
                </p>
              </Can>
            </div>
          )
        })}
        {rows.length === 0 && !loading && (
          <p className="text-xs text-slate-500">
            /meta 未下发阈值清单（后端 THRESHOLD_DEFAULTS 为空？），此处不提供手工输入。
          </p>
        )}
      </div>
    </div>
  )
}
