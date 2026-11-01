import { useState, useEffect, useCallback } from 'react'
import {
  AlertTriangle,
  AlertCircle,
  ShieldCheck,
  ShieldAlert,
  Eye,
  Activity,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'
import { apiService } from '../services/api'

/* ===================== 类型定义 ===================== */

type RiskLevel = 'high' | 'medium' | 'low'
type RiskCategory = 'finance' | 'operation' | 'compliance' | 'strategy' | 'market'
type RiskStatus = 'identified' | 'assessing' | 'mitigating' | 'monitoring' | 'closed'

interface Risk {
  id: string
  name: string
  category: RiskCategory
  impact: RiskLevel
  probability: RiskLevel
  score: number
  status: RiskStatus
  owner: string
  description: string
  mitigation: string
  contingency: string
}

/* ===================== 状态配置 ===================== */

const LEVEL_NUM: Record<RiskLevel, number> = { high: 3, medium: 2, low: 1 }
const LEVEL_LABEL: Record<RiskLevel, string> = { high: '高', medium: '中', low: '低' }
const LEVEL_VARIANT: Record<RiskLevel, 'danger' | 'warning' | 'success'> = {
  high: 'danger',
  medium: 'warning',
  low: 'success',
}

const CATEGORY_LABEL: Record<RiskCategory, string> = {
  finance: '财务',
  operation: '运营',
  compliance: '合规',
  strategy: '战略',
  market: '市场',
}

const STATUS_CONFIG: Record<
  RiskStatus,
  { variant: 'neutral' | 'info' | 'warning' | 'primary' | 'success'; label: string }
> = {
  identified: { variant: 'neutral', label: '识别' },
  assessing: { variant: 'info', label: '评估' },
  mitigating: { variant: 'warning', label: '缓解' },
  monitoring: { variant: 'primary', label: '监控' },
  closed: { variant: 'success', label: '关闭' },
}

const scoreLevel = (score: number): RiskLevel =>
  score >= 7 ? 'high' : score >= 4 ? 'medium' : 'low'

const MATRIX_CELL_STYLES: Record<RiskLevel, string> = {
  high: 'bg-status-failed/15 border-status-failed/40 text-status-failed',
  medium: 'bg-status-paused/15 border-status-paused/40 text-status-paused',
  low: 'bg-accent/15 border-accent/40 text-accent',
}

/* ===================== 后端统计类型 ===================== */

/** 后端风险统计响应结构（字段均为可选，兼容降级） */
interface RiskStats {
  risks_total?: number
  risks_assessed?: number
  risks_unassessed?: number
  risks_mitigating?: number
  level_distribution?: Record<string, number>
  category_distribution?: Record<string, number>
  alerts_total?: number
  alerts_open?: number
  assessments_total?: number
}

/* ===================== 数据映射 ===================== */

/** 将后端数字等级（1-5）映射为前端高中低 */
function numToLevel(n: number): RiskLevel {
  if (n >= 4) return 'high'
  if (n >= 3) return 'medium'
  return 'low'
}

/** 后端类别映射为前端类别 */
function mapCategory(raw: string): RiskCategory {
  const map: Record<string, RiskCategory> = {
    financial: 'finance',
    compliance: 'compliance',
    operational: 'operation',
    market: 'market',
    technology: 'strategy',
    strategy: 'strategy',
  }
  return map[raw] ?? 'operation'
}

/** 后端状态映射为前端状态 */
function mapStatus(raw: string): RiskStatus {
  const map: Record<string, RiskStatus> = {
    identified: 'identified',
    assessed: 'assessing',
    mitigating: 'mitigating',
    mitigated: 'monitoring',
    monitoring: 'monitoring',
    closed: 'closed',
  }
  return map[raw] ?? 'identified'
}

/** 将后端风险数据（snake_case）映射为前端 Risk 结构 */
function mapRisk(raw: any): Risk {
  const probabilityNum = Number(raw.probability) || 0
  const impactNum = Number(raw.impact) || 0
  return {
    id: String(raw.risk_id ?? raw.id ?? ''),
    name: String(raw.title ?? raw.name ?? '—'),
    category: mapCategory(String(raw.category ?? 'operation')),
    impact: numToLevel(impactNum),
    probability: numToLevel(probabilityNum),
    score: Number(raw.risk_score ?? raw.score) || impactNum * probabilityNum,
    status: mapStatus(String(raw.status ?? 'identified')),
    owner: String(raw.owner ?? '—'),
    description: String(raw.description ?? '暂无描述'),
    mitigation: String(raw.mitigation_plan ?? raw.mitigation ?? ''),
    contingency: String(raw.contingency ?? ''),
  }
}

/* ===================== 主组件 ===================== */

export default function RiskManagement() {
  const [risks, setRisks] = useState<Risk[]>([])
  const [stats, setStats] = useState<RiskStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Risk | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [riskList, riskStats] = await Promise.all([
      apiService.getRisks(),
      apiService.getRiskStats(),
    ])
    setRisks((Array.isArray(riskList) ? riskList : []).map(mapRisk))
    setStats(riskStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const impactRows: RiskLevel[] = ['high', 'medium', 'low']
  const probCols: RiskLevel[] = ['low', 'medium', 'high']

  const columns: Column<Risk>[] = [
    {
      key: 'id',
      label: '风险编号',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.id}</span>,
    },
    {
      key: 'name',
      label: '风险名称',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    {
      key: 'category',
      label: '类别',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant="neutral">{CATEGORY_LABEL[r.category]}</StatusBadge>,
    },
    {
      key: 'impact',
      label: '影响等级',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant={LEVEL_VARIANT[r.impact]}>{LEVEL_LABEL[r.impact]}</StatusBadge>,
    },
    {
      key: 'probability',
      label: '概率等级',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant={LEVEL_VARIANT[r.probability]}>{LEVEL_LABEL[r.probability]}</StatusBadge>,
    },
    {
      key: 'score',
      label: '风险评分',
      align: 'center',
      sortable: true,
      render: (r) => (
        <span
          className={cn(
            'inline-flex items-center justify-center w-7 h-7 rounded-md text-xs font-bold tabular-nums',
            MATRIX_CELL_STYLES[scoreLevel(r.score)],
          )}
        >
          {r.score}
        </span>
      ),
    },
    {
      key: 'status',
      label: '状态',
      align: 'center',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={(STATUS_CONFIG[r.status] ?? Object.values(STATUS_CONFIG)[0]).variant} dot>
          {(STATUS_CONFIG[r.status] ?? Object.values(STATUS_CONFIG)[0]).label}
        </StatusBadge>
      ),
    },
    { key: 'owner', label: '负责人', render: (r) => <span className="text-slate-300">{r.owner}</span> },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <button
          onClick={() => setSelected(r)}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
        >
          <Eye className="w-3.5 h-3.5" /> 查看详情
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="风险管理"
        description="风险识别、评估、缓解与持续监控"
        icon={<AlertTriangle className="w-5 h-5" />}
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="总风险数" value={stats?.risks_total ?? risks.length} icon={<ShieldAlert className="w-5 h-5" />} trend="全量台账" variant="primary" />
        <StatCard label="高风险" value={stats?.level_distribution?.high ?? risks.filter((r) => r.impact === 'high').length} icon={<AlertCircle className="w-5 h-5" />} trend="需立即处置" variant="danger" />
        <StatCard label="中风险" value={stats?.level_distribution?.medium ?? risks.filter((r) => r.impact === 'medium').length} icon={<AlertTriangle className="w-5 h-5" />} trend="持续关注" variant="warning" />
        <StatCard label="低风险" value={stats?.level_distribution?.low ?? risks.filter((r) => r.impact === 'low').length} icon={<ShieldCheck className="w-5 h-5" />} trend="可控范围" trendUp variant="accent" />
      </div>

      {/* 主体：风险矩阵 + 风险列表 */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* 左侧：风险矩阵 */}
        <div className="xl:col-span-1">
          <Card title="风险矩阵" action={<Activity className="w-4 h-4 text-slate-500" />}>
            <div className="space-y-1.5">
              {/* 表头：概率轴 */}
              <div className="grid grid-cols-[60px_repeat(3,1fr)] gap-1.5">
                <div />
                {probCols.map((p) => (
                  <div key={p} className="text-center text-[11px] text-slate-500 pb-1">
                    概率{LEVEL_LABEL[p]}
                  </div>
                ))}
              </div>
              {/* 数据行：影响轴 */}
              {impactRows.map((imp) => (
                <div key={imp} className="grid grid-cols-[60px_repeat(3,1fr)] gap-1.5">
                  <div className="flex items-center justify-end text-[11px] text-slate-500 pr-1">
                    影响{LEVEL_LABEL[imp]}
                  </div>
                  {probCols.map((prob) => {
                    const count = risks.filter(
                      (r) => r.impact === imp && r.probability === prob,
                    ).length
                    const score = LEVEL_NUM[imp] * LEVEL_NUM[prob]
                    const lvl = scoreLevel(score)
                    return (
                      <div
                        key={prob}
                        className={cn(
                          'aspect-square rounded-lg border flex flex-col items-center justify-center transition-all cursor-pointer hover:scale-105',
                          MATRIX_CELL_STYLES[lvl],
                        )}
                      >
                        <span className="text-xl font-bold tabular-nums">{count}</span>
                        <span className="text-[10px] opacity-80">评分 {score}</span>
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>

            {/* 图例 */}
            <div className="mt-4 pt-3 border-t border-[#1e2536] space-y-1.5">
              <div className="flex items-center gap-2 text-xs">
                <span className="w-3 h-3 rounded bg-status-failed/30 border border-status-failed/40" />
                <span className="text-slate-400">高风险（评分 7-9）</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="w-3 h-3 rounded bg-status-paused/30 border border-status-paused/40" />
                <span className="text-slate-400">中风险（评分 4-6）</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="w-3 h-3 rounded bg-accent/30 border border-accent/40" />
                <span className="text-slate-400">低风险（评分 1-3）</span>
              </div>
            </div>
          </Card>
        </div>

        {/* 右侧：风险列表 */}
        <div className="xl:col-span-2">
          <Card title="风险列表">
            <DataTable
              columns={columns}
              data={risks}
              loading={loading}
              searchKeys={['name']}
              searchPlaceholder="搜索风险名称..."
              pageSize={8}
            />
          </Card>
        </div>
      </div>

      {/* 风险详情弹窗 */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="风险详情" size="lg">
        {selected && <RiskDetail risk={selected} />}
      </Modal>
    </div>
  )
}

/* ===================== 风险详情 ===================== */

function RiskDetail({ risk }: { risk: Risk }) {
  const lvl = scoreLevel(risk.score)
  const rows: { label: string; value: string }[] = [
    { label: '风险编号', value: risk.id },
    { label: '风险名称', value: risk.name },
    { label: '风险类别', value: CATEGORY_LABEL[risk.category] },
    { label: '影响等级', value: LEVEL_LABEL[risk.impact] },
    { label: '概率等级', value: LEVEL_LABEL[risk.probability] },
    { label: '风险评分', value: `${risk.score} 分` },
    { label: '当前状态', value: (STATUS_CONFIG[risk.status] ?? Object.values(STATUS_CONFIG)[0]).label },
    { label: '负责人', value: risk.owner },
  ]

  return (
    <div className="space-y-5">
      {/* 风险等级与评分 */}
      <div className="flex items-center gap-3">
        <div
          className={cn(
            'w-12 h-12 rounded-xl border flex items-center justify-center text-lg font-bold tabular-nums',
            MATRIX_CELL_STYLES[lvl],
          )}
        >
          {risk.score}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <StatusBadge variant={LEVEL_VARIANT[lvl]} dot>
            {lvl === 'high' ? '高风险' : lvl === 'medium' ? '中风险' : '低风险'}
          </StatusBadge>
          <StatusBadge variant={(STATUS_CONFIG[risk.status] ?? Object.values(STATUS_CONFIG)[0]).variant} dot>
            {(STATUS_CONFIG[risk.status] ?? Object.values(STATUS_CONFIG)[0]).label}
          </StatusBadge>
          <StatusBadge variant="neutral">{CATEGORY_LABEL[risk.category]}</StatusBadge>
        </div>
      </div>

      {/* 基本信息 */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{r.label}</span>
            <span className="text-sm text-white font-medium">{r.value}</span>
          </div>
        ))}
      </div>

      {/* 风险描述 */}
      <div className="rounded-lg bg-bg-input border border-[#1e2536] p-4">
        <p className="text-xs text-slate-500 mb-1.5">风险描述</p>
        <p className="text-sm text-slate-300 leading-relaxed">{risk.description}</p>
      </div>

      {/* 缓解措施 */}
      <div className="rounded-lg bg-accent/5 border border-accent/20 p-4">
        <p className="text-xs text-accent mb-1.5 font-medium">缓解措施</p>
        <p className="text-sm text-slate-300 leading-relaxed">{risk.mitigation}</p>
      </div>

      {/* 应急预案 */}
      <div className="rounded-lg bg-status-paused/5 border border-status-paused/20 p-4">
        <p className="text-xs text-status-paused mb-1.5 font-medium">应急预案</p>
        <p className="text-sm text-slate-300 leading-relaxed">{risk.contingency}</p>
      </div>
    </div>
  )
}
