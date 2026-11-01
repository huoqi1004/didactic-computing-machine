import { useState, useEffect, useCallback, useMemo } from 'react'
import { TrendingUp, Target, CheckCircle2, Activity, Percent, Eye } from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  type Column,
} from '../components/ui'
import { apiService } from '../services/api'

/* ===================== 类型定义 ===================== */

type StageKey = 'lead' | 'visit' | 'proposal' | 'quote' | 'negotiate' | 'sign'
type DealStatus = 'open' | 'won' | 'lost' | 'stalled'

interface Deal {
  id: string
  name: string
  customer: string
  stage: StageKey
  amount: number
  closeDate: string
  owner: string
  status: DealStatus
  probability: number
  remark: string
}

interface StageInfo {
  key: StageKey
  label: string
  count: number
  amount: number
}

/* ===================== 状态配置 ===================== */

const STAGE_LABEL: Record<StageKey, string> = {
  lead: '线索',
  visit: '初访',
  proposal: '方案',
  quote: '报价',
  negotiate: '谈判',
  sign: '签约',
}

const STAGE_VARIANT: Record<StageKey, 'neutral' | 'info' | 'primary' | 'warning' | 'success'> = {
  lead: 'neutral',
  visit: 'info',
  proposal: 'primary',
  quote: 'primary',
  negotiate: 'warning',
  sign: 'success',
}

const DEAL_STATUS_CONFIG: Record<DealStatus, { variant: 'info' | 'success' | 'danger' | 'warning'; label: string }> = {
  open: { variant: 'info', label: '进行中' },
  won: { variant: 'success', label: '已赢单' },
  lost: { variant: 'danger', label: '已输单' },
  stalled: { variant: 'warning', label: '停滞' },
}

const fmtMoney = (n: number) => `¥${n.toLocaleString('zh-CN')}`

/* ===================== 漏斗阶段配置 ===================== */

const STAGE_ORDER: StageKey[] = ['lead', 'visit', 'proposal', 'quote', 'negotiate', 'sign']

const STAGE_COLORS = ['#4f7cff', '#5b86ff', '#6a92ff', '#7c9ef0', '#3fc8a8', '#00d9a3']

/* ===================== 销售统计类型 ===================== */

interface SalesStats {
  orders?: number
  confirmed_orders?: number
  pending_orders?: number
  targets?: number
  forecasts?: number
  contracts?: number
  total_confirmed_amount?: number
  total_pending_amount?: number
  total_target_amount?: number
  total_achieved_amount?: number
  overall_completion_rate?: number
}

/* ===================== 数据映射 ===================== */

/** 将后端商机阶段映射为前端 StageKey */
function mapStage(rawStage: string): StageKey {
  switch (rawStage) {
    case 'lead':
      return 'lead'
    case 'visit':
      return 'visit'
    case 'proposal':
      return 'proposal'
    case 'quote':
      return 'quote'
    case 'negotiation':
    case 'negotiate':
      return 'negotiate'
    case 'won':
    case 'sign':
      return 'sign'
    default:
      return 'negotiate' // lost 等未明确阶段归入谈判
  }
}

/** 根据阶段与状态推导赢单概率（后端未返回时使用） */
function deriveProbability(stage: StageKey, status: DealStatus): number {
  if (status === 'won') return 100
  if (status === 'lost') return 0
  switch (stage) {
    case 'lead':
      return 10
    case 'visit':
      return 25
    case 'proposal':
      return 45
    case 'quote':
      return 60
    case 'negotiate':
      return 70
    case 'sign':
      return 90
    default:
      return 0
  }
}

/** 将后端返回的商机数据映射为前端 Deal 结构（兼容 snake_case / camelCase） */
function mapDeal(raw: Record<string, unknown>): Deal {
  const stage = mapStage(String(raw.stage ?? ''))
  const rawStatus = String(raw.status ?? 'open')
  let status: DealStatus = 'open'
  if (rawStatus === 'won') status = 'won'
  else if (rawStatus === 'lost') status = 'lost'
  else if (rawStatus === 'stalled') status = 'stalled'
  const probability = Number(raw.probability) || deriveProbability(stage, status)
  return {
    id: String(raw.id ?? raw.opportunity_id ?? ''),
    name: String(raw.name ?? '—'),
    customer: String(raw.customer ?? raw.customer_name ?? raw.customer_id ?? '—'),
    stage,
    amount: Number(raw.amount) || 0,
    closeDate: String(raw.close_date ?? raw.expected_close_date ?? ''),
    owner: String(raw.owner ?? raw.salesperson ?? '—'),
    status,
    probability,
    remark: String(raw.remark ?? raw.notes ?? ''),
  }
}

/* ===================== 主组件 ===================== */

export default function SalesPipeline() {
  const [selected, setSelected] = useState<Deal | null>(null)
  const [opportunities, setOpportunities] = useState<Deal[]>([])
  const [stats, setStats] = useState<SalesStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, salesStats] = await Promise.all([
      apiService.getOpportunities(),
      apiService.getSalesStats(),
    ])
    setOpportunities((Array.isArray(list) ? list : []).map(mapDeal))
    setStats(salesStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // 由商机列表聚合各阶段分布（漏斗）
  const stages: StageInfo[] = useMemo(() => {
    return STAGE_ORDER.map((key) => {
      const dealsInStage = opportunities.filter((d) => d.stage === key)
      return {
        key,
        label: STAGE_LABEL[key],
        count: dealsInStage.length,
        amount: dealsInStage.reduce((s, d) => s + d.amount, 0),
      }
    })
  }, [opportunities])

  const maxAmount = Math.max(1, ...stages.map((s) => s.amount))

  // 漏斗汇总指标
  const leadCount = opportunities.filter((d) => d.stage === 'lead').length
  const signCount = opportunities.filter((d) => d.stage === 'sign').length
  const wonCount = opportunities.filter((d) => d.status === 'won').length
  const lostCount = opportunities.filter((d) => d.status === 'lost').length
  const closedCount = wonCount + lostCount
  const conversionRate = leadCount > 0 ? (signCount / leadCount) * 100 : 0
  const winRate = closedCount > 0 ? (wonCount / closedCount) * 100 : 0
  const weightedForecast = opportunities
    .filter((d) => d.status === 'open')
    .reduce((s, d) => s + (d.amount * d.probability) / 100, 0)

  // 统计卡片指标
  const quarterlyTarget = stats?.total_target_amount ?? 0
  const signedAmount =
    stats?.total_confirmed_amount ??
    opportunities.filter((d) => d.status === 'won').reduce((s, d) => s + d.amount, 0)
  const openPipeline = opportunities
    .filter((d) => d.status === 'open')
    .reduce((s, d) => s + d.amount, 0)
  const openCount = opportunities.filter((d) => d.status === 'open').length
  const completionRate = stats?.overall_completion_rate ?? 0

  const columns: Column<Deal>[] = [
    {
      key: 'name',
      label: '商机名称',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    { key: 'customer', label: '客户名称', render: (r) => <span className="text-slate-300">{r.customer}</span> },
    {
      key: 'stage',
      label: '阶段',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant={STAGE_VARIANT[r.stage]}>{STAGE_LABEL[r.stage]}</StatusBadge>,
    },
    {
      key: 'amount',
      label: '预计金额',
      align: 'right',
      sortable: true,
      render: (r) => <span className="tabular-nums font-semibold text-accent">{fmtMoney(r.amount)}</span>,
    },
    { key: 'closeDate', label: '预计成交日期', sortable: true, render: (r) => <span className="text-slate-400 text-xs">{r.closeDate}</span> },
    { key: 'owner', label: '负责人', render: (r) => <span className="text-slate-300">{r.owner}</span> },
    {
      key: 'status',
      label: '状态',
      align: 'center',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={(DEAL_STATUS_CONFIG[r.status] ?? Object.values(DEAL_STATUS_CONFIG)[0]).variant} dot>
          {(DEAL_STATUS_CONFIG[r.status] ?? Object.values(DEAL_STATUS_CONFIG)[0]).label}
        </StatusBadge>
      ),
    },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <button
          onClick={(e) => {
            e.stopPropagation()
            setSelected(r)
          }}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
        >
          <Eye className="w-3.5 h-3.5" /> 查看
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="销售漏斗"
        description="销售阶段、商机推进与季度目标达成分析"
        icon={<TrendingUp className="w-5 h-5" />}
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="本季目标" value={fmtMoney(quarterlyTarget)} icon={<Target className="w-5 h-5" />} trend="2026 Q3" variant="primary" />
        <StatCard label="已签约" value={fmtMoney(Math.round(signedAmount))} icon={<CheckCircle2 className="w-5 h-5" />} trend={`赢单 ${wonCount} 单`} trendUp variant="accent" />
        <StatCard label="进行中" value={fmtMoney(Math.round(openPipeline))} icon={<Activity className="w-5 h-5" />} trend={`${openCount} 个商机`} variant="primary" />
        <StatCard label="达成率" value={`${completionRate.toFixed(1)}%`} icon={<Percent className="w-5 h-5" />} trend="同比 +8.2%" trendUp variant="accent" />
      </div>

      {/* 销售漏斗 */}
      <Card title="销售漏斗 - 各阶段商机分布">
        <div className="space-y-3">
          {stages.map((s, i) => {
            const pct = Math.round((s.amount / maxAmount) * 100)
            const color = STAGE_COLORS[i]
            return (
              <div key={s.key}>
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2">
                    <span
                      className="w-6 h-6 rounded-md text-xs font-bold flex items-center justify-center text-white"
                      style={{ backgroundColor: color }}
                    >
                      {i + 1}
                    </span>
                    <span className="text-sm text-white font-medium">{s.label}</span>
                    <span className="text-xs text-slate-500">{s.count} 个商机</span>
                  </div>
                  <span className="text-sm text-accent font-semibold tabular-nums">{fmtMoney(s.amount)}</span>
                </div>
                <div className="h-7 rounded-lg bg-bg-input border border-[#1e2536] overflow-hidden">
                  <div
                    className="h-full rounded-lg flex items-center justify-end px-2 transition-all"
                    style={{
                      width: `${pct}%`,
                      background: `linear-gradient(90deg, ${color}66, ${color})`,
                    }}
                  >
                    <span className="text-[10px] text-white font-medium tabular-nums">{pct}%</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
        {/* 漏斗转化率汇总 */}
        <div className="mt-5 pt-4 border-t border-[#1e2536] grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="text-center">
            <p className="text-xs text-slate-500">线索→签约转化</p>
            <p className="text-lg font-bold text-accent tabular-nums">{conversionRate.toFixed(1)}%</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-slate-500">平均成交周期</p>
            <p className="text-lg font-bold text-white tabular-nums">—</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-slate-500">赢单率</p>
            <p className="text-lg font-bold text-white tabular-nums">{winRate.toFixed(1)}%</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-slate-500">加权预测金额</p>
            <p className="text-lg font-bold text-primary tabular-nums">{fmtMoney(Math.round(weightedForecast))}</p>
          </div>
        </div>
      </Card>

      {/* 商机列表 */}
      <Card title="商机列表">
        <DataTable
          columns={columns}
          data={opportunities}
          loading={loading}
          searchKeys={['name', 'customer']}
          searchPlaceholder="搜索商机名称、客户名称..."
          onRowClick={(r) => setSelected(r)}
          pageSize={10}
        />
      </Card>

      {/* 商机详情弹窗 */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="商机详情" size="lg">
        {selected && <DealDetail deal={selected} />}
      </Modal>
    </div>
  )
}

/* ===================== 商机详情 ===================== */

function DealDetail({ deal }: { deal: Deal }) {
  const rows: { label: string; value: string }[] = [
    { label: '商机编号', value: deal.id },
    { label: '商机名称', value: deal.name },
    { label: '客户名称', value: deal.customer },
    { label: '当前阶段', value: STAGE_LABEL[deal.stage] },
    { label: '预计金额', value: fmtMoney(deal.amount) },
    { label: '预计成交日期', value: deal.closeDate },
    { label: '负责人', value: deal.owner },
    { label: '赢单概率', value: `${deal.probability}%` },
  ]

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 flex-wrap">
        <StatusBadge variant={STAGE_VARIANT[deal.stage]} dot>
          {STAGE_LABEL[deal.stage]}阶段
        </StatusBadge>
        <StatusBadge variant={(DEAL_STATUS_CONFIG[deal.status] ?? Object.values(DEAL_STATUS_CONFIG)[0]).variant} dot>
          {(DEAL_STATUS_CONFIG[deal.status] ?? Object.values(DEAL_STATUS_CONFIG)[0]).label}
        </StatusBadge>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{r.label}</span>
            <span className="text-sm text-white font-medium">{r.value}</span>
          </div>
        ))}
      </div>

      {/* 赢单概率进度条 */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs text-slate-500">赢单概率</span>
          <span className="text-sm font-semibold text-accent tabular-nums">{deal.probability}%</span>
        </div>
        <div className="h-2 rounded-full bg-bg-input overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-primary to-accent transition-all"
            style={{ width: `${deal.probability}%` }}
          />
        </div>
      </div>

      <div className="rounded-lg bg-bg-input border border-[#1e2536] p-4">
        <p className="text-xs text-slate-500 mb-1.5">商机备注</p>
        <p className="text-sm text-slate-300 leading-relaxed">{deal.remark}</p>
      </div>
    </div>
  )
}
