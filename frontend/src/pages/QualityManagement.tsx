import {
  BadgeCheck,
  Percent,
  Bug,
  ClipboardCheck,
  Wrench,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownRight,
  Eye,
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

/* ===================== 类型定义 ===================== */

type DefectSource = 'incoming' | 'process' | 'customer'
type DefectSeverity = 'critical' | 'major' | 'minor' | 'cosmetic'
type DefectStatus = 'pending' | 'processing' | 'closed'

interface Defect {
  id: string
  code: string
  description: string
  source: DefectSource
  severity: DefectSeverity
  status: DefectStatus
  owner: string
  found_date: string
}

/* ===================== 状态配置 ===================== */

const SOURCE_CONFIG: Record<DefectSource, { label: string; variant: 'info' | 'primary' | 'warning' }> = {
  incoming: { label: '来料', variant: 'info' },
  process: { label: '过程', variant: 'primary' },
  customer: { label: '客诉', variant: 'warning' },
}

const SEVERITY_CONFIG: Record<
  DefectSeverity,
  { label: string; variant: 'danger' | 'warning' | 'info' | 'neutral' }
> = {
  critical: { label: '致命', variant: 'danger' },
  major: { label: '严重', variant: 'warning' },
  minor: { label: '一般', variant: 'info' },
  cosmetic: { label: '轻微', variant: 'neutral' },
}

const DEFECT_STATUS_CONFIG: Record<
  DefectStatus,
  { label: string; variant: 'warning' | 'primary' | 'success' }
> = {
  pending: { label: '待处理', variant: 'warning' },
  processing: { label: '处理中', variant: 'primary' },
  closed: { label: '已关闭', variant: 'success' },
}

/* ===================== 质量指标数据 ===================== */

interface QualityMetric {
  label: string
  value: string
  trend: string
  trendUp: boolean
  icon: typeof TrendingUp
}

const QUALITY_METRICS: QualityMetric[] = [
  { label: '来料合格率', value: '99.2%', trend: '0.3%', trendUp: true, icon: TrendingUp },
  { label: '过程合格率', value: '98.8%', trend: '0.5%', trendUp: true, icon: TrendingUp },
  { label: '出货合格率', value: '99.5%', trend: '0.1%', trendUp: true, icon: TrendingUp },
  { label: '客户投诉率', value: '0.12%', trend: '0.04%', trendUp: false, icon: TrendingDown },
]

/* ===================== Mock 缺陷数据 ===================== */

const DEFECTS: Defect[] = [
  {
    id: '1',
    code: 'DEF-2026-001',
    description: 'PCB 板焊接虚焊，批次 BX-0721',
    source: 'incoming',
    severity: 'major',
    status: 'processing',
    owner: '张工',
    found_date: '2026-07-25',
  },
  {
    id: '2',
    code: 'DEF-2026-002',
    description: '外壳表面划痕，色差超公差',
    source: 'process',
    severity: 'cosmetic',
    status: 'pending',
    owner: '李工',
    found_date: '2026-07-26',
  },
  {
    id: '3',
    code: 'DEF-2026-003',
    description: '电源模块输出电压波动超标',
    source: 'incoming',
    severity: 'critical',
    status: 'processing',
    owner: '王工',
    found_date: '2026-07-24',
  },
  {
    id: '4',
    code: 'DEF-2026-004',
    description: '客户反馈设备偶发重启',
    source: 'customer',
    severity: 'major',
    status: 'processing',
    owner: '赵工',
    found_date: '2026-07-22',
  },
  {
    id: '5',
    code: 'DEF-2026-005',
    description: '密封圈尺寸偏差，导致防水测试不合格',
    source: 'incoming',
    severity: 'minor',
    status: 'closed',
    owner: '孙工',
    found_date: '2026-07-20',
  },
  {
    id: '6',
    code: 'DEF-2026-006',
    description: '装配工序螺丝扭矩不足',
    source: 'process',
    severity: 'minor',
    status: 'closed',
    owner: '周工',
    found_date: '2026-07-19',
  },
  {
    id: '7',
    code: 'DEF-2026-007',
    description: '客户投诉包装破损，产品外观受损',
    source: 'customer',
    severity: 'cosmetic',
    status: 'pending',
    owner: '吴工',
    found_date: '2026-07-27',
  },
  {
    id: '8',
    code: 'DEF-2026-008',
    description: '芯片批次热稳定性不达标',
    source: 'incoming',
    severity: 'critical',
    status: 'pending',
    owner: '郑工',
    found_date: '2026-07-28',
  },
  {
    id: '9',
    code: 'DEF-2026-009',
    description: '软件界面响应延迟超 2 秒',
    source: 'customer',
    severity: 'major',
    status: 'closed',
    owner: '钱工',
    found_date: '2026-07-18',
  },
  {
    id: '10',
    code: 'DEF-2026-010',
    description: '标签印刷模糊，条码无法识别',
    source: 'process',
    severity: 'cosmetic',
    status: 'processing',
    owner: '冯工',
    found_date: '2026-07-26',
  },
  {
    id: '11',
    code: 'DEF-2026-011',
    description: '电机轴承异响，转速不稳',
    source: 'incoming',
    severity: 'major',
    status: 'pending',
    owner: '陈工',
    found_date: '2026-07-27',
  },
  {
    id: '12',
    code: 'DEF-2026-012',
    description: '出厂检测漏检，绝缘电阻未达标',
    source: 'process',
    severity: 'critical',
    status: 'processing',
    owner: '褚工',
    found_date: '2026-07-25',
  },
]

/* ===================== 主组件 ===================== */

export default function QualityManagement() {
  const columns: Column<Defect>[] = [
    {
      key: 'code',
      label: '缺陷编号',
      sortable: true,
      render: (row) => <span className="font-mono text-xs text-primary">{row.code}</span>,
    },
    {
      key: 'description',
      label: '缺陷描述',
      render: (row) => (
        <span className="text-slate-300 max-w-[280px] truncate block" title={row.description}>
          {row.description}
        </span>
      ),
    },
    {
      key: 'source',
      label: '来源',
      sortable: true,
      render: (row) => {
        const cfg = SOURCE_CONFIG[row.source]
        return <StatusBadge variant={cfg.variant}>{cfg.label}</StatusBadge>
      },
    },
    {
      key: 'severity',
      label: '严重等级',
      sortable: true,
      render: (row) => {
        const cfg = SEVERITY_CONFIG[row.severity]
        return <StatusBadge variant={cfg.variant} dot>{cfg.label}</StatusBadge>
      },
    },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (row) => {
        const cfg = DEFECT_STATUS_CONFIG[row.status]
        return <StatusBadge variant={cfg.variant} dot>{cfg.label}</StatusBadge>
      },
    },
    {
      key: 'owner',
      label: '负责人',
      render: (row) => <span className="text-slate-300">{row.owner}</span>,
    },
    {
      key: 'found_date',
      label: '发现日期',
      sortable: true,
      render: (row) => <span className="text-slate-400 text-xs">{row.found_date}</span>,
    },
    {
      key: 'action',
      label: '操作',
      align: 'center',
      render: () => (
        <button className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors text-xs">
          <Eye className="w-3.5 h-3.5" />
          处理
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="质量管理"
        description="质量指标监控、缺陷追踪与 CAPA 闭环管理"
        icon={<BadgeCheck className="w-5 h-5" />}
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="合格率"
          value="98.6%"
          icon={<Percent className="w-5 h-5" />}
          trend="0.4%"
          trendUp
          variant="accent"
        />
        <StatCard
          label="待处理缺陷"
          value="8"
          icon={<Bug className="w-5 h-5" />}
          trend="较上周 +2"
          trendUp={false}
          variant="warning"
        />
        <StatCard
          label="本月审计"
          value="3"
          icon={<ClipboardCheck className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="CAPA 数量"
          value="5"
          icon={<Wrench className="w-5 h-5" />}
          trend="待闭环 2"
          trendUp={false}
          variant="danger"
        />
      </div>

      {/* 质量指标卡片 */}
      <Card title="质量指标概览">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {QUALITY_METRICS.map((metric) => {
            const Icon = metric.icon
            return (
              <div
                key={metric.label}
                className="p-4 rounded-xl bg-bg-input border border-[#1e2536]"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-slate-500 font-medium">{metric.label}</span>
                  <Icon
                    className={cn(
                      'w-4 h-4',
                      metric.trendUp ? 'text-accent' : 'text-status-paused',
                    )}
                  />
                </div>
                <div className="text-2xl font-bold text-white tabular-nums">{metric.value}</div>
                <div
                  className={cn(
                    'flex items-center gap-1 text-xs mt-1',
                    metric.trendUp ? 'text-accent' : 'text-status-paused',
                  )}
                >
                  {metric.trendUp ? (
                    <ArrowUpRight className="w-3 h-3" />
                  ) : (
                    <ArrowDownRight className="w-3 h-3" />
                  )}
                  <span>{metric.trend}</span>
                  <span className="text-slate-600">vs 上月</span>
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      {/* 缺陷列表 */}
      <Card title="缺陷列表">
        <DataTable
          columns={columns}
          data={DEFECTS}
          pageSize={10}
          searchKeys={['code', 'description']}
          searchPlaceholder="搜索缺陷编号/描述..."
        />
      </Card>
    </div>
  )
}
