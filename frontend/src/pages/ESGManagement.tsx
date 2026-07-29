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

/* ===================== 指标数据 ===================== */

const ENVIRONMENT_METRICS: ESGMetric[] = [
  { label: '碳排放量', value: '12,580', unit: 'tCO₂e', trend: '5.2%', trendUp: false, icon: Factory },
  { label: '能耗总量', value: '8,640', unit: 'MWh', trend: '3.1%', trendUp: false, icon: Globe },
  { label: '用水总量', value: '45,200', unit: 'm³', trend: '2.8%', trendUp: false, icon: Droplets },
  { label: '废气回收率', value: '92.5', unit: '%', trend: '1.5%', trendUp: true, icon: Recycle },
]

const SOCIAL_METRICS: ESGMetric[] = [
  { label: '员工满意度', value: '88.6', unit: '%', trend: '2.3%', trendUp: true, icon: Smile },
  { label: '社区投入', value: '1,280', unit: '万元', trend: '8.5%', trendUp: true, icon: HeartHandshake },
  { label: '培训覆盖率', value: '95.2', unit: '%', trend: '3.0%', trendUp: true, icon: GraduationCap },
  { label: '安全事故率', value: '0.08', unit: '‰', trend: '0.02%', trendUp: false, icon: AlertOctagon },
]

const GOVERNANCE_METRICS: ESGMetric[] = [
  { label: '合规率', value: '99.8', unit: '%', trend: '0.3%', trendUp: true, icon: Scale },
  { label: '审计通过率', value: '100', unit: '%', trend: '维持', trendUp: true, icon: ClipboardCheck },
  { label: '董事会多样性', value: '37.5', unit: '%', trend: '5.0%', trendUp: true, icon: Users2 },
  { label: '信息披露及时率', value: '98.5', unit: '%', trend: '1.2%', trendUp: true, icon: FileClock },
]

/* ===================== Mock 改进项目 ===================== */

const ESG_PROJECTS: ESGProject[] = [
  {
    id: '1',
    name: '屋顶光伏发电项目',
    dimension: 'E',
    owner: '黄丽',
    progress: 72,
    status: 'in_progress',
  },
  {
    id: '2',
    name: '碳中和路线图制定',
    dimension: 'E',
    owner: '孙伟',
    progress: 45,
    status: 'in_progress',
  },
  {
    id: '3',
    name: '废水循环利用系统',
    dimension: 'E',
    owner: '李华',
    progress: 100,
    status: 'completed',
  },
  {
    id: '4',
    name: '员工心理健康关怀计划',
    dimension: 'S',
    owner: '林雪',
    progress: 60,
    status: 'in_progress',
  },
  {
    id: '5',
    name: '乡村教育帮扶项目',
    dimension: 'S',
    owner: '王芳',
    progress: 35,
    status: 'in_progress',
  },
  {
    id: '6',
    name: '供应商 ESG 审核体系',
    dimension: 'G',
    owner: '郑磊',
    progress: 20,
    status: 'planning',
  },
  {
    id: '7',
    name: '反腐败合规培训',
    dimension: 'G',
    owner: '刘洋',
    progress: 88,
    status: 'in_progress',
  },
  {
    id: '8',
    name: 'ESG 信息披露平台建设',
    dimension: 'G',
    owner: '陈强',
    progress: 50,
    status: 'in_progress',
  },
]

/* ===================== 主组件 ===================== */

export default function ESGManagement() {
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
          value="82.5"
          icon={<Award className="w-5 h-5" />}
          trend="行业领先"
          trendUp
          variant="primary"
        />
        <StatCard
          label="环境 (E) 得分"
          value="85"
          icon={<Leaf className="w-5 h-5" />}
          trend="2.0"
          trendUp
          variant="accent"
        />
        <StatCard
          label="社会 (S) 得分"
          value="80"
          icon={<Users className="w-5 h-5" />}
          trend="1.5"
          trendUp
          variant="primary"
        />
        <StatCard
          label="治理 (G) 得分"
          value="83"
          icon={<ShieldCheck className="w-5 h-5" />}
          trend="1.0"
          trendUp
          variant="warning"
        />
      </div>

      {/* E - 环境维度 */}
      <DimensionSection
        title="环境维度 (E)"
        subtitle="气候变化、能源管理、资源利用与生态保护"
        icon={Leaf}
        accentColor="text-accent"
        bgColor="bg-accent/10"
        metrics={ENVIRONMENT_METRICS}
      />

      {/* S - 社会维度 */}
      <DimensionSection
        title="社会维度 (S)"
        subtitle="员工权益、社区贡献、人才培养与安全生产"
        icon={Users}
        accentColor="text-primary"
        bgColor="bg-primary/10"
        metrics={SOCIAL_METRICS}
      />

      {/* G - 治理维度 */}
      <DimensionSection
        title="治理维度 (G)"
        subtitle="合规管理、审计监督、董事会治理与信息披露"
        icon={ShieldCheck}
        accentColor="text-status-paused"
        bgColor="bg-status-paused/10"
        metrics={GOVERNANCE_METRICS}
      />

      {/* ESG 改进项目列表 */}
      <Card title="ESG 改进项目">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {ESG_PROJECTS.map((project) => {
            const dimCfg = DIMENSION_CONFIG[project.dimension]
            const statusCfg = PROJECT_STATUS_CONFIG[project.status]
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
          })}
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
