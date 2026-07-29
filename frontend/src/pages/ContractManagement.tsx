import { useState } from 'react'
import {
  FileText,
  Clock,
  AlertTriangle,
  Wallet,
  Plus,
  Eye,
  ShieldCheck,
  XCircle,
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

const fmtMoney = (n: number) => `¥${n.toLocaleString('zh-CN')}`

/* ===================== Mock 数据 ===================== */

const initialContracts: Contract[] = [
  { id: '1', code: 'HT-2026-001', name: '智云数据中台年度服务合同', partyA: '上海智云科技有限公司', partyB: '本公司', amount: 2860000, signDate: '2026-01-15', expireDate: '2027-01-14', status: 'active', content: '提供数据中台建设与年度运维服务，含 7×24 技术支持。', owner: '张明' },
  { id: '2', code: 'HT-2026-002', name: '华信风控系统采购合同', partyA: '北京华信数据股份', partyB: '本公司', amount: 520000, signDate: '2026-02-20', expireDate: '2026-08-19', status: 'reviewing', content: '风控系统软件授权及实施部署服务。', owner: '李芳' },
  { id: '3', code: 'HT-2026-003', name: '前海产线改造项目合同', partyA: '深圳前海智能制造', partyB: '本公司', amount: 1280000, signDate: '2026-03-10', expireDate: '2026-12-31', status: 'active', content: '智能制造产线数字化改造，分三期交付。', owner: '王强' },
  { id: '4', code: 'HT-2026-004', name: '数联门店数字化合同', partyA: '杭州数联零售集团', partyB: '本公司', amount: 680000, signDate: '2025-09-01', expireDate: '2026-08-31', status: 'active', content: '全国 200 家门店数字化系统建设。', owner: '陈浩' },
  { id: '5', code: 'HT-2026-005', name: '西部仓储管理系统合同', partyA: '成都西部物流股份', partyB: '本公司', amount: 320000, signDate: '2026-04-05', expireDate: '2027-04-04', status: 'draft', content: '仓储管理系统 SaaS 订阅及定制开发。', owner: '孙伟' },
  { id: '6', code: 'HT-2026-006', name: '光谷电子采购平台合同', partyA: '武汉光谷电子', partyB: '本公司', amount: 180000, signDate: '2025-06-15', expireDate: '2026-06-14', status: 'expired', content: '电子采购平台年度服务，合同已到期。', owner: '周婷' },
  { id: '7', code: 'HT-2026-007', name: '建工集团 ERP 实施合同', partyA: '南京建工集团', partyB: '本公司', amount: 460000, signDate: '2026-05-18', expireDate: '2026-11-17', status: 'active', content: 'ERP 系统实施及培训服务。', owner: '吴磊' },
  { id: '8', code: 'HT-2026-008', name: '能源管理平台续约合同', partyA: '苏州工业园区能源', partyB: '本公司', amount: 720000, signDate: '2026-06-01', expireDate: '2027-05-31', status: 'reviewing', content: '智慧能源管理平台年度续约，含功能升级。', owner: '郑凯' },
  { id: '9', code: 'HT-2026-009', name: '海蓝数据中台合同', partyA: '青岛海蓝新材料', partyB: '本公司', amount: 540000, signDate: '2026-03-22', expireDate: '2026-09-21', status: 'active', content: '研发数据中台建设，含数据治理服务。', owner: '高翔' },
  { id: '10', code: 'HT-2025-088', name: '山城供应链系统合同', partyA: '重庆山城食品', partyB: '本公司', amount: 260000, signDate: '2025-03-08', expireDate: '2026-03-07', status: 'terminated', content: '因业务调整，合同提前终止。', owner: '黄丽' },
  { id: '11', code: 'HT-2026-010', name: '港通贸易平台合同', partyA: '天津港通贸易', partyB: '本公司', amount: 380000, signDate: '2026-04-12', expireDate: '2027-04-11', status: 'draft', content: '贸易管理平台开发合同，待法务审核。', owner: '林峰' },
  { id: '12', code: 'HT-2026-011', name: '山城食品供应链续约', partyA: '重庆山城食品', partyB: '本公司', amount: 295000, signDate: '2026-03-08', expireDate: '2027-03-07', status: 'reviewing', content: '供应链系统年度续约合同。', owner: '黄丽' },
]

/* ===================== 主组件 ===================== */

export default function ContractManagement() {
  const [contracts, setContracts] = useState<Contract[]>(initialContracts)
  const [detail, setDetail] = useState<Contract | null>(null)
  const [review, setReview] = useState<Contract | null>(null)

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
        <StatusBadge variant={STATUS_CONFIG[r.status].variant} dot>
          {STATUS_CONFIG[r.status].label}
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
        <StatCard label="有效合同" value={contracts.filter((c) => c.status === 'active').length} icon={<FileText className="w-5 h-5" />} trend="持续增长" trendUp variant="primary" />
        <StatCard label="待审核" value={contracts.filter((c) => c.status === 'draft' || c.status === 'reviewing').length} icon={<Clock className="w-5 h-5" />} trend="需及时处理" variant="warning" />
        <StatCard label="即将到期" value={12} icon={<AlertTriangle className="w-5 h-5" />} trend="30 天内" variant="warning" />
        <StatCard label="合同总金额" value="¥28,560,000" icon={<Wallet className="w-5 h-5" />} trend="较去年 +15.6%" trendUp variant="primary" />
      </div>

      {/* 合同列表 */}
      <Card title="合同列表">
        <DataTable
          columns={columns}
          data={contracts}
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
  const cfg = STATUS_CONFIG[contract.status]
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
          <StatusBadge variant={STATUS_CONFIG[contract.status].variant} dot>
            {STATUS_CONFIG[contract.status].label}
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
