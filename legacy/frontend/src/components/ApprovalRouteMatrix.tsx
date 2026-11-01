/**
 * 审批人路由矩阵编辑（Phase F2.5 后端 + F2.7 管理端二期）
 * ======================================================
 * 数据源：
 *   GET  /agent-ops/customizations/meta            角色清单 + 可选单据类型
 *   GET  /agent-ops/customizations                 生效矩阵（type=gate, target=approval_route_matrix）
 *   PUT  /agent-ops/customizations/gate/approval_route_matrix
 * 语义（后端 _classify_change 已锁）：矩阵变更影响面大，**任何方向都转人工审批**（红线 2），
 * 保存返回 pending_approval 时不得显示"已生效"。
 * 命中规则：按列表顺序取**首条**命中（单据类型 × 金额区间）；未命中回退存量三级审批链。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, RefreshCw, Route, Save, Trash2 } from 'lucide-react'
import { apiService } from '../services/api'
import { cn } from '../lib/utils'

export interface MatrixRole { code: string; name: string; level?: number }

export interface MatrixRule {
  document_type: string
  amount_min?: number
  amount_max?: number
  approve_roles: string[]
}

const inputCls =
  'w-full text-[12px] px-2 py-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-200 ' +
  'focus:outline-none focus:border-primary/60'

function toAmount(raw: string): number | undefined {
  const t = raw.trim()
  if (t === '') return undefined
  const n = Number(t)
  return Number.isFinite(n) ? n : undefined
}

export function rulesFromOverrides(ov: Record<string, unknown> | undefined): MatrixRule[] {
  const raw = Array.isArray(ov?.rules) ? (ov.rules as Record<string, unknown>[]) : []
  return raw.map((r) => ({
    document_type: String(r?.document_type || ''),
    amount_min: typeof r?.amount_min === 'number' ? r.amount_min : toAmount(String(r?.amount_min ?? '')),
    amount_max: typeof r?.amount_max === 'number' ? r.amount_max : toAmount(String(r?.amount_max ?? '')),
    approve_roles: Array.isArray(r?.approve_roles)
      ? (r.approve_roles as unknown[]).map((x) => String(x)) : [],
  }))
}

/** 表单态（金额用字符串承载，便于"留空=不限"） */
export interface RuleForm {
  document_type: string
  amount_min: string
  amount_max: string
  approve_roles: string[]
}

export function ruleToForm(r: MatrixRule): RuleForm {
  return {
    document_type: r.document_type,
    amount_min: r.amount_min === undefined ? '' : String(r.amount_min),
    amount_max: r.amount_max === undefined ? '' : String(r.amount_max),
    approve_roles: [...r.approve_roles],
  }
}

export function formToRule(f: RuleForm): MatrixRule {
  const rule: MatrixRule = { document_type: f.document_type.trim(), approve_roles: f.approve_roles }
  const min = toAmount(f.amount_min)
  const max = toAmount(f.amount_max)
  if (min !== undefined) rule.amount_min = min
  if (max !== undefined) rule.amount_max = max
  return rule
}

/**
 * 前端预检与后端落库口径一致：角色必须真实存在（否则审批任务将无人可见），
 * 区间必须可解析（后端只做数值比较，写反了会永远不命中）。
 */
export function validateRules(rules: MatrixRule[], knownRoles: string[]): string[] {
  const errors: string[] = []
  const roles = new Set(knownRoles)
  rules.forEach((r, i) => {
    const at = `第 ${i + 1} 条`
    if (!r.document_type) errors.push(`${at}：单据类型不能为空`)
    if (!r.approve_roles.length) errors.push(`${at}：至少指定一个审批角色`)
    for (const role of r.approve_roles) {
      if (!roles.has(role)) errors.push(`${at}：角色 ${role} 不在 RBAC 角色清单内`)
    }
    if (r.amount_min !== undefined && r.amount_max !== undefined && r.amount_min > r.amount_max) {
      errors.push(`${at}：金额区间下限大于上限（该规则永远不会命中）`)
    }
  })
  return errors
}

export function matrixOverrides(rules: MatrixRule[]): Record<string, unknown> {
  return { rules, default: 'legacy' }
}

export function ruleSummary(r: MatrixRule): string {
  const range = r.amount_min !== undefined || r.amount_max !== undefined
    ? `${r.amount_min ?? '不限'} ~ ${r.amount_max ?? '不限'}`
    : '任意金额'
  return `${r.document_type || '（未填类型）'} · ${range} → ${r.approve_roles.join(', ') || '（未选角色）'}`
}

export function ApprovalRouteMatrix({
  pendingCount = 0,
  onChanged,
}: {
  pendingCount?: number
  /** 父级待审批队列刷新回调：矩阵变更一律转人审，不回调则队列停在保存前的计数 */
  onChanged?: () => void | Promise<void>
}) {
  const [roles, setRoles] = useState<MatrixRole[]>([])
  const [docTypes, setDocTypes] = useState<string[]>([])
  const [forms, setForms] = useState<RuleForm[]>([])
  const [saved, setSaved] = useState<RuleForm[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [preflight, setPreflight] = useState<string[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [metaRes, custRes] = await Promise.all([
        apiService.getCustomizationMeta(),
        apiService.getAgentCustomizations(),
      ])
      const roleList: MatrixRole[] = Array.isArray(metaRes?.roles) ? metaRes.roles : []
      setRoles(roleList)
      setDocTypes(Array.isArray(metaRes?.document_types) ? metaRes.document_types : [])
      const list = Array.isArray(custRes?.customizations) ? custRes.customizations : []
      const entry = list.find((c: any) => c.type === 'gate' && c.target === 'approval_route_matrix')
      const initial = rulesFromOverrides(entry?.overrides).map(ruleToForm)
      setForms(initial)
      setSaved(initial)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '审批矩阵数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const dirty = useMemo(
    () => JSON.stringify(forms) !== JSON.stringify(saved),
    [forms, saved],
  )

  const patch = (index: number, p: Partial<RuleForm>) => {
    setForms((prev) => prev.map((f, i) => (i === index ? { ...f, ...p } : f)))
  }

  const addRow = () => {
    setForms((prev) => [...prev, { document_type: '', amount_min: '', amount_max: '', approve_roles: [] }])
  }

  const removeRow = (index: number) => {
    setForms((prev) => prev.filter((_, i) => i !== index))
  }

  const toggleRole = (index: number, code: string) => {
    const cur = forms[index]
    const has = cur.approve_roles.includes(code)
    patch(index, {
      approve_roles: has
        ? cur.approve_roles.filter((r) => r !== code)
        : [...cur.approve_roles, code],
    })
  }

  const save = async () => {
    const rules = forms.map(formToRule)
    const errs = validateRules(rules, roles.map((r) => r.code))
    setPreflight(errs)
    if (errs.length) {
      setError('')
      return
    }
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const res = await apiService.putAgentCustomization(
        'gate', 'approval_route_matrix',
        { overrides: matrixOverrides(rules), enabled: true, comment: '管理端审批路由矩阵编辑' },
      )
      if (res?.status === 'pending_approval') {
        setMessage('矩阵变更已提交人工审批，批准后才参与路由（红线 2）')
      } else if (res?.status === 'duplicate') {
        setMessage('与上一版内容相同，后端已忽略本次重复请求')
      } else {
        setMessage(`保存返回状态：${res?.status || '未知'}`)
      }
      await load()
      await onChanged?.()
    } catch (e: any) {
      setError(e?.response?.data?.detail || '矩阵保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Route className="w-4 h-4 text-accent" />
          <h3 className="text-sm font-semibold text-white">
            审批人路由矩阵
            <span className="ml-2 text-xs text-slate-500">
              单据类型 × 金额区间 → 审批角色 · 按顺序首条命中 · 未命中回退存量三级链
            </span>
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {pendingCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-400 border border-yellow-500/30">
              {pendingCount} 条矩阵变更待审批
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
      {preflight.length > 0 && (
        <div className="p-2.5 rounded-lg bg-yellow-500/10 border border-yellow-500/30 text-xs space-y-1">
          {preflight.map((e, i) => <div key={i} className="text-yellow-400">{e}</div>)}
        </div>
      )}

      <div className="space-y-2">
        {forms.map((f, i) => (
          <div key={i} className="rounded-xl border border-[#2a3142] bg-bg-card/80 p-3 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/40 text-slate-300">#{i + 1}</span>
              <input
                list="wf-doc-types"
                value={f.document_type}
                onChange={(e) => patch(i, { document_type: e.target.value })}
                placeholder="单据类型（如 treasury_fund_transfer_request）"
                className={cn(inputCls, 'max-w-xs')}
              />
              <input type="number" value={f.amount_min} placeholder="金额下限（空=不限）"
                onChange={(e) => patch(i, { amount_min: e.target.value })}
                className={cn(inputCls, 'w-32')} />
              <input type="number" value={f.amount_max} placeholder="金额上限（空=不限）"
                onChange={(e) => patch(i, { amount_max: e.target.value })}
                className={cn(inputCls, 'w-32')} />
              <button onClick={() => removeRow(i)}
                className="ml-auto text-slate-600 hover:text-status-circuit" title="删除规则">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {roles.map((r) => {
                const on = f.approve_roles.includes(r.code)
                return (
                  <button key={r.code} onClick={() => toggleRole(i, r.code)}
                    title={r.name}
                    className={cn(
                      'text-[10px] px-1.5 py-0.5 rounded border',
                      on ? 'border-primary/50 bg-primary/15 text-primary'
                        : 'border-slate-600 text-slate-500 hover:text-slate-300',
                    )}
                  >
                    {r.code}
                  </button>
                )
              })}
            </div>
            <p className="text-[10px] text-slate-600 font-mono truncate">{ruleSummary(formToRule(f))}</p>
          </div>
        ))}
        {forms.length === 0 && (
          <p className="text-xs text-slate-500">
            尚无矩阵规则：write_high 中断全部走存量三级审批链。新增规则后任何变更都需人工审批才生效。
          </p>
        )}
      </div>

      <datalist id="wf-doc-types">
        {docTypes.map((d) => <option key={d} value={d} />)}
      </datalist>

      <div className="flex items-center gap-2">
        <button onClick={addRow}
          className="text-[11px] px-2.5 py-1.5 rounded-lg border border-slate-600 text-slate-300 hover:bg-slate-600/10 flex items-center gap-1">
          <Plus className="w-3 h-3" /> 新增规则
        </button>
        <button onClick={save} disabled={!dirty || busy}
          className="text-[11px] px-2.5 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-40 flex items-center gap-1">
          <Save className="w-3 h-3" /> 提交变更（转人审）
        </button>
        <span className="text-[10px] text-slate-600">
          已生效 {saved.length} 条 · 编辑中 {forms.length} 条
        </span>
      </div>
    </div>
  )
}
