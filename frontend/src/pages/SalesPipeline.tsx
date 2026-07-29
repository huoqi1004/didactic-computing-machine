import { useState } from 'react'
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

/* ===================== 漏斗阶段数据 ===================== */

const stages: StageInfo[] = [
  { key: 'lead', label: '线索', count: 86, amount: 8200000 },
  { key: 'visit', label: '初访', count: 62, amount: 6500000 },
  { key: 'proposal', label: '方案', count: 38, amount: 4800000 },
  { key: 'quote', label: '报价', count: 24, amount: 3600000 },
  { key: 'negotiate', label: '谈判', count: 15, amount: 2400000 },
  { key: 'sign', label: '签约', count: 9, amount: 1200000 },
]

const STAGE_COLORS = ['#4f7cff', '#5b86ff', '#6a92ff', '#7c9ef0', '#3fc8a8', '#00d9a3']

/* ===================== Mock 商机数据 ===================== */

const mockDeals: Deal[] = [
  { id: 'D-2026-001', name: '智云数据中台升级', customer: '上海智云科技', stage: 'negotiate', amount: 860000, closeDate: '2026-08-15', owner: '张明', status: 'open', probability: 70, remark: '客户预算已批复，进入最终商务谈判。' },
  { id: 'D-2026-002', name: '华信风控系统', customer: '北京华信数据', stage: 'proposal', amount: 520000, closeDate: '2026-09-01', owner: '李芳', status: 'open', probability: 50, remark: '方案设计中，预计下周提交客户评审。' },
  { id: 'D-2026-003', name: '前海产线改造', customer: '深圳前海智能', stage: 'quote', amount: 1280000, closeDate: '2026-08-30', owner: '王强', status: 'open', probability: 60, remark: '已提交报价单，等待客户比价反馈。' },
  { id: 'D-2026-004', name: '数联门店数字化', customer: '杭州数联零售', stage: 'sign', amount: 680000, closeDate: '2026-07-20', owner: '陈浩', status: 'won', probability: 100, remark: '合同已签订，进入交付阶段。' },
  { id: 'D-2026-005', name: '西部仓储系统', customer: '成都西部物流', stage: 'visit', amount: 320000, closeDate: '2026-10-15', owner: '孙伟', status: 'open', probability: 30, remark: '已完成首次拜访，需求待进一步明确。' },
  { id: 'D-2026-006', name: '光谷电子采购平台', customer: '武汉光谷电子', stage: 'lead', amount: 180000, closeDate: '2026-11-30', owner: '周婷', status: 'stalled', probability: 10, remark: '客户决策周期拉长，暂无明确推进。' },
  { id: 'D-2026-007', name: '建工集团 ERP', customer: '南京建工集团', stage: 'negotiate', amount: 460000, closeDate: '2026-08-20', owner: '吴磊', status: 'open', probability: 65, remark: '价格谈判中，差距已缩小至 8%。' },
  { id: 'D-2026-008', name: '能源管理平台续约', customer: '苏州能源', stage: 'sign', amount: 720000, closeDate: '2026-07-18', owner: '郑凯', status: 'won', probability: 100, remark: '续约成功，合同金额较上年度增长 12%。' },
  { id: 'D-2026-009', name: '海蓝数据中台', customer: '青岛海蓝新材料', stage: 'proposal', amount: 540000, closeDate: '2026-09-20', owner: '高翔', status: 'open', probability: 45, remark: '方案二次评审中，技术指标全部满足。' },
  { id: 'D-2026-010', name: '山城供应链', customer: '重庆山城食品', stage: 'quote', amount: 260000, closeDate: '2026-08-25', owner: '黄丽', status: 'lost', probability: 0, remark: '客户选择了竞品方案，输单。' },
]

/* ===================== 主组件 ===================== */

export default function SalesPipeline() {
  const [selected, setSelected] = useState<Deal | null>(null)

  const maxAmount = Math.max(...stages.map((s) => s.amount))

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
        <StatusBadge variant={DEAL_STATUS_CONFIG[r.status].variant} dot>
          {DEAL_STATUS_CONFIG[r.status].label}
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
        <StatCard label="本季目标" value="¥5,000,000" icon={<Target className="w-5 h-5" />} trend="2026 Q3" variant="primary" />
        <StatCard label="已签约" value="¥3,286,000" icon={<CheckCircle2 className="w-5 h-5" />} trend="赢单 9 单" trendUp variant="accent" />
        <StatCard label="进行中" value="¥1,856,000" icon={<Activity className="w-5 h-5" />} trend="36 个商机" variant="primary" />
        <StatCard label="达成率" value="65.7%" icon={<Percent className="w-5 h-5" />} trend="同比 +8.2%" trendUp variant="accent" />
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
            <p className="text-lg font-bold text-accent tabular-nums">10.5%</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-slate-500">平均成交周期</p>
            <p className="text-lg font-bold text-white tabular-nums">62 天</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-slate-500">赢单率</p>
            <p className="text-lg font-bold text-white tabular-nums">47.3%</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-slate-500">加权预测金额</p>
            <p className="text-lg font-bold text-primary tabular-nums">¥4,128,000</p>
          </div>
        </div>
      </Card>

      {/* 商机列表 */}
      <Card title="商机列表">
        <DataTable
          columns={columns}
          data={mockDeals}
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
        <StatusBadge variant={DEAL_STATUS_CONFIG[deal.status].variant} dot>
          {DEAL_STATUS_CONFIG[deal.status].label}
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
