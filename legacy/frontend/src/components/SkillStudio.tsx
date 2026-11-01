/**
 * Skill 工坊（路线图 Phase F / F4.6 管理端，覆盖 F4.4 编辑器 + F4.5 版本与搬运）
 * =============================================================================
 * 数据源：
 *   GET  /agent-ops/skills/meta                     编辑器元数据（按域工具清单/模板/发布目录）
 *   GET  /agent-ops/skills/{name}                   版本历史 + stable + 启停 + 运行时视图 + 待批提案
 *   GET  /agent-ops/skills/{name}/{version}         完整定义
 *   POST /agent-ops/skills/validate                 校验（后端校验器是唯一权威）
 *   PUT  /agent-ops/skills/{name}/{version}/draft   存草稿
 *   POST /agent-ops/skills/{name}/{version}/publish 申请发布 → 人审
 *   PUT  /agent-ops/skills/{name}/enabled           启停（停用即时 / 启用须批）
 *   POST /agent-ops/skills/{name}/rollback          回滚到历史已批版本
 *   GET  /agent-ops/skills/export                   导出 JSON 定制包（customization:export）
 *   POST /agent-ops/skills/import                   导入 → 只落草稿
 *
 * 红线口径：
 * - 发布只能"申请"：技能指令进 LLM 系统提示，影响面等同 DSL，后端判为放宽 → 人审；
 * - 已发布版本不可变：编辑历史版本前先"另存为新版本"；
 * - 导入不等于生效：导入项一律落草稿，仍须逐条发布过人审；
 * - 生效证据是"发布目录里出现 YAML 且运行时目录视图版本变化"，界面把两者都显示出来。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  Download,
  History,
  Power,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  Sparkles,
  Upload,
  X,
} from 'lucide-react'
import { apiService } from '../services/api'
import Can from './Can'
import { cn } from '../lib/utils'

const inputCls =
  'w-full text-[12px] px-2 py-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-200 ' +
  'focus:outline-none focus:border-primary/60 disabled:opacity-50'

interface SkillRow {
  id: number
  name: string
  version: string
  status: string
  domain: string | null
  display_name: string | null
  tool_count: number
  comment: string | null
  created_by: string | null
  published_at: string | null
}

interface CatalogSkill {
  name: string
  display_name: string
  description: string
  domain: string
  tools: string[]
  valid: boolean
  invalid_reason: string
  enabled: boolean
  version: string
  published: boolean
}

interface SkillDefinition {
  name: string
  display_name: string
  description: string
  domain: string
  tools: string[]
  instructions: string
  guardrails: Record<string, unknown>
}

interface DesignerMeta {
  tools_by_domain: Record<string, string[]>
  domains: string[]
  skills: CatalogSkill[]
  template: SkillDefinition
  overlay_dir: string
  name_pattern: string
}

/* ================================================================
 *  纯函数（供单测）
 * ================================================================ */

export function nextVersion(versions: { version: string }[]): string {
  const nums = versions
    .map((v) => Number(String(v.version).replace(/^v/i, '')))
    .filter((n) => Number.isFinite(n))
  return `v${(nums.length ? Math.max(...nums) : 0) + 1}`
}

/** 域切换时清掉不属于新域的工具勾选：跨域白名单会让运行期图编译直接失败。 */
export function toolsOnDomainChange(tools: string[], nextDomain: string,
                                   byDomain: Record<string, string[]>): string[] {
  const allowed = new Set(byDomain[nextDomain] || [])
  return tools.filter((t) => allowed.has(t))
}

export function parseGuardrails(text: string):
  { ok: boolean; value: Record<string, unknown>; error: string } {
  const trimmed = (text || '').trim()
  if (!trimmed) return { ok: true, value: {}, error: '' }
  try {
    const v = JSON.parse(trimmed)
    if (!v || typeof v !== 'object' || Array.isArray(v)) {
      return { ok: false, value: {}, error: 'guardrails 必须是 JSON 对象' }
    }
    return { ok: true, value: v as Record<string, unknown>, error: '' }
  } catch (e: any) {
    return { ok: false, value: {}, error: `JSON 解析失败：${e?.message || e}` }
  }
}

export function buildDefinition(name: string, form: SkillDefinition,
                                guardrails: Record<string, unknown>): SkillDefinition {
  return {
    name: name.trim(),
    display_name: form.display_name.trim() || name.trim(),
    description: form.description.trim(),
    domain: form.domain,
    tools: form.tools,
    instructions: form.instructions,
    guardrails,
  }
}

/** 版本行 → 可执行动作（已发布不可变；草稿可编辑；历史已批版本可回滚）。 */
export function editableVersion(rows: SkillRow[], selected: string): SkillRow | null {
  return rows.find((r) => r.version === selected) || null
}

export function canEdit(row: SkillRow | null): boolean {
  return !row || row.status === 'draft'
}

export function canRollback(row: SkillRow | null): boolean {
  return !!row && (row.status === 'stable' || row.status === 'superseded')
}

export function idemKey(scope: string, target: string, state: string): string {
  return `${scope}:${target}:${state}`
}

/** 后端 status → 界面结论。放宽类必须显示"待审批"，不能显示成已生效。 */
export function changeNotice(result: Record<string, any> | null | undefined): string {
  switch (result?.status) {
    case 'pending_approval':
      return '已提交人工审批（放宽类变更），批准后生效并物化到发布目录。'
    case 'applied':
      return '收紧类变更，已即时生效。'
    case 'duplicate':
      return '重复请求已被幂等键拦下（5 分钟窗口）。'
    case 'rejected':
      return '变更已被驳回。'
    case 'published':
      return `已发布为 ${result?.version}（生效版本切换）。`
    case 'rolled_back':
      return `已回滚到 ${result?.version}。`
    default:
      return result?.reply || '已完成。'
  }
}

/**
 * 导出结论：按包内条目的来源分开计数。出厂技能没有版本流行，把它们说成
 * "已发布"会让管理员以为搬过去就直接生效。
 */
export function exportSummary(bundle: Record<string, any> | null | undefined): string {
  const items = Array.isArray(bundle?.skills) ? bundle.skills : []
  const n = (st: string) => items.filter((s) => s?.status === st).length
  const parts: string[] = []
  if (n('draft')) parts.push(`${n('draft')} 草稿`)
  if (n('factory')) parts.push(`${n('factory')} 出厂`)
  const published = items.length - n('draft') - n('factory')
  if (published > 0) parts.push(`${published} 已发布`)
  return `已导出 ${bundle?.count ?? items.length} 个技能定义`
    + (parts.length ? `（${parts.join(' · ')}）。` : '（无）。')
}

/* ================================================================
 *  小组件
 * ================================================================ */

function Tag({ tone, children }: { tone: 'ok' | 'warn' | 'bad' | 'mute' | 'info'; children: React.ReactNode }) {
  const cls = {
    ok: 'bg-status-ok/10 text-status-ok border-status-ok/30',
    warn: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
    bad: 'bg-status-circuit/10 text-status-circuit border-status-circuit/30',
    info: 'bg-primary/10 text-primary border-primary/30',
    mute: 'bg-bg-input text-slate-400 border-[#2a3142]',
  }[tone]
  return (
    <span className={cn('inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px]', cls)}>
      {children}
    </span>
  )
}

const STATUS_TONE: Record<string, 'ok' | 'warn' | 'mute'> = {
  stable: 'ok', draft: 'warn', superseded: 'mute',
}

/* ================================================================
 *  主组件
 * ================================================================ */

export function SkillStudio() {
  const [meta, setMeta] = useState<DesignerMeta | null>(null)
  const [rows, setRows] = useState<SkillRow[]>([])
  const [status, setStatus] = useState<Record<string, any> | null>(null)
  const [name, setName] = useState('')
  const [version, setVersion] = useState('')
  const [form, setForm] = useState<SkillDefinition | null>(null)
  const [grText, setGrText] = useState('{}')
  const [comment, setComment] = useState('')
  const [errors, setErrors] = useState<string[]>([])
  const [publishErrors, setPublishErrors] = useState<string[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [versionRows, setVersionRows] = useState<SkillRow[]>([])

  const catalog = meta?.skills || []
  const currentRow = useMemo(() => editableVersion(rows, version), [rows, version])
  const toolOptions = useMemo(
    () => (form ? (meta?.tools_by_domain?.[form.domain] || []) : []), [form, meta])

  const loadMeta = useCallback(async () => {
    try {
      const [res, vs] = await Promise.all([
        apiService.getSkillDesignerMeta(),
        apiService.listSkillVersions(),
      ])
      setMeta(res as DesignerMeta)
      setVersionRows(Array.isArray(vs?.versions) ? vs.versions : [])
    } catch (e: any) {
      setError(e?.response?.data?.detail || '元数据加载失败')
    }
  }, [])

  useEffect(() => { loadMeta() }, [loadMeta])

  // 目录来自运行时加载（发布目录 + 出厂目录），只有草稿的技能根本不在里面：
  // 导入或新建后左侧点不到，只能手输标识符再点"重新载入"（真机踩过）。
  // 已发布的技能其草稿在右侧版本流里，这里只补"从未发布过"的名字。
  const orphanDrafts = useMemo(() => {
    const known = new Set(catalog.map((s) => s.name))
    const byName = new Map<string, SkillRow>()
    for (const r of versionRows) {
      if (r.status !== 'draft' || known.has(r.name) || byName.has(r.name)) continue
      byName.set(r.name, r)          // 版本行按 id 倒序，首见即该名字的最新草稿
    }
    return [...byName.values()]
  }, [catalog, versionRows])

  // notice：动作结论要跟着重载一起显示。重载本身会清 message，
  // 不传结论就等于"点了发布，提示一闪没了"。
  // prefer：刚保存/申请发布的版本。重载若一律跳回 stable，版本选择器会从 v2
  // 掉回 v1，接着点"申请发布"就把已生效的 v1 再发一遍（真机验证踩过）。
  const selectSkill = useCallback(async (skill: string, notice = '', prefer = '') => {
    setName(skill)
    setError('')
    setMessage(notice)
    if (!skill) return
    const st = await apiService.getSkillStatus(skill)
    setStatus(st)
    const list: SkillRow[] = Array.isArray(st?.versions) ? st.versions : []
    setRows(list)
    // 默认载入生效版本；没有则载入最新草稿；都没有则空白模板
    const target = (prefer && list.some((r) => r.version === prefer) ? prefer : '')
      || st?.stable?.version
      || list.find((r) => r.status === 'draft')?.version
      || list[0]?.version || ''
    if (target) {
      const row = await apiService.getSkillDefinition(skill, target)
      const defn = row?.definition || {}
      setForm({
        name: skill,
        display_name: defn.display_name || skill,
        description: defn.description || '',
        domain: defn.domain || '',
        tools: Array.isArray(defn.tools) ? defn.tools : [],
        instructions: defn.instructions || '',
        guardrails: defn.guardrails || {},
      })
      setGrText(JSON.stringify(defn.guardrails || {}, null, 2))
      setVersion(target)
    } else {
      const blank = meta?.template || {
        name: skill, display_name: skill, description: '', domain: '',
        tools: [], instructions: '', guardrails: {},
      }
      setForm({ ...blank, name: skill, display_name: skill })
      setGrText('{}')
      setVersion(nextVersion(list))
    }
  }, [meta])

  const newSkill = async () => {
    const skill = (name || 'new_skill').trim()
    setName(skill)
    setRows([])
    setStatus(null)
    const blank = meta?.template || {} as SkillDefinition
    setForm({ ...blank, name: skill, display_name: skill, domain: meta?.domains?.[0] || '', tools: [] })
    setGrText('{}')
    setVersion('v1')
    setError('')
    setMessage('技能名需与标识符规则一致（' + (meta?.name_pattern || '') + '），存草稿即创建。')
  }

  const run = async (key: string, fn: () => Promise<any>, onOk?: (r: any) => Promise<void> | void) => {
    setBusy(key)
    setError('')
    setMessage('')
    try {
      const res = await fn()
      if (onOk) await onOk(res)
      return res
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '操作失败')
      return null
    } finally {
      setBusy(null)
    }
  }

  const definition = (): SkillDefinition | null => {
    if (!form) return null
    const gr = parseGuardrails(grText)
    if (!gr.ok) {
      setError(gr.error)
      return null
    }
    return buildDefinition(name, { ...form, guardrails: gr.value }, gr.value)
  }

  const doValidate = async () => {
    const defn = definition()
    if (!defn) return
    await run('validate', () => apiService.validateSkillDefinition(defn as any), (r) => {
      setErrors(Array.isArray(r?.errors) ? r.errors : [])
      setPublishErrors(Array.isArray(r?.publish_errors) ? r.publish_errors : [])
      setMessage(r?.valid ? '结构校验通过（发布还会额外要求工具都已在治理面注册）。' : '结构校验未通过。')
    })
  }

  const doSave = async () => {
    const defn = definition()
    if (!defn) return
    await run('save', () => apiService.saveSkillDraft(name, version, defn as any, comment.trim()), async (r) => {
      setComment('')
      await selectSkill(name, `草稿已保存：${r?.name}@${version}`, version)
      // 新建技能的草稿要立刻进左侧目录：目录行来自 meta/版本流，不重取就只能手输
      // 标识符再点"重新载入"才能接着改（真机验证踩过）。
      await loadMeta()
    })
  }

  const doPublish = async () => {
    const defn = definition()
    if (!defn) return
    await run('publish', async () => {
      // 先让后端校验器把关（前端不复制校验规则），未过则直接抛错给界面
      const v = await apiService.validateSkillDefinition(defn as any)
      if (!v?.valid || (v?.publish_errors || []).length) {
        const err: any = new Error(
          [...(v?.errors || []), ...(v?.publish_errors || [])].join('；') || '校验未通过')
        err.response = { data: { detail: err.message } }
        throw err
      }
      return apiService.requestSkillPublish(
        name, version, comment.trim(), idemKey('skill-publish', name, version))
    }, async (r) => {
      setComment('')
      await selectSkill(name, changeNotice(r), version)
      await loadMeta()
    })
  }

  // 启停/发布/回滚都会改变左侧目录行的徽标（停用 · 版本号），目录来自 meta，
  // 不重取就会停在动作前的状态——"停用即时生效"却在列表里仍显示启用中。
  const doToggle = async (enabled: boolean) => {
    await run('toggle', () => apiService.setSkillEnabled(
      name, enabled, enabled ? '管理端启用技能' : '管理端停用技能',
      idemKey('skill-enabled', name, String(enabled))), async (r) => {
      await selectSkill(name, changeNotice(r))
      await loadMeta()
    })
  }

  const doRollback = async (target: string) => {
    await run(`rollback:${target}`, () => apiService.rollbackSkill(name, target), async (r) => {
      await selectSkill(name, changeNotice(r))
      await loadMeta()
    })
  }

  const doExport = async () => {
    await run('export', () => apiService.exportSkills(undefined, true), (r) => {
      const text = JSON.stringify(r, null, 2)
      const blob = new Blob([text], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `skills-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      setMessage(exportSummary(r))
    })
  }

  const doImport = async (file: File) => {
    let bundle: any = null
    try {
      bundle = JSON.parse(await file.text())
    } catch {
      setError('定制包不是合法 JSON')
      return
    }
    await run('import', () => apiService.importSkills(bundle,
      idemKey('skill-import', String(bundle?.format || ''), String(bundle?.count || 0))),
    (r) => {
      const failed = Array.isArray(r?.failed) ? r.failed : []
      setMessage(`导入 ${r?.imported ?? 0} 条为草稿${failed.length ? `，${failed.length} 条被拒` : ''}。`
        + '导入内容不会自动生效，需逐条申请发布并过人审。')
      if (failed.length) setError(failed.map((f: any) => `${f.name}：${(f.errors || []).join('；')}`).join('\n'))
      loadMeta()
    })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[260px_minmax(0,1fr)] gap-4">
      {/* 左：技能目录 */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-[12px] text-slate-300 inline-flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-primary" />
            技能目录
          </div>
          <div className="flex items-center gap-1">
            <Can resource="customization" action="export">
              <button onClick={doExport} disabled={busy !== null}
                title="导出 JSON 定制包"
                className="p-1.5 rounded text-slate-400 hover:text-white hover:bg-bg-hover disabled:opacity-40">
                <Download className="w-3.5 h-3.5" />
              </button>
            </Can>
            <Can resource="customization" action="update">
              <label
                title="导入 JSON 定制包（只落草稿）"
                className={cn('p-1.5 rounded text-slate-400 hover:text-white hover:bg-bg-hover inline-flex cursor-pointer',
                  busy !== null && 'opacity-40 pointer-events-none')}
              >
                <Upload className="w-3.5 h-3.5" />
                <input
                  type="file"
                  accept="application/json,.json"
                  className="hidden"
                  data-testid="skill-import-input"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) doImport(f); e.target.value = '' }}
                />
              </label>
            </Can>
          </div>
        </div>

        <Can resource="customization" action="update">
          <button
            onClick={newSkill}
            disabled={busy !== null}
            className="w-full inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-[11px] bg-primary/20 border border-primary/40 text-primary disabled:opacity-40"
          >
            <Save className="w-3 h-3" />
            新建技能（存草稿即创建）
          </button>
        </Can>

        <div className="rounded-xl border border-[#1e2536] overflow-hidden max-h-[70vh] overflow-y-auto">
          {catalog.map((s) => (
            <button
              key={s.name}
              onClick={() => selectSkill(s.name)}
              className={cn(
                'w-full text-left px-2.5 py-2 border-b border-[#1e2536] last:border-b-0 hover:bg-bg-hover/50',
                name === s.name && 'bg-primary/10',
              )}
            >
              <div className="flex items-center justify-between gap-1">
                <span className="text-[12px] text-slate-200 truncate">{s.display_name || s.name}</span>
                {!s.enabled && <Tag tone="bad">停用</Tag>}
                {s.enabled && !s.published && <Tag tone="mute">出厂</Tag>}
                {s.published && <Tag tone="info">{s.version || '已发布'}</Tag>}
              </div>
              <div className="text-[10px] text-slate-500 truncate">{s.domain} · {s.tools.length} 工具</div>
              {!s.valid && <div className="text-[10px] text-status-circuit">{s.invalid_reason}</div>}
            </button>
          ))}
          {catalog.length === 0 && (
            <div className="px-2.5 py-4 text-[11px] text-slate-500">目录为空</div>
          )}
        </div>

        {orphanDrafts.length > 0 && (
          <div className="rounded-xl border border-[#1e2536] overflow-hidden">
            <div className="px-2.5 py-1.5 text-[10px] text-slate-500 bg-bg-input/40">
              仅存草稿（不参与运行，需逐条申请发布过人审）
            </div>
            {orphanDrafts.map((d) => (
              <button
                key={`${d.name}:${d.version}`}
                onClick={() => selectSkill(d.name)}
                className={cn(
                  'w-full text-left px-2.5 py-2 border-t border-[#1e2536] hover:bg-bg-hover/50',
                  name === d.name && 'bg-primary/10',
                )}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[12px] text-slate-200 truncate">{d.display_name || d.name}</span>
                  <Tag tone="warn">草稿 {d.version}</Tag>
                </div>
                <div className="text-[10px] text-slate-500 truncate">{d.domain || '—'} · {d.tool_count} 工具</div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 右：编辑器 + 版本流 */}
      <div className="space-y-4">
        <div className="flex items-center gap-2 flex-wrap">
          <input
            className={cn(inputCls, 'w-40')}
            value={name}
            placeholder="技能标识符"
            data-testid="skill-name"
            onChange={(e) => setName(e.target.value)}
          />
          <select
            className={cn(inputCls, 'w-28')}
            value={version}
            data-testid="skill-version"
            onChange={(e) => setVersion(e.target.value)}
          >
            {rows.map((r) => <option key={r.version} value={r.version}>{r.version} · {r.status}</option>)}
            {!rows.some((r) => r.version === version) && version && (
              <option value={version}>{version} · 新草稿</option>
            )}
          </select>
          <button
            onClick={() => selectSkill(name)}
            disabled={busy !== null || !name}
            title="重新载入该技能"
            className="p-1.5 rounded text-slate-400 hover:text-white hover:bg-bg-hover disabled:opacity-40"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', busy === name && 'animate-spin')} />
          </button>
          <div className="flex-1" />
          {status && (
            <Can resource="customization" action="update">
              <button
                onClick={() => doToggle(!(status.enabled ?? true))}
                disabled={busy !== null}
                className={cn(
                  'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] border disabled:opacity-40',
                  status.enabled
                    ? 'text-status-circuit border-status-circuit/30 hover:bg-status-circuit/10'
                    : 'text-status-ok border-status-ok/30 hover:bg-status-ok/10',
                )}
              >
                <Power className="w-3 h-3" />
                {status.enabled ? '停用（即时生效）' : '启用（转人审）'}
              </button>
            </Can>
          )}
        </div>

        {message && <div className="text-[11px] text-status-ok whitespace-pre-line">{message}</div>}
        {error && (
          <div className="text-[11px] text-status-circuit flex items-start gap-1 whitespace-pre-line">
            <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />{error}
          </div>
        )}

        {!form ? (
          <div className="rounded-xl border border-dashed border-[#2a3142] p-8 text-center text-[11px] text-slate-500">
            左侧选择技能，或点「新建技能」开始编辑。
            <div className="mt-1">发布目录：<code className="text-slate-400">{meta?.overlay_dir || '—'}</code></div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="rounded-xl border border-[#1e2536] bg-bg-card/40 p-3 space-y-2">
              <div className="text-[12px] text-slate-200">技能定义（表单 → YAML → 热加载）</div>
              <label className="block space-y-1">
                <span className="text-[10px] uppercase text-slate-500">显示名</span>
                <input className={inputCls} value={form.display_name}
                  onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
              </label>
              <label className="block space-y-1">
                <span className="text-[10px] uppercase text-slate-500">描述</span>
                <input className={inputCls} value={form.description}
                  data-testid="skill-description"
                  onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </label>
              <label className="block space-y-1">
                <span className="text-[10px] uppercase text-slate-500">归属域（决定场景图）</span>
                <select
                  className={inputCls}
                  value={form.domain}
                  data-testid="skill-domain"
                  onChange={(e) => setForm({
                    ...form,
                    domain: e.target.value,
                    tools: toolsOnDomainChange(form.tools, e.target.value, meta?.tools_by_domain || {}),
                  })}
                >
                  <option value="">— 选择业务域 —</option>
                  {(meta?.domains || []).map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
              <div className="space-y-1">
                <span className="text-[10px] uppercase text-slate-500">
                  工具白名单（{form.tools.length} / {toolOptions.length}）
                </span>
                <div className="max-h-36 overflow-y-auto grid grid-cols-1 gap-1">
                  {toolOptions.map((t) => (
                    <label key={t} className="inline-flex items-center gap-1.5 text-[11px] text-slate-300">
                      <input
                        type="checkbox"
                        checked={form.tools.includes(t)}
                        onChange={() => setForm({
                          ...form,
                          tools: form.tools.includes(t)
                            ? form.tools.filter((x) => x !== t) : [...form.tools, t],
                        })}
                      />
                      {t}
                    </label>
                  ))}
                  {toolOptions.length === 0 && (
                    <div className="text-[10px] text-slate-500">该域无可选工具（工具由治理面注册表提供）</div>
                  )}
                </div>
              </div>
              <label className="block space-y-1">
                <span className="text-[10px] uppercase text-slate-500">技能指令（进系统提示，须精炼）</span>
                <textarea
                  className={cn(inputCls, 'h-32 font-mono')}
                  value={form.instructions}
                  data-testid="skill-instructions"
                  onChange={(e) => setForm({ ...form, instructions: e.target.value })}
                />
              </label>
              <label className="block space-y-1">
                <span className="text-[10px] uppercase text-slate-500">护栏参数（JSON 对象，透传治理面）</span>
                <textarea
                  className={cn(inputCls, 'h-20 font-mono')}
                  value={grText}
                  data-testid="skill-guardrails"
                  onChange={(e) => setGrText(e.target.value)}
                />
              </label>
              <input
                className={inputCls}
                placeholder="变更说明（进审计与审批卡片）"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={doValidate}
                  disabled={busy !== null}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-bg-input border border-[#2a3142] text-slate-300 disabled:opacity-40"
                >
                  <Check className="w-3 h-3" />校验
                </button>
                <Can resource="customization" action="update">
                  <button
                    onClick={doSave}
                    disabled={busy !== null || !canEdit(currentRow)}
                    title={canEdit(currentRow) ? '保存草稿' : '已发布版本不可变，请另存新版本'}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-bg-input border border-[#2a3142] text-slate-200 disabled:opacity-40"
                  >
                    <Save className="w-3 h-3" />存草稿
                  </button>
                </Can>
                <Can resource="customization" action="publish">
                  <button
                    onClick={doPublish}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] bg-primary/20 border border-primary/40 text-primary disabled:opacity-40"
                  >
                    <ShieldAlert className="w-3 h-3" />申请发布（转人审）
                  </button>
                </Can>
                {!canEdit(currentRow) && (
                  <button
                    onClick={() => { setVersion(nextVersion(rows)); setMessage(`已切到新草稿版本 ${nextVersion(rows)}，保存即另存新版本`) }}
                    className="inline-flex items-center gap-1 px-2 py-1.5 rounded-lg text-[11px] text-slate-300 hover:bg-bg-hover"
                  >
                    <X className="w-3 h-3" />另存为新版本
                  </button>
                )}
              </div>
              {(errors.length > 0 || publishErrors.length > 0) && (
                <div className="space-y-0.5">
                  {errors.map((e) => <div key={`e${e}`} className="text-[10px] text-status-circuit">· {e}</div>)}
                  {publishErrors.map((e) => <div key={`p${e}`} className="text-[10px] text-yellow-400">· 发布门槛：{e}</div>)}
                </div>
              )}
            </div>

            <div className="space-y-3">
              {/* 运行时视图：证明"生效"而非只改了库 */}
              <div className="rounded-xl border border-[#1e2536] bg-bg-card/40 p-3 space-y-1.5">
                <div className="text-[12px] text-slate-200">运行时视图（进程内热加载结果）</div>
                {status?.runtime ? (
                  <>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <Tag tone="ok">{`已加载 ${status.runtime.version || '未标版'}`}</Tag>
                      <Tag tone={status.enabled ? 'ok' : 'bad'}>{status.enabled ? '启用中' : '已停用'}</Tag>
                      {!status.runtime.valid && <Tag tone="bad">{status.runtime.invalid_reason}</Tag>}
                      {!status.runtime.published && <Tag tone="mute">出厂版</Tag>}
                    </div>
                    <div className="text-[10px] text-slate-500">
                      {status.runtime.domain} · {status.runtime.tools.length} 工具
                    </div>
                  </>
                ) : (
                  <div className="text-[11px] text-slate-500">
                    运行时不可见（未发布 / 已停用 / 仅出厂目录未加载）
                  </div>
                )}
                {status?.pending && (
                  <div className="text-[10px] text-yellow-400">
                    待批提案：{status.pending.proposed?.version || status.pending.comment}
                    （由 {status.pending.changed_by} 提交，批准后才会物化）
                  </div>
                )}
              </div>

              {/* 版本历史 */}
              <div className="rounded-xl border border-[#1e2536] overflow-hidden">
                <div className="px-3 py-2 text-[11px] text-slate-400 inline-flex items-center gap-1.5 bg-bg-input/60">
                  <History className="w-3 h-3" />版本历史（已发布不可变）
                </div>
                <table className="w-full text-[11px]">
                  <tbody>
                    {rows.length === 0 && (
                      <tr><td className="px-3 py-4 text-center text-slate-500">尚无版本（存草稿后出现）</td></tr>
                    )}
                    {rows.map((r) => (
                      <tr key={r.id} className="border-t border-[#1e2536]">
                        <td className="px-3 py-1.5 text-slate-200">{r.version}</td>
                        <td className="px-3 py-1.5"><Tag tone={STATUS_TONE[r.status] || 'mute'}>{r.status}</Tag></td>
                        <td className="px-3 py-1.5 text-slate-500 truncate max-w-[130px]">{r.comment || '—'}</td>
                        <td className="px-3 py-1.5 text-slate-500">{r.created_by || '—'}</td>
                        <td className="px-3 py-1.5 whitespace-nowrap text-right">
                          <button
                            onClick={() => { setVersion(r.version); selectSkill(name) }}
                            className="px-1.5 py-1 rounded text-[10px] text-primary hover:bg-primary/10"
                          >载入</button>
                          <Can resource="customization" action="publish">
                            <button
                              onClick={() => doRollback(r.version)}
                              disabled={!canRollback(r) || r.status === 'stable' || busy !== null}
                              title={canRollback(r) && r.status !== 'stable'
                                ? '回滚到该版本（历史已批版本，即时生效）' : ''}
                              className="inline-flex items-center gap-1 px-1.5 py-1 rounded text-[10px] text-slate-400 hover:text-white hover:bg-bg-hover disabled:opacity-30"
                            >
                              <RotateCcw className="w-3 h-3" />回滚
                            </button>
                          </Can>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default SkillStudio
