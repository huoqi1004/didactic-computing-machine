/**
 * Agent 定制中心（Phase F1 一期 + F2.7 二期）
 * =============================================
 * 数据源：
 *   GET  /agent-ops/tools                       治理面工具目录（含风险级/权限/调用统计）
 *   GET  /agent-ops/customizations              当前生效覆盖（启停/风险分级/Agent/矩阵）
 *   GET  /agent-ops/customizations/meta         表单元数据（域/参数清单/角色/单据类型）
 *   PUT  /agent-ops/customizations/{type}/{target}   应用定制（收紧即时/放宽转人审）
 *   GET  /agent-ops/customizations/versions     变更历史（含待批提案行 pending_approval）
 *   POST /agent-ops/customizations/{type}/{target}/rollback  撤销某次变更
 *   GET  /agent-ops/approvals?status=pending    待审批队列（放宽类定制变更在此闭环）
 *   POST /agent-ops/approvals/{thread_id}/decide              批准/驳回（生效走 apply_pending）
 * 操作语义：收紧（write_low→write_high、停用）即时生效；放宽自动转人审（后端判定）。
 * 四个分区：工具治理（F1.7）· Agent 人设与链路参数（F2.7）· 风控阈值（F5.6）· 审批人路由矩阵（F2.7）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bot,
  Check,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  SlidersHorizontal,
  Gauge,
  History,
  Route,
  UserCog,
  X,
} from 'lucide-react'
import { apiService } from '../services/api'
import { AgentProfileCards } from './AgentProfileCards'
import { ApprovalRouteMatrix } from './ApprovalRouteMatrix'
import { ThresholdOverrides } from './ThresholdOverrides'
import Can from './Can'
import { cn } from '../lib/utils'

type Section = 'tools' | 'agents' | 'thresholds' | 'matrix'

const SECTIONS: { key: Section; label: string; icon: typeof SlidersHorizontal }[] = [
  { key: 'tools', label: '工具治理', icon: SlidersHorizontal },
  { key: 'agents', label: 'Agent 人设与参数', icon: UserCog },
  { key: 'thresholds', label: '风控阈值', icon: Gauge },
  { key: 'matrix', label: '审批路由矩阵', icon: Route },
]

interface ToolEntry {
  name: string
  description: string
  risk_level: string
  permission: string
  domain: string
  has_compensation: boolean
}

interface CustomizationEntry {
  type: string
  target: string
  enabled: boolean
  overrides: Record<string, unknown>
}

interface VersionEntry {
  id: number
  type: string
  target: string
  overrides: Record<string, unknown> | null
  status: string
  changed_by: string | null
  created_at: string | null
}

/** 人审任务（agent_approval_requests，domain=customization 即放宽类定制变更） */
interface PendingApproval {
  thread_id: string
  domain: string
  tool: string
  preview: string
  requested_by: string | null
  created_at: string | null
}

/** 版本流的"已生效"状态：仅这些行有变更前快照可撤销 */
const ROLLBACKABLE = new Set(['active', 'approved_executed', 'rolled_back'])

const RISK_OPTIONS = ['readonly', 'write_low', 'write_high']

function riskBadgeClass(risk: string) {
  if (risk === 'write_high') return 'bg-status-circuit/15 text-status-circuit border-status-circuit/30'
  if (risk === 'write_low') return 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30'
  return 'bg-primary/10 text-primary border-primary/30'
}

export function AgentCustomizationCenter() {
  const [tools, setTools] = useState<ToolEntry[]>([])
  const [overrides, setOverrides] = useState<Record<string, CustomizationEntry>>({})
  const [versions, setVersions] = useState<VersionEntry[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [loading, setLoading] = useState(false)
  const [acting, setActing] = useState<string | null>(null)
  const [section, setSection] = useState<Section>('tools')
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [toolsRes, custRes, verRes, apprRes] = await Promise.all([
        apiService.getAgentTools(),
        apiService.getAgentCustomizations(),
        apiService.getCustomizationVersions(),
        apiService.getAgentApprovals('pending'),
      ])
      setTools(Array.isArray(toolsRes?.tools) ? toolsRes.tools : [])
      const list = Array.isArray(custRes?.customizations) ? custRes.customizations : []
      const map: Record<string, CustomizationEntry> = {}
      for (const c of list) map[`${c.type}:${c.target}`] = c
      setOverrides(map)
      setVersions(Array.isArray(verRes?.versions) ? verRes.versions : [])
      // 待审批任务以 agent_approval_requests 为准（decide 接口按 thread_id 生效/驳回）；
      // 版本流里的 pending_approval 行只是提案留痕，不能直接决定
      setPendingApprovals(
        (Array.isArray(apprRes?.approvals) ? apprRes.approvals : [])
          .filter((a: PendingApproval & { domain?: string }) => a.domain === 'customization'),
      )
    } catch {
      setError('定制数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const matrixPending = useMemo(
    () => pendingApprovals.filter((a) => a.tool === 'gate:approval_route_matrix').length,
    [pendingApprovals],
  )

  // 阈值与矩阵同挂 gate 类型，待审批计数按 target 分开，否则两个分区互相冒领
  const thresholdPending = useMemo(
    () => pendingApprovals.filter((a) => a.tool.startsWith('gate:')
      && a.tool !== 'gate:approval_route_matrix').length,
    [pendingApprovals],
  )

  const decide = async (a: PendingApproval, approved: boolean) => {
    setBusy(a.thread_id)
    setError('')
    setMessage('')
    try {
      const res = await apiService.decideAgentApproval(a.thread_id, approved,
        approved ? '管理端批准定制变更' : '管理端驳回定制变更')
      setMessage(`${a.tool}：${res?.status === 'executed' ? '已批准并生效'
        : res?.status === 'rejected' ? '已驳回（未生效）' : `决定已提交（${res?.status || '未知' }）`}`)
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || `${a.tool} 审批决定失败`)
    } finally {
      setBusy(null)
    }
  }

  const rollback = async (v: VersionEntry) => {
    setBusy(`rollback:${v.id}`)
    setError('')
    setMessage('')
    try {
      const res = await apiService.rollbackCustomization(v.type, v.target, v.id)
      if (res?.status === 'pending_approval') {
        setMessage(`${v.type}:${v.target}：撤销目标为放宽态，已转人工审批`)
      } else {
        setMessage(`${v.type}:${v.target}：${res?.reason || '已撤销该次变更'}`)
      }
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || `${v.type}:${v.target} 撤销失败`)
    } finally {
      setBusy(null)
    }
  }

  useEffect(() => { load() }, [load])

  const save = async (tool: ToolEntry, patch: { enabled?: boolean; risk_override?: string }) => {
    const key = `tool:${tool.name}`
    const current = overrides[key]
    setActing(tool.name)
    setError('')
    try {
      const merged = {
        ...(current?.overrides || {}),
        ...(patch.risk_override ? { risk_override: patch.risk_override } : {}),
      }
      const res = await apiService.putAgentCustomization(
        'tool', tool.name,
        { overrides: merged, enabled: patch.enabled ?? current?.enabled ?? true },
      )
      if (res?.status === 'pending_approval') {
        setMessage(`${tool.name}：放宽类变更已提交人工审批，批准后生效`)
      } else if (res?.status === 'applied') {
        setMessage(`${tool.name}：已生效`)
      } else if (res?.status === 'duplicate') {
        setMessage(`${tool.name}：重复请求已忽略`)
      }
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || `${tool.name} 保存失败`)
      await load()
    } finally {
      setActing(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1.5 flex-wrap">
        {SECTIONS.map((s) => {
          const Icon = s.icon
          const on = section === s.key
          return (
            <button key={s.key} onClick={() => setSection(s.key)}
              className={cn(
                'text-[11px] px-2.5 py-1.5 rounded-lg border flex items-center gap-1.5 transition-colors',
                on ? 'border-primary/50 bg-primary/10 text-primary'
                  : 'border-slate-600 text-slate-400 hover:text-white',
              )}
            >
              <Icon className="w-3 h-3" />
              {s.label}
            </button>
          )
        })}
        <button onClick={load} disabled={loading}
          className="ml-auto text-xs text-slate-400 hover:text-white flex items-center gap-1">
          <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
          刷新
        </button>
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

      {/* 待审批队列：放宽类变更在此闭环（批准即生效/驳回不留痕），跨分区可见 */}
      {pendingApprovals.length > 0 && (
        <div className="rounded-xl border border-yellow-500/30 bg-yellow-500/5 p-3 space-y-2">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-3.5 h-3.5 text-yellow-400 shrink-0" />
            <h4 className="text-xs font-semibold text-yellow-400">
              待审批的定制变更（{pendingApprovals.length}）
              <span className="ml-2 font-normal text-slate-500">
                未批准前不参与运行 · 与 Agent 审批队列为同一份任务
              </span>
            </h4>
          </div>
          {pendingApprovals.map((a) => (
            <div key={a.thread_id} className="flex items-center gap-2 text-[11px] min-w-0">
              <code className="text-slate-200 truncate shrink-0 max-w-[35%]">{a.tool}</code>
              <span className="text-slate-500 truncate flex-1" title={a.preview}>{a.preview}</span>
              <span className="text-slate-600 shrink-0 hidden lg:inline">
                {a.requested_by} · {a.created_at?.slice(0, 16).replace('T', ' ')}
              </span>
              <Can resource="agent" action="approve"
                fallback={<span className="text-[10px] text-slate-600 shrink-0">需 agent:approve 权限</span>}>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => decide(a, true)} disabled={busy === a.thread_id}
                    className="text-[11px] px-2 py-1 rounded-lg border border-status-running/40 text-status-running hover:bg-status-running/10 flex items-center gap-1">
                    <Check className="w-3 h-3" /> 批准
                  </button>
                  <button onClick={() => decide(a, false)} disabled={busy === a.thread_id}
                    className="text-[11px] px-2 py-1 rounded-lg border border-slate-600 text-slate-400 hover:text-white flex items-center gap-1">
                    <X className="w-3 h-3" /> 驳回
                  </button>
                </div>
              </Can>
            </div>
          ))}
        </div>
      )}

      {section === 'tools' && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-accent" />
            工具治理
            <span className="text-xs font-normal text-slate-500">
              收紧即时生效 · 放宽需审批（红线：治理审计不可关闭）
            </span>
          </h3>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {tools.map((tool) => {
              const key = `tool:${tool.name}`
              const ov = overrides[key]
              const enabled = ov ? ov.enabled : true
              const risk: string = (ov?.overrides?.risk_override as string) || tool.risk_level
              const overridden = Boolean(ov)
              return (
                <div key={tool.name}
                  className="rounded-xl border border-[#2a3142] bg-bg-card/80 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 min-w-0">
                      <Bot className="w-3.5 h-3.5 text-primary shrink-0" />
                      <code className="text-xs text-white truncate">{tool.name}</code>
                      {overridden && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-400 border border-yellow-500/30">
                          已定制
                        </span>
                      )}
                    </div>
                    <span className={cn('text-[10px] px-1.5 py-0.5 rounded border', riskBadgeClass(risk))}>
                      {risk}
                    </span>
                  </div>

                  <p className="text-[11px] text-slate-500 leading-relaxed line-clamp-2">
                    {tool.description}
                  </p>

                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] text-slate-600">
                      {tool.permission} · {tool.domain}
                      {tool.has_compensation ? ' · 可回滚' : ''}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <Can resource="customization" action="update">
                        <select
                          value={risk}
                          disabled={acting === tool.name}
                          onChange={(e) => save(tool, { risk_override: e.target.value })}
                          className="text-[11px] px-2 py-1 rounded-lg bg-bg-input border border-[#2a3142] text-slate-300"
                        >
                          {RISK_OPTIONS.map((r) => (
                            <option key={r} value={r}>{r}</option>
                          ))}
                        </select>
                      </Can>
                      <Can resource="customization" action="update">
                        <button
                          onClick={() => save(tool, { enabled: !enabled })}
                          disabled={acting === tool.name}
                          className={cn(
                            'text-[11px] px-2.5 py-1 rounded-lg border',
                            enabled
                              ? 'border-status-running/40 text-status-running hover:bg-status-running/10'
                              : 'border-slate-600 text-slate-400 hover:bg-slate-600/10',
                          )}
                        >
                          {enabled ? '停用' : '启用'}
                        </button>
                      </Can>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {section === 'agents' && <AgentProfileCards />}
      {section === 'thresholds' && (
        <ThresholdOverrides pendingCount={thresholdPending} onChanged={load} />
      )}
      {section === 'matrix' && (
        <ApprovalRouteMatrix pendingCount={matrixPending} onChanged={load} />
      )}

      {/* 变更历史：三种定制类型共用一份版本流，因此放在分区之外 */}
      <div>
        <button onClick={() => setShowHistory((v) => !v)}
          className="text-xs text-slate-400 hover:text-white flex items-center gap-1.5">
          <History className="w-3.5 h-3.5" />
          变更历史（{versions.length}）
        </button>
        {showHistory && (
          <div className="mt-2 space-y-1.5">
            {versions.slice(0, 20).map((v) => (
              <div key={v.id}
                className="text-[11px] text-slate-500 flex items-center gap-2 px-2 py-1 rounded bg-bg-input/40">
                <span className="text-slate-300">{v.type}:{v.target}</span>
                <span className={cn(
                  v.status === 'active' ? 'text-primary'
                    : v.status === 'pending_approval' ? 'text-yellow-400'
                    : v.status === 'rolled_back' ? 'text-blue-400' : 'text-slate-500')}>
                  {v.status}
                </span>
                {ROLLBACKABLE.has(v.status) && (
                  <Can resource="customization" action="update"
                    fallback={<span className="text-[10px] text-slate-600">需 customization:update</span>}>
                    <button onClick={() => rollback(v)} disabled={busy === `rollback:${v.id}`}
                      title="撤销这一条记录，恢复它变更前的配置"
                      className="text-blue-400 hover:text-blue-300 flex items-center gap-1">
                      <RotateCcw className="w-2.5 h-2.5" /> 撤销此次变更
                    </button>
                  </Can>
                )}
                <span className="ml-auto shrink-0">{v.changed_by} · {v.created_at?.slice(0, 16).replace('T', ' ')}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
