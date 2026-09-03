import { useState, useEffect, useCallback } from 'react'
import { Briefcase, Users, UserCheck, UserPlus, UserMinus, Plus, Eye } from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  type Column,
} from '../components/ui'
import { apiService } from '../services/api'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type CustomerLevel = 'S' | 'A' | 'B' | 'C'
type CustomerStatus = 'active' | 'dormant' | 'churned'

interface Customer {
  id: string
  code: string
  name: string
  contact: string
  phone: string
  industry: string
  level: CustomerLevel
  status: CustomerStatus
  address: string
  email: string
  revenue: number
  createdAt: string
  lastContact: string
  remark: string
}

/* ===================== 状态配置 ===================== */

const LEVEL_VARIANT: Record<CustomerLevel, 'primary' | 'info' | 'success' | 'neutral'> = {
  S: 'primary',
  A: 'info',
  B: 'success',
  C: 'neutral',
}

const STATUS_VARIANT: Record<CustomerStatus, 'success' | 'warning' | 'danger'> = {
  active: 'success',
  dormant: 'warning',
  churned: 'danger',
}

const STATUS_LABEL: Record<CustomerStatus, string> = {
  active: '活跃',
  dormant: '沉睡',
  churned: '流失',
}

const fmtMoney = (n: number) => `¥${n.toLocaleString('zh-CN')}`

/* ===================== CRM 统计类型 ===================== */

interface CRMStats {
  customers?: number
  opportunities?: number
  open_opportunities?: number
  won_opportunities?: number
  lost_opportunities?: number
  contacts?: number
  total_pipeline_amount?: number
  total_won_amount?: number
  win_rate?: number
  segments?: Record<string, number>
}

/* ===================== 数据映射 ===================== */

/** 将后端返回的客户数据映射为前端 Customer 结构（兼容 snake_case / camelCase） */
function mapCustomer(raw: Record<string, unknown>): Customer {
  const rawLevel = String(raw.level ?? raw.segment ?? 'C').toUpperCase()
  const level: CustomerLevel = (['S', 'A', 'B', 'C'] as const).some((v) => v === rawLevel)
    ? (rawLevel as CustomerLevel)
    : 'C'
  const rawStatus = String(raw.status ?? 'active')
  let status: CustomerStatus = 'active'
  if (rawStatus === 'dormant' || rawStatus === 'inactive') status = 'dormant'
  else if (rawStatus === 'churned' || rawStatus === 'lost') status = 'churned'
  return {
    id: String(raw.id ?? raw.customer_id ?? ''),
    code: String(raw.code ?? raw.customer_id ?? ''),
    name: String(raw.name ?? '—'),
    contact: String(raw.contact ?? raw.contact_name ?? '—'),
    phone: String(raw.phone ?? raw.contact_phone ?? '—'),
    industry: String(raw.industry ?? ''),
    level,
    status,
    address: String(raw.address ?? ''),
    email: String(raw.email ?? ''),
    revenue: Number(raw.revenue) || 0,
    createdAt: String(raw.created_at ?? raw.createdAt ?? ''),
    lastContact: String(raw.last_contact ?? raw.lastContact ?? raw.updated_at ?? ''),
    remark: String(raw.remark ?? ''),
  }
}

/* ===================== 主组件 ===================== */

export default function CustomerManagement() {
  const [selected, setSelected] = useState<Customer | null>(null)
  const [customers, setCustomers] = useState<Customer[]>([])
  const [stats, setStats] = useState<CRMStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, crmStats] = await Promise.all([
      apiService.getCustomers(),
      apiService.getCRMStats(),
    ])
    setCustomers((Array.isArray(list) ? list : []).map(mapCustomer))
    setStats(crmStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const currentMonth = new Date().toISOString().slice(0, 7)
  const totalCustomers = stats?.customers ?? customers.length
  const activeCustomers = customers.filter((c) => c.status === 'active').length
  const newThisMonth = customers.filter((c) => c.createdAt.startsWith(currentMonth)).length
  const churnedCustomers = customers.filter((c) => c.status === 'churned').length

  const columns: Column<Customer>[] = [
    {
      key: 'code',
      label: '客户编号',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.code}</span>,
    },
    {
      key: 'name',
      label: '客户名称',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    { key: 'contact', label: '联系人', render: (r) => <span className="text-slate-300">{r.contact}</span> },
    {
      key: 'phone',
      label: '电话',
      render: (r) => <span className="text-slate-400 font-mono text-xs">{r.phone}</span>,
    },
    { key: 'industry', label: '行业', render: (r) => <span className="text-slate-400">{r.industry}</span> },
    {
      key: 'level',
      label: '客户等级',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant={LEVEL_VARIANT[r.level]}>{r.level} 级</StatusBadge>,
    },
    {
      key: 'status',
      label: '状态',
      align: 'center',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={STATUS_VARIANT[r.status]} dot>
          {STATUS_LABEL[r.status]}
        </StatusBadge>
      ),
    },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <Can
          resource="customer"
          action="update"
          fallback={<span className="text-xs text-slate-600">—</span>}
        >
          <button
            onClick={(e) => {
              e.stopPropagation()
              setSelected(r)
            }}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
          >
            <Eye className="w-3.5 h-3.5" /> 查看
          </button>
        </Can>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="客户管理"
        description="客户档案、分级与全生命周期管理"
        icon={<Briefcase className="w-5 h-5" />}
        actions={
          <Can resource="customer" action="create">
            <button className="btn-primary">
              <Plus className="w-4 h-4" /> 新增客户
            </button>
          </Can>
        }
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="总客户数" value={totalCustomers} icon={<Users className="w-5 h-5" />} trend="较上月 +2.1%" trendUp variant="primary" />
        <StatCard label="活跃客户" value={activeCustomers} icon={<UserCheck className="w-5 h-5" />} trend="活跃率 75%" trendUp variant="accent" />
        <StatCard label="本月新增" value={newThisMonth} icon={<UserPlus className="w-5 h-5" />} trend="环比 +3 家" trendUp variant="primary" />
        <StatCard label="流失客户" value={churnedCustomers} icon={<UserMinus className="w-5 h-5" />} trend="需重点挽回" variant="danger" />
      </div>

      {/* 客户列表 */}
      <Card title="客户列表">
        <DataTable
          columns={columns}
          data={customers}
          loading={loading}
          searchKeys={['name', 'contact']}
          searchPlaceholder="搜索客户名称、联系人..."
          onRowClick={(r) => setSelected(r)}
          pageSize={10}
        />
      </Card>

      {/* 客户详情弹窗 */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="客户详情" size="lg">
        {selected && <CustomerDetail customer={selected} />}
      </Modal>
    </div>
  )
}

/* ===================== 客户详情 ===================== */

function CustomerDetail({ customer }: { customer: Customer }) {
  const rows: { label: string; value: string }[] = [
    { label: '客户编号', value: customer.code },
    { label: '客户名称', value: customer.name },
    { label: '联系人', value: customer.contact },
    { label: '联系电话', value: customer.phone },
    { label: '所属行业', value: customer.industry },
    { label: '客户等级', value: `${customer.level} 级` },
    { label: '客户状态', value: STATUS_LABEL[customer.status] },
    { label: '电子邮箱', value: customer.email },
    { label: '联系地址', value: customer.address },
    { label: '累计营收', value: fmtMoney(customer.revenue) },
    { label: '建档日期', value: customer.createdAt },
    { label: '最近联系', value: customer.lastContact },
  ]

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 flex-wrap">
        <StatusBadge variant={LEVEL_VARIANT[customer.level]} dot>
          {customer.level} 级客户
        </StatusBadge>
        <StatusBadge variant={STATUS_VARIANT[customer.status]} dot>
          {STATUS_LABEL[customer.status]}
        </StatusBadge>
        <StatusBadge variant="neutral">{customer.industry}</StatusBadge>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{r.label}</span>
            <span className="text-sm text-white font-medium">{r.value}</span>
          </div>
        ))}
      </div>

      <div className="rounded-lg bg-bg-input border border-[#1e2536] p-4">
        <p className="text-xs text-slate-500 mb-1.5">客户备注</p>
        <p className="text-sm text-slate-300 leading-relaxed">{customer.remark}</p>
      </div>
    </div>
  )
}
