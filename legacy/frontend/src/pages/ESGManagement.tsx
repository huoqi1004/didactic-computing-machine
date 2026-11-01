import { useState, useEffect, useCallback } from 'react'
import {
  Leaf,
  Award,
  Globe,
  Users,
  ShieldCheck,
  Factory,
  Droplets,
  Recycle,
  Smile,
  HeartHandshake,
  GraduationCap,
  AlertOctagon,
  Scale,
  ClipboardCheck,
  Users2,
  FileClock,
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

type ESGDimension = 'E' | 'S' | 'G'
type ProjectStatus = 'in_progress' | 'planning' | 'completed' | 'paused'

interface ESGProject {
  id: string
  name: string
  dimension: ESGDimension
  owner: string
  progress: number
  status: ProjectStatus
}

interface ESGMetric {
  label: string
  value: string
  unit: string
  trend: string
  trendUp: boolean
  icon: LucideIcon
}

/* ===================== 配置 ===================== */

const DIMENSION_CONFIG: Record<
  ESGDimension,
  { label: string; variant: 'success' | 'primary' | 'warning' }
> = {
  E: { label: '环境', variant: 'success' },
  S: { label: '社会', variant: 'primary' },
  G: { label: '治理', variant: 'warning' },
}

const PROJECT_STATUS_CONFIG: Record<
  ProjectStatus,
  { label: string; variant: 'primary' | 'info' | 'success' | 'warning' }
> = {
  in_progress: { label: '进行中', variant: 'primary' },
  planning: { label: '规划中', variant: 'info' },
  completed: { label: '已完成', variant: 'success' },
  paused: { label: '已暂停', variant: 'warning' },
}

/* ===================== 后端类型 ===================== */

/** 后端 ESG 评分响应结构 */
interface ESGScore {
  total?: number
  rating?: string
  by_category?: Record<string, { score?: number; count?: number; avg_achievement?: number }>
  weights?: Record<string, number>
}

/** 后端 ESG 统计响应结构 */
interface ESGStats {
  total_indicators?: number
  by_category?: Record<string, number>
  total_reports?: number
  total_carbon_records?: number
  total_carbon_emission?: number
  total_social_activities?: number
  by_activity_status?: Record<string, number>
}

/* ===================== 维度图标映射 ===================== */

/** 各维度默认图标（按索引顺序） */
const DIMENSION_ICONS: Record<ESGDimension, LucideIcon[]> = {
  E: [Factory, Globe, Droplets, Recycle],
  S: [Smile, HeartHandshake, GraduationCap, AlertOctagon],
  G: [Scale, ClipboardCheck, Users2, FileClock],
}

/* ===================== 数据映射 ===================== */

/** 将后端 ESG 指标映射为前端 ESGMetric */
function mapIndicatorToMetric(raw: any, dimension: ESGDimension, index: number): ESGMetric {
  const current = Number(raw.current) || 0
  const target = Number(raw.target) || 0
  const direction = String(raw.direction ?? 'higher_better')
  const achievement = Number(raw.achievement_rate) || 0

  // 趋势判断：higher_better 时 current >= target 为好；lower_better 时 current <= target 为好
  let trendUp: boolean
  if (direction === 'lower_better') {
    trendUp = current <= target
  } else {
    trendUp = current >= target
  }

  const trendPct = target > 0 ? Math.abs(((current - target) / target) * 100).toFixed(1) : '0.0'

  const icons = DIMENSION_ICONS[dimension]
  const icon = icons[index % icons.length]

  // 格式化 value 显示
  const valueStr = current % 1 === 0 ? current.toFixed(0) : current.toFixed(2)

  return {
    label: String(raw.name ?? '—'),
    value: valueStr,
    unit: String(raw.unit ?? ''),
    trend: `${trendPct}%`,
    trendUp,
    icon,
  }
}

/** 将后端 ESG 指标映射为前端 ESGProject */
function mapIndicatorToProject(raw: any): ESGProject {
  const achievement = Number(raw.achievement_rate) || 0
  const progress = Math.min(Math.round(achievement * 100), 100)
  let status: ProjectStatus = 'in_progress'
  if (progress >= 100) status = 'completed'
  else if (progress < 10) status = 'planning'

  return {
    id: String(raw.indicator_id ?? raw.id ?? ''),
    name: String(raw.name ?? '—'),
    dimension: String(raw.category ?? 'E') as ESGDimension,
    owner: String(raw.owner ?? '—'),
    progress,
    status,
  }
}

/* ===================== 主组件 ===================== */

export default function ESGManagement() {
  const [indicators, setIndicators] = useState<any[]>([])
  const [score, setScore] = useState<ESGScore | null>(null)
  const [stats, setStats] = useState<ESGStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [indicatorList, esgScore, esgStats] = await Promise.all([
      apiService.getESGIndicators(),
      apiService.getESGScore(),
      apiService.getESGStats(),
    ])
    setIndicators(Array.isArray(indicatorList) ? indicatorList : [])
    setScore(esgScore ?? null)
    setStats(esgStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // 按维度分组指标
  const envIndicators = indicators.filter((i) => i.category === 'E')
  const socialIndicators = indicators.filter((i) => i.category === 'S')
  const govIndicators = indicators.filter((i) => i.category === 'G')

  const environmentMetrics: ESGMetric[] = envIndicators.map((raw, idx) =>
    mapIndicatorToMetric(raw, 'E', idx),
  )
  const socialMetrics: ESGMetric[] = socialIndicators.map((raw, idx) =>
    mapIndicatorToMetric(raw, 'S', idx),
  )
  const governanceMetrics: ESGMetric[] = govIndicators.map((raw, idx) =>
    mapIndicatorToMetric(raw, 'G', idx),
  )

  const projects: ESGProject[] = indicators.map(mapIndicatorToProject)
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="ESG 管理"
        description="环境、社会与治理可持续发展管理平台"
        icon={<Leaf className="w-5 h-5" />}
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="ESG 总分"
          value={score?.total ?? '—'}
          icon={<Award className="w-5 h-5" />}
          trend={score?.rating ? `评级 ${score.rating}` : '待评估'}
          trendUp
          variant="primary"
        />
        <StatCard
          label="环境 (E) 得分"
          value={score?.by_category?.E?.score ?? '—'}
          icon={<Leaf className="w-5 h-5" />}
          trend={`${envIndicators.length} 项指标`}
          trendUp
          variant="accent"
        />
        <StatCard
          label="社会 (S) 得分"
          value={score?.by_category?.S?.score ?? '—'}
          icon={<Users className="w-5 h-5" />}
          trend={`${socialIndicators.length} 项指标`}
          trendUp
          variant="primary"
        />
        <StatCard
          label="治理 (G) 得分"
          value={score?.by_category?.G?.score ?? '—'}
          icon={<ShieldCheck className="w-5 h-5" />}
          trend={`${govIndicators.length} 项指标`}
          trendUp
          variant="warning"
        />
      </div>

      {/* E - 环境维度 */}
      {loading ? (
        <div className="text-center py-12">
          <Loader2 className="w-6 h-6 text-primary animate-spin mx-auto" />
          <p className="text-sm text-slate-500 mt-2">加载中...</p>
        </div>
      ) : (
      <>
      <DimensionSection
        title="环境维度 (E)"
        subtitle="气候变化、能源管理、资源利用与生态保护"
        icon={Leaf}
        accentColor="text-accent"
        bgColor="bg-accent/10"
        metrics={environmentMetrics}
      />

      {/* S - 社会维度 */}
      <DimensionSection
        title="社会维度 (S)"
        subtitle="员工权益、社区贡献、人才培养与安全生产"
        icon={Users}
        accentColor="text-primary"
        bgColor="bg-primary/10"
        metrics={socialMetrics}
      />

      {/* G - 治理维度 */}
      <DimensionSection
        title="治理维度 (G)"
        subtitle="合规管理、审计监督、董事会治理与信息披露"
        icon={ShieldCheck}
        accentColor="text-status-paused"
        bgColor="bg-status-paused/10"
        metrics={governanceMetrics}
      />
      </>
      )}

      {/* ESG 改进项目列表 */}
      <Card title="ESG 改进项目">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {loading ? (
            <div className="col-span-full text-center py-12">
              <Loader2 className="w-6 h-6 text-primary animate-spin mx-auto" />
              <p className="text-sm text-slate-500 mt-2">加载中...</p>
            </div>
          ) : projects.length === 0 ? (
            <div className="col-span-full text-center py-12">
              <p className="text-sm text-slate-500">暂无 ESG 改进项目</p>
            </div>
          ) : (
          projects.map((project) => {
            const dimCfg = DIMENSION_CONFIG[project.dimension] ?? Object.values(DIMENSION_CONFIG)[0]
            const statusCfg = PROJECT_STATUS_CONFIG[project.status] ?? Object.values(PROJECT_STATUS_CONFIG)[0]
            const progressColor =
              project.progress === 100
                ? 'bg-accent'
                : project.status === 'paused'
                  ? 'bg-status-paused'
                  : 'bg-primary'
            return (
              <div
                key={project.id}
                className="p-4 rounded-xl bg-bg-input border border-[#1e2536] hover:border-primary/40 transition-all"
              >
                <div className="flex items-start justify-between mb-3">
                  <h4 className="text-sm font-semibold text-white flex-1 pr-2">{project.name}</h4>
                  <StatusBadge variant={dimCfg.variant}>{dimCfg.label}</StatusBadge>
                </div>

                {/* 进度条 */}
                <div className="mb-3">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-slate-500">进度</span>
                    <span className="text-xs text-slate-400 tabular-nums">{project.progress}%</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-bg-hover overflow-hidden">
                    <div
                      className={cn('h-full rounded-full transition-all', progressColor)}
                      style={{ width: `${project.progress}%` }}
                    />
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-500">负责人：{project.owner}</span>
                  <StatusBadge variant={statusCfg.variant} dot>
                    {statusCfg.label}
                  </StatusBadge>
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

/* ===================== 维度区块组件 ===================== */

function DimensionSection({
  title,
  subtitle,
  icon: Icon,
  accentColor,
  bgColor,
  metrics,
}: {
  title: string
  subtitle: string
  icon: LucideIcon
  accentColor: string
  bgColor: string
  metrics: ESGMetric[]
}) {
  return (
    <Card>
      <div className="flex items-center gap-3 mb-5">
        <div
          className={cn(
            'w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
            bgColor,
            accentColor,
          )}
        >
          <Icon className="w-5 h-5" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-white">{title}</h3>
          <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {metrics.map((metric) => {
          const MetricIcon = metric.icon
          return (
            <div
              key={metric.label}
              className="p-4 rounded-xl bg-bg-input border border-[#1e2536]"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-slate-500 font-medium">{metric.label}</span>
                <MetricIcon className={cn('w-4 h-4', accentColor)} />
              </div>
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-bold text-white tabular-nums">{metric.value}</span>
                <span className="text-xs text-slate-500">{metric.unit}</span>
              </div>
              <div
                className={cn(
                  'text-xs mt-1',
                  metric.trendUp ? 'text-accent' : 'text-status-paused',
                )}
              >
                {metric.trendUp ? '↑' : '↓'} {metric.trend}
                <span className="text-slate-600 ml-1">vs 上年</span>
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
