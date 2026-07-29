import {
  BarChart3,
  DollarSign,
  TrendingUp,
  Percent,
  FileText,
  Eye,
  Clock,
  Loader2,
  type LucideIcon,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  StatusBadge,
  StatCard,
} from '../components/ui'
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type ReportType = 'finance' | 'sales' | 'operations' | 'hr'
type ReportStatus = 'generated' | 'generating'

interface Report {
  id: string
  name: string
  type: ReportType
  updated_at: string
  status: ReportStatus
  description: string
}

/* ===================== 配置 ===================== */

const REPORT_TYPE_CONFIG: Record<
  ReportType,
  { label: string; variant: 'primary' | 'accent' | 'info' | 'warning' }
> = {
  finance: { label: '财务', variant: 'primary' },
  sales: { label: '销售', variant: 'accent' },
  operations: { label: '运营', variant: 'info' },
  hr: { label: 'HR', variant: 'warning' },
}

const REPORT_TYPE_ICON: Record<ReportType, LucideIcon> = {
  finance: DollarSign,
  sales: TrendingUp,
  operations: BarChart3,
  hr: FileText,
}

/* ===================== Mock 数据 ===================== */

// 营收趋势（近 6 个月，单位：万元）
const REVENUE_TREND = [
  { month: '2月', value: 1420 },
  { month: '3月', value: 1580 },
  { month: '4月', value: 1350 },
  { month: '5月', value: 1680 },
  { month: '6月', value: 1750 },
  { month: '7月', value: 1856 },
]

// 部门营收占比
const DEPT_REVENUE = [
  { name: '产品事业部', value: 720, percent: 38.8 },
  { name: '解决方案部', value: 580, percent: 31.2 },
  { name: '客户服务部', value: 320, percent: 17.2 },
  { name: '研发中心', value: 156, percent: 8.4 },
  { name: '其他', value: 80, percent: 4.4 },
]

// 报表列表
const REPORTS: Report[] = [
  {
    id: '1',
    name: '季度财务分析报告',
    type: 'finance',
    updated_at: '2026-07-28 09:30',
    status: 'generated',
    description: '本季度营收、成本、利润全维度分析',
  },
  {
    id: '2',
    name: '销售业绩月报',
    type: 'sales',
    updated_at: '2026-07-28 08:15',
    status: 'generated',
    description: '各区域、产品线销售达成情况',
  },
  {
    id: '3',
    name: '供应链运营周报',
    type: 'operations',
    updated_at: '2026-07-28 07:00',
    status: 'generated',
    description: '库存周转、交付及时率、供应商绩效',
  },
  {
    id: '4',
    name: '人力成本分析报告',
    type: 'hr',
    updated_at: '生成中...',
    status: 'generating',
    description: '各部门人力成本与人效分析',
  },
  {
    id: '5',
    name: '产品利润率分析',
    type: 'finance',
    updated_at: '2026-07-27 18:20',
    status: 'generated',
    description: '各产品线毛利率与净利润对比',
  },
  {
    id: '6',
    name: '客户增长趋势报告',
    type: 'sales',
    updated_at: '2026-07-27 16:45',
    status: 'generated',
    description: '新增客户、留存率、客户生命周期价值',
  },
  {
    id: '7',
    name: '生产效率月报',
    type: 'operations',
    updated_at: '2026-07-27 14:10',
    status: 'generated',
    description: 'OEE、设备稼动率、良品率综合分析',
  },
  {
    id: '8',
    name: '员工流动率报告',
    type: 'hr',
    updated_at: '2026-07-26 17:30',
    status: 'generated',
    description: '离职率、入职率与人才结构分析',
  },
]

/* ===================== 主组件 ===================== */

export default function BIReports() {
  const maxRevenue = Math.max(...REVENUE_TREND.map((d) => d.value))
  const maxDeptRevenue = Math.max(...DEPT_REVENUE.map((d) => d.value))

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="BI 报表"
        description="商业智能报表中心，数据驱动经营决策"
        icon={<BarChart3 className="w-5 h-5" />}
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="本季营收"
          value="¥18,560,000"
          icon={<DollarSign className="w-5 h-5" />}
          trend="12.3%"
          trendUp
          variant="primary"
        />
        <StatCard
          label="净利润"
          value="¥3,286,000"
          icon={<TrendingUp className="w-5 h-5" />}
          trend="8.5%"
          trendUp
          variant="accent"
        />
        <StatCard
          label="利润率"
          value="17.7%"
          icon={<Percent className="w-5 h-5" />}
          trend="0.6%"
          trendUp
          variant="primary"
        />
        <StatCard
          label="同比增长"
          value="12.3%"
          icon={<TrendingUp className="w-5 h-5" />}
          trend="超预期"
          trendUp
          variant="accent"
        />
      </div>

      {/* 图表区域 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 营收趋势 */}
        <Card title="营收趋势（近 6 个月）">
          <div className="flex items-end justify-between gap-3 h-56 pt-4">
            {REVENUE_TREND.map((item) => {
              const heightRatio = (item.value / maxRevenue) * 100
              const isLatest = item.month === '7月'
              return (
                <div key={item.month} className="flex-1 flex flex-col items-center gap-2 group">
                  <span className="text-xs text-slate-400 tabular-nums opacity-0 group-hover:opacity-100 transition-opacity">
                    {item.value}万
                  </span>
                  <div className="w-full flex items-end justify-center h-full">
                    <div
                      className={cn(
                        'w-full max-w-[40px] rounded-t-md transition-all duration-300',
                        isLatest ? 'bg-accent' : 'bg-primary/60 hover:bg-primary',
                      )}
                      style={{ height: `${heightRatio}%` }}
                    />
                  </div>
                  <span className="text-xs text-slate-500">{item.month}</span>
                </div>
              )
            })}
          </div>
          <div className="flex items-center justify-between mt-4 pt-3 border-t border-[#1e2536]">
            <span className="text-xs text-slate-500">单位：万元</span>
            <span className="text-xs text-accent flex items-center gap-1">
              <TrendingUp className="w-3 h-3" />
              环比增长 6.1%
            </span>
          </div>
        </Card>

        {/* 部门营收占比 */}
        <Card title="部门营收占比">
          <div className="space-y-4 pt-2">
            {DEPT_REVENUE.map((dept) => {
              const widthRatio = (dept.value / maxDeptRevenue) * 100
              return (
                <div key={dept.name}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-sm text-slate-300">{dept.name}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-500 tabular-nums">
                        ¥{dept.value}万
                      </span>
                      <span className="text-xs text-accent font-medium tabular-nums w-12 text-right">
                        {dept.percent}%
                      </span>
                    </div>
                  </div>
                  <div className="h-2 rounded-full bg-bg-input overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-primary to-accent transition-all duration-500"
                      style={{ width: `${widthRatio}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </Card>
      </div>

      {/* 报表列表 */}
      <Card title="报表列表">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {REPORTS.map((report) => {
            const Icon = REPORT_TYPE_ICON[report.type]
            const typeCfg = REPORT_TYPE_CONFIG[report.type]
            return (
              <div
                key={report.id}
                className="p-4 rounded-xl bg-bg-input border border-[#1e2536] hover:border-primary/40 transition-all group cursor-pointer"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center text-primary shrink-0">
                    <Icon className="w-5 h-5" />
                  </div>
                  <StatusBadge
                    variant={report.status === 'generated' ? 'success' : 'primary'}
                    dot
                  >
                    {report.status === 'generated' ? '已生成' : '生成中'}
                  </StatusBadge>
                </div>

                <h4 className="text-sm font-semibold text-white mb-1 group-hover:text-primary transition-colors">
                  {report.name}
                </h4>
                <p className="text-xs text-slate-500 mb-3 line-clamp-2">{report.description}</p>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <StatusBadge variant={typeCfg.variant}>{typeCfg.label}</StatusBadge>
                    <span className="flex items-center gap-1 text-xs text-slate-600">
                      {report.status === 'generating' ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <Clock className="w-3 h-3" />
                      )}
                      {report.updated_at}
                    </span>
                  </div>
                  {report.status === 'generated' && (
                    <button className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors text-xs">
                      <Eye className="w-3.5 h-3.5" />
                      查看
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </Card>
    </div>
  )
}
