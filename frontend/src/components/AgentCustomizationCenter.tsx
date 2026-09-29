/**
 * Agent 定制中心（Phase F1 / F1.7 管理端一期）
 * =============================================
 * 数据源：
 *   GET  /agent-ops/tools                       治理面工具目录（含风险级/权限/调用统计）
 *   GET  /agent-ops/customizations              当前生效覆盖（启停/风险分级）
 *   PUT  /agent-ops/customizations/{type}/{target}   应用定制（收紧即时/放宽转人审）
 *   GET  /agent-ops/customizations/versions     变更历史
 * 操作语义：收紧（write_low→write_high、停用）即时生效；放宽自动转人审（后端判定）。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Bot,
  RefreshCw,
  ShieldAlert,
  SlidersHorizontal,
  History,
} from 'lucide-react'
import { apiService } from '../services/api'
import { cn } from '../lib/utils'

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
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [toolsRes, custRes, verRes] = await Promise.all([
        apiService.getAgentTools(),
        apiService.getAgentCustomizations(),
        apiService.getCustomizationVersions(),
      ])
      setTools(Array.isArray(toolsRes?.tools) ? toolsRes.tools : [])
      const list = Array.isArray(custRes?.customizations) ? custRes.customizations : []
      const map: Record<string, CustomizationEntry> = {}
      for (const c of list) map[`${c.type}:${c.target}`] = c
      setOverrides(map)
      setVersions(Array.isArray(verRes?.versions) ? verRes.versions : [])
    } catch {
      setError('定制数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

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
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="w-4 h-4 text-accent" />
          <h3 className="text-sm font-semibold text-white">
            工具治理
            <span className="ml-2 text-xs text-slate-500">
              收紧即时生效 · 放宽需审批（红线：治理审计不可关闭）
            </span>
          </h3>
        </div>
        <button onClick={load} disabled={loading}
          className="text-xs text-slate-400 hover:text-white flex items-center gap-1">
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {tools.map((tool) => {
          const key = `tool:${tool.name}`
          const ov = overrides[key]
          const enabled = ov ? ov.enabled : true
          const risk: string = (ov?.overrides?.risk_override as string) || tool.risk_level
          const overridden = Boolean(ov)
          const isWrite = risk !== 'readonly'
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
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* 变更历史 */}
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
                <span className="ml-auto">{v.changed_by} · {v.created_at?.slice(0, 16).replace('T', ' ')}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
