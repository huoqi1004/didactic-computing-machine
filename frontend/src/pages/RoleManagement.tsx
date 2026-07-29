import { useState } from 'react'
import {
  Shield,
  Plus,
  Eye,
  Pencil,
  Users,
  CheckCircle2,
  Crown,
  Building2,
  Check,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  FormField,
  inputClass,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type RoleLevel = 'executive' | 'management' | 'employee'
type RoleStatus = 'active' | 'inactive'

interface Role {
  code: string
  name: string
  level: RoleLevel
  description: string
  status: RoleStatus
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

const ALL_OPS = ['read', 'create', 'update', 'delete', 'approve', 'manage']

const OP_LABELS: Record<string, string> = {
  read: '查看',
  create: '创建',
  update: '编辑',
  delete: '删除',
  approve: '审批',
  manage: '管理',
}

const LEVEL_CONFIG: Record<RoleLevel, { label: string; className: string }> = {
  executive: { label: '高管', className: 'bg-primary/10 text-primary border-primary/20' },
  management: { label: '管理', className: 'bg-accent/10 text-accent border-accent/20' },
  employee: { label: '员工', className: 'bg-slate-500/10 text-slate-400 border-slate-500/20' },
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
    // 董事长 / CEO 拥有全部权限
    if (code === 'chairman' || code === 'ceo') {
      PERMISSION_MODULES.forEach((m) => (result[m.key] = [...ALL_OPS]))
      return result
    }
    // 其他高管：管辖模块全权限，其余可查看 + 审批
    PERMISSION_MODULES.forEach((m) => {
      result[m.key] = domain.includes(m.key) ? [...ALL_OPS] : ['read', 'approve']
    })
    return result
  }

  if (level === 'management') {
    // 部门经理：管辖模块可读/建/改/审批，其余只读
    PERMISSION_MODULES.forEach((m) => {
      result[m.key] = domain.includes(m.key)
        ? ['read', 'create', 'update', 'approve']
        : ['read']
    })
    return result
  }

  // 员工层
  if (code === 'intern') {
    return { reimbursement: ['read', 'create'], knowledge: ['read'] }
  }
  PERMISSION_MODULES.forEach((m) => {
    result[m.key] = domain.includes(m.key) ? ['read', 'create'] : ['read']
  })
  return result
}

/* ===================== Mock 角色数据 ===================== */

const ROLES_RAW: Array<{
  code: string
  name: string
  level: RoleLevel
  description: string
  status: RoleStatus
}> = [
  // 高管层（9）
  { code: 'chairman', name: '董事会主席', level: 'executive', description: '公司最高决策者，拥有全部系统权限', status: 'active' },
  { code: 'ceo', name: '首席执行官', level: 'executive', description: '负责公司整体战略制定与全面运营', status: 'active' },
  { code: 'cto', name: '首席技术官', level: 'executive', description: '统筹技术研发、架构与信息化建设', status: 'active' },
  { code: 'cfo', name: '首席财务官', level: 'executive', description: '主管财务核算、税务与资金管理', status: 'active' },
  { code: 'cio', name: '首席信息官', level: 'executive', description: '负责信息系统规划与数据治理', status: 'active' },
  { code: 'cmo', name: '首席营销官', level: 'executive', description: '主导市场营销、品牌与客户战略', status: 'active' },
  { code: 'cho', name: '首席人力官', level: 'executive', description: '统筹人力资源规划与组织发展', status: 'active' },
  { code: 'cro', name: '首席风险官', level: 'executive', description: '负责风险管控与合规审计（岗位空缺中）', status: 'inactive' },
  { code: 'coo', name: '首席运营官', level: 'executive', description: '负责日常运营与供应链管理', status: 'active' },
  // 管理层（9）
  { code: 'finance_manager', name: '财务部经理', level: 'management', description: '管理财务核算、报表与资金结算', status: 'active' },
  { code: 'hr_manager', name: '人力资源部经理', level: 'management', description: '管理招聘、薪酬绩效与员工关系', status: 'active' },
  { code: 'sales_manager', name: '销售部经理', level: 'management', description: '管理销售团队、客户与业绩目标', status: 'active' },
  { code: 'procurement_manager', name: '采购部经理', level: 'management', description: '管理采购流程、合同与供应商', status: 'active' },
  { code: 'inventory_manager', name: '仓储部经理', level: 'management', description: '管理库存调度与仓储作业', status: 'active' },
  { code: 'legal_manager', name: '法务部经理', level: 'management', description: '管理合同审查与法律事务', status: 'active' },
  { code: 'risk_manager', name: '风控部经理', level: 'management', description: '管理风险评估与内控审计', status: 'active' },
  { code: 'it_manager', name: 'IT部经理', level: 'management', description: '管理IT基础设施与系统运维', status: 'active' },
  { code: 'operations_manager', name: '运营部经理', level: 'management', description: '管理运营流程与执行落地', status: 'active' },
  // 员工层（5）
  { code: 'accountant', name: '会计', level: 'employee', description: '负责账务处理与凭证录入', status: 'active' },
  { code: 'sales_rep', name: '销售专员', level: 'employee', description: '负责客户开发与订单跟进', status: 'active' },
  { code: 'procurement_specialist', name: '采购专员', level: 'employee', description: '负责采购订单执行与对账', status: 'active' },
  { code: 'warehouse_keeper', name: '仓管员', level: 'employee', description: '负责出入库登记与盘点', status: 'active' },
  { code: 'intern', name: '实习生', level: 'employee', description: '轮岗学习岗位（暂未启用）', status: 'inactive' },
]

const ROLES: Role[] = ROLES_RAW.map((r) => ({
  ...r,
  permissions: buildPermissions(r.code, r.level),
}))

/* ===================== 主组件 ===================== */

export default function RoleManagement() {
  const [viewRole, setViewRole] = useState<Role | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  // 统计指标（由数据计算，保证一致性）
  const totalRoles = ROLES.length
  const activeRoles = ROLES.filter((r) => r.status === 'active').length
  const cSuite = ROLES.filter(
    (r) => r.level === 'executive' && r.status === 'active' && r.code !== 'chairman',
  ).length
  const managementCount = ROLES.filter((r) => r.level === 'management').length

  const columns: Column<Role>[] = [
    {
      key: 'code',
      label: '角色代码',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.code}</span>,
    },
    {
      key: 'name',
      label: '角色名称',
      sortable: true,
      render: (r) => <span className="text-sm font-medium text-white">{r.name}</span>,
    },
    {
      key: 'level',
      label: '层级',
      sortable: true,
      render: (r) => {
        const cfg = LEVEL_CONFIG[r.level]
        return (
          <span
            className={cn(
              'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border',
              cfg.className,
            )}
          >
            {cfg.label}
          </span>
        )
      },
    },
    {
      key: 'description',
      label: '描述',
      render: (r) => (
        <span className="text-xs text-slate-400 max-w-[280px] truncate block" title={r.description}>
          {r.description}
        </span>
      ),
    },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={r.status === 'active' ? 'success' : 'neutral'} dot>
          {r.status === 'active' ? '启用' : '停用'}
        </StatusBadge>
      ),
    },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setViewRole(r)}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
          >
            <Eye className="w-3.5 h-3.5" />
            查看权限
          </button>
          <button
            onClick={() => setViewRole(r)}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-bg-input text-slate-400 hover:text-white border border-[#2a3142] transition-colors"
          >
            <Pencil className="w-3.5 h-3.5" />
            编辑
          </button>
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="角色管理"
        description="管理系统角色、层级划分与权限分配"
        icon={<Shield className="w-5 h-5" />}
        actions={
          <button
            onClick={() => setShowCreate(true)}
            className="btn-primary"
          >
            <Plus className="w-4 h-4" />
            创建角色
          </button>
        }
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="总角色数"
          value={totalRoles}
          icon={<Users className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="活跃角色"
          value={activeRoles}
          icon={<CheckCircle2 className="w-5 h-5" />}
          variant="accent"
          trend="较上月 +2"
          trendUp
        />
        <StatCard
          label="C级高管"
          value={cSuite}
          icon={<Crown className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="部门管理"
          value={managementCount}
          icon={<Building2 className="w-5 h-5" />}
          variant="accent"
        />
      </div>

      {/* 角色列表 */}
      <Card>
        <DataTable
          columns={columns}
          data={ROLES}
          searchKeys={['code', 'name', 'description']}
          searchPlaceholder="搜索角色代码 / 名称 / 描述..."
          pageSize={10}
        />
      </Card>

      {/* 查看权限弹窗 */}
      <Modal
        open={!!viewRole}
        onClose={() => setViewRole(null)}
        title={viewRole ? `${viewRole.name} · 权限详情` : ''}
        size="lg"
      >
        {viewRole && <PermissionView role={viewRole} />}
      </Modal>

      {/* 创建角色弹窗 */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="创建角色" size="md">
        <CreateRoleForm onClose={() => setShowCreate(false)} />
      </Modal>
    </div>
  )
}

/* ===================== 权限详情视图 ===================== */

function PermissionView({ role }: { role: Role }) {
  const grantedModules = PERMISSION_MODULES.filter((m) => {
    const ops = role.permissions[m.key]
    return ops && ops.length > 0
  })

  const totalGranted = Object.values(role.permissions).reduce(
    (sum, ops) => sum + (ops?.length ?? 0),
    0,
  )

  return (
    <div className="space-y-4">
      {/* 角色概要 */}
      <div className="flex flex-wrap items-center gap-3 p-3 rounded-lg bg-bg-input border border-[#2a3142]">
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">角色代码</span>
          <span className="font-mono text-xs text-primary">{role.code}</span>
        </div>
        <div className="w-px h-3 bg-[#2a3142]" />
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">层级</span>
          <span
            className={cn(
              'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border',
              LEVEL_CONFIG[role.level].className,
            )}
          >
            {LEVEL_CONFIG[role.level].label}
          </span>
        </div>
        <div className="w-px h-3 bg-[#2a3142]" />
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">已授权</span>
          <span className="text-xs font-semibold text-accent">{totalGranted} 项</span>
        </div>
        <div className="w-px h-3 bg-[#2a3142]" />
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">覆盖模块</span>
          <span className="text-xs font-semibold text-white">{grantedModules.length} / 15</span>
        </div>
      </div>

      {/* 按资源模块分组展示 */}
      <div className="space-y-2.5 max-h-[480px] overflow-y-auto pr-1">
        {grantedModules.map((mod) => {
          const ops = role.permissions[mod.key] ?? []
          return (
            <div
              key={mod.key}
              className="p-3 rounded-lg bg-bg-input border border-[#1e2536]"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-white">{mod.name}</span>
                <span className="text-[11px] text-slate-500 font-mono">{mod.key}</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {ops.map((op) => (
                  <span
                    key={op}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-accent/10 text-accent border border-accent/20"
                  >
                    <Check className="w-3 h-3" />
                    {OP_LABELS[op] ?? op}
                  </span>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ===================== 创建角色表单 ===================== */

function CreateRoleForm({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({
    code: '',
    name: '',
    level: 'employee' as RoleLevel,
    description: '',
  })
  const [error, setError] = useState('')

  const handleSubmit = () => {
    if (!form.code.trim()) {
      setError('请输入角色代码')
      return
    }
    if (!form.name.trim()) {
      setError('请输入角色名称')
      return
    }
    // Mock：实际场景会调用后端接口
    onClose()
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <FormField label="角色代码" required hint="如：finance_manager">
          <input
            type="text"
            value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value })}
            placeholder="role_code"
            className={inputClass}
          />
        </FormField>
        <FormField label="角色名称" required hint="如：财务部经理">
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="请输入角色名称"
            className={inputClass}
          />
        </FormField>
      </div>

      <FormField label="层级" required>
        <select
          value={form.level}
          onChange={(e) => setForm({ ...form, level: e.target.value as RoleLevel })}
          className={inputClass}
        >
          <option value="executive">高管（executive）</option>
          <option value="management">管理（management）</option>
          <option value="employee">员工（employee）</option>
        </select>
      </FormField>

      <FormField label="角色描述">
        <textarea
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="请简要描述该角色的职责范围..."
          rows={3}
          className={cn(inputClass, 'resize-none')}
        />
      </FormField>

      {error && (
        <div className="p-3 rounded-lg bg-status-failed/10 border border-status-failed/30 text-xs text-status-failed">
          {error}
        </div>
      )}

      <div className="flex gap-3 pt-1">
        <button onClick={onClose} className="btn-ghost flex-1">
          取消
        </button>
        <button onClick={handleSubmit} className="btn-primary flex-1">
          <CheckCircle2 className="w-4 h-4" />
          确认创建
        </button>
      </div>
    </div>
  )
}
