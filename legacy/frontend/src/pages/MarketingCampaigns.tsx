import { useState, useEffect, useCallback } from 'react'
import {
  Megaphone,
  Users,
  Target,
  TrendingUp,
  Plus,
  Globe,
  MapPin,
  Layers,
  Calendar,
  CheckCircle2,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  StatusBadge,
  Modal,
  StatCard,
  FormField,
  inputClass,
} from '../components/ui'
import { cn } from '../lib/utils'
import { apiService } from '../services/api'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type CampaignType = 'online' | 'offline' | 'mixed'
type CampaignStatus = 'planning' | 'running' | 'ended'

interface Campaign {
  id: string
  name: string
  type: CampaignType
  status: CampaignStatus
  startDate: string
  endDate: string
  budget: number
  reach: number
  conversion: number
  roi: number
  description: string
}

/* ===================== 状态配置 ===================== */

const TYPE_CONFIG: Record<CampaignType, { label: string; variant: 'info' | 'success' | 'primary'; icon: typeof Globe }> = {
  online: { label: '线上', variant: 'info', icon: Globe },
  offline: { label: '线下', variant: 'success', icon: MapPin },
  mixed: { label: '混合', variant: 'primary', icon: Layers },
}

const STATUS_CONFIG: Record<CampaignStatus, { label: string; variant: 'warning' | 'success' | 'neutral' }> = {
  planning: { label: '策划中', variant: 'warning' },
  running: { label: '进行中', variant: 'success' },
  ended: { label: '已结束', variant: 'neutral' },
}

const fmtMoney = (n: number) => `¥${n.toLocaleString('zh-CN')}`
const fmtNum = (n: number) => n.toLocaleString('zh-CN')

/* ===================== 后端统计类型 ===================== */

/** 后端营销统计响应结构（字段均为可选，兼容降级） */
interface MarketingStats {
  campaigns?: number
  active_campaigns?: number
  completed_campaigns?: number
  planning_campaigns?: number
  leads?: number
  new_leads?: number
  qualified_leads?: number
  total_budget?: number
  total_spend?: number
  total_revenue?: number
}

/* ===================== 数据映射 ===================== */

/** 将后端营销活动数据（snake_case）映射为前端 Campaign 结构 */
function mapCampaign(raw: any): Campaign {
  const rawStatus = String(raw.status ?? 'planning')
  let status: CampaignStatus = 'planning'
  if (rawStatus === 'active') status = 'running'
  else if (rawStatus === 'completed') status = 'ended'

  const channels: string[] = Array.isArray(raw.channels) ? raw.channels : []
  let type: CampaignType = 'mixed'
  if (channels.length > 0) {
    const hasOffline = channels.some((c) => /展会|线下|沙龙|答谢|大会|巡展|线下活动/.test(String(c)))
    const hasOnline = channels.some((c) => /线上|直播|广告|邮件|社交|媒体|KOL/.test(String(c)))
    if (hasOffline && !hasOnline) type = 'offline'
    else if (hasOnline && !hasOffline) type = 'online'
  }

  const budget = Number(raw.budget) || 0
  const actualSpend = Number(raw.actual_spend) || 0
  const revenue = Number(raw.revenue) || 0
  const reach = Number(raw.leads_generated) || 0
  const roi = actualSpend > 0 ? Math.round(((revenue - actualSpend) / actualSpend) * 100) : 0

  return {
    id: String(raw.campaign_id ?? raw.id ?? ''),
    name: String(raw.name ?? '—'),
    type,
    status,
    startDate: String(raw.start_date ?? ''),
    endDate: String(raw.end_date ?? ''),
    budget,
    reach,
    conversion: 0,
    roi,
    description: String(raw.target_audience ?? raw.description ?? '暂无描述'),
  }
}

/* ===================== 主组件 ===================== */

export default function MarketingCampaigns() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [stats, setStats] = useState<MarketingStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, marketingStats] = await Promise.all([
      apiService.getMarketingCampaigns(),
      apiService.getMarketingStats(),
    ])
    setCampaigns((Array.isArray(list) ? list : []).map(mapCampaign))
    setStats(marketingStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const runningCount = campaigns.filter((c) => c.status === 'running').length
  const totalReach = stats?.leads ?? campaigns.reduce((s, c) => s + c.reach, 0)
  const totalConversion = campaigns.reduce((s, c) => s + c.conversion, 0)
  const conversionRate = totalReach > 0 ? ((totalConversion / totalReach) * 100).toFixed(2) : '0.0'
  const avgRoi =
    campaigns.length > 0
      ? Math.round(campaigns.reduce((s, c) => s + c.roi, 0) / campaigns.length)
      : 0

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="营销活动"
        description="营销活动策划、执行与效果追踪"
        icon={<Megaphone className="w-5 h-5" />}
        actions={
          <Can resource="marketing" action="create">
            <button className="btn-primary" onClick={() => setShowCreate(true)}>
              <Plus className="w-4 h-4" /> 创建活动
            </button>
          </Can>
        }
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="进行中活动" value={runningCount} icon={<Megaphone className="w-5 h-5" />} trend="持续监控中" variant="primary" />
        <StatCard label="总触达" value={fmtNum(totalReach)} icon={<Users className="w-5 h-5" />} trend="环比 +18%" trendUp variant="primary" />
        <StatCard label="总转化" value={totalConversion} icon={<Target className="w-5 h-5" />} trend={`转化率 ${conversionRate}%`} trendUp variant="accent" />
        <StatCard label="平均 ROI" value={`${avgRoi}%`} icon={<TrendingUp className="w-5 h-5" />} trend="高于行业均值" trendUp variant="accent" />
      </div>

      {/* 活动卡片网格 */}
      <Card title="活动列表" action={<span className="text-xs text-slate-500">{campaigns.length} 个活动</span>}>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {campaigns.map((c) => (
            <CampaignCard key={c.id} campaign={c} />
          ))}
        </div>
      </Card>

      {/* 创建活动弹窗 */}
      <CreateCampaignModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreate={(c) => {
          setCampaigns((prev) => [c, ...prev])
          setShowCreate(false)
        }}
      />
    </div>
  )
}

/* ===================== 活动卡片 ===================== */

function CampaignCard({ campaign }: { campaign: Campaign }) {
  const typeCfg = TYPE_CONFIG[campaign.type] ?? Object.values(TYPE_CONFIG)[0]
  const statusCfg = STATUS_CONFIG[campaign.status] ?? Object.values(STATUS_CONFIG)[0]
  const TypeIcon = typeCfg.icon
  const convRate = campaign.reach > 0 ? ((campaign.conversion / campaign.reach) * 100).toFixed(1) : '0.0'
  const roiVariant = campaign.roi >= 300 ? 'success' : campaign.roi >= 150 ? 'primary' : campaign.roi > 0 ? 'warning' : 'neutral'

  return (
    <div className="rounded-xl border border-[#1e2536] bg-bg-input/50 p-4 hover:border-primary/40 transition-all">
      {/* 头部 */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
            <TypeIcon className="w-4.5 h-4.5 text-primary" />
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-semibold text-white leading-snug truncate">{campaign.name}</h4>
            <p className="text-[11px] text-slate-500 mt-0.5">{campaign.id}</p>
          </div>
        </div>
        <StatusBadge variant={statusCfg.variant} dot>
          {statusCfg.label}
        </StatusBadge>
      </div>

      {/* 标签 */}
      <div className="flex items-center gap-2 mb-3">
        <StatusBadge variant={typeCfg.variant}>
          <TypeIcon className="w-3 h-3" />
          {typeCfg.label}
        </StatusBadge>
      </div>

      {/* 日期 */}
      <div className="flex items-center gap-1.5 text-xs text-slate-500 mb-3">
        <Calendar className="w-3.5 h-3.5" />
        <span>{campaign.startDate}</span>
        <span className="text-slate-600">→</span>
        <span>{campaign.endDate}</span>
      </div>

      {/* 指标网格 */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="rounded-lg bg-bg-deep/60 p-2.5">
          <p className="text-[11px] text-slate-500">预算</p>
          <p className="text-sm font-semibold text-white tabular-nums">{fmtMoney(campaign.budget)}</p>
        </div>
        <div className="rounded-lg bg-bg-deep/60 p-2.5">
          <p className="text-[11px] text-slate-500">触达数</p>
          <p className="text-sm font-semibold text-white tabular-nums">{fmtNum(campaign.reach)}</p>
        </div>
        <div className="rounded-lg bg-bg-deep/60 p-2.5">
          <p className="text-[11px] text-slate-500">转化数</p>
          <p className="text-sm font-semibold text-accent tabular-nums">{campaign.conversion}</p>
        </div>
        <div className="rounded-lg bg-bg-deep/60 p-2.5">
          <p className="text-[11px] text-slate-500">ROI</p>
          <p className={cn('text-sm font-semibold tabular-nums', campaign.roi >= 300 ? 'text-accent' : 'text-status-paused')}>
            {campaign.roi > 0 ? `${campaign.roi}%` : '—'}
          </p>
        </div>
      </div>

      {/* 转化率进度条 */}
      <div className="mb-2">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[11px] text-slate-500">转化率</span>
          <span className="text-[11px] font-medium text-slate-300 tabular-nums">{convRate}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-bg-deep overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-primary to-accent"
            style={{ width: `${Math.min(parseFloat(convRate) * 10, 100)}%` }}
          />
        </div>
      </div>

      {/* ROI 标签 */}
      <div className="flex items-center justify-between pt-2 border-t border-[#1e2536]">
        <span className="text-[11px] text-slate-500">效果评级</span>
        <StatusBadge variant={roiVariant}>{campaign.roi >= 300 ? '优秀' : campaign.roi >= 150 ? '良好' : campaign.roi > 0 ? '一般' : '未开始'}</StatusBadge>
      </div>

      <p className="text-[11px] text-slate-500 leading-relaxed mt-2.5 line-clamp-2">{campaign.description}</p>
    </div>
  )
}

/* ===================== 创建活动弹窗 ===================== */

function CreateCampaignModal({
  open,
  onClose,
  onCreate,
}: {
  open: boolean
  onClose: () => void
  onCreate: (c: Campaign) => void
}) {
  const [form, setForm] = useState({
    name: '',
    type: 'online' as CampaignType,
    startDate: '',
    endDate: '',
    budget: '',
    description: '',
  })

  const handleSubmit = () => {
    if (!form.name.trim() || !form.startDate || !form.endDate) return
    const budget = parseFloat(form.budget) || 0
    onCreate({
      id: `C-${Date.now().toString().slice(-6)}`,
      name: form.name.trim(),
      type: form.type,
      status: 'planning',
      startDate: form.startDate,
      endDate: form.endDate,
      budget,
      reach: 0,
      conversion: 0,
      roi: 0,
      description: form.description.trim() || '暂无描述',
    })
    setForm({ name: '', type: 'online', startDate: '', endDate: '', budget: '', description: '' })
  }

  return (
    <Modal open={open} onClose={onClose} title="创建营销活动" size="lg">
      <div className="space-y-4">
        <FormField label="活动名称" required>
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="请输入活动名称"
            className={inputClass}
          />
        </FormField>

        <div className="grid grid-cols-2 gap-4">
          <FormField label="活动类型" required>
            <select
              value={form.type}
              onChange={(e) => setForm({ ...form, type: e.target.value as CampaignType })}
              className={inputClass}
            >
              <option value="online">线上</option>
              <option value="offline">线下</option>
              <option value="mixed">混合</option>
            </select>
          </FormField>

          <FormField label="活动预算" hint="单位：元">
            <input
              type="number"
              value={form.budget}
              onChange={(e) => setForm({ ...form, budget: e.target.value })}
              placeholder="0"
              className={inputClass}
            />
          </FormField>

          <FormField label="开始日期" required>
            <input
              type="date"
              value={form.startDate}
              onChange={(e) => setForm({ ...form, startDate: e.target.value })}
              className={inputClass}
            />
          </FormField>

          <FormField label="结束日期" required>
            <input
              type="date"
              value={form.endDate}
              onChange={(e) => setForm({ ...form, endDate: e.target.value })}
              className={inputClass}
            />
          </FormField>
        </div>

        <FormField label="活动描述">
          <textarea
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder="请输入活动描述..."
            rows={3}
            className={cn(inputClass, 'resize-none')}
          />
        </FormField>

        <div className="flex gap-3 pt-2">
          <button onClick={onClose} className="btn-secondary flex-1">
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={!form.name.trim() || !form.startDate || !form.endDate}
            className="btn-primary flex-1"
          >
            <CheckCircle2 className="w-4 h-4" />
            创建活动
          </button>
        </div>
      </div>
    </Modal>
  )
}
