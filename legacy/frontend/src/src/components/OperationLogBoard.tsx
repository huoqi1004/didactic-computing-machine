/**
 * 操作日志看板（roadmap-logging-sandbox G3.2）
 * =============================================
 * 数据源：GET /agent-ops/operation-logs（business_key/trace_id 双键 + 多维过滤）
 *         POST /agent-ops/operation-logs/verify/run（审计链手动即时校验，P2-1）
 *         GET  /agent-ops/operation-logs/export（CSV 导出，行为自身审计）
 *
 * 红线 5 的可见面（闭环 P0-1/P1-3/P2-3）：
 * - 第一入口 = 单据号搜索框（业务语言，不要求用户知道 trace_id）；
 * - 详情抽屉内提供"同链视图"：一键按 trace_id 过滤还原完整链路；
 * - 校验报告卡：断链当场点名（broken_at / expected / actual）。
 */
import { useCallback, useEffect, useState } from 'react'
import { Download, FileSearch, Link2, RefreshCw, ShieldCheck } from 'lucide-react'
import { apiService } from '../services/api'
import { cn } from '../lib/utils'

interface OpLogRow {
  id: number
  ts: string
  actor: string
  source: string
  action: string
  resource: string
  result: string
  risk_level: string
  trace_id: string
  business_key: string
  request_id: string
  details?: Record<string, unknown> | null
  table?: string
}

interface VerifyState {
  last_incremental_at?: string | null
  last_full_at?: string | null
  last_report?: Record<string, unknown> | null
}

const SOURCES = ['', 'http', 'agent', 'mcp', 'workflow', 'system']
const RISKS = ['', 'low', 'medium', 'high']
const RESULTS = ['', 'success', 'failure', 'denied', 'pending']

function riskTone(level: string): string {
  if (level === 'high') return 'text-rose-600 bg-rose-50'
  if (level === 'medium') return 'text-amber-600 bg-amber-50'
  return 'text-slate-500 bg-slate-100'
}

function resultTone(result: string): string {
  if (result === 'success') return 'text-emerald-600'
  if (result === 'denied') return 'text-rose-600'
  if (result === 'pending') return 'text-amber-600'
  return 'text-slate-500'
}

const emptyFilters = {
  business_key: '', trace_id: '', actor: '', action: '',
  source: '', risk_level: '', result: '',
}

export function OperationLogBoard() {
  const [filters, setFilters] = useState({ ...emptyFilters })
  const [page, setPage] = useState(1)
  const [data, setData] = useState<{ items: OpLogRow[]; total: number; table?: string }>({ items: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<OpLogRow | null>(null)
  const [verify, setVerify] = useState<VerifyState | null>(null)
  const [verifyBusy, setVerifyBusy] = useState(false)
  const [verifyMsg, setVerifyMsg] = useState('')

  const query = useCallback(async (p = page, f = filters) => {
    setLoading(true)
    setError('')
    try {
      const clean = Object.fromEntries(
        Object.entries(f).filter(([, v]) => v !== ''),
      )
      const res = await apiService.queryOperationLogs({ ...clean, page: p, limit: 50 })
      setData({ items: (res?.items ?? []) as OpLogRow[], total: res?.total ?? 0, table: res?.table })
    } catch (e) {
      setError(e instanceof Error ? e.message : '查询失败')
    } finally {
      setLoading(false)
    }
  }, [page, filters])

  useEffect(() => {
    query(1, filters)
    apiService.getAuditVerifyState?.().then((s) => setVerify(s?.state ?? s)).catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const runVerify = async () => {
    setVerifyBusy(true)
    setVerifyMsg('')
    try {
      const r = await apiService.runAuditVerify()
      setVerifyMsg(
        r?.status === 'ok'
          ? `校验通过：共 ${r.checked} 条链上记录完整`
          : r?.status === 'broken'
            ? `断链！位于 ${r.broken_at}（预期 ${String(r.expected).slice(0, 16)}…，实际 ${String(r.actual).slice(0, 16)}…）`
            : `校验未执行：${r?.reason ?? r?.error ?? '未知'}`,
      )
      apiService.getAuditVerifyState?.().then((s) => setVerify(s?.state ?? s)).catch(() => undefined)
    } catch (e) {
      setVerifyMsg(e instanceof Error ? e.message : '校验失败（可能缺少 audit_verify:run 权限）')
    } finally {
      setVerifyBusy(false)
    }
  }

  const exportCsv = async () => {
    try {
      await apiService.exportOperationLogs(
        Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== '')),
      )
    } catch {
      setError('导出失败（可能缺少 operationlog:export 权限）')
    }
  }

  const jumpTrace = (tid: string) => {
    if (!tid) return
    setFilters({ ...emptyFilters, trace_id: tid })
    setPage(1)
    query(1, { ...emptyFilters, trace_id: tid })
  }

  const setF = (k: keyof typeof emptyFilters, v: string) => {
    setFilters((prev) => ({ ...prev, [k]: v }))
  }

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault()
    setPage(1)
    query(1, filters)
  }

  const pages = Math.max(1, Math.ceil(data.total / 50))

  return (
    <div className="space-y-4">
      {/* 完整性报告卡（P2-1：篡改当场点名） */}
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck className="h-4 w-4 text-emerald-600" />
            审计链完整性
            {verify?.last_report && (
              <span className={cn(
                'rounded px-2 py-0.5 text-xs',
                (verify.last_report as any).status === 'ok' ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700',
              )}>
                {String((verify.last_report as any).status) === 'ok'
                  ? `上次校验通过（${String((verify.last_report as any).checked)} 条）`
                  : `上次结果：${String((verify.last_report as any).status)}`}
              </span>
            )}
          </div>
          <button
            onClick={runVerify}
            disabled={verifyBusy}
            className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-bg-hover disabled:opacity-50"
          >
            {verifyBusy ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            立即全量校验
          </button>
        </div>
        {verifyMsg && <p className="mt-2 text-xs text-slate-600">{verifyMsg}</p>}
      </div>

      {/* 查询区：第一入口 = 单据号（P0-1） */}
      <form onSubmit={submit} className="rounded-lg border border-border bg-surface p-4 space-y-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="block">
            <span className="mb-1 flex items-center gap-1 text-xs font-medium text-slate-600">
              <FileSearch className="h-3.5 w-3.5" /> 业务单据号（第一入口）
            </span>
            <input
              value={filters.business_key}
              onChange={(e) => setF('business_key', e.target.value)}
              placeholder="如 RE-2026-0001 / 调拨单号 / 合同号"
              className="w-full rounded-md border border-border bg-bg-input px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-accent"
            />
          </label>
          <label className="block">
            <span className="mb-1 flex items-center gap-1 text-xs font-medium text-slate-600">
              <Link2 className="h-3.5 w-3.5" /> 链路键 trace_id（二等入口）
            </span>
            <input
              value={filters.trace_id}
              onChange={(e) => setF('trace_id', e.target.value)}
              placeholder="32 位十六进制（详情页可一键带出）"
              className="w-full rounded-md border border-border bg-bg-input px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-accent"
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
          <input value={filters.actor} onChange={(e) => setF('actor', e.target.value)}
                 placeholder="操作人" className="rounded-md border border-border bg-bg-input px-2 py-1.5 text-xs" />
          <input value={filters.action} onChange={(e) => setF('action', e.target.value)}
                 placeholder="动作前缀，如 tool." className="rounded-md border border-border bg-bg-input px-2 py-1.5 text-xs" />
          <select value={filters.source} onChange={(e) => setF('source', e.target.value)}
                  className="rounded-md border border-border bg-bg-input px-2 py-1.5 text-xs">
            {SOURCES.map((s) => <option key={s} value={s}>{s ? `来源 ${s}` : '全部来源'}</option>)}
          </select>
          <select value={filters.risk_level} onChange={(e) => setF('risk_level', e.target.value)}
                  className="rounded-md border border-border bg-bg-input px-2 py-1.5 text-xs">
            {RISKS.map((r) => <option key={r} value={r}>{r ? `风险 ${r}` : '全部风险'}</option>)}
          </select>
          <select value={filters.result} onChange={(e) => setF('result', e.target.value)}
                  className="rounded-md border border-border bg-bg-input px-2 py-1.5 text-xs">
            {RESULTS.map((r) => <option key={r} value={r}>{r ? `结果 ${r}` : '全部结果'}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <button type="submit" className="flex items-center gap-1 rounded-md bg-accent px-4 py-1.5 text-xs font-medium text-white hover:opacity-90">
            <FileSearch className="h-3.5 w-3.5" /> 查询
          </button>
          <button type="button" onClick={() => { setFilters({ ...emptyFilters }); setPage(1); query(1, { ...emptyFilters }) }}
                  className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-bg-hover">
            重置
          </button>
          <button type="button" onClick={exportCsv}
                  className="ml-auto flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-bg-hover">
            <Download className="h-3.5 w-3.5" /> 导出 CSV
          </button>
        </div>
      </form>

      {error && <p className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>}

      {/* 结果表 */}
      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full text-left text-xs">
          <thead className="bg-bg-hover text-slate-500">
            <tr>
              <th className="px-3 py-2">时间</th>
              <th className="px-3 py-2">操作人</th>
              <th className="px-3 py-2">动作</th>
              <th className="px-3 py-2">来源</th>
              <th className="px-3 py-2">结果</th>
              <th className="px-3 py-2">风险</th>
              <th className="px-3 py-2">单据号</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border bg-surface">
            {loading && (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-400">加载中…</td></tr>
            )}
            {!loading && data.items.length === 0 && (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-400">无匹配记录（含归档表检索）</td></tr>
            )}
            {!loading && data.items.map((row) => (
              <tr key={`${row.id}`} className="cursor-pointer hover:bg-bg-hover" onClick={() => setDetail(row)}>
                <td className="whitespace-nowrap px-3 py-2 text-slate-500">{(row.ts || '').replace('T', ' ').slice(0, 19)}</td>
                <td className="px-3 py-2 font-medium">{row.actor}</td>
                <td className="px-3 py-2 font-mono">{row.action}</td>
                <td className="px-3 py-2 text-slate-500">{row.source}</td>
                <td className={cn('px-3 py-2 font-medium', resultTone(row.result))}>{row.result}</td>
                <td className="px-3 py-2">
                  <span className={cn('rounded px-1.5 py-0.5', riskTone(row.risk_level))}>{row.risk_level}</span>
                </td>
                <td className="px-3 py-2 font-mono text-slate-600">{row.business_key || '—'}</td>
                <td className="px-3 py-2">
                  {row.trace_id && (
                    <button onClick={(e) => { e.stopPropagation(); jumpTrace(row.trace_id) }}
                            className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] hover:bg-bg-hover">
                      <Link2 className="h-3 w-3" /> 同链
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>共 {data.total} 条{data.table === 'archive' ? '（命中归档表）' : ''}</span>
        <div className="flex items-center gap-2">
          <button disabled={page <= 1} onClick={() => { const p = page - 1; setPage(p); query(p) }}
                  className="rounded border border-border px-2 py-1 disabled:opacity-40">上一页</button>
          <span>{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => { const p = page + 1; setPage(p); query(p) }}
                  className="rounded border border-border px-2 py-1 disabled:opacity-40">下一页</button>
        </div>
      </div>

      {/* 详情抽屉 */}
      {detail && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setDetail(null)}>
          <div className="h-full w-[480px] overflow-y-auto bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">操作详情 #{detail.id}</h3>
              <button onClick={() => setDetail(null)} className="text-xs text-slate-400 hover:text-slate-600">关闭</button>
            </div>
            <dl className="space-y-2 text-xs">
              {([
                ['时间', detail.ts], ['操作人', detail.actor], ['动作', detail.action],
                ['资源', detail.resource], ['来源', detail.source], ['结果', detail.result],
                ['风险', detail.risk_level], ['单据号', detail.business_key], ['request_id', detail.request_id],
              ] as const).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <dt className="w-20 shrink-0 text-slate-400">{k}</dt>
                  <dd className="break-all font-mono">{String(v || '—')}</dd>
                </div>
              ))}
              <div className="flex gap-2">
                <dt className="w-20 shrink-0 text-slate-400">trace_id</dt>
                <dd className="break-all font-mono text-accent">{detail.trace_id || '—'}</dd>
              </div>
            </dl>
            <button onClick={() => jumpTrace(detail.trace_id)}
                    className="mt-3 flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
              <Link2 className="h-3.5 w-3.5" /> 查看同链全部操作
            </button>
            <h4 className="mb-1 mt-4 text-xs font-semibold text-slate-500">details</h4>
            <pre className="max-h-72 overflow-auto rounded-md bg-bg-input p-3 text-[11px] leading-relaxed">
              {JSON.stringify(detail.details ?? {}, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
