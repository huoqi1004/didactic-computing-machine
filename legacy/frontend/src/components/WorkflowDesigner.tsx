/**
 * 工作流设计器（路线图 Phase F / F3.6 管理端二期）
 * ==================================================
 * 数据源：
 *   GET  /agent-ops/workflow-definitions/meta                 设计器元数据（白名单）
 *   GET  /agent-ops/workflow-definitions/{name}               版本历史 + 生效版本 + 评测门禁
 *   GET  /agent-ops/workflow-definitions/{name}/{version}     完整 DSL
 *   POST /agent-ops/workflow-definitions/validate             校验（后端为准）
 *   PUT  /agent-ops/workflow-definitions/{name}/{version}/draft   存草稿
 *   POST /agent-ops/workflow-definitions/{name}/{version}/publish 申请发布（转人审）
 *   POST /agent-ops/workflow-definitions/{name}/rollback      回滚
 *
 * 治理口径（红线）：
 * - 发布只能"申请"：后端判为放宽类 → 转人工审批，批准后才是 stable；
 * - 已发布版本不可变：编辑历史版本前先"另存为新版本"；
 * - 灰度（canary）按单据哈希粘性命中，上一 stable 保留为主版本；
 * - 校验/发布失败必须显式报错（不走 withFallback）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  GitBranch,
  History,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  Trash2,
  Upload,
  Workflow as WorkflowIcon,
} from 'lucide-react'
import { apiService } from '../services/api'
import Can from './Can'
import { cn } from '../lib/utils'

/* ================================================================
 *  DSL 类型（与后端 app/core/agent/workflow_dsl.py 同构）
 * ================================================================ */

export type WfNodeType = 'task' | 'condition' | 'gate'

export interface WfNode {
  id: string
  type: WfNodeType
  [key: string]: unknown
}

export interface WfDefinition {
  name: string
  entry?: string
  nodes: WfNode[]
}

interface DesignerMeta {
  node_types: WfNodeType[]
  condition_ops: string[]
  handlers: string[]
  threshold_keys: string[]
  workflows: { name: string; description: string; input_hint: string }[]
  builtin_dsl: Record<string, WfDefinition>
}

interface VersionRow {
  id: number
  name: string
  version: string
  status: string
  traffic: number
  comment: string | null
  created_by: string | null
  created_at: string | null
  published_at: string | null
  node_count: number
}

const NUMERIC_OPS = ['gt', 'gte', 'lt', 'lte']
const END_TARGET = 'END'

/* ================================================================
 *  纯函数（供单测）
 * ================================================================ */

export function branchOpOf(branch: Record<string, unknown>, ops: string[]): string {
  return ops.find((op) => branch[op] !== undefined) || 'equals'
}

export function parseRoles(text: string): string[] {
  return text.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean)
}

export function serializeBranchValue(op: string, raw: string): unknown {
  if (op === 'exists') return raw !== 'false'
  if (op === 'in') return raw.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  if (NUMERIC_OPS.includes(op)) {
    const n = Number(raw)
    return raw.trim() === '' || Number.isNaN(n) ? raw : n
  }
  return raw
}

export function parseBranchValue(op: string, value: unknown): string {
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'boolean') return String(value)
  return value === undefined || value === null ? '' : String(value)
}

export interface WfRoute {
  label: string
  target: string
}

export function nodeRoutes(node: WfNode): WfRoute[] {
  if (node.type === 'condition') {
    const rows = ((node.branches as Record<string, unknown>[]) || []).map((b, i) => ({
      label: `分支${i + 1}`,
      target: typeof b?.goto === 'string' && b.goto ? b.goto : END_TARGET,
    }))
    rows.push({ label: 'default', target: (node.default as string) || END_TARGET })
    return rows
  }
  if (node.type === 'gate') {
    return [
      { label: '批准', target: (node.next_approved as string) || '—' },
      { label: '驳回', target: (node.next_rejected as string) || '—' },
    ]
  }
  return [{ label: 'next', target: (node.next as string) || '—' }]
}

export interface WfLayer {
  depth: number
  nodes: WfNode[]
  unreachable: boolean
}

/** 按最长路径分层（预览用）；未连通节点单独一层，与校验器的孤立节点判定同口径。 */
export function layoutLayers(defn: WfDefinition): WfLayer[] {
  const nodes = defn?.nodes || []
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const depth = new Map<string, number>()
  const startId = defn?.entry || nodes[0]?.id
  if (startId && byId.has(startId)) {
    depth.set(startId, 0)
    let frontier = [startId]
    let guard = 0
    // 草稿可能有环：松弛次数设上限，避免预览渲染卡死
    while (frontier.length && guard++ < nodes.length * nodes.length + 8) {
      const next: string[] = []
      for (const id of frontier) {
        const d = depth.get(id) ?? 0
        for (const route of nodeRoutes(byId.get(id) as WfNode)) {
          const t = route.target
          if (!byId.has(t)) continue
          if ((depth.get(t) ?? -1) < d + 1) {
            depth.set(t, d + 1)
            next.push(t)
          }
        }
      }
      frontier = next
    }
  }

  const maxDepth = Math.max(-1, ...Array.from(depth.values()))
  const layers: WfLayer[] = []
  for (let d = 0; d <= maxDepth; d++) {
    const group = nodes.filter((n) => depth.get(n.id) === d)
    if (group.length) layers.push({ depth: d, nodes: group, unreachable: false })
  }
  const orphans = nodes.filter((n) => !depth.has(n.id))
  if (orphans.length) layers.push({ depth: maxDepth + 1, nodes: orphans, unreachable: true })
  return layers
}

export function newNode(type: WfNodeType, index: number): WfNode {
  const id = `node_${index}`
  if (type === 'gate') {
    return {
      id,
      type,
      amount_from: 'result.amount',
      next_approved: END_TARGET,
      next_rejected: END_TARGET,
    }
  }
  if (type === 'condition') {
    return {
      id,
      type,
      field: 'input.category',
      branches: [{ equals: '', goto: END_TARGET }],
      default: END_TARGET,
    }
  }
  return { id, type, handler: '', next: END_TARGET }
}

export function withNode(defn: WfDefinition, index: number, patch: Partial<WfNode>): WfDefinition {
  const nodes = defn.nodes.map((n, i) => (i === index ? { ...n, ...patch } : n))
  const renamed = patch.id !== undefined && patch.id !== defn.nodes[index].id
  return {
    ...defn,
    entry: renamed || defn.entry === undefined ? (patch.id as string) ?? defn.entry : defn.entry,
    nodes,
  }
}

export function withoutNode(defn: WfDefinition, index: number): WfDefinition {
  return { ...defn, nodes: defn.nodes.filter((_, i) => i !== index) }
}

export function nextVersion(versions: { version: string }[]): string {
  const nums = versions
    .map((v) => Number(String(v.version).replace(/^v/i, '')))
    .filter((n) => Number.isFinite(n))
  return `v${(nums.length ? Math.max(...nums) : 0) + 1}`
}

export function statusBadgeClass(status: string): string {
  if (status === 'stable') return 'bg-status-running/10 text-status-running border-status-running/30'
  if (status === 'canary') return 'bg-accent/10 text-accent border-accent/30'
  if (status === 'pending_approval') return 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30'
  return 'bg-slate-500/10 text-slate-400 border-slate-500/30'
}

/* ================================================================
 *  小组件
 * ================================================================ */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  )
}

const inputCls =
  'w-full text-[12px] px-2 py-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-200 ' +
  'focus:outline-none focus:border-primary/60 disabled:opacity-50'

/* ================================================================
 *  主组件
 * ================================================================ */

export function WorkflowDesigner() {
  const [meta, setMeta] = useState<DesignerMeta | null>(null)
  const [wfName, setWfName] = useState('')
  const [versions, setVersions] = useState<VersionRow[]>([])
  const [stable, setStable] = useState<VersionRow | null>(null)
  const [canary, setCanary] = useState<VersionRow | null>(null)
  const [evalGate, setEvalGate] = useState<Record<string, unknown>>({ status: 'none' })

  const [version, setVersion] = useState('')
  const [defn, setDefn] = useState<WfDefinition | null>(null)
  const [sel, setSel] = useState(0)
  const [errors, setErrors] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const [traffic, setTraffic] = useState(0)
  const [showRaw, setShowRaw] = useState(false)
  const [rawText, setRawText] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const targetIds = useMemo(() => {
    const ids = (defn?.nodes || []).map((n) => n.id).filter(Boolean)
    return [END_TARGET, ...ids]
  }, [defn])

  const loadStatus = useCallback(async (name: string) => {
    if (!name) return
    const res = await apiService.getWorkflowStatus(name)
    setVersions(Array.isArray(res?.versions) ? res.versions : [])
    setStable(res?.stable || null)
    setCanary(res?.canary || null)
    setEvalGate(res?.publish_eval || { status: 'none' })
  }, [])

  useEffect(() => {
    let cancelled = false
    apiService
      .getWorkflowDesignerMeta()
      .then((res: DesignerMeta) => {
        if (cancelled) return
        setMeta(res)
        const first = res?.workflows?.[0]?.name || ''
        if (first) setWfName(first)
      })
      .catch((e: any) => setError(e?.message || '设计器元数据加载失败'))
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!wfName || !meta) return
    loadStatus(wfName).catch((e: any) => setError(e?.response?.data?.detail || '版本历史加载失败'))
    const fallback = meta.builtin_dsl?.[wfName]
    if (!defn && fallback) {
      setDefn(JSON.parse(JSON.stringify(fallback)))
      setVersion(nextVersion([]))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wfName, meta])

  const refresh = async () => {
    setBusy('refresh')
    setError('')
    try {
      await loadStatus(wfName)
      setMessage('已刷新')
    } catch (e: any) {
      setError(e?.response?.data?.detail || '刷新失败')
    } finally {
      setBusy(null)
    }
  }

  const loadVersion = async (v: string) => {
    setBusy(`load:${v}`)
    setError('')
    try {
      const res = await apiService.getWorkflowDefinition(wfName, v)
      setDefn(res?.definition as WfDefinition)
      setVersion(v)
      setSel(0)
      setErrors([])
      setMessage(`已载入 ${wfName}@${v}（${res?.status}）——修改后请另存为新版本`)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '版本载入失败')
    } finally {
      setBusy(null)
    }
  }

  const loadBuiltin = () => {
    const tpl = meta?.builtin_dsl?.[wfName]
    if (!tpl) return
    setDefn(JSON.parse(JSON.stringify(tpl)))
    setVersion(nextVersion(versions))
    setSel(0)
    setErrors([])
    setMessage(`已载入出厂模板，建议版本 ${version}`)
  }

  const validate = async () => {
    if (!defn) return
    setBusy('validate')
    setError('')
    try {
      const res = await apiService.validateWorkflowDefinition(defn)
      setErrors(res?.errors || [])
      setMessage(res?.valid ? '校验通过' : `校验未通过（${res?.errors?.length || 0} 项）`)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '校验请求失败')
    } finally {
      setBusy(null)
    }
  }

  const saveDraft = async () => {
    if (!defn || !version) {
      setError('请先填写版本号')
      return
    }
    setBusy('draft')
    setError('')
    try {
      const res = await apiService.saveWorkflowDraft(wfName, version, defn, comment)
      setMessage(`草稿已保存：${wfName}@${res?.version}（${res?.nodes} 个节点）`)
      setComment('')
      await loadStatus(wfName)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '草稿保存失败')
    } finally {
      setBusy(null)
    }
  }

  const requestPublish = async () => {
    if (!version) {
      setError('请先填写版本号')
      return
    }
    setBusy('publish')
    setError('')
    try {
      const res = await apiService.requestWorkflowPublish(wfName, version, traffic, comment)
      if (res?.status === 'pending_approval') {
        setMessage(`发布申请已提交人工审批（${wfName}@${version}${traffic > 0 && traffic < 100 ? ` 灰度 ${traffic}%` : ''}）`)
      } else {
        setMessage(`发布返回状态：${res?.status || '未知'}`)
      }
      await loadStatus(wfName)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '发布申请失败')
    } finally {
      setBusy(null)
    }
  }

  const rollback = async (v: string) => {
    setBusy(`rollback:${v}`)
    setError('')
    try {
      await apiService.rollbackWorkflow(wfName, v)
      setMessage(`已回滚到 ${wfName}@${v}（即时生效，属收紧方向）`)
      await loadStatus(wfName)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '回滚失败')
    } finally {
      setBusy(null)
    }
  }

  const applyRaw = () => {
    setError('')
    try {
      const parsed = JSON.parse(rawText) as WfDefinition
      if (!parsed || !Array.isArray(parsed.nodes)) throw new Error('nodes 必须是数组')
      setDefn(parsed)
      setErrors([])
      setShowRaw(false)
      setMessage('已应用 JSON 编辑')
    } catch (e: any) {
      setError(`JSON 解析失败：${e?.message || e}`)
    }
  }

  const patchNode = (patch: Partial<WfNode>) => {
    if (!defn) return
    setDefn(withNode(defn, sel, patch))
  }

  const addNode = (type: WfNodeType) => {
    if (!defn) return
    const node = newNode(type, defn.nodes.length + 1)
    setDefn({ ...defn, nodes: [...defn.nodes, node] })
    setSel(defn.nodes.length)
  }

  const removeNodeAt = (index: number) => {
    if (!defn) return
    setDefn(withoutNode(defn, index))
    setSel(Math.max(0, Math.min(index, defn.nodes.length - 2)))
  }

  const changeNodeType = (type: WfNodeType) => {
    if (!defn) return
    const current = defn.nodes[sel]
    if (!current) return
    const fresh = newNode(type, sel + 1)
    setDefn(withNode(defn, sel, { ...fresh, id: current.id }))
  }

  const node = defn?.nodes?.[sel]

  return (
    <div className="space-y-4">
      {/* 头部：流程选择 + 版本 + 动作 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <WorkflowIcon className="w-4 h-4 text-accent" />
          <h3 className="text-sm font-semibold text-white">
            工作流设计器
            <span className="ml-2 text-xs text-slate-500">
              DSL 声明式链路 · 发布必经人审 · 灰度按单据粘性绑版本
            </span>
          </h3>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={wfName}
            onChange={(e) => setWfName(e.target.value)}
            className={cn(inputCls, 'w-auto')}
          >
            {(meta?.workflows || []).map((w) => (
              <option key={w.name} value={w.name}>{w.name}</option>
            ))}
          </select>
          <input
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            placeholder="版本号"
            className={cn(inputCls, 'w-24')}
          />
          <button onClick={loadBuiltin}
            className="text-[11px] px-2.5 py-1.5 rounded-lg border border-slate-600 text-slate-300 hover:bg-slate-600/10">
            载入出厂模板
          </button>
          <button onClick={refresh} disabled={busy === 'refresh'}
            className="text-[11px] px-2.5 py-1.5 rounded-lg border border-slate-600 text-slate-300 hover:bg-slate-600/10 flex items-center gap-1">
            <RefreshCw className={cn('w-3 h-3', busy === 'refresh' && 'animate-spin')} />
            刷新
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="text-slate-500">当前生效：</span>
        <span className={cn('px-2 py-0.5 rounded border', statusBadgeClass('stable'))}>
          {stable ? `stable ${stable.version}` : 'stable 出厂模板'}
        </span>
        {canary && (
          <span className={cn('px-2 py-0.5 rounded border', statusBadgeClass('canary'))}>
            canary {canary.version} · {canary.traffic}%
          </span>
        )}
        {String((evalGate as any)?.summary || '') === 'FAIL' && (
          <span className="px-2 py-0.5 rounded border bg-status-circuit/10 text-status-circuit border-status-circuit/30">
            发布评测 FAIL{(evalGate as any)?.blocking ? '（已强制回滚）' : '（仅告警）'}
          </span>
        )}
        <button
          onClick={() => setShowRaw((v) => !v)}
          className="ml-auto text-[11px] text-slate-400 hover:text-white"
        >
          {showRaw ? '收起 JSON' : '高级 JSON 编辑'}
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
      {errors.length > 0 && (
        <div className="p-2.5 rounded-lg bg-yellow-500/10 border border-yellow-500/30 text-xs space-y-1">
          {errors.map((e, i) => (
            <div key={i} className="flex items-start gap-1.5 text-yellow-400">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
              <span>{e}</span>
            </div>
          ))}
        </div>
      )}

      {showRaw && (
        <div className="space-y-2">
          <textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            onFocus={() => { if (!rawText && defn) setRawText(JSON.stringify(defn, null, 2)) }}
            rows={12}
            className={cn(inputCls, 'font-mono text-[11px]')}
            placeholder="聚焦后自动填入当前定义 JSON"
          />
          <div className="flex gap-2">
            <button onClick={applyRaw}
              className="text-[11px] px-3 py-1.5 rounded-lg bg-primary/20 text-primary border border-primary/30">
              应用 JSON
            </button>
            <button onClick={() => setRawText(defn ? JSON.stringify(defn, null, 2) : '')}
              className="text-[11px] px-3 py-1.5 rounded-lg border border-slate-600 text-slate-300">
              重新填入
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-3">
        {/* 节点列表 */}
        <div className="xl:col-span-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">节点（{defn?.nodes.length || 0}）</span>
            <div className="flex gap-1">
              {(['task', 'condition', 'gate'] as WfNodeType[]).map((t) => (
                <button key={t} onClick={() => addNode(t)}
                  className="text-[10px] px-1.5 py-1 rounded border border-slate-600 text-slate-400 hover:text-white hover:bg-slate-600/10 flex items-center gap-0.5">
                  <Plus className="w-2.5 h-2.5" />
                  {t}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            {(defn?.nodes || []).map((n, i) => (
              <div key={`${n.id}-${i}`}
                className={cn(
                  'flex items-center gap-1.5 px-2 py-1.5 rounded-lg border cursor-pointer',
                  i === sel ? 'border-primary/60 bg-primary/5' : 'border-[#2a3142] bg-bg-input/40',
                )}
                onClick={() => setSel(i)}
              >
                <span className="text-[10px] px-1 py-0.5 rounded bg-slate-700/40 text-slate-300">
                  {n.type}
                </span>
                <code className="text-[11px] text-slate-200 truncate">{n.id || '(未命名)'}</code>
                <button
                  onClick={(e) => { e.stopPropagation(); removeNodeAt(i) }}
                  className="ml-auto text-slate-600 hover:text-status-circuit"
                  title="删除节点"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* 节点表单 */}
        <div className="xl:col-span-5 space-y-3 rounded-xl border border-[#2a3142] bg-bg-card/80 p-3">
          {!node && <p className="text-xs text-slate-500">请选择或添加节点。</p>}
          {node && (
            <>
              <div className="flex items-center gap-2">
                <GitBranch className="w-3.5 h-3.5 text-primary" />
                <span className="text-xs font-semibold text-white">节点编辑</span>
                <select
                  value={node.type}
                  onChange={(e) => changeNodeType(e.target.value as WfNodeType)}
                  className={cn(inputCls, 'w-auto ml-auto')}
                >
                  {(meta?.node_types || ['task', 'condition', 'gate']).map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <Field label="节点 ID">
                  <input value={node.id} onChange={(e) => patchNode({ id: e.target.value })}
                    className={inputCls} />
                </Field>
                {node.type === 'task' && (
                  <Field label="handler（白名单）">
                    <select value={(node.handler as string) || ''}
                      onChange={(e) => patchNode({ handler: e.target.value })}
                      className={inputCls}>
                      <option value="">— 选择 —</option>
                      {(meta?.handlers || []).map((h) => (
                        <option key={h} value={h}>{h}</option>
                      ))}
                    </select>
                  </Field>
                )}
              </div>

              {node.type === 'task' && (
                <Field label="next（下一节点 / END）">
                  <select value={(node.next as string) || ''}
                    onChange={(e) => patchNode({ next: e.target.value })}
                    className={inputCls}>
                    <option value="">— 选择 —</option>
                    {targetIds.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </Field>
              )}

              {node.type === 'condition' && (
                <div className="space-y-2">
                  <Field label="状态路径 field（如 input.category / result.amount）">
                    <input value={(node.field as string) || ''}
                      onChange={(e) => patchNode({ field: e.target.value })}
                      className={inputCls} />
                  </Field>
                  <div className="space-y-1.5">
                    <span className="text-[10px] uppercase tracking-wide text-slate-500">
                      条件分支（首个命中即路由）
                    </span>
                    {((node.branches as Record<string, unknown>[]) || []).map((b, bi) => {
                      const op = branchOpOf(b, meta?.condition_ops || ['equals'])
                      return (
                        <div key={bi} className="flex items-center gap-1.5">
                          <select value={op}
                            onChange={(e) => {
                              const cleaned: Record<string, unknown> = {
                                goto: b.goto,
                                [e.target.value]: serializeBranchValue(e.target.value, ''),
                              }
                              const nextBranches = [...(node.branches as Record<string, unknown>[])]
                              nextBranches[bi] = cleaned
                              patchNode({ branches: nextBranches })
                            }}
                            className={cn(inputCls, 'w-24 shrink-0')}>
                            {(meta?.condition_ops || []).map((o) => (
                              <option key={o} value={o}>{o}</option>
                            ))}
                          </select>
                          <input
                            value={parseBranchValue(op, b[op])}
                            placeholder={op === 'in' ? 'a, b, c' : op === 'exists' ? 'true/false' : '值'}
                            onChange={(e) => {
                              const nextBranches = [...(node.branches as Record<string, unknown>[])]
                              nextBranches[bi] = { ...b, [op]: serializeBranchValue(op, e.target.value) }
                              patchNode({ branches: nextBranches })
                            }}
                            className={inputCls}
                          />
                          <select value={(b.goto as string) || ''}
                            onChange={(e) => {
                              const nextBranches = [...(node.branches as Record<string, unknown>[])]
                              nextBranches[bi] = { ...b, goto: e.target.value }
                              patchNode({ branches: nextBranches })
                            }}
                            className={cn(inputCls, 'w-28 shrink-0')}>
                            {targetIds.map((t) => <option key={t} value={t}>{t}</option>)}
                          </select>
                          <button
                            onClick={() => patchNode({
                              branches: ((node.branches as Record<string, unknown>[]) || [])
                                .filter((_, i) => i !== bi),
                            })}
                            className="text-slate-600 hover:text-status-circuit shrink-0">
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      )
                    })}
                    <button
                      onClick={() => patchNode({
                        branches: [
                          ...((node.branches as Record<string, unknown>[]) || []),
                          { equals: '', goto: END_TARGET },
                        ],
                      })}
                      className="text-[11px] px-2 py-1 rounded-lg border border-slate-600 text-slate-300 flex items-center gap-1">
                      <Plus className="w-3 h-3" /> 添加分支
                    </button>
                  </div>
                  <Field label="default（必须声明——失败安全）">
                    <select value={(node.default as string) || ''}
                      onChange={(e) => patchNode({ default: e.target.value })}
                      className={inputCls}>
                      <option value="">— 选择 —</option>
                      {targetIds.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </Field>
                </div>
              )}

              {node.type === 'gate' && (
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="阈值键（白名单，可留空）">
                      <select value={((node.threshold as any)?.key as string) || ''}
                        onChange={(e) => patchNode({
                          threshold: { ...(node.threshold as object), key: e.target.value || undefined },
                        })}
                        className={inputCls}>
                        <option value="">— 无 —</option>
                        {(meta?.threshold_keys || []).map((k) => (
                          <option key={k} value={k}>{k}</option>
                        ))}
                      </select>
                    </Field>
                    <Field label="默认阈值">
                      <input type="number"
                        value={String((node.threshold as any)?.default ?? '')}
                        onChange={(e) => patchNode({
                          threshold: {
                            ...(node.threshold as object),
                            default: e.target.value === '' ? undefined : Number(e.target.value),
                          },
                        })}
                        className={inputCls} />
                    </Field>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="金额来源 amount_from">
                      <input value={(node.amount_from as string) || ''}
                        onChange={(e) => patchNode({ amount_from: e.target.value })}
                        className={inputCls} />
                    </Field>
                    <Field label="单据类型 document_type">
                      <input value={(node.document_type as string) || ''}
                        onChange={(e) => patchNode({ document_type: e.target.value })}
                        className={inputCls} />
                    </Field>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="审批角色（逗号分隔，留空走路由矩阵）">
                      <input
                        value={(node.approve_roles as string[] | undefined)?.join(', ') || ''}
                        onChange={(e) => {
                          const roles = parseRoles(e.target.value)
                          patchNode({ approve_roles: roles.length ? roles : undefined })
                        }}
                        className={inputCls} />
                    </Field>
                    <Field label="超时提醒（小时）">
                      <input type="number"
                        value={String(node.timeout_hours ?? '')}
                        onChange={(e) => patchNode({
                          timeout_hours: e.target.value === '' ? undefined : Number(e.target.value),
                        })}
                        className={inputCls} />
                    </Field>
                  </div>
                  <Field label="审批预览文案（{字段} 占位）">
                    <input value={(node.preview as string) || ''}
                      onChange={(e) => patchNode({ preview: e.target.value })}
                      className={inputCls} />
                  </Field>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="批准 → ">
                      <select value={(node.next_approved as string) || ''}
                        onChange={(e) => patchNode({ next_approved: e.target.value })}
                        className={inputCls}>
                        {targetIds.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </Field>
                    <Field label="驳回 →（红线：必填）">
                      <select value={(node.next_rejected as string) || ''}
                        onChange={(e) => patchNode({ next_rejected: e.target.value })}
                        className={inputCls}>
                        {targetIds.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </Field>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* 版本历史 + 发布 */}
        <div className="xl:col-span-4 space-y-3">
          <div className="rounded-xl border border-[#2a3142] bg-bg-card/80 p-3 space-y-2">
            <div className="flex items-center gap-2">
              <ShieldAlert className="w-3.5 h-3.5 text-yellow-400" />
              <span className="text-xs font-semibold text-white">发布（需人审）</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="灰度比例 %（0/100=全量）">
                <input type="number" min={0} max={100} value={traffic}
                  onChange={(e) => setTraffic(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
                  className={inputCls} />
              </Field>
              <Field label="变更说明">
                <input value={comment} onChange={(e) => setComment(e.target.value)}
                  className={inputCls} />
              </Field>
            </div>
            <div className="flex flex-wrap gap-2">
              <button onClick={validate} disabled={busy === 'validate'}
                className="text-[11px] px-2.5 py-1.5 rounded-lg border border-slate-600 text-slate-300 hover:bg-slate-600/10 flex items-center gap-1">
                <Check className="w-3 h-3" /> 校验
              </button>
              <Can resource="customization" action="update">
                <button onClick={saveDraft} disabled={busy === 'draft'}
                  className="text-[11px] px-2.5 py-1.5 rounded-lg bg-primary/20 text-primary border border-primary/30 hover:bg-primary/30 flex items-center gap-1">
                  <Save className="w-3 h-3" /> 保存草稿
                </button>
              </Can>
              <Can resource="customization" action="publish"
                fallback={<span className="text-[10px] text-slate-600">需 customization:publish 权限</span>}>
                <button onClick={requestPublish} disabled={busy === 'publish'}
                  className="text-[11px] px-2.5 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 flex items-center gap-1">
                  <Upload className="w-3 h-3" /> 申请发布
                </button>
              </Can>
            </div>
            <p className="text-[10px] text-slate-500 leading-relaxed">
              放宽类变更转人工审批，批准后才切换 stable/canary；已发布版本内容不可变。
            </p>
          </div>

          <div className="rounded-xl border border-[#2a3142] bg-bg-card/80 p-3 space-y-2">
            <div className="flex items-center gap-2">
              <History className="w-3.5 h-3.5 text-slate-400" />
              <span className="text-xs font-semibold text-white">版本历史（{versions.length}）</span>
            </div>
            {versions.length === 0 && (
              <p className="text-[11px] text-slate-500">尚无发布版本，当前运行出厂模板。</p>
            )}
            <div className="space-y-1.5 max-h-72 overflow-y-auto">
              {versions.map((v) => (
                <div key={v.id} className="text-[11px] px-2 py-1.5 rounded bg-bg-input/40 space-y-1">
                  <div className="flex items-center gap-1.5">
                    <code className="text-slate-200">{v.version}</code>
                    <span className={cn('px-1.5 py-0.5 rounded border text-[10px]', statusBadgeClass(v.status))}>
                      {v.status}{v.status === 'canary' ? ` ${v.traffic}%` : ''}
                    </span>
                    <span className="ml-auto text-slate-500">
                      {v.created_at?.slice(0, 16).replace('T', ' ')}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-slate-500">
                    <span>{v.node_count} 节点 · {v.created_by || '—'}</span>
                    <button onClick={() => loadVersion(v.version)} disabled={busy === `load:${v.version}`}
                      className="ml-auto text-slate-300 hover:text-white">载入编辑</button>
                    {(v.status === 'superseded' || v.status === 'canary') && (
                      <Can resource="customization" action="publish">
                        <button onClick={() => rollback(v.version)} disabled={busy === `rollback:${v.version}`}
                          className="text-yellow-400 hover:text-yellow-300 flex items-center gap-0.5">
                          <RotateCcw className="w-2.5 h-2.5" /> 回滚
                        </button>
                      </Can>
                    )}
                  </div>
                  {v.comment && <p className="text-slate-500">{v.comment}</p>}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* 流程预览（分层渲染，无第三方图编辑依赖） */}
      <div className="rounded-xl border border-[#2a3142] bg-bg-card/80 p-3 space-y-2">
        <span className="text-xs font-semibold text-white">流程预览</span>
        <div className="flex items-start gap-2 overflow-x-auto pb-1">
          {defn && layoutLayers(defn).map((layer, li) => (
            <div key={layer.depth} className="flex items-start gap-2 shrink-0">
              {li > 0 && <span className="text-slate-600 text-sm self-center">→</span>}
              <div className="space-y-1.5">
                {layer.unreachable && (
                  <span className="text-[10px] text-status-circuit">未连通（校验会拒绝）</span>
                )}
                {layer.nodes.map((n) => (
                  <div key={n.id}
                    className={cn(
                      'rounded-lg border px-2 py-1.5 min-w-[150px] space-y-1',
                      n.type === 'gate'
                        ? 'border-yellow-500/40 bg-yellow-500/5'
                        : n.type === 'condition'
                          ? 'border-accent/40 bg-accent/5'
                          : 'border-[#2a3142] bg-bg-input/40',
                    )}
                  >
                    <div className="flex items-center gap-1.5">
                      <code className="text-[11px] text-slate-200">{n.id}</code>
                      <span className="text-[10px] text-slate-500 ml-auto">{n.type}</span>
                    </div>
                    {n.type === 'task' && (
                      <p className="text-[10px] text-slate-500 truncate">{String(n.handler || '未选 handler')}</p>
                    )}
                    {n.type === 'condition' && (
                      <p className="text-[10px] text-slate-500 truncate">{String(n.field || '未填 field')}</p>
                    )}
                    {n.type === 'gate' && (
                      <p className="text-[10px] text-slate-500 truncate">
                        {((n.threshold as any)?.key as string) || '固定阈值'} · {String((n.threshold as any)?.default ?? '—')}
                      </p>
                    )}
                    <div className="space-y-0.5">
                      {nodeRoutes(n).map((r, ri) => (
                        <div key={ri} className="flex items-center gap-1 text-[10px]">
                          <span className="text-slate-600">{r.label}</span>
                          <span className="text-slate-500">→</span>
                          <span className={cn(
                            'px-1 rounded',
                            r.target === END_TARGET ? 'bg-slate-700/40 text-slate-400' : 'bg-primary/10 text-primary',
                          )}>
                            {r.target}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {!defn && <p className="text-[11px] text-slate-500">尚未载入定义。</p>}
        </div>
      </div>
    </div>
  )
}

export default WorkflowDesigner
