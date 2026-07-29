import { useState } from 'react'
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

/* ===================== 状态配置 ===================== */

const statusConfig: Record<PayrollStatus, { label: string; variant: 'warning' | 'success' }> = {
  pending: { label: '待发', variant: 'warning' },
  paid: { label: '已发', variant: 'success' },
}

/* ===================== 工具函数 ===================== */

const fmt = (n: number) => `¥${n.toLocaleString('zh-CN')}`

const netSalary = (r: PayrollRecord) => r.baseSalary + r.bonus - r.socialInsurance - r.housingFund

/* ===================== Mock 数据 ===================== */

const payrollRecords: PayrollRecord[] = [
  { id: 'EMP-001', name: '张伟', department: '技术部', baseSalary: 18000, bonus: 4500, socialInsurance: 2160, housingFund: 2160, status: 'paid' },
  { id: 'EMP-002', name: '李娜', department: '产品部', baseSalary: 22000, bonus: 6000, socialInsurance: 2640, housingFund: 2640, status: 'paid' },
  { id: 'EMP-003', name: '王强', department: '技术部', baseSalary: 19000, bonus: 3800, socialInsurance: 2280, housingFund: 2280, status: 'pending' },
  { id: 'EMP-004', name: '赵敏', department: '财务部', baseSalary: 15000, bonus: 3000, socialInsurance: 1800, housingFund: 1800, status: 'paid' },
  { id: 'EMP-005', name: '刘洋', department: '销售部', baseSalary: 12000, bonus: 8000, socialInsurance: 1440, housingFund: 1440, status: 'pending' },
  { id: 'EMP-006', name: '陈静', department: '人力资源部', baseSalary: 16000, bonus: 3200, socialInsurance: 1920, housingFund: 1920, status: 'paid' },
  { id: 'EMP-007', name: '杨光', department: '技术部', baseSalary: 13000, bonus: 2000, socialInsurance: 1560, housingFund: 1560, status: 'pending' },
  { id: 'EMP-008', name: '周婷', department: '市场部', baseSalary: 14000, bonus: 3500, socialInsurance: 1680, housingFund: 1680, status: 'pending' },
  { id: 'EMP-009', name: '孙磊', department: '运营部', baseSalary: 11000, bonus: 1800, socialInsurance: 1320, housingFund: 1320, status: 'pending' },
  { id: 'EMP-010', name: '吴雪', department: '技术部', baseSalary: 15500, bonus: 3000, socialInsurance: 1860, housingFund: 1860, status: 'paid' },
  { id: 'EMP-012', name: '黄丽', department: '财务部', baseSalary: 12500, bonus: 2200, socialInsurance: 1500, housingFund: 1500, status: 'pending' },
  { id: 'EMP-013', name: '林峰', department: '技术部', baseSalary: 20500, bonus: 5200, socialInsurance: 2460, housingFund: 2460, status: 'paid' },
]

/* ===================== 主组件 ===================== */

export default function Payroll() {
  const currentMonth = new Date().toISOString().slice(0, 7)
  const [month, setMonth] = useState(currentMonth)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const pendingCount = payrollRecords.filter((r) => r.status === 'pending').length

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
            <button
              onClick={() => setConfirmOpen(true)}
              disabled={pendingCount === 0}
              className="btn-accent"
            >
              <Send className="w-4 h-4" />
              批量发放
            </button>
          </div>
        }
      />

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="本月薪资总额"
          value="¥1,286,500"
          icon={<Wallet className="w-5 h-5" />}
          variant="primary"
          trend="环比 +2.3%"
          trendUp
        />
        <StatCard
          label="平均薪资"
          value="¥14,960"
          icon={<TrendingUp className="w-5 h-5" />}
          variant="accent"
          trend="环比 +1.1%"
          trendUp
        />
        <StatCard
          label="社保支出"
          value="¥185,200"
          icon={<ShieldCheck className="w-5 h-5" />}
          variant="warning"
        />
        <StatCard
          label="公积金"
          value="¥97,200"
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
