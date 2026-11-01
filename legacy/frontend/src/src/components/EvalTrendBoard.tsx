/**
 * 评测趋势看板（Phase F5.2）
 * ===========================
 * 数据源：GET /agent-ops/evals/cases（按域用例数）+ GET /agent-ops/evals/history
 * （每次评测一行，含 pytest 解析出的通过/失败/时长）+ GET /agent-ops/evals/status/{run_id}
 *
 * 退出标准对应"按域一键触发评测并查看历史趋势"：
 * - 触发＝POST /evals/run（customization:update），跑完自动回到历史列表；
 * - 趋势＝通过率折线 + 每次运行明细，数据来自 eval_runs 表（服务重启后仍在）；
 * - 门禁触发的运行（source=gate）同表可见，"发布被拦几次"是可数的。
 *
 * 口径纪律：pytest 没打出计数时一律显示"未取到"，不拿 0% 或 100% 冒充趋势点。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, Play, RefreshCw } from 'lucide-react'
import { apiService } from '../services/api'
import Can from './Can'
import { cn } from '../lib/utils'

export interface EvalRun {
  run_id: string
  domain: string
  source?: string | null
  status: string
  passed?: number | null
  failed?: number | null
  errors?: number | null
  skipped?: number | null
  deselected?: number | null
  duration_s?: number | null
  exit_code?: number | null
  triggered_by?: string | null
  created_at?: string | null
  finished_at?: string | null
  tail?: string | null
}

export interface EvalDomain { domain: string; cases: number }

const POLL_MS = 3000

/** pass/fail/running 之外的值一律当未知（库里没有第三种终态，出现就要能被看见）。 */
export function verdictOf(run: EvalRun): 'pass' | 'fail' | 'running' | 'unknown' {
  if (run.status === 'pass') return 'pass'
  if (run.status === 'fail') return 'fail'
  if (run.status === 'running') return 'running'
  return 'unknown'
}

/** 通过率：以 pytest 报出的 passed/failed/errors 为分母；一个数都没取到 → null。 */
export function passRateOf(run: EvalRun): number | null {
  if (run.passed === null || run.passed === undefined) return null
  const bad = (run.failed ?? 0) + (run.errors ?? 0)
  const total = run.passed + bad
  if (total <= 0) return null
  return run.passed / total
}

export function countsLabel(run: EvalRun): string {
  const parts: string[] = []
  if (run.passed !== null && run.passed !== undefined) parts.push(`${run.passed} 通过`)
  if (run.failed) parts.push(`${run.failed} 失败`)
  if (run.errors) parts.push(`${run.errors} 错误`)
  if (run.deselected) parts.push(`${run.deselected} 未选`)
  return parts.length ? parts.join(' · ') : '计数未取到'
}

export function durationLabel(run: EvalRun): string {
  return run.duration_s === null || run.duration_s === undefined
    ? '—' : `${run.duration_s.toFixed(2)}s`
}

/**
 * 折线坐标（百分比空间，0–100）。按时间升序，跳过没有通过率的运行。
 * 返回里带 run_id，鼠标悬停/明细行高亮才认得回是哪一次。
 */
export function trendPoints(runs: EvalRun[]): Array<{ x: number; y: number; runId: string }> {
  const chrono = [...runs].reverse()
    .filter((r) => passRateOf(r) !== null)
  if (chrono.length === 0) return []
  return chrono.map((r, i) => ({
    x: chrono.length === 1 ? 50 : (i / (chrono.length - 1)) * 100,
    y: 100 - (passRateOf(r) as number) * 100,
    runId: r.run_id,
  }))
}

export function trendSummary(runs: EvalRun[]): {
  total: number; pass: number; fail: number; running: number; noCounts: number
} {
  return {
    total: runs.length,
    pass: runs.filter((r) => verdictOf(r) === 'pass').length,
    fail: runs.filter((r) => verdictOf(r) === 'fail').length,
    running: runs.filter((r) => verdictOf(r) === 'running').length,
    noCounts: runs.filter((r) => passRateOf(r) === null).length,
  }
}

export function EvalTrendBoard() {
  const [domains, setDomains] = useState<EvalDomain[]>([])
  const [totalCases, setTotalCases] = useState(0)
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [domain, setDomain] = useState('')
  const [source, setSource] = useState('')
  const [limit, setLimit] = useState(50)
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [openRun, setOpenRun] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      const [c, h] = await Promise.all([
        apiService.getEvalCases(),
        apiService.getEvalHistory(domain || undefined, source || undefined, limit),
      ])
      setDomains(Array.isArray(c?.domains) ? c.domains : [])
      setTotalCases(Number(c?.total) || 0)
      setRuns(Array.isArray(h?.runs) ? h.runs : [])
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '评测历史加载失败')
      setRuns([])
    } finally {
      setBusy(false)
    }
  }, [domain, source, limit])

  useEffect(() => { load() }, [load])
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const poll = useCallback(async (runId: string) => {
    try {
      const s = await apiService.getEvalStatus(runId)
      const done = s?.finished === true || (s?.status && s?.status !== 'running')
      if (!done) {
        setMessage(`评测进行中（${runId}）…`)
        timer.current = setTimeout(() => poll(runId), POLL_MS)
        return
      }
      const summary = s?.summary ?? s?.status ?? '—'
      const tail = typeof s?.tail === 'string' ? s.tail.trim() : ''
      setMessage(`本轮评测结束：${summary}`
        + (tail ? ` · ${tail.split('\n').filter(Boolean).slice(-1)[0]}` : ''))
      setRunning('')
      await load()
    } catch (e: any) {
      setRunning('')
      setError(e?.response?.data?.detail || e?.message || '评测状态轮询失败')
    }
  }, [load])

  const trigger = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current)
    setMessage('')
    setError('')
    try {
      const r = await apiService.runEvals(domain)
      setRunning(r?.run_id || '')
      setMessage(`已触发评测（${domain || '全量'}，${r?.run_id || ''}）…`)
      if (r?.run_id) poll(r.run_id)
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '评测触发失败')
    }
  }, [domain, poll])

  const points = useMemo(() => trendPoints(runs), [runs])
  const stat = useMemo(() => trendSummary(runs), [runs])
  const casesOfDomain = useMemo(
    () => domains.find((d) => d.domain === domain)?.cases ?? null, [domains, domain])

  const selCls = 'bg-bg-input border border-[#2a3142] rounded-lg px-2 py-1.5 text-[11px] text-slate-200'

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-primary" />
          <h2 className="text-sm font-semibold text-slate-100">评测趋势</h2>
          <span className="text-[11px] text-slate-500">
            按域一键触发 · 历史通过率 · 门禁拦截次数
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} disabled={busy || !!running}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-bg-input border border-[#2a3142] text-slate-200 disabled:opacity-40">
            <RefreshCw className={cn('w-3 h-3', busy && 'animate-spin')} /> 刷新
          </button>
          <Can resource="customization" action="update"
            fallback={<span className="text-[10px] text-slate-600">需 customization:update</span>}>
            <button onClick={trigger} disabled={!!running || busy}
              title={domain ? `只跑 ${domain} 域用例` : '跑全量评测集'}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-primary/15 border border-primary/30 text-primary disabled:opacity-40">
              <Play className="w-3 h-3" /> {running ? '评测中…' : '触发评测'}
            </button>
          </Can>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select className={selCls} data-testid="eval-domain" value={domain}
          onChange={(e) => setDomain(e.target.value)}>
          <option value="">全部（{totalCases} 用例）</option>
          {domains.map((d) => (
            <option key={d.domain} value={d.domain}>{d.domain}（{d.cases} 用例）</option>
          ))}
        </select>
        <select className={selCls} data-testid="eval-source" value={source}
          onChange={(e) => setSource(e.target.value)}>
          <option value="">全部来源</option>
          <option value="manual">手动触发</option>
          <option value="gate">发布门禁</option>
        </select>
        <select className={selCls} value={String(limit)}
          onChange={(e) => setLimit(Number(e.target.value))}>
          {[20, 50, 100].map((n) => <option key={n} value={String(n)}>最近 {n} 次</option>)}
        </select>
        <span className="text-[11px] text-slate-500">
          共 {stat.total} 次 · 通过 {stat.pass} · 失败 {stat.fail}
          {stat.running > 0 && ` · 进行中 ${stat.running}`}
          {stat.noCounts > 0 && ` · ${stat.noCounts} 次未取到计数（不计入趋势）`}
        </span>
      </div>

      {message && <div className="text-[11px] text-slate-400" data-testid="eval-message">{message}</div>}
      {error && <div className="text-[11px] text-status-circuit">{error}</div>}

      {/* 通过率折线：纵轴 0–100%，点按时间升序（最新在右） */}
      <div className="rounded-xl border border-[#1e2536] bg-bg-deep/40 p-3">
        <div className="text-[10px] text-slate-500 mb-2">
          通过率趋势{domain ? ` · ${domain}` : ' · 全部运行'}
          {casesOfDomain !== null && ` · 选中域 ${casesOfDomain} 用例`}
        </div>
        {points.length === 0 ? (
          <div className="h-24 flex items-center justify-center text-[11px] text-slate-600">
            暂无可绘制的趋势点（评测历史为空，或这些运行未取到 pytest 计数）
          </div>
        ) : (
          <svg viewBox="0 0 100 100" preserveAspectRatio="none"
            className="h-24 w-full" data-testid="eval-chart">
            <line x1="0" y1="0" x2="100" y2="0" stroke="#2a3142" strokeWidth="0.6" />
            <line x1="0" y1="50" x2="100" y2="50" stroke="#2a3142" strokeWidth="0.4"
              strokeDasharray="2 2" />
            <line x1="0" y1="100" x2="100" y2="100" stroke="#2a3142" strokeWidth="0.6" />
            <polyline fill="none" stroke="#22c55e" strokeWidth="1.2"
              points={points.map((p) => `${p.x},${p.y}`).join(' ')} />
            {points.map((p) => (
              <circle key={p.runId} cx={p.x} cy={p.y} r="1.4"
                fill={p.y > 0 ? '#22c55e' : '#ef4444'} />
            ))}
          </svg>
        )}
      </div>

      <div className="rounded-xl border border-[#1e2536] overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="bg-bg-input/60 text-slate-500 text-left">
              <th className="px-3 py-2 font-normal">时间</th>
              <th className="px-3 py-2 font-normal">筛选域（-k）</th>
              <th className="px-3 py-2 font-normal">来源</th>
              <th className="px-3 py-2 font-normal">结果</th>
              <th className="px-3 py-2 font-normal">用例计数</th>
              <th className="px-3 py-2 font-normal">通过率</th>
              <th className="px-3 py-2 font-normal">时长</th>
              <th className="px-3 py-2 font-normal">触发人</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && !busy && (
              <tr><td colSpan={8} className="px-3 py-4 text-center text-slate-500">
                {error ? '查询失败，未取到记录' : '还没有评测记录：选一个域点「触发评测」'}
              </td></tr>
            )}
            {runs.map((r) => {
              const v = verdictOf(r)
              const rate = passRateOf(r)
              const open = openRun === r.run_id
              return (
                <FragmentRun key={r.run_id} run={r} open={open} verdict={v}
                  rateLabel={rate === null ? '—' : `${(rate * 100).toFixed(0)}%`}
                  onToggle={() => setOpenRun(open ? null : r.run_id)} />
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-slate-600">
        「筛选域」列存的是 pytest -k 表达式：手动触发传业务域名，发布门禁传工作流登记的关键词
        （如 reimb 是用例 id 前缀）。D-4 的资金/薪酬/合同域发布门禁 FAIL 会自动回滚上一版本，
        这类运行在表里以 source=gate 单列可查。
      </p>
    </div>
  )
}

function FragmentRun({ run, open, verdict, rateLabel, onToggle }: {
  run: EvalRun
  open: boolean
  verdict: ReturnType<typeof verdictOf>
  rateLabel: string
  onToggle: () => void
}) {
  const tone = {
    pass: 'bg-status-ok/10 text-status-ok border-status-ok/30',
    fail: 'bg-status-circuit/10 text-status-circuit border-status-circuit/30',
    running: 'bg-status-warn/10 text-status-warn border-status-warn/30',
    unknown: 'bg-bg-input text-slate-400 border-[#2a3142]',
  }[verdict]
  const label = verdict === 'running' ? '进行中'
    : verdict === 'pass' ? '通过'
      : verdict === 'fail' ? '失败' : run.status

  return (
    <>
      <tr className="border-t border-[#1e2536] hover:bg-bg-hover/40" onClick={onToggle}>
        <td className="px-3 py-2 font-mono text-slate-400 whitespace-nowrap">
          {(run.created_at || '').slice(0, 19).replace('T', ' ') || '—'}
        </td>
        <td className="px-3 py-2 text-slate-200 whitespace-nowrap">{run.domain || '全量'}</td>
        <td className="px-3 py-2 text-slate-400 whitespace-nowrap">
          {run.source === 'gate' ? '门禁' : run.source === 'manual' ? '手动' : run.source || '—'}
        </td>
        <td className="px-3 py-2">
          <span className={cn('px-1.5 py-0.5 rounded border text-[10px] whitespace-nowrap', tone)}>
            {label}
          </span>
        </td>
        <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{countsLabel(run)}</td>
        <td className="px-3 py-2 text-slate-300 whitespace-nowrap">{rateLabel}</td>
        <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{durationLabel(run)}</td>
        <td className="px-3 py-2 text-slate-300 whitespace-nowrap">{run.triggered_by || '—'}</td>
      </tr>
      {open && (
        <tr className="border-t border-[#1e2536] bg-bg-input/30">
          <td colSpan={8} className="px-3 py-2.5 space-y-1.5">
            <div className="text-[10px] text-slate-500">
              run_id：{run.run_id} · exit_code：{run.exit_code ?? '—'} ·
              结束于：{(run.finished_at || '').slice(0, 19).replace('T', ' ') || '未结束'}
            </div>
            <pre data-testid={`eval-tail-${run.run_id}`}
              className="max-h-48 overflow-auto rounded bg-bg-deep/60 p-2 text-[10px] text-slate-400 whitespace-pre-wrap">
              {run.tail || '无输出尾部'}
            </pre>
          </td>
        </tr>
      )}
    </>
  )
}
