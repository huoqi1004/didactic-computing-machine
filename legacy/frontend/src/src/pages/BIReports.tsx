import { useState, useEffect, useCallback } from 'react'
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
import { apiService } from '../services/api'

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

/* ===================== 后端统计类型 ===================== */

/** 后端 BI 统计响应结构（字段均为可选，兼容降级） */
interface BIStats {
  total_reports?: number
  total_dashboards?: number
  total_kpis?: number
  total_analyses?: number
  kpi_on_track?: number
  kpi_at_risk?: number
  kpi_behind?: number
  by_report_type?: Record<string, number>
  metrics_tracked?: number
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

/* ===================== 数据映射 ===================== */

/** 后端报表类型映射为前端类型 */
function mapReportType(raw: string): ReportType {
  if (raw.includes('finance')) return 'finance'
  if (raw.includes('sales')) return 'sales'
  if (raw.includes('operation')) return 'operations'
  return 'hr'
}

/** 将后端报表数据（snake_case）映射为前端 Report 结构 */
function mapReport(raw: any): Report {
  const rawType = String(raw.type ?? '')
  const rawStatus = String(raw.status ?? 'generated')
  const createdAt = String(raw.created_at ?? '')
  // 截取日期时间前 16 位用于显示
  const dateStr = createdAt ? createdAt.slice(0, 16).replace('T', ' ') : ''

  // 从 data 中尝试提取描述
  const data = raw.data ?? {}
  let description = String(raw.title ?? '—')
  if (data.summary) {
    description = String(data.summary)
  } else if (data.description) {
    description = String(data.description)
  }

  return {
    id: String(raw.report_id ?? raw.id ?? ''),
    name: String(raw.title ?? raw.name ?? '—'),
    type: mapReportType(rawType),
    updated_at: dateStr || '—',
    status: rawStatus === 'generating' ? 'generating' : 'generated',
    description,
  }
}

/* ===================== 主组件 ===================== */

export default function BIReports() {
  const [reports, setReports] = useState<Report[]>([])
  const [stats, setStats] = useState<BIStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [reportList, biStats] = await Promise.all([
      apiService.getBIReports(),
      apiService.getBIStats(),
    ])
    setReports((Array.isArray(reportList) ? reportList : []).map(mapReport))
    setStats(biStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

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
          label="报表总数"
          value={stats?.total_reports ?? reports.length}
          icon={<FileText className="w-5 h-5" />}
          trend="持续更新"
          trendUp
          variant="primary"
        />
        <StatCard
          label="仪表盘数"
          value={stats?.total_dashboards ?? 0}
          icon={<BarChart3 className="w-5 h-5" />}
          trend="可视化监控"
          trendUp
          variant="accent"
        />
        <StatCard
          label="KPI 总数"
          value={stats?.total_kpis ?? 0}
          icon={<Percent className="w-5 h-5" />}
          trend="全量跟踪"
          trendUp
          variant="primary"
        />
        <StatCard
          label="达标 KPI"
          value={stats?.kpi_on_track ?? 0}
          icon={<TrendingUp className="w-5 h-5" />}
          trend="绩效良好"
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
          {loading ? (
            <div className="col-span-full text-center py-12">
              <Loader2 className="w-6 h-6 text-primary animate-spin mx-auto" />
              <p className="text-sm text-slate-500 mt-2">加载中...</p>
            </div>
          ) : reports.length === 0 ? (
            <div className="col-span-full text-center py-12">
              <p className="text-sm text-slate-500">暂无报表数据</p>
            </div>
          ) : (
          reports.map((report) => {
            const Icon = REPORT_TYPE_ICON[report.type]
            const typeCfg = REPORT_TYPE_CONFIG[report.type] ?? Object.values(REPORT_TYPE_CONFIG)[0]
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
          })
          )}
        </div>
      </Card>
    </div>
  )
}
