/**
 * MCP 治理台（路线图 Phase F / F4.6 管理端）
 * ==========================================
 * 两个方向分两个分区，因为它们相反且治理口径不同：
 *
 * 入口（外部 MCP Server 接入，F4.1/F4.2）
 *   GET    /agent-ops/mcp-servers                连接清单 + 各自生效中的暴露工具
 *   POST   /agent-ops/mcp-servers                登记（URL 过 §2.4 SSRF）
 *   POST   /agent-ops/mcp-servers/probe          保存前预检（不发到生产配置）
 *   POST   /agent-ops/mcp-servers/{name}/probe   连接测试 + 工具发现（结果写回）
 *   GET    /agent-ops/mcp-servers/{name}/tools   最近一次探测到的工具清单（只读）
 *   PUT    /agent-ops/mcp-servers/{name}/tools   勾选暴露（全量覆盖；扩面→人审，收缩→即时）
 *   DELETE /agent-ops/mcp-servers/{name}         删除登记并撤销暴露
 *
 * 出口（本系统把工具导出给外部调用，F4.3）
 *   GET  /agent-ops/mcp-exposure                 候选域 × 暴露状态 × 工具计数
 *   PUT  /agent-ops/mcp-exposure/{domain}        开/关该域（关闭即时，开放须人审）
 *
 * 红线口径：
 * - 工具级启停**不在这里做**：沿用定制中心"工具治理"分区的 tool 类定制，
 *   避免同一个工具两处可停、两处真相；
 * - 改地址属高风险变更：该域已暴露时后端会拒绝（须先撤销暴露）；
 * - 放宽类动作（首次接入/扩面/开放出口域）返回 pending_approval —— 界面必须
 *   显示"已提交人审"而不是"已生效"。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  Download,
  Plug,
  Plus,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  Trash2,
  X,
} from 'lucide-react'
import { apiService } from '../services/api'
import Can from './Can'
import { cn } from '../lib/utils'

type Section = 'inbound' | 'outbound'

const SECTIONS: { key: Section; label: string; icon: typeof Plug }[] = [
  { key: 'inbound', label: '外部 MCP 接入', icon: Server },
  { key: 'outbound', label: 'MCP 对外出口', icon: Download },
]

const inputCls =
  'w-full text-[12px] px-2 py-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-200 ' +
  'focus:outline-none focus:border-primary/60 disabled:opacity-50'

interface McpServer {
  name: string
  url: string
  description: string
  last_probe: Record<string, unknown> | null
  exposed_tools: string[]
  exposure_enabled: boolean
  created_by?: string | null
  updated_at?: string | null
}

interface ExposureDomain {
  domain: string
  label: string
  exposed: boolean
  customized: boolean
  tool_total: number
  tool_exposed: number
}

interface ProbeResult {
  ok?: boolean
  tools?: string[]
  error?: string
  at?: string
}

/* ================================================================
 *  纯函数（供单测）
 * ================================================================ */

/** 探测结果归一：列表行与探测面板共用同一判定，避免"绿勾但工具数为 0"的误导。 */
export function probeOf(server: McpServer): { ok: boolean; tools: string[]; at: string; error: string } {
  const p = (server.last_probe || {}) as ProbeResult
  return {
    ok: Boolean(p.ok),
    tools: Array.isArray(p.tools) ? p.tools : [],
    at: typeof p.at === 'string' ? p.at : '',
    error: typeof p.error === 'string' ? p.error : '',
  }
}

/** 勾选差异：扩面（added 非空）须人审，纯收缩即时生效——界面据此预告文案。 */
export function diffTools(
  current: string[], next: string[],
): { added: string[]; removed: string[] } {
  const c = new Set(current)
  const n = new Set(next)
  return {
    added: [...n].filter((t) => !c.has(t)),
    removed: [...c].filter((t) => !n.has(t)),
  }
}

/**
 * 幂等键：由动作语义（对象 + 目标状态）决定，而不是每次点击新生成——
 * 双击/重发才会落在同一个键上被后端 5 分钟窗口拦下（§2.4 防重放）。
 */
export function idemKey(scope: string, target: string, state: string): string {
  return `${scope}:${target}:${state}`
}

/** 生效后的变更提示语：把后端 status 翻译成人看得懂的"生效范围"。 */
export function changeNotice(result: Record<string, any> | null | undefined): string {
  const status = result?.status
  if (status === 'pending_approval') {
    return '已提交人工审批（放宽类变更），批准后才会生效。当前运行时配置未改动。'
  }
  if (status === 'applied') return '收紧类变更，已即时生效。'
  if (status === 'duplicate') return '重复请求已被幂等键拦下（5 分钟窗口）。'
  if (status === 'rejected') return '变更已被驳回。'
  return result?.reply || '已完成。'
}

/* ================================================================
 *  小组件
 * ================================================================ */

function SectionTabs({ section, onChange }: { section: Section; onChange: (s: Section) => void }) {
  return (
    <div className="flex items-center gap-1.5">
      {SECTIONS.map((s) => {
        const isActive = section === s.key
        return (
          <button
            key={s.key}
            onClick={() => onChange(s.key)}
            className={cn(
              'inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-[12px] font-medium border transition-all',
              isActive
                ? 'text-primary border-primary/40 bg-primary/5'
                : 'text-slate-400 border-[#1e2536] hover:text-white hover:bg-bg-hover',
            )}
          >
            <s.icon className="w-3.5 h-3.5" />
            {s.label}
          </button>
        )
      })}
    </div>
  )
}

function Tag({ tone, children }: { tone: 'ok' | 'warn' | 'bad' | 'mute'; children: React.ReactNode }) {
  const cls = {
    ok: 'bg-status-ok/10 text-status-ok border-status-ok/30',
    warn: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
    bad: 'bg-status-circuit/10 text-status-circuit border-status-circuit/30',
    mute: 'bg-bg-input text-slate-400 border-[#2a3142]',
  }[tone]
  return (
    <span className={cn('inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px]', cls)}>
      {children}
    </span>
  )
}

/* ================================================================
 *  入口分区：外部 MCP Server
 * ================================================================ */

function InboundSection({
  servers, onReload, pendingCount,
}: { servers: McpServer[]; onReload: () => void; pendingCount: number }) {
  const [form, setForm] = useState({ name: '', url: '', description: '' })
  const [precheck, setPrecheck] = useState<Record<string, any> | null>(null)
  const [selected, setSelected] = useState<string>('')
  const [discovered, setDiscovered] = useState<string[]>([])
  const [checked, setChecked] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const current = useMemo(() => servers.find((s) => s.name === selected) || null, [servers, selected])

  const openToolPanel = useCallback(async (name: string) => {
    setSelected(name)
    setError('')
    setNotice('')
    try {
      const res = await apiService.getMcpServerTools(name)
      const tools = Array.isArray(res?.tools) ? res.tools : []
      setDiscovered(tools)
      const server = servers.find((s) => s.name === name)
      const exposed = server?.exposed_tools || []
      // 已生效但本轮没探测到的工具仍保留在勾选态（全量覆盖语义下丢勾=撤销暴露）
      setChecked(Array.from(new Set([...tools.filter((t: string) => exposed.includes(t)), ...exposed])))
    } catch (e: any) {
      setError(e?.message || '工具清单读取失败')
    }
  }, [servers])

  const run = async (key: string, fn: () => Promise<any>, onOk?: (r: any) => void) => {
    setBusy(key)
    setError('')
    try {
      const res = await fn()
      if (onOk) onOk(res)
      return res
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '操作失败')
      return null
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-4">
      <Can resource="customization" action="update">
        <div className="rounded-xl border border-[#1e2536] bg-bg-card/40 p-3 space-y-2">
          <div className="flex items-center gap-2 text-[12px] text-slate-300">
            <Plus className="w-3.5 h-3.5 text-primary" />
            登记外部 MCP Server
            <span className="text-slate-500">（仅登记连接信息，暴露工具需另行勾选并过人审）</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
            <input
              className={inputCls}
              placeholder="名称：小写字母开头 a-z0-9_"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              data-testid="mcp-name"
            />
            <input
              className={cn(inputCls, 'md:col-span-2')}
              placeholder="https://mcp.example.com/mcp（须公网可达且在出站白名单内）"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              data-testid="mcp-url"
            />
            <input
              className={inputCls}
              placeholder="说明"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={!form.url || busy !== null}
              onClick={() => run('precheck', () => apiService.probeMcpUrl(form.url), (r) => setPrecheck(r))}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-bg-input border border-[#2a3142] text-slate-300 disabled:opacity-40"
            >
              <Search className="w-3 h-3" />
              保存前预检
            </button>
            <button
              disabled={!form.name || !form.url || busy !== null}
              onClick={() => run('create', () => apiService.createMcpServer({
                name: form.name.trim(), url: form.url.trim(), description: form.description.trim(),
              }), () => {
                setForm({ name: '', url: '', description: '' })
                setPrecheck(null)
                setNotice('已登记（未暴露任何工具）。')
                onReload()
              })}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-primary/20 border border-primary/40 text-primary disabled:opacity-40"
            >
              <Check className="w-3 h-3" />
              登记
            </button>
            {precheck && (
              <span className={cn('text-[11px]', precheck.ok ? 'text-status-ok' : 'text-status-circuit')}>
                {precheck.ok
                  ? `可达，发现 ${(precheck.tools || []).length} 个工具：${(precheck.tools || []).join(', ')}`
                  : `预检未通过：${precheck.error || '未知原因'}`}
              </span>
            )}
          </div>
        </div>
      </Can>

      {error && <div className="text-[11px] text-status-circuit flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{error}</div>}
      {notice && <div className="text-[11px] text-status-ok">{notice}</div>}
      {pendingCount > 0 && (
        <div className="text-[11px] text-yellow-400 flex items-center gap-1">
          <ShieldAlert className="w-3 h-3" />
          有 {pendingCount} 条 MCP 暴露变更正在等待人工审批（批准后进治理面才可见）
        </div>
      )}

      <div className="rounded-xl border border-[#1e2536] overflow-hidden">
        <table className="w-full text-[12px]">
          <thead className="bg-bg-input/60 text-slate-400 text-[11px]">
            <tr>
              {['名称', '地址', '连接测试', '生效中的暴露工具', '操作'].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {servers.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-slate-500 text-[11px]">
                暂无登记的外部 MCP Server
              </td></tr>
            )}
            {servers.map((s) => {
              const p = probeOf(s)
              return (
                <tr key={s.name} className="border-t border-[#1e2536] hover:bg-bg-hover/40">
                  <td className="px-3 py-2 text-slate-200">
                    {s.name}
                    {s.description && <div className="text-[10px] text-slate-500">{s.description}</div>}
                  </td>
                  <td className="px-3 py-2 text-slate-400 break-all max-w-[260px]">{s.url}</td>
                  <td className="px-3 py-2">
                    {p.at ? (
                      <Tag tone={p.ok ? 'ok' : 'bad'}>
                        {p.ok ? `OK · ${p.tools.length} 工具` : `失败 · ${p.error || '未知'}`}
                      </Tag>
                    ) : <Tag tone="mute">未测试</Tag>}
                  </td>
                  <td className="px-3 py-2">
                    {s.exposed_tools.length ? (
                      <div className="flex flex-wrap gap-1">
                        {s.exposed_tools.map((t) => <Tag key={t} tone="ok">{t}</Tag>)}
                      </div>
                    ) : <Tag tone="mute">未暴露</Tag>}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <Can resource="customization" action="update">
                      <button
                        onClick={() => run(`probe:${s.name}`, () => apiService.probeMcpServer(s.name), onReload)}
                        disabled={busy !== null}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-slate-300 hover:bg-bg-hover disabled:opacity-40"
                      >
                        <RefreshCw className="w-3 h-3" />测试
                      </button>
                      <button
                        onClick={() => openToolPanel(s.name)}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-primary hover:bg-primary/10"
                      >
                        <Plug className="w-3 h-3" />勾选工具
                      </button>
                      <button
                        onClick={() => run(`del:${s.name}`, () => apiService.deleteMcpServer(s.name), (r) => {
                          setNotice(`${s.name}：${changeNotice(r)}`)
                          onReload()
                        })}
                        disabled={busy !== null}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-status-circuit hover:bg-status-circuit/10 disabled:opacity-40"
                      >
                        <Trash2 className="w-3 h-3" />删除
                      </button>
                    </Can>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {current && (
        <div className="rounded-xl border border-[#1e2536] bg-bg-card/40 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-[12px] text-slate-200">
              暴露工具勾选 · {current.name}
              <span className="text-slate-500">（全量覆盖：取消勾选 = 撤销暴露，即时生效）</span>
            </div>
            <button onClick={() => setSelected('')} className="text-slate-500 hover:text-white">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
          {discovered.length === 0 && (
            <div className="text-[11px] text-slate-500">
              最近一次探测没有工具清单，请先点「测试」连接测试（GET /tools 只读上次结果，不发外部请求）。
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {discovered.map((t) => (
              <label key={t} className="inline-flex items-center gap-1.5 text-[11px] text-slate-300">
                <input
                  type="checkbox"
                  checked={checked.includes(t)}
                  onChange={() => setChecked((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]))}
                />
                {t}
              </label>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <input
              className={inputCls}
              placeholder="变更说明（进审计与审批卡片）"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            <Can resource="customization" action="update">
              <button
                disabled={busy !== null}
                onClick={() => run(`tools:${current.name}`, () => apiService.setMcpServerTools(
                  current.name, checked, comment.trim(),
                  idemKey('mcp-tools', current.name, checked.slice().sort().join(',')),
                ), (r) => {
                  setNotice(changeNotice(r))
                  setComment('')
                  onReload()
                })}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] bg-primary/20 border border-primary/40 text-primary whitespace-nowrap disabled:opacity-40"
              >
                <Check className="w-3 h-3" />
                提交
              </button>
            </Can>
          </div>
          {(() => {
            const d = diffTools(current.exposed_tools, checked)
            if (d.added.length) {
              return (
                <div className="text-[11px] text-yellow-400">
                  扩面 +{d.added.length} 个工具（{d.added.join(', ')}）：属放宽类，须人工审批后才注册进治理面。
                </div>
              )
            }
            if (d.removed.length) {
              return (
                <div className="text-[11px] text-status-ok">
                  收缩 −{d.removed.length} 个工具（{d.removed.join(', ')}）：属收紧类，即时生效。
                </div>
              )
            }
            return null
          })()}
          <div className="text-[10px] text-slate-500">
            注意：已注册进治理面的外部工具不会因为取消勾选而从注册表消失（注册表 append-only）。
            要让 Agent 立刻停止可调用它，请到「Agent 定制中心 · 工具治理」停用该工具。
          </div>
        </div>
      )}
    </div>
  )
}

/* ================================================================
 *  出口分区：域暴露开关
 * ================================================================ */

function OutboundSection({ domains, onReload, pendingCount }: {
  domains: ExposureDomain[]; onReload: () => void; pendingCount: number
}) {
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const toggle = async (d: ExposureDomain) => {
    setBusy(d.domain)
    setError('')
    setNotice('')
    try {
      const res = await apiService.setMcpDomainExposure(
        d.domain, !d.exposed,
        `管理端${d.exposed ? '关闭' : '开放'} MCP 出口域 ${d.label}`,
        idemKey('mcp-exp', d.domain, String(!d.exposed)))
      setNotice(`${d.label}：${changeNotice(res)}`)
      onReload()
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '操作失败')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-slate-400">
        出口 = 本系统把内部工具导出为 MCP 端点供外部系统调用（挂载路径
        <code className="text-slate-300"> /{'{domain}'}/mcp</code>
        ，Bearer Token 鉴权不变）。关闭为收紧即时生效，开放为放宽须人审。
      </div>
      {pendingCount > 0 && (
        <div className="text-[11px] text-yellow-400 flex items-center gap-1">
          <ShieldAlert className="w-3 h-3" />
          有 {pendingCount} 条出口开放申请等待人工审批
        </div>
      )}
      {error && <div className="text-[11px] text-status-circuit flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{error}</div>}
      {notice && <div className="text-[11px] text-status-ok">{notice}</div>}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {domains.map((d) => (
          <div key={d.domain} className={cn(
            'rounded-xl border p-3 space-y-1.5',
            d.exposed ? 'border-status-ok/30 bg-status-ok/[0.04]' : 'border-[#1e2536] bg-bg-card/40',
          )}>
            <div className="flex items-center justify-between">
              <div className="text-[12px] text-slate-200">{d.label}</div>
              <Tag tone={d.exposed ? 'ok' : 'mute'}>{d.exposed ? '已开放' : '已关闭'}</Tag>
            </div>
            <div className="text-[10px] text-slate-500">
              <code>{d.domain}</code> · 出口工具 {d.tool_exposed}/{d.tool_total}
              {!d.customized && <Tag tone="mute">默认</Tag>}
            </div>
            <Can resource="customization" action="update">
              <button
                onClick={() => toggle(d)}
                disabled={busy !== null}
                className={cn(
                  'w-full mt-1 px-2 py-1.5 rounded-lg text-[11px] border transition-colors disabled:opacity-40',
                  d.exposed
                    ? 'text-status-circuit border-status-circuit/30 hover:bg-status-circuit/10'
                    : 'text-primary border-primary/30 hover:bg-primary/10',
                )}
              >
                {d.exposed ? '关闭该域出口（即时生效）' : '开放该域出口（转人工审批）'}
              </button>
            </Can>
          </div>
        ))}
      </div>
      <div className="text-[10px] text-slate-500">
        工具级启停请到「Agent 定制中心 · 工具治理」，本页面只控域级通道，避免同一工具两处真相。
      </div>
    </div>
  )
}

/* ================================================================
 *  主组件
 * ================================================================ */

export function McpGovernance() {
  const [section, setSection] = useState<Section>('inbound')
  const [servers, setServers] = useState<McpServer[]>([])
  const [domains, setDomains] = useState<ExposureDomain[]>([])
  const [pending, setPending] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [srv, exp, appr] = await Promise.all([
        apiService.getMcpServers(),
        apiService.getMcpExposure(),
        apiService.getAgentApprovals('pending'),
      ])
      setServers(Array.isArray(srv?.servers) ? srv.servers : [])
      setDomains(Array.isArray(exp?.domains) ? exp.domains : [])
      const all = Array.isArray(appr?.approvals) ? appr.approvals : []
      setPending(all.filter((a: any) => typeof a?.tool === 'string'
        && (a.tool.startsWith('mcp:') || a.tool.startsWith('mcp_export:'))))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const inboundPending = useMemo(
    () => pending.filter((a) => String(a.tool).startsWith('mcp:')).length, [pending])
  const outboundPending = useMemo(
    () => pending.filter((a) => String(a.tool).startsWith('mcp_export:')).length, [pending])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <SectionTabs section={section} onChange={setSection} />
        <button
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] text-slate-400 hover:text-white hover:bg-bg-hover disabled:opacity-40"
        >
          <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
          刷新
        </button>
      </div>
      {section === 'inbound'
        ? <InboundSection servers={servers} onReload={load} pendingCount={inboundPending} />
        : <OutboundSection domains={domains} onReload={load} pendingCount={outboundPending} />}
    </div>
  )
}

export default McpGovernance
