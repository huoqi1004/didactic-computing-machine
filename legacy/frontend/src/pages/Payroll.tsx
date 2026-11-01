import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Banknote,
  Wallet,
  TrendingUp,
  ShieldCheck,
  Building,
  CalendarDays,
  Send,
  CheckCircle2,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  StatCard,
  Modal,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'
import { apiService } from '../services/api'
import ExportButton from '../components/ExportButton'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type PayrollStatus = 'pending' | 'paid'

interface PayrollRecord {
  id: string
  name: string
  department: string
  baseSalary: number
  bonus: number
  socialInsurance: number
  housingFund: number
  status: PayrollStatus
}

interface PayrollStats {
  total_net_salary?: number
  total_gross_salary?: number
  total_social_insurance?: number
  total_housing_fund?: number
  total_payroll_records?: number
}

/* ===================== 状态配置 ===================== */

const statusConfig: Record<PayrollStatus, { label: string; variant: 'warning' | 'success' }> = {
  pending: { label: '待发', variant: 'warning' },
  paid: { label: '已发', variant: 'success' },
}

/* ===================== 工具函数 ===================== */

const fmt = (n: number) => `¥${n.toLocaleString('zh-CN')}`

const netSalary = (r: PayrollRecord) => r.baseSalary + r.bonus - r.socialInsurance - r.housingFund

/** 将后端返回的薪酬记录映射为前端 PayrollRecord 结构（兼容 snake_case / camelCase） */
function mapPayrollRecord(raw: Record<string, unknown>): PayrollRecord {
  const rawStatus = String(raw.status ?? 'pending')
  // 后端可能返回 'calculated' 等状态，统一归一为 pending / paid
  const status: PayrollStatus = rawStatus === 'paid' ? 'paid' : 'pending'
  return {
    id: String(raw.id ?? raw.employee_id ?? raw.record_id ?? ''),
    name: String(raw.name ?? raw.employee_name ?? raw.employee_id ?? '—'),
    department: String(raw.department ?? raw.dept ?? ''),
    baseSalary: Number(raw.base_salary ?? raw.baseSalary ?? 0),
    bonus: Number(raw.bonus ?? raw.allowances ?? 0),
    socialInsurance: Number(raw.social_insurance ?? raw.socialInsurance ?? 0),
    housingFund: Number(raw.housing_fund ?? raw.housingFund ?? 0),
    status,
  }
}

/* ===================== 主组件 ===================== */

export default function Payroll() {
  const currentMonth = new Date().toISOString().slice(0, 7)
  const [month, setMonth] = useState(currentMonth)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [payrollRecords, setPayrollRecords] = useState<PayrollRecord[]>([])
  const [stats, setStats] = useState<PayrollStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async (period: string) => {
    setLoading(true)
    const [records, payrollStats] = await Promise.all([
      apiService.getPayrollRecords({ period }),
      apiService.getPayrollStats(),
    ])
    setPayrollRecords((Array.isArray(records) ? records : []).map(mapPayrollRecord))
    setStats(payrollStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData(month)
  }, [month, fetchData])

  const pendingCount = payrollRecords.filter((r) => r.status === 'pending').length

  // 统计数据：优先使用 API 返回，降级时从记录中计算
  const totalNetSalary = stats?.total_net_salary ?? payrollRecords.reduce((s, r) => s + netSalary(r), 0)
  const avgSalary = payrollRecords.length > 0 ? totalNetSalary / payrollRecords.length : 0
  const totalSocial = stats?.total_social_insurance ?? payrollRecords.reduce((s, r) => s + r.socialInsurance, 0)
  const totalFund = stats?.total_housing_fund ?? payrollRecords.reduce((s, r) => s + r.housingFund, 0)

  // 构造工资条导出数据（xlsx payroll 模板）
  const payrollExportData = useMemo(() => {
    const headers = ['工号', '姓名', '部门', '基本工资', '绩效奖金', '社保扣除', '公积金扣除', '实发工资']
    const rows = payrollRecords.map((r) => [
      r.id,
      r.name,
      r.department,
      r.baseSalary,
      r.bonus,
      r.socialInsurance,
      r.housingFund,
      netSalary(r),
    ])
    const totalBase = payrollRecords.reduce((s, r) => s + r.baseSalary, 0)
    const totalBonus = payrollRecords.reduce((s, r) => s + r.bonus, 0)
    const totalSocial = payrollRecords.reduce((s, r) => s + r.socialInsurance, 0)
    const totalFund = payrollRecords.reduce((s, r) => s + r.housingFund, 0)
    const totalNet = payrollRecords.reduce((s, r) => s + netSalary(r), 0)
    return {
      title: '员工工资表',
      month,
      headers,
      rows,
      total_row: ['合计', '', '', totalBase, totalBonus, totalSocial, totalFund, totalNet],
      notes: ['备注：本工资表仅供内部使用'],
    }
  }, [payrollRecords, month])

  const columns: Column<PayrollRecord>[] = [
    {
      key: 'id',
      label: '工号',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.id}</span>,
    },
    {
      key: 'name',
      label: '姓名',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    { key: 'department', label: '部门', sortable: true },
    {
      key: 'baseSalary',
      label: '基本工资',
      align: 'right',
      sortable: true,
      render: (r) => <span className="tabular-nums text-slate-300">{fmt(r.baseSalary)}</span>,
    },
    {
      key: 'bonus',
      label: '绩效奖金',
      align: 'right',
      sortable: true,
      render: (r) => (
        <span className={cn('tabular-nums', r.bonus > 0 ? 'text-accent' : 'text-slate-500')}>
          {r.bonus > 0 ? fmt(r.bonus) : '—'}
        </span>
      ),
    },
    {
      key: 'socialInsurance',
      label: '社保扣除',
      align: 'right',
      render: (r) => <span className="tabular-nums text-status-paused">-{fmt(r.socialInsurance)}</span>,
    },
    {
      key: 'housingFund',
      label: '公积金扣除',
      align: 'right',
      render: (r) => <span className="tabular-nums text-status-paused">-{fmt(r.housingFund)}</span>,
    },
    {
      key: 'net',
      label: '实发工资',
      align: 'right',
      sortable: true,
      render: (r) => <span className="tabular-nums font-semibold text-white">{fmt(netSalary(r))}</span>,
    },
    {
      key: 'status',
      label: '状态',
      align: 'center',
      render: (r) => {
        const cfg = statusConfig[r.status]
        return (
          <StatusBadge variant={cfg.variant} dot>
            {cfg.label}
          </StatusBadge>
        )
      },
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="薪酬管理"
        description="薪资核算、社保公积金与薪资发放管理"
        icon={<Banknote className="w-5 h-5" />}
        actions={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-bg-input border border-[#2a3142]">
              <CalendarDays className="w-4 h-4 text-slate-500" />
              <input
                type="month"
                value={month}
                onChange={(e) => setMonth(e.target.value)}
                className="bg-transparent text-xs text-white focus:outline-none"
              />
            </div>
            <Can resource="payroll" action="approve">
              <button
                onClick={() => setConfirmOpen(true)}
                disabled={pendingCount === 0}
                className="btn-accent"
              >
                <Send className="w-4 h-4" />
                批量发放
              </button>
            </Can>
            <ExportButton
              agentId="payroll"
              templateType="payroll"
              filename={`工资条_${month}`}
              data={payrollExportData}
              title="员工工资表"
              formats={['xlsx']}
              label="导出工资条"
            />
          </div>
        }
      />

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="本月薪资总额"
          value={fmt(Math.round(totalNetSalary))}
          icon={<Wallet className="w-5 h-5" />}
          variant="primary"
          trend="环比 +2.3%"
          trendUp
        />
        <StatCard
          label="平均薪资"
          value={fmt(Math.round(avgSalary))}
          icon={<TrendingUp className="w-5 h-5" />}
          variant="accent"
          trend="环比 +1.1%"
          trendUp
        />
        <StatCard
          label="社保支出"
          value={fmt(Math.round(totalSocial))}
          icon={<ShieldCheck className="w-5 h-5" />}
          variant="warning"
        />
        <StatCard
          label="公积金"
          value={fmt(Math.round(totalFund))}
          icon={<Building className="w-5 h-5" />}
          variant="warning"
        />
      </div>

      {/* 薪资明细 */}
      <Card
        title="薪资明细"
        action={
          <span className="text-xs text-slate-500">
            账期：<span className="text-slate-300 tabular-nums">{month}</span> · 待发{' '}
            <span className="text-status-paused">{pendingCount}</span> 人
          </span>
        }
      >
        <DataTable
          columns={columns}
          data={payrollRecords}
          loading={loading}
          searchKeys={['name']}
          searchPlaceholder="搜索姓名..."
          pageSize={10}
        />
      </Card>

      {/* 批量发放确认 */}
      <Modal open={confirmOpen} onClose={() => setConfirmOpen(false)} title="确认批量发放" size="sm">
        <div className="space-y-4">
          <div className="flex items-center gap-3 p-4 rounded-lg bg-bg-input border border-[#2a3142]">
            <div className="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
              <Send className="w-5 h-5 text-accent" />
            </div>
            <div>
              <p className="text-sm text-white">将对 <span className="font-semibold text-accent">{pendingCount}</span> 名待发员工发放薪资</p>
              <p className="text-xs text-slate-500 mt-0.5">账期 {month}，预计到账 T+1</p>
            </div>
          </div>
          <p className="text-xs text-slate-500">
            发放后状态将更新为「已发」，且不可撤销。请确认薪资数据无误后再执行。
          </p>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setConfirmOpen(false)} className="btn-secondary">
              取消
            </button>
            <button onClick={() => setConfirmOpen(false)} className="btn-accent">
              <CheckCircle2 className="w-4 h-4" />
              确认发放
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
