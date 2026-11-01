import { useState, useEffect, useMemo, type ReactNode } from 'react'
import {
  Key,
  Check,
  Minus,
  Layers,
  CheckCircle2,
  PieChart,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react'
import { PageHeader, Card, StatusBadge, inputClass } from '../components/ui'
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type RoleLevel = 'executive' | 'management' | 'employee'

interface RolePerm {
  code: string
  name: string
  level: RoleLevel
  permissions: Record<string, string[]>
}

/* ===================== 常量定义 ===================== */

/** 15 个资源模块 */
const PERMISSION_MODULES: { key: string; name: string }[] = [
  { key: 'agent', name: '智能体管理' },
  { key: 'invoice', name: '发票管理' },
  { key: 'reimbursement', name: '报销管理' },
  { key: 'tax', name: '税务管理' },
  { key: 'finance', name: '财务管理' },
  { key: 'inventory', name: '库存管理' },
  { key: 'procurement', name: '采购管理' },
  { key: 'hr', name: '人力资源' },
  { key: 'sales', name: '销售管理' },
  { key: 'legal', name: '法务合规' },
  { key: 'risk', name: '风险管理' },
  { key: 'knowledge', name: '知识库' },
  { key: 'approval', name: '审批流程' },
  { key: 'system', name: '系统管理' },
  { key: 'security_audit', name: '安全审计' },
]

/** 6 个操作 */
const OPERATIONS: { key: string; label: string }[] = [
  { key: 'read', label: '查看' },
  { key: 'create', label: '创建' },
  { key: 'update', label: '编辑' },
  { key: 'delete', label: '删除' },
  { key: 'approve', label: '审批' },
  { key: 'manage', label: '管理' },
]

const ALL_OPS = OPERATIONS.map((o) => o.key)

const LEVEL_LABEL: Record<RoleLevel, string> = {
  executive: '高管',
  management: '管理',
  employee: '员工',
}

/** 各角色管辖的资源模块 */
const DOMAIN_MAP: Record<string, string[]> = {
  chairman: PERMISSION_MODULES.map((m) => m.key),
  ceo: PERMISSION_MODULES.map((m) => m.key),
  cto: ['agent', 'system', 'knowledge'],
  cio: ['system', 'agent', 'knowledge', 'security_audit'],
  cfo: ['finance', 'tax', 'invoice', 'reimbursement'],
  cmo: ['sales'],
  cho: ['hr'],
  cro: ['risk', 'security_audit', 'legal'],
  coo: ['inventory', 'procurement', 'sales'],
  finance_manager: ['finance', 'tax', 'invoice', 'reimbursement'],
  hr_manager: ['hr'],
  sales_manager: ['sales'],
  procurement_manager: ['procurement'],
  inventory_manager: ['inventory'],
  legal_manager: ['legal'],
  risk_manager: ['risk', 'security_audit'],
  it_manager: ['agent', 'system', 'knowledge'],
  operations_manager: ['inventory', 'procurement'],
  accountant: ['finance', 'invoice', 'tax', 'reimbursement'],
  sales_rep: ['sales'],
  procurement_specialist: ['procurement'],
  warehouse_keeper: ['inventory'],
  intern: ['reimbursement', 'knowledge'],
}

/* ===================== 权限生成器 ===================== */

function buildPermissions(code: string, level: RoleLevel): Record<string, string[]> {
  const domain = DOMAIN_MAP[code] ?? []
  const result: Record<string, string[]> = {}

  if (level === 'executive') {
    if (code === 'chairman' || code === 'ceo') {
      PERMISSION_MODULES.forEach((m) => (result[m.key] = [...ALL_OPS]))
      return result
    }
    PERMISSION_MODULES.forEach((m) => {
      result[m.key] = domain.includes(m.key) ? [...ALL_OPS] : ['read', 'approve']
    })
    return result
  }

  if (level === 'management') {
    PERMISSION_MODULES.forEach((m) => {
      result[m.key] = domain.includes(m.key)
        ? ['read', 'create', 'update', 'approve']
        : ['read']
    })
    return result
  }

  if (code === 'intern') {
    return { reimbursement: ['read', 'create'], knowledge: ['read'] }
  }
  PERMISSION_MODULES.forEach((m) => {
    result[m.key] = domain.includes(m.key) ? ['read', 'create'] : ['read']
  })
  return result
}

/* ===================== Mock 角色权限数据 ===================== */

const ROLES_RAW: Array<{ code: string; name: string; level: RoleLevel }> = [
  { code: 'chairman', name: '董事会主席', level: 'executive' },
  { code: 'ceo', name: '首席执行官', level: 'executive' },
  { code: 'cto', name: '首席技术官', level: 'executive' },
  { code: 'cfo', name: '首席财务官', level: 'executive' },
  { code: 'cio', name: '首席信息官', level: 'executive' },
  { code: 'cmo', name: '首席营销官', level: 'executive' },
  { code: 'cho', name: '首席人力官', level: 'executive' },
  { code: 'cro', name: '首席风险官', level: 'executive' },
  { code: 'coo', name: '首席运营官', level: 'executive' },
  { code: 'finance_manager', name: '财务部经理', level: 'management' },
  { code: 'hr_manager', name: '人力资源部经理', level: 'management' },
  { code: 'sales_manager', name: '销售部经理', level: 'management' },
  { code: 'procurement_manager', name: '采购部经理', level: 'management' },
  { code: 'inventory_manager', name: '仓储部经理', level: 'management' },
  { code: 'legal_manager', name: '法务部经理', level: 'management' },
  { code: 'risk_manager', name: '风控部经理', level: 'management' },
  { code: 'it_manager', name: 'IT部经理', level: 'management' },
  { code: 'operations_manager', name: '运营部经理', level: 'management' },
  { code: 'accountant', name: '会计', level: 'employee' },
  { code: 'sales_rep', name: '销售专员', level: 'employee' },
  { code: 'procurement_specialist', name: '采购专员', level: 'employee' },
  { code: 'warehouse_keeper', name: '仓管员', level: 'employee' },
  { code: 'intern', name: '实习生', level: 'employee' },
]

const ROLES: RolePerm[] = ROLES_RAW.map((r) => ({
  ...r,
  permissions: buildPermissions(r.code, r.level),
}))

/* ===================== 全局统计（Mock） ===================== */

const TOTAL_ROLE_COUNT = ROLES.length // 23
const TOTAL_PERMISSION_CATALOG = 204 // 系统权限定义总数
const TOTAL_ASSIGNED = 730 // 已分配权限数
const MAX_MATRIX = TOTAL_ROLE_COUNT * PERMISSION_MODULES.length * OPERATIONS.length // 23 × 15 × 6
const COVERAGE = ((TOTAL_ASSIGNED / MAX_MATRIX) * 100).toFixed(1) // 覆盖率

/* ===================== 主组件 ===================== */

export default function PermissionManagement() {
  const [selectedCode, setSelectedCode] = useState<string>('ceo')
  const [matrix, setMatrix] = useState<Record<string, string[]>>(() =>
    clonePermissions(ROLES.find((r) => r.code === 'ceo')!.permissions),
  )

  const selectedRole = useMemo(
    () => ROLES.find((r) => r.code === selectedCode)!,
    [selectedCode],
  )

  // 切换角色时重置矩阵为该角色的原始权限
  useEffect(() => {
    setMatrix(clonePermissions(selectedRole.permissions))
  }, [selectedRole])

  const toggle = (moduleKey: string, op: string) => {
    setMatrix((prev) => {
      const next = { ...prev }
      const ops = new Set(next[moduleKey] ?? [])
      if (ops.has(op)) {
        ops.delete(op)
      } else {
        ops.add(op)
      }
      // 保持操作顺序与 OPERATIONS 一致
      next[moduleKey] = OPERATIONS.filter((o) => ops.has(o.key)).map((o) => o.key)
      return next
    })
  }

  // 当前角色已授权数
  const currentGranted = Object.values(matrix).reduce(
    (sum, ops) => sum + (ops?.length ?? 0),
    0,
  )
  const currentMax = PERMISSION_MODULES.length * OPERATIONS.length // 90
  const currentRatio = ((currentGranted / currentMax) * 100).toFixed(1)

  const isDirty = JSON.stringify(matrix) !== JSON.stringify(selectedRole.permissions)

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="权限管理"
        description="按资源模块与操作维度管理角色权限矩阵"
        icon={<Key className="w-5 h-5" />}
      />

      {/* 角色筛选 + 当前角色概览 */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
              <ShieldCheck className="w-5 h-5 text-primary" />
            </div>
            <div>
              <p className="text-xs text-slate-500">选择角色查看 / 编辑权限矩阵</p>
              <p className="text-sm font-semibold text-white">
                {selectedRole.name}
                <span className="ml-2 text-xs font-normal text-slate-500 font-mono">
                  {selectedRole.code}
                </span>
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">角色</span>
              <select
                value={selectedCode}
                onChange={(e) => setSelectedCode(e.target.value)}
                className={cn(inputClass, 'w-56')}
              >
                {ROLES.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.name}（{LEVEL_LABEL[r.level]}）
                  </option>
                ))}
              </select>
            </div>

            {isDirty && (
              <button
                onClick={() => setMatrix(clonePermissions(selectedRole.permissions))}
                className="btn-ghost"
                title="重置为原始权限"
              >
                <RotateCcw className="w-4 h-4" />
                重置
              </button>
            )}
          </div>
        </div>

        {/* 当前角色授权概览 */}
        <div className="flex flex-wrap items-center gap-4 mt-4 pt-4 border-t border-[#1e2536]">
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">层级</span>
            <StatusBadge variant="primary">{LEVEL_LABEL[selectedRole.level]}</StatusBadge>
          </div>
          <div className="w-px h-3 bg-[#2a3142]" />
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">已授权</span>
            <span className="text-sm font-bold text-accent tabular-nums">{currentGranted}</span>
            <span className="text-xs text-slate-500">/ {currentMax}</span>
          </div>
          <div className="w-px h-3 bg-[#2a3142]" />
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">授权率</span>
            <span className="text-sm font-bold text-primary tabular-nums">{currentRatio}%</span>
          </div>
          <div className="flex-1 min-w-[120px]">
            <div className="h-1.5 rounded-full bg-bg-input overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-primary to-accent transition-all duration-300"
                style={{ width: `${currentRatio}%` }}
              />
            </div>
          </div>
          {isDirty && (
            <StatusBadge variant="warning" dot>
              未保存变更
            </StatusBadge>
          )}
        </div>
      </Card>

      {/* 权限矩阵 */}
      <Card
        title="权限矩阵"
        action={
          <span className="text-xs text-slate-500">
            点击单元格切换授权状态
          </span>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#1e2536]">
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">
                  资源模块
                </th>
                {OPERATIONS.map((op) => (
                  <th
                    key={op.key}
                    className="px-4 py-3 text-center text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap"
                  >
                    {op.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PERMISSION_MODULES.map((mod) => {
                const grantedOps = matrix[mod.key] ?? []
                return (
                  <tr
                    key={mod.key}
                    className="border-b border-[#1e2536]/50 transition-colors hover:bg-bg-hover/40"
                  >
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-white">{mod.name}</span>
                        <span className="text-[11px] text-slate-600 font-mono">{mod.key}</span>
                      </div>
                    </td>
                    {OPERATIONS.map((op) => {
                      const granted = grantedOps.includes(op.key)
                      return (
                        <td key={op.key} className="px-4 py-3 text-center">
                          <button
                            onClick={() => toggle(mod.key, op.key)}
                            className={cn(
                              'w-7 h-7 rounded-md inline-flex items-center justify-center transition-all mx-auto',
                              granted
                                ? 'bg-accent/15 text-accent hover:bg-accent/25'
                                : 'bg-bg-input text-slate-700 hover:text-slate-400 border border-[#2a3142]',
                            )}
                            title={`${mod.name} · ${op.label} · ${granted ? '已授权' : '未授权'}`}
                          >
                            {granted ? <Check className="w-4 h-4" /> : <Minus className="w-3 h-3" />}
                          </button>
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 底部统计栏 */}
      <Card>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <StatBlock
            icon={<Layers className="w-5 h-5" />}
            label="系统权限定义总数"
            value={TOTAL_PERMISSION_CATALOG}
            unit="项"
            variant="primary"
          />
          <div className="hidden sm:block w-px bg-[#1e2536]" />
          <StatBlock
            icon={<CheckCircle2 className="w-5 h-5" />}
            label="已分配权限"
            value={TOTAL_ASSIGNED}
            unit="项"
            variant="accent"
          />
          <div className="hidden sm:block w-px bg-[#1e2536]" />
          <StatBlock
            icon={<PieChart className="w-5 h-5" />}
            label="权限覆盖率"
            value={COVERAGE}
            unit="%"
            variant="primary"
            sub={`基于 ${TOTAL_ROLE_COUNT} 角色 × ${PERMISSION_MODULES.length} 模块 × ${OPERATIONS.length} 操作`}
          />
        </div>
      </Card>
    </div>
  )
}

/* ===================== 工具函数 ===================== */

function clonePermissions(perms: Record<string, string[]>): Record<string, string[]> {
  return JSON.parse(JSON.stringify(perms))
}

/* ===================== 统计块 ===================== */

function StatBlock({
  icon,
  label,
  value,
  unit,
  variant,
  sub,
}: {
  icon: ReactNode
  label: string
  value: string | number
  unit?: string
  variant: 'primary' | 'accent'
  sub?: string
}) {
  const colorMap = {
    primary: 'text-primary bg-primary/10',
    accent: 'text-accent bg-accent/10',
  }
  return (
    <div className="flex items-center gap-3">
      <div
        className={cn(
          'w-10 h-10 rounded-lg flex items-center justify-center shrink-0',
          colorMap[variant],
        )}
      >
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-xs text-slate-500">{label}</p>
        <p className="text-2xl font-bold text-white tabular-nums">
          {value}
          {unit && <span className="text-sm font-normal text-slate-500 ml-0.5">{unit}</span>}
        </p>
        {sub && <p className="text-[11px] text-slate-600 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}
