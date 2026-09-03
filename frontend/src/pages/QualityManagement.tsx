import { useState, useEffect, useCallback } from 'react'
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
import { apiService } from '../services/api'
import Can from '../components/Can'

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

/* ===================== 后端统计类型 ===================== */

/** 后端质量统计响应结构（字段均为可选，兼容降级） */
interface QualityStats {
  total_inspections?: number
  completed?: number
  pending?: number
  passed?: number
  failed?: number
  pass_rate?: number
  defect_rate?: number
  by_type?: Record<string, { total: number; passed: number; failed: number }>
  total_standards?: number
  active_standards?: number
  total_improvements?: number
  improvements_by_status?: Record<string, number>
  total_defects?: number
  defect_rate_threshold?: number
}

/* ===================== 数据映射 ===================== */

/** 后端检验类型映射为前端缺陷来源 */
function mapInspectionTypeToSource(raw: string): DefectSource {
  if (raw === 'process') return 'process'
  if (raw === 'finished') return 'customer'
  return 'incoming'
}

/** 从缺陷列表中提取最高严重等级 */
function extractSeverity(defects: any[], result: string): DefectSeverity {
  if (defects && defects.length > 0) {
    for (const d of defects) {
      const sev = String(d.severity ?? '').toLowerCase()
      if (sev === 'critical') return 'critical'
    }
    for (const d of defects) {
      const sev = String(d.severity ?? '').toLowerCase()
      if (sev === 'major') return 'major'
    }
    for (const d of defects) {
      const sev = String(d.severity ?? '').toLowerCase()
      if (sev === 'minor') return 'minor'
    }
    return 'cosmetic'
  }
  // 无缺陷时根据结果推断
  return result === 'failed' ? 'major' : 'cosmetic'
}

/** 后端检验状态映射为前端缺陷状态 */
function mapInspectionStatus(raw: string, result: string): DefectStatus {
  if (raw === 'completed') {
    return result === 'failed' ? 'processing' : 'closed'
  }
  return 'pending'
}

/** 将后端质检记录映射为前端 Defect 结构 */
function mapInspectionToDefect(raw: any): Defect {
  const inspectionType = String(raw.inspection_type ?? 'incoming')
  const result = String(raw.result ?? 'pending')
  const defects = Array.isArray(raw.defects) ? raw.defects : []
  const createdAt = String(raw.created_at ?? '')
  const dateStr = createdAt ? createdAt.slice(0, 10) : ''

  // 构建描述：产品 + 批次 + 备注
  const parts = [
    String(raw.product ?? ''),
    raw.batch ? `批次 ${raw.batch}` : '',
    String(raw.notes ?? ''),
  ].filter(Boolean)

  return {
    id: String(raw.inspection_id ?? raw.id ?? ''),
    code: String(raw.inspection_id ?? raw.id ?? ''),
    description: parts.join('，') || '—',
    source: mapInspectionTypeToSource(inspectionType),
    severity: extractSeverity(defects, result),
    status: mapInspectionStatus(String(raw.status ?? 'pending'), result),
    owner: String(raw.inspector ?? '—'),
    found_date: dateStr,
  }
}

/* ===================== 主组件 ===================== */

export default function QualityManagement() {
  const [defects, setDefects] = useState<Defect[]>([])
  const [stats, setStats] = useState<QualityStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [inspectionList, qualityStats] = await Promise.all([
      apiService.getQualityInspections(),
      apiService.getQualityStats(),
    ])
    setDefects((Array.isArray(inspectionList) ? inspectionList : []).map(mapInspectionToDefect))
    setStats(qualityStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // 从 stats.by_type 计算各类合格率
  const byType = stats?.by_type ?? {}
  const computePassRate = (type: string): string => {
    const t = byType[type]
    if (t && t.total > 0) {
      return `${((t.passed / t.total) * 100).toFixed(1)}%`
    }
    return '—'
  }

  const qualityMetrics: QualityMetric[] = [
    { label: '来料合格率', value: computePassRate('incoming'), trend: '实时统计', trendUp: true, icon: TrendingUp },
    { label: '过程合格率', value: computePassRate('process'), trend: '实时统计', trendUp: true, icon: TrendingUp },
    { label: '成品合格率', value: computePassRate('finished'), trend: '实时统计', trendUp: true, icon: TrendingUp },
    { label: '不合格率', value: stats?.defect_rate != null ? `${(stats.defect_rate * 100).toFixed(2)}%` : '—', trend: '实时统计', trendUp: false, icon: TrendingDown },
  ]

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
        <Can
          resource="quality"
          action="update"
          fallback={<span className="text-xs text-slate-600">—</span>}
        >
          <button className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors text-xs">
            <Eye className="w-3.5 h-3.5" />
            处理
          </button>
        </Can>
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
          value={stats?.pass_rate != null ? `${(stats.pass_rate * 100).toFixed(1)}%` : '—'}
          icon={<Percent className="w-5 h-5" />}
          trend="实时计算"
          trendUp
          variant="accent"
        />
        <StatCard
          label="不合格数"
          value={stats?.failed ?? defects.filter((d) => d.status === 'processing').length}
          icon={<Bug className="w-5 h-5" />}
          trend="需关注"
          trendUp={false}
          variant="warning"
        />
        <StatCard
          label="待检验"
          value={stats?.pending ?? defects.filter((d) => d.status === 'pending').length}
          icon={<ClipboardCheck className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="质检总数"
          value={stats?.total_inspections ?? defects.length}
          icon={<Wrench className="w-5 h-5" />}
          trend="全量记录"
          trendUp={false}
          variant="danger"
        />
      </div>

      {/* 质量指标卡片 */}
      <Card title="质量指标概览">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {qualityMetrics.map((metric) => {
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
          data={defects}
          loading={loading}
          pageSize={10}
          searchKeys={['code', 'description']}
          searchPlaceholder="搜索缺陷编号/描述..."
        />
      </Card>
    </div>
  )
}
