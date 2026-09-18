import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  Wallet,
  CheckCircle2,
  XCircle,
  Clock,
  Plus,
  RefreshCw,
  FileCheck,
  Banknote,
  Search,
  FolderPlus,
  UserPlus,
  Ban,
  FileText,
  ScanLine,
  Upload,
  Loader2,
} from 'lucide-react'
import { apiService } from '../services/api'
import { formatCurrency, formatNumber, formatDateTime, cn } from '../lib/utils'
import ExportButton from '../components/ExportButton'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type ReimbStatus = 'draft' | 'pending' | 'verified' | 'approved' | 'rejected' | 'paid'

interface Reimbursement {
  reimbursement_id: string
  reimbursement_no: string
  employee_id: string
  employee_name: string
  department: string
  category: string
  amount: number
  description: string
  has_receipt: boolean
  receipt_amount: number
  invoice_id: string
  invoice_verified: boolean
  project_id: string
  project_name: string
  status: ReimbStatus
  submitted_at: string
  created_at: string
  verified_at?: string
  approved_at?: string
  rejected_at?: string
  paid_at?: string
  reject_reason?: string
  approval_remark?: string
}

interface ReimbStats {
  total_requests: number
  pending_count: number
  approved_count: number
  paid_count: number
  rejected_count: number
  total_pending_amount: number
  total_paid_amount: number
  by_category: Record<string, { count: number; amount: number }>
  by_project: Record<string, { project_name: string; count: number; amount: number }>
}

/* ===================== 状态配置 ===================== */

const REIMB_STATUS_CONFIG: Record<
  ReimbStatus,
  { label: string; color: string; bg: string; border: string }
> = {
  draft: { label: '草稿', color: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30' },
  pending: { label: '待核验', color: 'text-status-paused', bg: 'bg-status-paused/10', border: 'border-paused/30' },
  verified: { label: '已核验', color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30' },
  approved: { label: '已审批', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
  rejected: { label: '已拒绝', color: 'text-status-circuit', bg: 'bg-status-circuit/10', border: 'border-circuit/30' },
  paid: { label: '已付款', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
}

const CATEGORY_LABELS: Record<string, string> = {
  travel: '差旅费',
  meal: '餐饮费',
  office: '办公费',
  transport: '交通费',
  communication: '通讯费',
  training: '培训费',
  entertainment: '招待费',
  project: '项目费用',
  other: '其他',
}

/* ===================== 主组件 ===================== */

export default function Reimbursement() {
  const [reimbursements, setReimbursements] = useState<Reimbursement[]>([])
  const [stats, setStats] = useState<ReimbStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [filterStatus, setFilterStatus] = useState<ReimbStatus | 'all'>('all')
  const [searchTerm, setSearchTerm] = useState('')
  const [showSubmitForm, setShowSubmitForm] = useState(false)
  const [showProjectForm, setShowProjectForm] = useState(false)
  const [showEmployeeForm, setShowEmployeeForm] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [reimbList, reimbStats] = await Promise.all([
      apiService.getReimbursements({ limit: 100 }),
      apiService.getReimbursementStats(),
    ])
    setReimbursements(reimbList)
    setStats(reimbStats)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const filtered = reimbursements.filter((r) => {
    const matchStatus = filterStatus === 'all' || r.status === filterStatus
    const matchSearch =
      !searchTerm ||
      r.reimbursement_no.toLowerCase().includes(searchTerm.toLowerCase()) ||
      r.employee_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      r.description.toLowerCase().includes(searchTerm.toLowerCase())
    return matchStatus && matchSearch
  })

  /* ===================== 导出数据构造 ===================== */

  // 导出数据：报销清单（PDF basic 模板）
  const exportListData = useMemo(
    () => ({
      title: '报销清单汇总',
      paragraphs: [
        `报销总数：${reimbursements.length} 笔`,
        `待处理：${stats?.pending_count ?? 0} 笔`,
        `已付款金额：${formatCurrency(stats?.total_paid_amount ?? 0)}`,
      ],
      table: {
        headers: ['报销单号', '员工', '部门', '类别', '描述', '金额', '项目', '状态'],
        rows: reimbursements.map((r) => [
          r.reimbursement_no,
          r.employee_name || '—',
          r.department || '—',
          CATEGORY_LABELS[r.category] || r.category,
          r.description || '—',
          formatCurrency(r.amount),
          r.project_name || '—',
          REIMB_STATUS_CONFIG[r.status]?.label || r.status,
        ]),
      },
    }),
    [reimbursements, stats],
  )

  // 导出数据：报销管理报告（DOCX report 模板）
  const exportReportData = useMemo(() => {
    const today = new Date()
    const dateStr = today.toISOString().slice(0, 10)
    const totalAmount = reimbursements.reduce((sum, r) => sum + r.amount, 0)
    const paidCount = reimbursements.filter((r) => r.status === 'paid').length
    const pendingCount = reimbursements.filter(
      (r) => r.status === 'pending' || r.status === 'verified' || r.status === 'approved',
    ).length
    return {
      title: '报销管理报告',
      report_no: `RPT-REIMB-${dateStr}`,
      date: dateStr,
      summary: `本期共处理报销 ${reimbursements.length} 笔，总金额 ${formatCurrency(
        totalAmount,
      )}。其中已付款 ${paidCount} 笔，待处理 ${pendingCount} 笔。`,
      sections: [
        {
          heading: '报销明细',
          content: `共计 ${reimbursements.length} 笔报销记录，明细如下：`,
          table: {
            headers: ['报销单号', '员工', '部门', '类别', '描述', '金额', '项目', '状态'],
            rows: reimbursements.map((r) => [
              r.reimbursement_no,
              r.employee_name || '—',
              r.department || '—',
              CATEGORY_LABELS[r.category] || r.category,
              r.description || '—',
              formatCurrency(r.amount),
              r.project_name || '—',
              REIMB_STATUS_CONFIG[r.status]?.label || r.status,
            ]),
          },
        },
      ],
      conclusion:
        '建议持续关注待处理报销的审批进度，确保报销款项及时支付，同时加强票据核验管理，规范报销流程。',
    }
  }, [reimbursements])

  const handleVerify = async (id: string) => {
    setActionLoading(id)
    await apiService.verifyReimbursement(id)
    setActionLoading(null)
    fetchData()
  }

  const handleApprove = async (id: string, approved: boolean) => {
    setActionLoading(id)
    await apiService.approveReimbursement(id, approved)
    setActionLoading(null)
    fetchData()
  }

  const handlePay = async (id: string) => {
    if (!confirm('确认付款？')) return
    setActionLoading(id)
    await apiService.payReimbursement(id)
    setActionLoading(null)
    fetchData()
  }

  const handleSuccess = () => {
    setShowSubmitForm(false)
    setShowProjectForm(false)
    setShowEmployeeForm(false)
    fetchData()
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">报销管理</h2>
          <p className="text-sm text-slate-500 mt-0.5">员工报销申请、项目费用核验、审批与付款</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchData} className="btn-ghost">
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
            刷新
          </button>
          <Can resource="reimbursement" action="create">
            <button onClick={() => setShowProjectForm(true)} className="btn-ghost">
              <FolderPlus className="w-4 h-4" />
              注册项目
            </button>
          </Can>
          <Can resource="reimbursement" action="create">
            <button onClick={() => setShowEmployeeForm(true)} className="btn-ghost">
              <UserPlus className="w-4 h-4" />
              注册员工
            </button>
          </Can>
          <Can resource="reimbursement" action="create">
            <button onClick={() => setShowSubmitForm(true)} className="btn-accent">
              <Plus className="w-4 h-4" />
              提交报销
            </button>
          </Can>
          <Can resource="document" action="create">
            <ExportButton
              agentId="reimbursement"
              templateType="basic"
              filename="报销清单汇总"
              data={exportListData}
              title="报销清单汇总"
              formats={['pdf']}
              label="导出报销清单"
              size="sm"
            />
          </Can>
          <Can resource="document" action="create">
            <ExportButton
              agentId="reimbursement"
              templateType="report"
              filename="报销管理报告"
              data={exportReportData}
              title="报销管理报告"
              formats={['docx']}
              label="导出报销报告"
              size="sm"
            />
          </Can>
        </div>
      </div>

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatBox title="报销总数" value={formatNumber(stats?.total_requests ?? 0)} icon={FileCheck} color="primary" />
        <StatBox
          title="待处理"
          value={formatNumber(stats?.pending_count ?? 0)}
          subValue={formatCurrency(stats?.total_pending_amount ?? 0)}
          icon={Clock}
          color="paused"
        />
        <StatBox
          title="已付款"
          value={formatNumber(stats?.paid_count ?? 0)}
          subValue={formatCurrency(stats?.total_paid_amount ?? 0)}
          icon={Banknote}
          color="accent"
        />
        <StatBox
          title="已拒绝"
          value={formatNumber(stats?.rejected_count ?? 0)}
          icon={Ban}
          color="paused"
        />
      </div>

      {/* 分类统计 */}
      {stats?.by_category && Object.keys(stats.by_category).length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-3">报销分类统计</h3>
          <div className="grid grid-cols-3 lg:grid-cols-5 gap-3">
            {Object.entries(stats.by_category).map(([cat, data]) => (
              <div key={cat} className="p-3 rounded-lg bg-bg-input border border-[#2a3142]">
                <p className="text-xs text-slate-500 mb-1">{CATEGORY_LABELS[cat] || cat}</p>
                <p className="text-base font-bold text-white tabular-nums">{data.count} 笔</p>
                <p className="text-xs text-accent tabular-nums mt-1">{formatCurrency(data.amount)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 项目费用归集 */}
      {stats?.by_project && Object.keys(stats.by_project).length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-3">项目费用归集</h3>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>项目ID</th>
                  <th>项目名称</th>
                  <th className="text-right">报销笔数</th>
                  <th className="text-right">费用合计</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(stats.by_project).map(([pid, data]) => (
                  <tr key={pid}>
                    <td className="font-mono text-xs text-primary">{pid}</td>
                    <td className="text-sm text-white">{data.project_name || '—'}</td>
                    <td className="text-right tabular-nums text-slate-300">{data.count}</td>
                    <td className="text-right tabular-nums font-semibold text-accent">
                      {formatCurrency(data.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 报销列表 */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h3 className="text-sm font-semibold text-white">报销列表</h3>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
              <input
                type="text"
                placeholder="搜索单号/员工/描述..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-48 lg:w-64 pl-8 pr-3 py-1.5 rounded-md text-xs bg-bg-input border border-[#2a3142] text-white placeholder:text-slate-600 focus:outline-none focus:border-primary/50"
              />
            </div>
            <div className="flex items-center gap-1 flex-wrap">
              {(['all', 'pending', 'verified', 'approved', 'paid', 'rejected'] as const).map((st) => (
                <button
                  key={st}
                  onClick={() => setFilterStatus(st)}
                  className={cn(
                    'px-2.5 py-1 rounded-md text-xs font-medium transition-all',
                    filterStatus === st
                      ? 'bg-primary text-white'
                      : 'bg-bg-input text-slate-400 hover:text-white border border-[#2a3142]',
                  )}
                >
                  {st === 'all' ? '全部' : REIMB_STATUS_CONFIG[st].label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>报销单号</th>
                <th>员工</th>
                <th>部门</th>
                <th>类别</th>
                <th>描述</th>
                <th className="text-right">金额</th>
                <th>项目</th>
                <th>票据</th>
                <th>提交时间</th>
                <th>状态</th>
                <th className="text-center">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => {
                const cfg = REIMB_STATUS_CONFIG[r.status]
                return (
                  <tr key={r.reimbursement_id}>
                    <td className="font-mono text-xs text-primary">{r.reimbursement_no}</td>
                    <td className="text-sm text-white">{r.employee_name || '—'}</td>
                    <td className="text-xs text-slate-400">{r.department || '—'}</td>
                    <td>
                      <span className="badge bg-bg-input text-slate-300 border-[#2a3142] py-0">
                        {CATEGORY_LABELS[r.category] || r.category}
                      </span>
                    </td>
                    <td className="text-xs text-slate-400 max-w-[150px] truncate" title={r.description}>
                      {r.description || '—'}
                    </td>
                    <td className="text-right tabular-nums font-semibold text-accent">
                      {formatCurrency(r.amount)}
                    </td>
                    <td className="text-xs text-slate-400 max-w-[100px] truncate" title={r.project_name}>
                      {r.project_name || '—'}
                    </td>
                    <td>
                      {r.has_receipt ? (
                        <span className="text-xs text-accent">有票据</span>
                      ) : (
                        <span className="text-xs text-slate-600">无票据</span>
                      )}
                    </td>
                    <td className="text-xs text-slate-500">{formatDateTime(r.submitted_at)}</td>
                    <td>
                      <span className={cn('badge', cfg.bg, cfg.color, cfg.border, 'py-0')}>{cfg.label}</span>
                    </td>
                    <td>
                      <div className="flex items-center justify-center gap-1">
                        {/* 待核验 -> 核验 */}
                        {r.status === 'pending' && (
                          <Can
                            resource="reimbursement"
                            action="update"
                            fallback={<span className="text-xs text-slate-600">—</span>}
                          >
                            <button
                              onClick={() => handleVerify(r.reimbursement_id)}
                              disabled={actionLoading === r.reimbursement_id}
                              className="p-1.5 rounded-md bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 transition-colors disabled:opacity-50"
                              title="核验"
                            >
                              <FileCheck className="w-3.5 h-3.5" />
                            </button>
                          </Can>
                        )}
                        {/* 已核验 -> 审批通过/拒绝 */}
                        {r.status === 'verified' && (
                          <Can
                            resource="reimbursement"
                            action="approve"
                            fallback={<span className="text-xs text-slate-600">—</span>}
                          >
                            <button
                              onClick={() => handleApprove(r.reimbursement_id, true)}
                              disabled={actionLoading === r.reimbursement_id}
                              className="p-1.5 rounded-md bg-accent/10 hover:bg-accent/20 text-accent transition-colors disabled:opacity-50"
                              title="审批通过"
                            >
                              <CheckCircle2 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => handleApprove(r.reimbursement_id, false)}
                              disabled={actionLoading === r.reimbursement_id}
                              className="p-1.5 rounded-md bg-status-circuit/10 hover:bg-status-circuit/20 text-status-circuit transition-colors disabled:opacity-50"
                              title="拒绝"
                            >
                              <XCircle className="w-3.5 h-3.5" />
                            </button>
                          </Can>
                        )}
                        {/* 已审批 -> 付款 */}
                        {r.status === 'approved' && (
                          <Can
                            resource="reimbursement"
                            action="approve"
                            fallback={<span className="text-xs text-slate-600">—</span>}
                          >
                            <button
                              onClick={() => handlePay(r.reimbursement_id)}
                              disabled={actionLoading === r.reimbursement_id}
                              className="p-1.5 rounded-md bg-accent/10 hover:bg-accent/20 text-accent transition-colors disabled:opacity-50"
                              title="付款"
                            >
                              <Banknote className="w-3.5 h-3.5" />
                            </button>
                          </Can>
                        )}
                        {(r.status === 'paid' || r.status === 'rejected') && (
                          <span className="text-xs text-slate-600">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {filtered.length === 0 && !loading && (
            <div className="text-center py-8 text-sm text-slate-500">
              <Wallet className="w-8 h-8 mx-auto mb-2 text-slate-700" />
              暂无报销记录
            </div>
          )}
        </div>
      </div>

      {/* 弹窗 */}
      {showSubmitForm && <SubmitReimbursementModal onClose={() => setShowSubmitForm(false)} onSuccess={handleSuccess} />}
      {showProjectForm && <RegisterProjectModal onClose={() => setShowProjectForm(false)} onSuccess={handleSuccess} />}
      {showEmployeeForm && (
        <RegisterEmployeeModal onClose={() => setShowEmployeeForm(false)} onSuccess={handleSuccess} />
      )}
    </div>
  )
}

/* ===================== 统计卡片 ===================== */

function StatBox({
  title,
  value,
  subValue,
  icon: Icon,
  color,
}: {
  title: string
  value: string
  subValue?: string
  icon: typeof FileText
  color: 'primary' | 'accent' | 'paused'
}) {
  const colorMap = {
    primary: 'text-primary bg-primary/10',
    accent: 'text-accent bg-accent/10',
    paused: 'text-status-paused bg-status-paused/10',
  }
  return (
    <div className="card card-hover">
      <div className="flex items-center gap-3">
        <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center shrink-0', colorMap[color])}>
          <Icon className="w-5 h-5" />
        </div>
        <div className="min-w-0">
          <p className="text-xs text-slate-500">{title}</p>
          <p className="text-lg font-bold text-white tabular-nums truncate">{value}</p>
          {subValue && <p className="text-[11px] text-slate-500 tabular-nums">{subValue}</p>}
        </div>
      </div>
    </div>
  )
}

/* ===================== 提交报销弹窗 ===================== */

function SubmitReimbursementModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: () => void
}) {
  const [form, setForm] = useState({
    employee_id: '',
    employee_name: '',
    department: '',
    category: 'office',
    amount: '',
    description: '',
    has_receipt: true,
    receipt_amount: '',
    invoice_id: '',
    project_id: '',
    project_name: '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  // 发票上传识别
  const [uploading, setUploading] = useState(false)
  const [invoiceInfo, setInvoiceInfo] = useState<{
    invoice_id: string
    message: string
    reliable: boolean
    summary: Record<string, string | number | null>
  } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleInvoiceUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('仅支持 PDF 格式发票文件')
      return
    }
    setUploading(true)
    setError('')
    try {
      const result = await apiService.uploadReimbursementInvoice(file)
      if (!result || !result.invoice_id) {
        setError('发票识别失败，请手动填写表单')
        return
      }
      // 预填表单：类别/金额/事由/附票状态/发票关联
      const prefill = result.prefill || {}
      setForm((prev) => ({
        ...prev,
        invoice_id: result.invoice_id,
        category: prefill.category || prev.category,
        amount: prefill.amount != null ? String(prefill.amount) : prev.amount,
        receipt_amount:
          prefill.receipt_amount != null ? String(prefill.receipt_amount) : prev.receipt_amount,
        description: prefill.description || prev.description,
        has_receipt: prefill.has_receipt ?? prev.has_receipt,
      }))
      setInvoiceInfo({
        invoice_id: result.invoice_id,
        message: result.message || '识别完成，请核对预填内容',
        reliable: !!prefill.reliable,
        summary: prefill.invoice_summary || {},
      })
    } catch {
      setError('发票上传失败，请重试或手动填写')
    } finally {
      setUploading(false)
    }
  }

  const handleSubmit = async () => {
    if (!form.employee_name.trim()) {
      setError('请输入员工姓名')
      return
    }
    if (!form.amount || parseFloat(form.amount) <= 0) {
      setError('请输入有效的报销金额')
      return
    }
    if (!form.description.trim()) {
      setError('请输入报销描述')
      return
    }

    setSubmitting(true)
    setError('')
    try {
      const result = await apiService.submitReimbursement({
        ...form,
        amount: parseFloat(form.amount),
        receipt_amount: form.receipt_amount ? parseFloat(form.receipt_amount) : 0,
      })
      if (result && result.error) {
        setError(result.error)
      } else {
        onSuccess()
      }
    } catch {
      setError('提交失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl bg-bg-card rounded-2xl border border-[#2a3142] shadow-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#1e2536] sticky top-0 bg-bg-card z-10">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <Wallet className="w-4 h-4 text-primary" />
            </div>
            <h3 className="text-sm font-semibold text-white">提交报销申请</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <XCircle className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-4">
          {/* 发票上传识别：解析票面要素并预填表单，减少手工填写 */}
          <div className="rounded-xl border border-dashed border-[#2a3142] p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ScanLine className="w-4 h-4 text-primary" />
                <span className="text-xs font-medium text-slate-300">发票识别预填</span>
                <span className="text-xs text-slate-500">上传 PDF 发票自动填写</span>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,application/pdf"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) handleInvoiceUpload(file)
                  e.target.value = ''
                }}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="btn-ghost text-xs px-3 py-1.5 flex items-center gap-1.5"
              >
                {uploading ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Upload className="w-3.5 h-3.5" />
                )}
                {uploading ? '识别中…' : '上传发票 PDF'}
              </button>
            </div>

            {invoiceInfo && (
              <div className={cn(
                'rounded-lg p-3 space-y-2 text-xs',
                invoiceInfo.reliable
                  ? 'bg-primary/5 border border-primary/20'
                  : 'bg-yellow-500/5 border border-yellow-500/30'
              )}>
                <div className="flex items-center justify-between">
                  <span className={invoiceInfo.reliable ? 'text-primary' : 'text-yellow-400'}>
                    {invoiceInfo.message}
                  </span>
                  <span className="text-slate-500">ID: {invoiceInfo.invoice_id}</span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-slate-400">
                  <span>发票种类：<b className="text-slate-200 font-normal">{invoiceInfo.summary.invoice_type ?? '—'}</b></span>
                  <span>抬头类型：<b className="text-slate-200 font-normal">{invoiceInfo.summary.buyer_type ?? '—'}</b></span>
                  <span className="col-span-2">购买方抬头：<b className="text-slate-200 font-normal">{invoiceInfo.summary.invoice_title ?? '—'}</b></span>
                  <span className="col-span-2">纳税人识别号：<b className="text-slate-200 font-normal">{invoiceInfo.summary.taxpayer_id ?? '—'}</b></span>
                  <span>开票日期：<b className="text-slate-200 font-normal">{invoiceInfo.summary.invoice_date ?? '—'}</b></span>
                  <span>发票号码：<b className="text-slate-200 font-normal">{invoiceInfo.summary.invoice_number ?? '—'}</b></span>
                  <span>价税合计：<b className="text-accent font-normal">{invoiceInfo.summary.total_amount != null ? `¥${invoiceInfo.summary.total_amount}` : '—'}</b></span>
                  <span>税额：<b className="text-slate-200 font-normal">{invoiceInfo.summary.tax_amount != null ? `¥${invoiceInfo.summary.tax_amount}` : '—'}</b></span>
                </div>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <FormField label="员工姓名" required value={form.employee_name} onChange={(v) => setForm({ ...form, employee_name: v })} placeholder="请输入姓名" />
            <FormField label="员工ID" value={form.employee_id} onChange={(v) => setForm({ ...form, employee_id: v })} placeholder="（选填）" />
            <FormField label="部门" value={form.department} onChange={(v) => setForm({ ...form, department: v })} placeholder="如：技术部" />
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">报销类别</label>
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="w-full px-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-white focus:outline-none focus:border-primary/50"
              >
                {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <FormField label="报销金额" required type="number" value={form.amount} onChange={(v) => setForm({ ...form, amount: v })} placeholder="0.00" />
            <FormField label="报销描述" required value={form.description} onChange={(v) => setForm({ ...form, description: v })} placeholder="如：北京出差交通费" />
            <FormField label="项目ID" value={form.project_id} onChange={(v) => setForm({ ...form, project_id: v })} placeholder="（选填）" />
            <FormField label="项目名称" value={form.project_name} onChange={(v) => setForm({ ...form, project_name: v })} placeholder="（选填）" />
            <FormField label="发票ID" value={form.invoice_id} onChange={(v) => setForm({ ...form, invoice_id: v })} placeholder="关联发票（选填）" />
            <FormField label="票据金额" type="number" value={form.receipt_amount} onChange={(v) => setForm({ ...form, receipt_amount: v })} placeholder="0.00" />
          </div>

          {/* 票据勾选 */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={form.has_receipt}
              onChange={(e) => setForm({ ...form, has_receipt: e.target.checked })}
              className="w-4 h-4 rounded border-[#2a3142] bg-bg-input text-primary focus:ring-primary/50"
            />
            <span className="text-xs text-slate-400">已附发票/收据</span>
          </label>

          {error && (
            <div className="p-3 rounded-lg bg-status-circuit/10 border border-status-circuit/30 text-xs text-status-circuit">
              {error}
            </div>
          )}
        </div>

        <div className="flex gap-3 px-6 py-4 border-t border-[#1e2536] sticky bottom-0 bg-bg-card">
          <button onClick={onClose} className="btn-ghost flex-1">取消</button>
          <button onClick={handleSubmit} disabled={submitting} className="btn-accent flex-1">
            {submitting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            确认提交
          </button>
        </div>
      </div>
    </div>
  )
}

/* ===================== 注册项目弹窗 ===================== */

function RegisterProjectModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: () => void
}) {
  const [form, setForm] = useState({ project_id: '', name: '', budget: '' })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!form.project_id.trim() || !form.name.trim()) {
      setError('请输入项目ID和名称')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const result = await apiService.registerProject(form.project_id, form.name, parseFloat(form.budget) || 0)
      if (result && result.error) {
        setError(result.error)
      } else {
        onSuccess()
      }
    } catch {
      setError('注册失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-md bg-bg-card rounded-2xl border border-[#2a3142] shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#1e2536]">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <FolderPlus className="w-4 h-4 text-primary" />
            </div>
            <h3 className="text-sm font-semibold text-white">注册项目</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <XCircle className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <FormField label="项目ID" required value={form.project_id} onChange={(v) => setForm({ ...form, project_id: v })} placeholder="如：PRJ-001" />
          <FormField label="项目名称" required value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="如：数字化升级项目" />
          <FormField label="项目预算" type="number" value={form.budget} onChange={(v) => setForm({ ...form, budget: v })} placeholder="0.00" />
          {error && <div className="p-3 rounded-lg bg-status-circuit/10 border border-status-circuit/30 text-xs text-status-circuit">{error}</div>}
        </div>
        <div className="flex gap-3 px-6 py-4 border-t border-[#1e2536]">
          <button onClick={onClose} className="btn-ghost flex-1">取消</button>
          <button onClick={handleSubmit} disabled={submitting} className="btn-accent flex-1">
            {submitting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            确认注册
          </button>
        </div>
      </div>
    </div>
  )
}

/* ===================== 注册员工弹窗 ===================== */

function RegisterEmployeeModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: () => void
}) {
  const [form, setForm] = useState({ employee_id: '', name: '', department: '' })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!form.employee_id.trim() || !form.name.trim()) {
      setError('请输入员工ID和姓名')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const result = await apiService.registerEmployee(form.employee_id, form.name, form.department)
      if (result && result.error) {
        setError(result.error)
      } else {
        onSuccess()
      }
    } catch {
      setError('注册失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-md bg-bg-card rounded-2xl border border-[#2a3142] shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#1e2536]">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <UserPlus className="w-4 h-4 text-primary" />
            </div>
            <h3 className="text-sm font-semibold text-white">注册员工</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <XCircle className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <FormField label="员工ID" required value={form.employee_id} onChange={(v) => setForm({ ...form, employee_id: v })} placeholder="如：EMP-001" />
          <FormField label="员工姓名" required value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="如：张三" />
          <FormField label="部门" value={form.department} onChange={(v) => setForm({ ...form, department: v })} placeholder="如：技术部" />
          {error && <div className="p-3 rounded-lg bg-status-circuit/10 border border-status-circuit/30 text-xs text-status-circuit">{error}</div>}
        </div>
        <div className="flex gap-3 px-6 py-4 border-t border-[#1e2536]">
          <button onClick={onClose} className="btn-ghost flex-1">取消</button>
          <button onClick={handleSubmit} disabled={submitting} className="btn-accent flex-1">
            {submitting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            确认注册
          </button>
        </div>
      </div>
    </div>
  )
}

/* ===================== 表单字段组件 ===================== */

function FormField({
  label,
  value,
  onChange,
  placeholder,
  required,
  type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  required?: boolean
  type?: 'text' | 'number'
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1.5">
        {label}
        {required && <span className="text-status-circuit ml-0.5">*</span>}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-primary/50"
      />
    </div>
  )
}
