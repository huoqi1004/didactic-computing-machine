import { useState, useEffect, useCallback } from 'react'
import {
  PiggyBank,
  CalendarDays,
  CheckCircle2,
  Percent,
  Wallet,
  ArrowUpCircle,
  ArrowDownCircle,
  GitCommit,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  StatCard,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'
import { apiService } from '../services/api'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type BudgetStatus = 'normal' | 'warning' | 'overrun'

interface DepartmentBudget {
  id: string
  department: string
  annual_budget: number
  executed: number
  status: BudgetStatus
}

interface BudgetChange {
  id: string
  department: string
  type: 'increase' | 'decrease' | 'transfer'
  amount: number
  reason: string
  applicant: string
  date: string
}

/** 后端预算统计响应结构（字段均为可选，兼容降级） */
interface BudgetStats {
  total_budgets?: number
  total_planned?: number
  total_actual?: number
  exceeded_count?: number
  by_status?: Record<string, number>
  by_department?: Record<string, number>
}

/* ===================== 数据映射 ===================== */

/** 将后端预算数据（snake_case）映射为前端 DepartmentBudget 结构 */
function mapBudget(raw: any): DepartmentBudget {
  const annual = Number(raw.amount ?? raw.annual_budget ?? 0)
  const executed = Number(raw.actual_amount ?? raw.executed ?? 0)
  let status: BudgetStatus = 'normal'
  if (raw.status === 'exceeded' || executed > annual) {
    status = 'overrun'
  } else if (annual > 0 && executed / annual > 0.8) {
    status = 'warning'
  }
  return {
    id: String(raw.budget_id ?? raw.id ?? ''),
    department: String(raw.department ?? ''),
    annual_budget: annual,
    executed,
    status,
  }
}

/* ===================== 状态配置 ===================== */

const BUDGET_STATUS_CONFIG: Record<
  BudgetStatus,
  { label: string; variant: 'success' | 'warning' | 'danger' }
> = {
  normal: { label: '正常', variant: 'success' },
  warning: { label: '预警', variant: 'warning' },
  overrun: { label: '超支', variant: 'danger' },
}

/* ===================== 工具函数 ===================== */

function getProgressColor(ratio: number): string {
  if (ratio > 100) return 'bg-status-failed'
  if (ratio > 80) return 'bg-status-paused'
  return 'bg-accent'
}

function formatYuan(value: number): string {
  return `¥${value.toLocaleString('zh-CN')}`
}

/* ===================== Mock 数据 ===================== */

/* 预算变更记录：后端暂无对应接口，保留为本地回退数据 */
const BUDGET_CHANGES: BudgetChange[] = [
  {
    id: '1',
    department: '研发中心',
    type: 'increase',
    amount: 500000,
    reason: '新增 AI 大模型研发项目，追加研发预算',
    applicant: '陈强',
    date: '2026-07-25',
  },
  {
    id: '2',
    department: '供应链管理部',
    type: 'increase',
    amount: 300000,
    reason: '原材料价格上涨，采购成本超预期',
    applicant: '郑磊',
    date: '2026-07-22',
  },
  {
    id: '3',
    department: '信息技术部',
    type: 'transfer',
    amount: 200000,
    reason: '从行政预算调入，用于服务器扩容',
    applicant: '张明',
    date: '2026-07-20',
  },
  {
    id: '4',
    department: '销售与市场部',
    type: 'increase',
    amount: 280000,
    reason: 'Q3 市场推广活动增加，追加营销预算',
    applicant: '王芳',
    date: '2026-07-18',
  },
  {
    id: '5',
    department: '生产制造部',
    type: 'decrease',
    amount: 150000,
    reason: '产线优化节省成本，调减设备维护预算',
    applicant: '孙伟',
    date: '2026-07-15',
  },
  {
    id: '6',
    department: '人力资源部',
    type: 'transfer',
    amount: 100000,
    reason: '转拨至研发中心用于人才招聘',
    applicant: '林雪',
    date: '2026-07-12',
  },
]

/* ===================== 主组件 ===================== */

export default function BudgetManagement() {
  const [budgets, setBudgets] = useState<DepartmentBudget[]>([])
  const [stats, setStats] = useState<BudgetStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, budgetStats] = await Promise.all([
      apiService.getBudgets(),
      apiService.getBudgetStats(),
    ])
    setBudgets((Array.isArray(list) ? list : []).map(mapBudget))
    setStats(budgetStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const totalPlanned = stats?.total_planned ?? 0
  const totalActual = stats?.total_actual ?? 0
  const executionRate = totalPlanned > 0 ? (totalActual / totalPlanned) * 100 : 0
  const remaining = totalPlanned - totalActual

  const columns: Column<DepartmentBudget>[] = [
    {
      key: 'department',
      label: '部门',
      sortable: true,
      render: (row) => <span className="font-medium text-white">{row.department}</span>,
    },
    {
      key: 'annual_budget',
      label: '年度预算',
      sortable: true,
      align: 'right',
      render: (row) => (
        <span className="tabular-nums text-slate-300">{formatYuan(row.annual_budget)}</span>
      ),
    },
    {
      key: 'executed',
      label: '已执行',
      sortable: true,
      align: 'right',
      render: (row) => (
        <span className="tabular-nums text-accent">{formatYuan(row.executed)}</span>
      ),
    },
    {
      key: 'execution_rate',
      label: '执行率',
      sortable: true,
      render: (row) => {
        const ratio = Math.round((row.executed / row.annual_budget) * 100)
        return (
          <div className="flex items-center gap-2 min-w-[140px]">
            <div className="flex-1 h-1.5 rounded-full bg-bg-input overflow-hidden">
              <div
                className={cn('h-full rounded-full transition-all', getProgressColor(ratio))}
                style={{ width: `${Math.min(ratio, 100)}%` }}
              />
            </div>
            <span
              className={cn(
                'text-xs tabular-nums w-10 text-right font-medium',
                ratio > 100 ? 'text-status-failed' : ratio > 80 ? 'text-status-paused' : 'text-slate-400',
              )}
            >
              {ratio}%
            </span>
          </div>
        )
      },
    },
    {
      key: 'remaining',
      label: '剩余',
      align: 'right',
      render: (row) => {
        const remaining = row.annual_budget - row.executed
        return (
          <span
            className={cn(
              'tabular-nums',
              remaining < 0 ? 'text-status-failed' : 'text-slate-300',
            )}
          >
            {formatYuan(remaining)}
          </span>
        )
      },
    },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (row) => {
        const cfg = BUDGET_STATUS_CONFIG[row.status] ?? Object.values(BUDGET_STATUS_CONFIG)[0]
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
        title="预算管理"
        description="年度预算编制、执行监控与变更管理"
        icon={<PiggyBank className="w-5 h-5" />}
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="年度预算"
          value={formatYuan(totalPlanned)}
          icon={<CalendarDays className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="已执行"
          value={formatYuan(totalActual)}
          icon={<CheckCircle2 className="w-5 h-5" />}
          trend="本年累计"
          trendUp
          variant="accent"
        />
        <StatCard
          label="执行率"
          value={`${executionRate.toFixed(1)}%`}
          icon={<Percent className="w-5 h-5" />}
          trend="进度正常"
          trendUp
          variant="primary"
        />
        <StatCard
          label="剩余预算"
          value={formatYuan(remaining)}
          icon={<Wallet className="w-5 h-5" />}
          variant="accent"
        />
      </div>

      {/* 部门预算表 */}
      <Card title="部门预算执行情况">
        <DataTable
          columns={columns}
          data={budgets}
          loading={loading}
          pageSize={10}
          searchable={false}
        />
      </Card>

      {/* 预算变更记录 */}
      <Card title="预算变更记录">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#1e2536]">
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                  部门
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                  变更类型
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase tracking-wider">
                  金额
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                  变更原因
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                  申请人
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                  日期
                </th>
              </tr>
            </thead>
            <tbody>
              {BUDGET_CHANGES.map((change) => {
                const isIncrease = change.type === 'increase'
                const isDecrease = change.type === 'decrease'
                return (
                  <tr
                    key={change.id}
                    className="border-b border-[#1e2536]/50 transition-colors hover:bg-bg-hover"
                  >
                    <td className="px-4 py-3 text-sm text-white font-medium">{change.department}</td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border',
                          isIncrease &&
                            'bg-accent/10 text-accent border-accent/20',
                          isDecrease &&
                            'bg-status-paused/10 text-status-paused border-status-paused/20',
                          !isIncrease &&
                            !isDecrease &&
                            'bg-primary/10 text-primary border-primary/20',
                        )}
                      >
                        {isIncrease && <ArrowUpCircle className="w-3 h-3" />}
                        {isDecrease && <ArrowDownCircle className="w-3 h-3" />}
                        {!isIncrease && !isDecrease && <GitCommit className="w-3 h-3" />}
                        {isIncrease ? '追加' : isDecrease ? '调减' : '调剂'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-right tabular-nums">
                      <span
                        className={cn(
                          'font-medium',
                          isIncrease ? 'text-accent' : isDecrease ? 'text-status-paused' : 'text-primary',
                        )}
                      >
                        {isIncrease ? '+' : isDecrease ? '-' : '±'}
                        {formatYuan(change.amount)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-400 max-w-[280px]">
                      {change.reason}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-300">{change.applicant}</td>
                    <td className="px-4 py-3 text-xs text-slate-500">{change.date}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
