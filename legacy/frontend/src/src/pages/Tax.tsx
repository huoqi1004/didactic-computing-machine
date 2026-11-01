import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Receipt,
  TrendingUp,
  TrendingDown,
  Calculator,
  Wallet,
  FileText,
  RefreshCw,
  ArrowRight,
  AlertCircle,
} from 'lucide-react'
import { apiService } from '../services/api'
import { formatCurrency, formatNumber, formatDateTime, cn } from '../lib/utils'
import ExportButton from '../components/ExportButton'

/* ===================== 类型定义 ===================== */

interface TaxDashboardData {
  tax_records: any[]
  declarations: any[]
  tax_rates: Record<string, any>
  invoice_stats: {
    total_invoices: number
    issued_count: number
    cancelled_count: number
    total_amount: number
    total_tax: number
    total_with_tax: number
    by_type?: Record<string, { count: number; amount: number; tax: number; total: number }>
  }
  reimbursement_stats: {
    total_requests: number
    pending_count: number
    approved_count: number
    paid_count: number
    rejected_count: number
    total_pending_amount: number
    total_paid_amount: number
    by_category?: Record<string, { count: number; amount: number }>
    by_project?: Record<string, { project_name: string; count: number; amount: number }>
  }
  summary: {
    output_tax: number
    input_tax: number
    vat_payable: number
    deductible_expenses: number
    total_invoices: number
    pending_reimbursements: number
  }
}

/* ===================== 主组件 ===================== */

export default function Tax() {
  const [data, setData] = useState<TaxDashboardData | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const result = await apiService.getTaxDashboard()
    setData(result)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const summary = data?.summary
  const invoiceStats = data?.invoice_stats
  const reimbStats = data?.reimbursement_stats
  const taxRates = data?.tax_rates
  const declarations = data?.declarations || []
  const taxRecords = data?.tax_records || []

  // 构造导出数据（税务申报表）
  const exportData = useMemo(() => {
    if (!data) return {}
    const s = data.summary
    const currentPeriod = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`
    return {
      title: '税务申报表',
      tax_type: '增值税',
      period: currentPeriod,
      headers: ['税种', '计税依据', '税率', '应缴税额', '状态'],
      rows: (data.tax_records || []).map((record: any) => [
        record.tax_type || record.type || '—',
        record.taxable_amount || record.base_amount || 0,
        record.tax_rate ? `${(record.tax_rate * 100).toFixed(1)}%` : '—',
        record.tax_amount || record.amount || 0,
        record.status || '—',
      ]),
      total_tax: s?.vat_payable ?? 0,
      notes: [
        `销项税额：¥${s?.output_tax ?? 0}`,
        `进项税额：¥${s?.input_tax ?? 0}`,
        `应缴增值税：¥${s?.vat_payable ?? 0}`,
      ],
    }
  }, [data])

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">报税管理</h2>
          <p className="text-sm text-slate-500 mt-0.5">整合发票销项税、进项税抵扣与报销可抵扣费用，综合计算应缴税额</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchData} className="btn-ghost">
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
            刷新
          </button>
          {data && (
            <ExportButton
              agentId="tax"
              templateType="tax"
              filename="税务申报表"
              data={exportData}
              title="税务申报表"
              formats={['xlsx', 'pdf']}
              size="md"
            />
          )}
        </div>
      </div>

      {/* 增值税计算核心看板 */}
      <div className="card border-primary/20">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
            <Calculator className="w-4 h-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">增值税计算</h3>
            <p className="text-xs text-slate-500">应缴增值税 = 销项税额 - 进项税额</p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* 销项税 */}
          <div className="p-4 rounded-xl bg-bg-input border border-[#2a3142]">
            <div className="flex items-center gap-2 mb-2">
              <TrendingUp className="w-4 h-4 text-blue-400" />
              <span className="text-xs text-slate-400">销项税额（输出税）</span>
            </div>
            <p className="text-2xl font-bold text-blue-400 tabular-nums">
              {formatCurrency(summary?.output_tax ?? 0)}
            </p>
            <p className="text-xs text-slate-500 mt-1">
              来自 {formatNumber(invoiceStats?.issued_count ?? 0)} 张有效发票
            </p>
          </div>

          {/* 进项税 */}
          <div className="p-4 rounded-xl bg-bg-input border border-[#2a3142]">
            <div className="flex items-center gap-2 mb-2">
              <TrendingDown className="w-4 h-4 text-accent" />
              <span className="text-xs text-slate-400">进项税额（可抵扣）</span>
            </div>
            <p className="text-2xl font-bold text-accent tabular-nums">
              {formatCurrency(summary?.input_tax ?? 0)}
            </p>
            <p className="text-xs text-slate-500 mt-1">
              来自已验证发票的税额
            </p>
          </div>

          {/* 应缴增值税 */}
          <div className="p-4 rounded-xl bg-primary/5 border border-primary/30">
            <div className="flex items-center gap-2 mb-2">
              <Wallet className="w-4 h-4 text-primary" />
              <span className="text-xs text-slate-400">应缴增值税</span>
            </div>
            <p className="text-2xl font-bold text-primary tabular-nums">
              {formatCurrency(summary?.vat_payable ?? 0)}
            </p>
            <div className="flex items-center gap-1 mt-1 text-xs text-slate-500">
              <span>{formatCurrency(summary?.output_tax ?? 0)}</span>
              <ArrowRight className="w-3 h-3" />
              <span>{formatCurrency(summary?.input_tax ?? 0)}</span>
              <ArrowRight className="w-3 h-3" />
              <span className="text-primary font-medium">{formatCurrency(summary?.vat_payable ?? 0)}</span>
            </div>
          </div>
        </div>
      </div>

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatBox
          title="发票总数"
          value={formatNumber(summary?.total_invoices ?? 0)}
          subValue={`价税合计 ${formatCurrency(invoiceStats?.total_with_tax ?? 0)}`}
          icon={Receipt}
          color="primary"
        />
        <StatBox
          title="发票税额合计"
          value={formatCurrency(invoiceStats?.total_tax ?? 0)}
          icon={FileText}
          color="accent"
        />
        <StatBox
          title="待处理报销"
          value={formatNumber(summary?.pending_reimbursements ?? 0)}
          subValue={`待付金额 ${formatCurrency(reimbStats?.total_pending_amount ?? 0)}`}
          icon={AlertCircle}
          color="paused"
        />
        <StatBox
          title="可抵扣报销费用"
          value={formatCurrency(summary?.deductible_expenses ?? 0)}
          subValue={`已付款 ${formatNumber(reimbStats?.paid_count ?? 0)} 笔`}
          icon={Wallet}
          color="accent"
        />
      </div>

      {/* 两栏布局：税率表 + 申报记录 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 税率表 */}
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-3">税率表</h3>
          <div className="space-y-2">
            {taxRates && Object.entries(taxRates).length > 0 ? (
              Object.entries(taxRates).map(([name, info]: [string, any]) => (
                <div key={name} className="flex items-center justify-between p-2.5 rounded-lg bg-bg-input border border-[#2a3142]">
                  <span className="text-sm text-slate-300">{name}</span>
                  <span className="text-sm font-semibold text-accent tabular-nums">
                    {typeof info === 'object' ? `${(info.rate * 100).toFixed(1)}%` : `${info}`}
                  </span>
                </div>
              ))
            ) : (
              <div className="text-center py-6 text-sm text-slate-500">暂无税率数据</div>
            )}
          </div>
        </div>

        {/* 税务申报记录 */}
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-3">税务申报记录</h3>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>税种</th>
                  <th>期间</th>
                  <th className="text-right">应缴税额</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {declarations.length > 0 ? (
                  declarations.map((decl: any, i: number) => (
                    <tr key={i}>
                      <td className="text-sm text-white">{decl.tax_type || decl.type || '—'}</td>
                      <td className="text-xs text-slate-400">{decl.period || decl.period_start || '—'}</td>
                      <td className="text-right tabular-nums text-accent">
                        {formatCurrency(decl.amount || decl.tax_amount || 0)}
                      </td>
                      <td>
                        <span className={cn(
                          'badge py-0',
                          decl.status === 'filed' || decl.status === 'submitted'
                            ? 'bg-accent/10 text-accent border-accent/30'
                            : decl.status === 'pending'
                              ? 'bg-status-paused/10 text-status-paused border-status-paused/30'
                              : 'bg-bg-input text-slate-400 border-[#2a3142]',
                        )}>
                          {decl.status === 'filed' || decl.status === 'submitted' ? '已申报' : decl.status === 'pending' ? '待申报' : decl.status || '—'}
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="text-center py-6 text-sm text-slate-500">暂无申报记录</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 税务记录 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-white mb-3">税务计算记录</h3>
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>税种</th>
                <th>计税依据</th>
                <th>税率</th>
                <th className="text-right">应缴税额</th>
                <th>计算时间</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {taxRecords.length > 0 ? (
                taxRecords.map((record: any, i: number) => (
                  <tr key={i}>
                    <td className="text-sm text-white">{record.tax_type || record.type || '—'}</td>
                    <td className="text-right tabular-nums text-slate-300">
                      {formatCurrency(record.taxable_amount || record.base_amount || 0)}
                    </td>
                    <td className="text-xs text-slate-400">
                      {record.tax_rate ? `${(record.tax_rate * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="text-right tabular-nums font-semibold text-accent">
                      {formatCurrency(record.tax_amount || record.amount || 0)}
                    </td>
                    <td className="text-xs text-slate-500">
                      {record.calculated_at ? formatDateTime(record.calculated_at) : '—'}
                    </td>
                    <td>
                      <span className={cn(
                        'badge py-0',
                        record.status === 'calculated'
                          ? 'bg-blue-500/10 text-blue-400 border-blue-500/30'
                          : 'bg-bg-input text-slate-400 border-[#2a3142]',
                      )}>
                        {record.status || '—'}
                      </span>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="text-center py-6 text-sm text-slate-500">暂无税务记录</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 发票类型税额分布 */}
      {invoiceStats?.by_type && Object.keys(invoiceStats.by_type).length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-3">发票类型税额分布</h3>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.entries(invoiceStats.by_type).map(([type, d]: [string, any]) => (
              <div key={type} className="p-3 rounded-lg bg-bg-input border border-[#2a3142]">
                <p className="text-xs text-slate-500 mb-1">{type}</p>
                <p className="text-sm font-bold text-white tabular-nums">{d.count} 张</p>
                <div className="mt-1 space-y-0.5">
                  <p className="text-[11px] text-slate-400">金额: {formatCurrency(d.amount)}</p>
                  <p className="text-[11px] text-accent">税额: {formatCurrency(d.tax)}</p>
                  <p className="text-[11px] text-slate-300">合计: {formatCurrency(d.total)}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 报销分类可抵扣分析 */}
      {reimbStats?.by_category && Object.keys(reimbStats.by_category).length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-3">报销分类费用分析</h3>
          <div className="grid grid-cols-3 lg:grid-cols-5 gap-3">
            {Object.entries(reimbStats.by_category).map(([cat, data]: [string, any]) => (
              <div key={cat} className="p-3 rounded-lg bg-bg-input border border-[#2a3142]">
                <p className="text-xs text-slate-500 mb-1">{CATEGORY_LABELS[cat] || cat}</p>
                <p className="text-base font-bold text-white tabular-nums">{data.count} 笔</p>
                <p className="text-xs text-accent tabular-nums mt-1">{formatCurrency(data.amount)}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ===================== 分类标签 ===================== */

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
