import { useState, useEffect, useCallback } from 'react'
import {
  FileText,
  Clock,
  AlertTriangle,
  Wallet,
  Plus,
  Eye,
  ShieldCheck,
  XCircle,
  Play,
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
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type ContractStatus = 'draft' | 'reviewing' | 'active' | 'expired' | 'terminated'

interface Contract {
  id: string
  code: string
  name: string
  partyA: string
  partyB: string
  amount: number
  signDate: string
  expireDate: string
  status: ContractStatus
  content: string
  owner: string
}

/* ===================== 状态配置 ===================== */

const STATUS_CONFIG: Record<
  ContractStatus,
  { variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger'; label: string }
> = {
  draft: { variant: 'neutral', label: '草拟' },
  reviewing: { variant: 'info', label: '审核中' },
  active: { variant: 'success', label: '有效' },
  expired: { variant: 'warning', label: '已到期' },
  terminated: { variant: 'danger', label: '已终止' },
}

/** 后端合同状态 → 前端 ContractStatus 映射 */
const CONTRACT_STATUS_MAP: Record<string, ContractStatus> = {
  draft: 'draft',
  reviewing: 'reviewing',
  approved: 'active',
  executing: 'active',
  completed: 'active',
  expired: 'expired',
  terminated: 'terminated',
}

/** 后端合同统计响应结构（字段均为可选，兼容降级） */
interface ContractStats {
  contracts_total?: number
  contracts_executing?: number
  contracts_completed?: number
  contracts_expiring_30d?: number
  total_contract_value?: number
  status_distribution?: Record<string, number>
  type_distribution?: Record<string, number>
}

const fmtMoney = (n: number) => `¥${n.toLocaleString('zh-CN')}`

/* ===================== 数据映射 ===================== */

/** 将后端合同数据（snake_case）映射为前端 Contract 结构 */
function mapContract(raw: any): Contract {
  const rawStatus = String(raw.status ?? 'draft')
  const status: ContractStatus = CONTRACT_STATUS_MAP[rawStatus] ?? 'active'
  return {
    id: String(raw.contract_id ?? raw.id ?? ''),
    code: String(raw.code ?? raw.contract_id ?? raw.id ?? ''),
    name: String(raw.title ?? raw.name ?? '—'),
    partyA: String(raw.party_a ?? raw.partyA ?? raw.counterparty ?? '—'),
    partyB: String(raw.party_b ?? raw.partyB ?? '本公司'),
    amount: Number(raw.amount ?? 0),
    signDate: String(raw.sign_date ?? raw.signDate ?? raw.start_date ?? ''),
    expireDate: String(raw.expire_date ?? raw.expireDate ?? raw.end_date ?? ''),
    status,
    content: String(raw.content ?? raw.terms ?? ''),
    owner: String(raw.owner ?? raw.approved_by ?? raw.department ?? '—'),
  }
}

/* ===================== 主组件 ===================== */

export default function ContractManagement() {
  const [contracts, setContracts] = useState<Contract[]>([])
  const [stats, setStats] = useState<ContractStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<Contract | null>(null)
  const [review, setReview] = useState<Contract | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, contractStats] = await Promise.all([
      apiService.getContracts(),
      apiService.getContractStats(),
    ])
    setContracts((Array.isArray(list) ? list : []).map(mapContract))
    setStats(contractStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const statusDist = stats?.status_distribution ?? {}
  const activeCount = (statusDist.approved ?? 0) + (statusDist.executing ?? 0)
  const pendingCount = (statusDist.draft ?? 0) + (statusDist.reviewing ?? 0)
  const expiringCount = stats?.contracts_expiring_30d ?? 0
  const totalValue = stats?.total_contract_value ?? 0

  const handleReview = (id: string, action: 'approve' | 'reject') => {
    setContracts((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, status: action === 'approve' ? 'active' : 'draft' } : c,
      ),
    )
    setReview(null)
  }

  const isReviewable = (status: ContractStatus) => status === 'draft' || status === 'reviewing'

  const columns: Column<Contract>[] = [
    {
      key: 'code',
      label: '合同编号',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.code}</span>,
    },
    {
      key: 'name',
      label: '合同名称',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    {
      key: 'partyA',
      label: '甲方',
      render: (r) => <span className="text-slate-300 text-xs">{r.partyA}</span>,
    },
    {
      key: 'partyB',
      label: '乙方',
      render: (r) => <span className="text-slate-300 text-xs">{r.partyB}</span>,
    },
    {
      key: 'amount',
      label: '合同金额',
      align: 'right',
      sortable: true,
      render: (r) => <span className="tabular-nums font-semibold text-accent">{fmtMoney(r.amount)}</span>,
    },
    {
      key: 'signDate',
      label: '签订日期',
      sortable: true,
      render: (r) => <span className="text-slate-400 text-xs">{r.signDate}</span>,
    },
    {
      key: 'expireDate',
      label: '到期日期',
      sortable: true,
      render: (r) => <span className="text-slate-400 text-xs">{r.expireDate}</span>,
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
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <div className="flex items-center justify-center gap-1.5">
          <button
            onClick={() => setDetail(r)}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
          >
            <Eye className="w-3.5 h-3.5" /> 查看
          </button>
          {isReviewable(r.status) && (
            <button
              onClick={() => setReview(r)}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-accent/10 text-accent hover:bg-accent/20 text-xs font-medium transition-colors"
            >
              <ShieldCheck className="w-3.5 h-3.5" /> 审核
            </button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="合同管理"
        description="合同起草、审核、归档与到期预警"
        icon={<FileText className="w-5 h-5" />}
        actions={
          <button className="btn-primary">
            <Plus className="w-4 h-4" /> 起草合同
          </button>
        }
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="有效合同" value={activeCount} icon={<FileText className="w-5 h-5" />} trend="持续增长" trendUp variant="primary" />
        <StatCard label="待审核" value={pendingCount} icon={<Clock className="w-5 h-5" />} trend="需及时处理" variant="warning" />
        <StatCard label="即将到期" value={expiringCount} icon={<AlertTriangle className="w-5 h-5" />} trend="30 天内" variant="warning" />
        <StatCard label="合同总金额" value={fmtMoney(totalValue)} icon={<Wallet className="w-5 h-5" />} trend="较去年 +15.6%" trendUp variant="primary" />
      </div>

      {/* 合同列表 */}
      <Card title="合同列表">
        <DataTable
          columns={columns}
          data={contracts}
          loading={loading}
          searchKeys={['code', 'name']}
          searchPlaceholder="搜索合同编号、合同名称..."
          pageSize={10}
        />
      </Card>

      {/* 合同详情弹窗 */}
      <Modal open={!!detail} onClose={() => setDetail(null)} title="合同详情" size="lg">
        {detail && <ContractDetail contract={detail} />}
      </Modal>

      {/* 合同审核弹窗 */}
      <Modal open={!!review} onClose={() => setReview(null)} title="合同审核" size="md">
        {review && (
          <ReviewForm
            contract={review}
            onApprove={() => handleReview(review.id, 'approve')}
            onReject={() => handleReview(review.id, 'reject')}
          />
        )}
      </Modal>
    </div>
  )
}

/* ===================== 合同详情 ===================== */

function ContractDetail({ contract }: { contract: Contract }) {
  const cfg = STATUS_CONFIG[contract.status] ?? Object.values(STATUS_CONFIG)[0]
  const rows: { label: string; value: string }[] = [
    { label: '合同编号', value: contract.code },
    { label: '合同名称', value: contract.name },
    { label: '甲方', value: contract.partyA },
    { label: '乙方', value: contract.partyB },
    { label: '合同金额', value: fmtMoney(contract.amount) },
    { label: '合同状态', value: cfg.label },
    { label: '签订日期', value: contract.signDate },
    { label: '到期日期', value: contract.expireDate },
    { label: '负责人', value: contract.owner },
  ]

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 flex-wrap">
        <StatusBadge variant={cfg.variant} dot>
          {cfg.label}
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

      <div className="rounded-lg bg-bg-input border border-[#1e2536] p-4">
        <p className="text-xs text-slate-500 mb-1.5">合同内容摘要</p>
        <p className="text-sm text-slate-300 leading-relaxed">{contract.content}</p>
      </div>
    </div>
  )
}

/* ===================== 合同审核 ===================== */

function ReviewForm({
  contract,
  onApprove,
  onReject,
}: {
  contract: Contract
  onApprove: () => void
  onReject: () => void
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg bg-bg-input border border-[#1e2536] p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-xs text-primary">{contract.code}</span>
          <StatusBadge variant={(STATUS_CONFIG[contract.status] ?? Object.values(STATUS_CONFIG)[0]).variant} dot>
            {(STATUS_CONFIG[contract.status] ?? Object.values(STATUS_CONFIG)[0]).label}
          </StatusBadge>
        </div>
        <h4 className="text-sm font-semibold text-white">{contract.name}</h4>
        <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">{contract.content}</p>
        <div className="flex items-center justify-between mt-3 pt-3 border-t border-[#1e2536]">
          <span className="text-xs text-slate-500">合同金额</span>
          <span className="text-base font-bold text-accent tabular-nums">{fmtMoney(contract.amount)}</span>
        </div>
      </div>

      <p className="text-xs text-slate-500">
        请确认合同条款无误后进行审核操作。审核通过后合同将转为有效状态；驳回将退回草拟状态。
      </p>

      <div className="flex gap-3 pt-2">
        <button
          onClick={onReject}
          className={cn('btn-secondary flex-1 !bg-status-failed/10 !text-status-failed !border-status-failed/30 hover:!bg-status-failed/20')}
        >
          <XCircle className="w-4 h-4" /> 驳回
        </button>
        <button onClick={onApprove} className="btn-accent flex-1">
          <ShieldCheck className="w-4 h-4" /> 审核通过
        </button>
      </div>
    </div>
  )
}
