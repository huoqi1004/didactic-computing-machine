/**
 * Agent 卡片式定制（Phase F2.7 管理端二期）
 * ==========================================
 * 数据源：
 *   GET  /agent-ops/customizations/meta        域清单 + 该域工具子集 + 声明式参数清单
 *   GET  /agent-ops/customizations             当前生效覆盖（type=agent）
 *   PUT  /agent-ops/customizations/agent/{域}  应用覆盖（即时生效）
 * 语义（后端 _classify_change 已锁）：agent 级定制一律即时生效——工具白名单只收窄
 * LLM 可见集，治理面仍逐调用校验权限，因此不构成放宽；红线 2 不受影响。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bot, Eraser, RefreshCw, Save, UserCog } from 'lucide-react'
import { apiService } from '../services/api'
import { cn } from '../lib/utils'

export interface ParamSpec {
  key: string
  label: string
  /** tri=三态（跟随默认/开/关）；bool/number/string 直接映射控件 */
  type: string
  description?: string
}

export interface AgentMeta {
  domain: string
  label: string
  tools: string[]
  params: ParamSpec[]
}

export interface AgentDraft {
  prompt_override: string
  temperature: string
  daily_token_budget: string
  tool_whitelist: string[]
  /** 声明式参数：统一以字符串承载（tri → 'default' | 'true' | 'false'） */
  params: Record<string, string>
}

const EMPTY_DRAFT: AgentDraft = {
  prompt_override: '',
  temperature: '',
  daily_token_budget: '',
  tool_whitelist: [],
  params: {},
}

/** tri/bool 值 → 表单字符串 */
function flagToText(v: unknown): string {
  if (v === true) return 'true'
  if (v === false) return 'false'
  return 'default'
}

function paramToText(spec: ParamSpec, v: unknown): string {
  if (spec.type === 'tri' || spec.type === 'bool') return flagToText(v)
  return v === undefined || v === null ? '' : String(v)
}

function textToParam(spec: ParamSpec, raw: string): unknown {
  if (spec.type === 'tri') {
    if (raw === 'true') return true
    if (raw === 'false') return false
    return null // 跟随内置默认：写 null，运行期 resolve() 视同未覆盖
  }
  if (spec.type === 'bool') return raw === 'true'
  if (spec.type === 'number') return raw.trim() === '' ? null : Number(raw)
  return raw.trim() === '' ? null : raw
}

export function draftFromOverrides(meta: AgentMeta, ov: Record<string, unknown>): AgentDraft {
  const params: Record<string, string> = {}
  const declared = (ov?.params || {}) as Record<string, unknown>
  for (const spec of meta.params || []) {
    params[spec.key] = paramToText(spec, declared[spec.key])
  }
  const whitelist = Array.isArray(ov?.tool_whitelist) ? (ov.tool_whitelist as string[]) : []
  return {
    prompt_override: String(ov?.prompt_override || ''),
    temperature: ov?.temperature === undefined || ov?.temperature === null
      ? '' : String(ov.temperature),
    daily_token_budget: ov?.daily_token_budget === undefined || ov?.daily_token_budget === null
      ? '' : String(ov.daily_token_budget),
    tool_whitelist: whitelist,
    params,
  }
}

/**
 * 表单 → overrides。留空字段一律"不下发"（回到出厂默认），
 * 白名单为全选/全不选时同样不下发——空数组在运行期等于"不限制"，
 * 因此不能把"一个都没勾"当成"禁用全部工具"来表达（会造成意外的全开）。
 */
export function buildAgentOverrides(meta: AgentMeta, d: AgentDraft): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const persona = d.prompt_override.trim()
  if (persona) out.prompt_override = persona
  if (d.temperature.trim() !== '') out.temperature = Number(d.temperature)
  if (d.daily_token_budget.trim() !== '') out.daily_token_budget = Number(d.daily_token_budget)

  const all = meta.tools || []
  if (d.tool_whitelist.length > 0 && d.tool_whitelist.length < all.length) {
    out.tool_whitelist = d.tool_whitelist
  }

  const params: Record<string, unknown> = {}
  for (const spec of meta.params || []) {
    params[spec.key] = textToParam(spec, d.params[spec.key] ?? 'default')
  }
  if (Object.keys(params).length) out.params = params
  return out
}

export function isDraftDirty(base: AgentDraft, d: AgentDraft): boolean {
  return JSON.stringify(base) !== JSON.stringify(d)
}

const inputCls =
  'w-full text-[12px] px-2 py-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-200 ' +
  'focus:outline-none focus:border-primary/60'

export function AgentProfileCards() {
  const [agents, setAgents] = useState<AgentMeta[]>([])
  const [overrides, setOverrides] = useState<Record<string, Record<string, unknown>>>({})
  const [drafts, setDrafts] = useState<Record<string, AgentDraft>>({})
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
      const list: AgentMeta[] = Array.isArray(metaRes?.agents) ? metaRes.agents : []
      setAgents(list)
      const map: Record<string, Record<string, unknown>> = {}
      for (const c of (Array.isArray(custRes?.customizations) ? custRes.customizations : [])) {
        if (c.type === 'agent') map[c.target] = c.overrides || {}
      }
      setOverrides(map)
      setDrafts(Object.fromEntries(list.map((a) => [
        a.domain, draftFromOverrides(a, map[a.domain] || {}),
      ])))
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Agent 定制数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const patchDraft = (domain: string, patch: Partial<AgentDraft>) => {
    setDrafts((prev) => ({ ...prev, [domain]: { ...prev[domain], ...patch } }))
  }

  const patchParam = (domain: string, key: string, raw: string) => {
    setDrafts((prev) => {
      const cur = prev[domain]
      return {
        ...prev,
        [domain]: { ...cur, params: { ...cur.params, [key]: raw } },
      }
    })
  }

  const toggleTool = (domain: string, tool: string) => {
    setDrafts((prev) => {
      const cur = prev[domain]
      const has = cur.tool_whitelist.includes(tool)
      return {
        ...prev,
        [domain]: {
          ...cur,
          tool_whitelist: has
            ? cur.tool_whitelist.filter((t) => t !== tool)
            : [...cur.tool_whitelist, tool],
        },
      }
    })
  }

  const save = async (agent: AgentMeta, clear = false) => {
    setBusy(agent.domain)
    setError('')
    setMessage('')
    try {
      const overrides_ = clear ? {} : buildAgentOverrides(agent, drafts[agent.domain] || EMPTY_DRAFT)
      const res = await apiService.putAgentCustomization('agent', agent.domain, {
        overrides: overrides_,
        enabled: true,
        comment: clear ? '清除 Agent 定制' : '管理端 Agent 卡片编辑',
      })
      if (res?.status === 'pending_approval') {
        setMessage(`${agent.label}：变更已转人工审批（不应发生——agent 级定制应即时生效）`)
      } else {
        setMessage(`${agent.label}：已生效（${clear ? '已清除定制' : `${Object.keys(overrides_).length} 项覆盖`}）`)
      }
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || `${agent.label} 保存失败`)
    } finally {
      setBusy(null)
    }
  }

  const baseDrafts = useMemo(() => Object.fromEntries(
    agents.map((a) => [a.domain, draftFromOverrides(a, overrides[a.domain] || {})]),
  ), [agents, overrides])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <UserCog className="w-4 h-4 text-accent" />
          <h3 className="text-sm font-semibold text-white">
            Agent 人设与链路参数
            <span className="ml-2 text-xs text-slate-500">
              人设/温度/预算/工具子集即时生效 · 参数清单由后端声明表生成
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

      {agents.length === 0 && !loading && (
        <p className="text-xs text-slate-500">无可定制的 Agent 域（工具注册表为空或元数据加载失败）。</p>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {agents.map((agent) => {
          const draft = drafts[agent.domain] || EMPTY_DRAFT
          const dirty = isDraftDirty(baseDrafts[agent.domain] || EMPTY_DRAFT, draft)
          const customized = Object.keys(overrides[agent.domain] || {}).length > 0
          return (
            <div key={agent.domain}
              className="rounded-xl border border-[#2a3142] bg-bg-card/80 p-3 space-y-3">
              <div className="flex items-center gap-2">
                <Bot className="w-3.5 h-3.5 text-primary shrink-0" />
                <span className="text-xs font-semibold text-white">{agent.label}</span>
                <code className="text-[10px] text-slate-500">{agent.domain}</code>
                {customized && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-400 border border-yellow-500/30">
                    已定制
                  </span>
                )}
                <div className="ml-auto flex items-center gap-1.5">
                  {customized && (
                    <button onClick={() => save(agent, true)} disabled={busy === agent.domain}
                      className="text-[11px] px-2 py-1 rounded-lg border border-slate-600 text-slate-400 hover:text-white flex items-center gap-1">
                      <Eraser className="w-3 h-3" /> 清除定制
                    </button>
                  )}
                  <button onClick={() => save(agent)} disabled={!dirty || busy === agent.domain}
                    className="text-[11px] px-2.5 py-1 rounded-lg bg-primary/20 text-primary border border-primary/30 hover:bg-primary/30 disabled:opacity-40 flex items-center gap-1">
                    <Save className="w-3 h-3" /> 保存
                  </button>
                </div>
              </div>

              <label className="block space-y-1">
                <span className="text-[10px] uppercase tracking-wide text-slate-500">
                  人设模板 prompt_override（留空=用内置场景提示词）
                </span>
                <textarea
                  rows={2}
                  value={draft.prompt_override}
                  onChange={(e) => patchDraft(agent.domain, { prompt_override: e.target.value })}
                  className={cn(inputCls, 'leading-relaxed')}
                  placeholder="例：你是报销领域的严谨审核助手，只依据单据事实作答，金额存疑必须转人审。"
                />
              </label>

              <div className="grid grid-cols-2 gap-2">
                <label className="block space-y-1">
                  <span className="text-[10px] uppercase tracking-wide text-slate-500">
                    温度（留空=全局默认）
                  </span>
                  <input type="number" step="0.1" min="0" max="2"
                    value={draft.temperature}
                    onChange={(e) => patchDraft(agent.domain, { temperature: e.target.value })}
                    className={inputCls} />
                </label>
                <label className="block space-y-1">
                  <span className="text-[10px] uppercase tracking-wide text-slate-500">
                    日 token 预算（留空=全局默认）
                  </span>
                  <input type="number" min="0" step="1000"
                    value={draft.daily_token_budget}
                    onChange={(e) => patchDraft(agent.domain, { daily_token_budget: e.target.value })}
                    className={inputCls} />
                </label>
              </div>

              {agent.params.length > 0 && (
                <div className="space-y-1.5">
                  <span className="text-[10px] uppercase tracking-wide text-slate-500">业务链路参数</span>
                  {agent.params.map((spec) => (
                    <div key={spec.key} className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-slate-300">{spec.label}</span>
                        <code className="text-[10px] text-slate-600">{spec.key}</code>
                        {spec.type === 'tri' || spec.type === 'bool' ? (
                          <select
                            value={draft.params[spec.key] || 'default'}
                            onChange={(e) => patchParam(agent.domain, spec.key, e.target.value)}
                            className={cn(inputCls, 'w-auto ml-auto')}
                          >
                            <option value="default">跟随默认</option>
                            <option value="true">开</option>
                            <option value="false">关</option>
                          </select>
                        ) : (
                          <input
                            type={spec.type === 'number' ? 'number' : 'text'}
                            value={draft.params[spec.key] || ''}
                            onChange={(e) => patchParam(agent.domain, spec.key, e.target.value)}
                            className={cn(inputCls, 'w-32 ml-auto')}
                          />
                        )}
                      </div>
                      {spec.description && (
                        <p className="text-[10px] text-slate-600">{spec.description}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] uppercase tracking-wide text-slate-500">
                    工具子集（{draft.tool_whitelist.length}/{agent.tools.length}）
                  </span>
                  <span className="text-[10px] text-slate-600">
                    全选或全不选=不限制；治理面仍逐调用校验权限
                  </span>
                </div>
                <div className="max-h-32 overflow-y-auto space-y-1 rounded-lg bg-bg-input/40 border border-[#2a3142] p-2">
                  {agent.tools.map((tool) => (
                    <label key={tool} className="flex items-center gap-2 text-[11px] text-slate-300">
                      <input
                        type="checkbox"
                        checked={draft.tool_whitelist.includes(tool)}
                        onChange={() => toggleTool(agent.domain, tool)}
                      />
                      <code className="truncate">{tool}</code>
                    </label>
                  ))}
                  {agent.tools.length === 0 && (
                    <p className="text-[11px] text-slate-600">该域无在册工具</p>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
