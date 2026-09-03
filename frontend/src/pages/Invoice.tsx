import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  FileText,
  CheckCircle2,
  XCircle,
  Ban,
  Plus,
  RefreshCw,
  Receipt,
  TrendingUp,
  ShieldCheck,
  Search,
} from 'lucide-react'
import { apiService } from '../services/api'
import { formatCurrency, formatNumber, formatDateTime, cn } from '../lib/utils'
import ExportButton from '../components/ExportButton'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type InvoiceStatus = 'draft' | 'issued' | 'verified' | 'cancelled' | 'rejected'

interface Invoice {
  invoice_id: string
  invoice_no: string
  invoice_type: string
  invoice_type_name: string
  invoice_code: string
  buyer_name: string
  buyer_tax_id: string
  seller_name: string
  seller_tax_id: string
  amount: number
  tax_rate: number
  tax_amount: number
  total: number
  description: string
  status: InvoiceStatus
  issue_date: string
  created_at: string
  auto_generated: boolean
  verified_at?: string
  cancelled_at?: string
  cancel_reason?: string
}

interface InvoiceStats {
  total_invoices: number
  issued_count: number
  cancelled_count: number
  total_amount: number
  total_tax: number
  total_with_tax: number
  by_type?: Record<string, { count: number; amount: number; tax: number; total: number }>
}

/* ===================== 状态配置 ===================== */

const INVOICE_STATUS_CONFIG: Record<
  InvoiceStatus,
  { label: string; color: string; bg: string; border: string }
> = {
  draft: { label: '草稿', color: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30' },
  issued: { label: '已开具', color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30' },
  verified: { label: '已验证', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
  cancelled: { label: '已红冲', color: 'text-status-circuit', bg: 'bg-status-circuit/10', border: 'border-circuit/30' },
  rejected: { label: '已拒绝', color: 'text-status-paused', bg: 'bg-status-paused/10', border: 'border-paused/30' },
}

const INVOICE_TYPES: Record<string, string> = {
  vat_special: '增值税专用发票',
  vat_normal: '增值税普通发票',
  electronic: '增值税电子普通发票',
  electronic_special: '增值税电子专用发票',
}

/* ===================== 主组件 ===================== */

export default function Invoice() {
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [stats, setStats] = useState<InvoiceStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [filterStatus, setFilterStatus] = useState<InvoiceStatus | 'all'>('all')
  const [searchTerm, setSearchTerm] = useState('')
  const [showIssueForm, setShowIssueForm] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [invoiceList, invoiceStats] = await Promise.all([
      apiService.getInvoices({ limit: 100 }),
      apiService.getInvoiceStats(),
    ])
    setInvoices(invoiceList)
    setStats(invoiceStats)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const filtered = invoices.filter((inv) => {
    const matchStatus = filterStatus === 'all' || inv.status === filterStatus
    const matchSearch =
      !searchTerm ||
      inv.invoice_no.toLowerCase().includes(searchTerm.toLowerCase()) ||
      inv.buyer_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      inv.description.toLowerCase().includes(searchTerm.toLowerCase())
    return matchStatus && matchSearch
  })

  // 构造导出数据（发票清单汇总，PDF basic 模板）
  const exportData = useMemo(() => {
    const totalCount = stats?.total_invoices ?? invoices.length
    const totalWithTax = stats?.total_with_tax ?? invoices.reduce((sum, inv) => sum + inv.total, 0)
    return {
      title: '发票清单汇总',
      paragraphs: [
        `发票总数：${totalCount} 张`,
        `价税合计：${formatCurrency(totalWithTax)}`,
      ],
      table: {
        headers: ['发票号', '类型', '购买方', '商品/服务', '金额', '税率', '税额', '价税合计', '状态'],
        rows: invoices.map((inv) => [
          inv.invoice_no,
          inv.invoice_type_name,
          inv.buyer_name || '—',
          inv.description || '—',
          formatCurrency(inv.amount),
          `${(inv.tax_rate * 100).toFixed(0)}%`,
          formatCurrency(inv.tax_amount),
          formatCurrency(inv.total),
          INVOICE_STATUS_CONFIG[inv.status].label,
        ]),
      },
    }
  }, [invoices, stats])

  const handleVerify = async (id: string) => {
    setActionLoading(id)
    await apiService.verifyInvoice(id)
    setActionLoading(null)
    fetchData()
  }

  const handleCancel = async (id: string) => {
    if (!confirm('确认红冲此发票？此操作不可撤销。')) return
    setActionLoading(id)
    await apiService.cancelInvoice(id, '手动红冲')
    setActionLoading(null)
    fetchData()
  }

  const handleIssueSuccess = () => {
    setShowIssueForm(false)
    fetchData()
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">发票管理</h2>
          <p className="text-sm text-slate-500 mt-0.5">发票开具、验证、红冲与统计分析</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchData} className="btn-ghost">
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
            刷新
          </button>
          <Can resource="document" action="create">
            <ExportButton
              agentId="invoice"
              templateType="basic"
              filename="发票清单"
              data={exportData}
              title="发票清单汇总"
              formats={['pdf']}
              label="导出发票清单"
              size="sm"
            />
          </Can>
          <Can resource="invoice" action="create">
            <button onClick={() => setShowIssueForm(true)} className="btn-accent">
              <Plus className="w-4 h-4" />
              开具发票
            </button>
          </Can>
        </div>
      </div>

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatBox title="发票总数" value={formatNumber(stats?.total_invoices ?? 0)} icon={FileText} color="primary" />
        <StatBox
          title="有效发票"
          value={formatNumber(stats?.issued_count ?? 0)}
          subValue={formatCurrency(stats?.total_with_tax ?? 0)}
          icon={CheckCircle2}
          color="accent"
        />
        <StatBox
          title="税额合计"
          value={formatCurrency(stats?.total_tax ?? 0)}
          icon={TrendingUp}
          color="primary"
        />
        <StatBox
          title="红冲发票"
          value={formatNumber(stats?.cancelled_count ?? 0)}
          icon={Ban}
          color="paused"
        />
      </div>

      {/* 发票类型分布 */}
      {stats?.by_type && Object.keys(stats.by_type).length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-3">发票类型分布</h3>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.entries(stats.by_type).map(([type, data]) => (
              <div key={type} className="p-3 rounded-lg bg-bg-input border border-[#2a3142]">
                <p className="text-xs text-slate-500 mb-1">{INVOICE_TYPES[type] || type}</p>
                <p className="text-lg font-bold text-white tabular-nums">{data.count} 张</p>
                <p className="text-xs text-accent tabular-nums mt-1">{formatCurrency(data.total)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 发票列表 */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h3 className="text-sm font-semibold text-white">发票列表</h3>
          <div className="flex items-center gap-2">
            {/* 搜索框 */}
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
              <input
                type="text"
                placeholder="搜索发票号/购买方..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-48 lg:w-64 pl-8 pr-3 py-1.5 rounded-md text-xs bg-bg-input border border-[#2a3142] text-white placeholder:text-slate-600 focus:outline-none focus:border-primary/50"
              />
            </div>
            {/* 状态筛选 */}
            <div className="flex items-center gap-1">
              {(['all', 'issued', 'verified', 'cancelled'] as const).map((st) => (
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
                  {st === 'all' ? '全部' : INVOICE_STATUS_CONFIG[st].label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>发票号</th>
                <th>类型</th>
                <th>购买方</th>
                <th>商品/服务</th>
                <th className="text-right">金额</th>
                <th className="text-right">税率</th>
                <th className="text-right">税额</th>
                <th className="text-right">价税合计</th>
                <th>开票日期</th>
                <th>状态</th>
                <th className="text-center">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((inv) => {
                const cfg = INVOICE_STATUS_CONFIG[inv.status]
                return (
                  <tr key={inv.invoice_id}>
                    <td className="font-mono text-xs text-primary">{inv.invoice_no}</td>
                    <td className="text-xs text-slate-400">{inv.invoice_type_name}</td>
                    <td className="text-sm text-white max-w-[120px] truncate" title={inv.buyer_name}>
                      {inv.buyer_name || '—'}
                    </td>
                    <td className="text-xs text-slate-400 max-w-[150px] truncate" title={inv.description}>
                      {inv.description || '—'}
                    </td>
                    <td className="text-right tabular-nums text-slate-300">{formatCurrency(inv.amount)}</td>
                    <td className="text-right tabular-nums text-slate-400 text-xs">
                      {(inv.tax_rate * 100).toFixed(0)}%
                    </td>
                    <td className="text-right tabular-nums text-slate-400">{formatCurrency(inv.tax_amount)}</td>
                    <td className="text-right tabular-nums font-semibold text-accent">
                      {formatCurrency(inv.total)}
                    </td>
                    <td className="text-xs text-slate-500">{formatDateTime(inv.issue_date)}</td>
                    <td>
                      <span className={cn('badge', cfg.bg, cfg.color, cfg.border, 'py-0')}>{cfg.label}</span>
                    </td>
                    <td>
                      <div className="flex items-center justify-center gap-1">
                        {inv.status === 'issued' && (
                          <>
                            <Can
                              resource="invoice"
                              action="update"
                              fallback={<span className="text-xs text-slate-600">—</span>}
                            >
                              <button
                                onClick={() => handleVerify(inv.invoice_id)}
                                disabled={actionLoading === inv.invoice_id}
                                className="p-1.5 rounded-md bg-accent/10 hover:bg-accent/20 text-accent transition-colors disabled:opacity-50"
                                title="验证发票"
                              >
                                <ShieldCheck className="w-3.5 h-3.5" />
                              </button>
                            </Can>
                            <Can
                              resource="invoice"
                              action="delete"
                              fallback={<span className="text-xs text-slate-600">—</span>}
                            >
                              <button
                                onClick={() => handleCancel(inv.invoice_id)}
                                disabled={actionLoading === inv.invoice_id}
                                className="p-1.5 rounded-md bg-status-circuit/10 hover:bg-status-circuit/20 text-status-circuit transition-colors disabled:opacity-50"
                                title="红冲发票"
                              >
                                <Ban className="w-3.5 h-3.5" />
                              </button>
                            </Can>
                          </>
                        )}
                        {inv.status === 'verified' && (
                          <Can
                            resource="invoice"
                            action="delete"
                            fallback={<span className="text-xs text-slate-600">—</span>}
                          >
                            <button
                              onClick={() => handleCancel(inv.invoice_id)}
                              disabled={actionLoading === inv.invoice_id}
                              className="p-1.5 rounded-md bg-status-circuit/10 hover:bg-status-circuit/20 text-status-circuit transition-colors disabled:opacity-50"
                              title="红冲发票"
                            >
                              <Ban className="w-3.5 h-3.5" />
                            </button>
                          </Can>
                        )}
                        {(inv.status === 'cancelled' || inv.status === 'rejected') && (
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
              <Receipt className="w-8 h-8 mx-auto mb-2 text-slate-700" />
              暂无发票记录
            </div>
          )}
        </div>
      </div>

      {/* 开具发票弹窗 */}
      {showIssueForm && (
        <IssueInvoiceModal onClose={() => setShowIssueForm(false)} onSuccess={handleIssueSuccess} />
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

/* ===================== 开具发票弹窗 ===================== */

function IssueInvoiceModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: () => void
}) {
  const [form, setForm] = useState({
    invoice_type: 'vat_normal',
    buyer_name: '',
    buyer_tax_id: '',
    buyer_address: '',
    buyer_phone: '',
    buyer_bank: '',
    buyer_account: '',
    amount: '',
    description: '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!form.buyer_name.trim()) {
      setError('请输入购买方名称')
      return
    }
    if (!form.amount || parseFloat(form.amount) <= 0) {
      setError('请输入有效的发票金额')
      return
    }
    if (!form.description.trim()) {
      setError('请输入商品/服务名称')
      return
    }

    setSubmitting(true)
    setError('')
    try {
      const result = await apiService.issueInvoice({
        ...form,
        amount: parseFloat(form.amount),
      })
      if (result && result.error) {
        setError(result.error)
      } else {
        onSuccess()
      }
    } catch {
      setError('开具失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl bg-bg-card rounded-2xl border border-[#2a3142] shadow-2xl max-h-[90vh] overflow-y-auto">
        {/* 弹窗头 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#1e2536] sticky top-0 bg-bg-card z-10">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <FileText className="w-4 h-4 text-primary" />
            </div>
            <h3 className="text-sm font-semibold text-white">开具发票</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <XCircle className="w-5 h-5" />
          </button>
        </div>

        {/* 表单 */}
        <div className="p-6 space-y-4">
          {/* 发票类型 */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">发票类型</label>
            <select
              value={form.invoice_type}
              onChange={(e) => setForm({ ...form, invoice_type: e.target.value })}
              className="w-full px-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-white focus:outline-none focus:border-primary/50"
            >
              {Object.entries(INVOICE_TYPES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>

          {/* 购买方信息 */}
          <div className="grid grid-cols-2 gap-3">
            <FormField
              label="购买方名称"
              required
              value={form.buyer_name}
              onChange={(v) => setForm({ ...form, buyer_name: v })}
              placeholder="请输入企业名称"
            />
            <FormField
              label="购买方税号"
              value={form.buyer_tax_id}
              onChange={(v) => setForm({ ...form, buyer_tax_id: v })}
              placeholder="纳税人识别号"
            />
            <FormField
              label="购买方地址"
              value={form.buyer_address}
              onChange={(v) => setForm({ ...form, buyer_address: v })}
              placeholder="（选填）"
            />
            <FormField
              label="购买方电话"
              value={form.buyer_phone}
              onChange={(v) => setForm({ ...form, buyer_phone: v })}
              placeholder="（选填）"
            />
            <FormField
              label="开户银行"
              value={form.buyer_bank}
              onChange={(v) => setForm({ ...form, buyer_bank: v })}
              placeholder="（选填）"
            />
            <FormField
              label="银行账号"
              value={form.buyer_account}
              onChange={(v) => setForm({ ...form, buyer_account: v })}
              placeholder="（选填）"
            />
          </div>

          {/* 金额与描述 */}
          <div className="grid grid-cols-2 gap-3">
            <FormField
              label="不含税金额"
              required
              type="number"
              value={form.amount}
              onChange={(v) => setForm({ ...form, amount: v })}
              placeholder="0.00"
            />
            <FormField
              label="商品/服务名称"
              required
              value={form.description}
              onChange={(v) => setForm({ ...form, description: v })}
              placeholder="如：咨询服务费"
            />
          </div>

          {/* 税率预览 */}
          {form.amount && parseFloat(form.amount) > 0 && (
            <div className="p-3 rounded-lg bg-bg-input border border-[#2a3142]">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">税率</span>
                <span className="text-slate-300">
                  {(
                    (INVOICE_TAX_RATES[form.invoice_type] ?? 0.03) * 100
                  ).toFixed(0)}
                  %
                </span>
              </div>
              <div className="flex items-center justify-between text-xs mt-1">
                <span className="text-slate-500">税额</span>
                <span className="text-slate-300">
                  {formatCurrency(
                    parseFloat(form.amount) * (INVOICE_TAX_RATES[form.invoice_type] ?? 0.03),
                  )}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm mt-1 pt-1 border-t border-[#2a3142]">
                <span className="text-slate-400">价税合计</span>
                <span className="text-accent font-bold">
                  {formatCurrency(
                    parseFloat(form.amount) *
                      (1 + (INVOICE_TAX_RATES[form.invoice_type] ?? 0.03)),
                  )}
                </span>
              </div>
            </div>
          )}

          {/* 错误提示 */}
          {error && (
            <div className="p-3 rounded-lg bg-status-circuit/10 border border-status-circuit/30 text-xs text-status-circuit">
              {error}
            </div>
          )}
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-3 px-6 py-4 border-t border-[#1e2536] sticky bottom-0 bg-bg-card">
          <button onClick={onClose} className="btn-ghost flex-1">
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="btn-accent flex-1"
          >
            {submitting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            确认开具
          </button>
        </div>
      </div>
    </div>
  )
}

const INVOICE_TAX_RATES: Record<string, number> = {
  vat_special: 0.13,
  vat_normal: 0.03,
  electronic: 0.03,
  electronic_special: 0.13,
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
