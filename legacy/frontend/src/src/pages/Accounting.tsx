import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Calculator,
  CheckCircle2,
  XCircle,
  CalendarCheck,
  FileText,
  Clock,
  TrendingUp,
  RefreshCw,
  Wallet,
} from 'lucide-react'
import { type Voucher, type VoucherStatus } from '../stores/systemStore'
import { apiService } from '../services/api'
import { VOUCHER_STATUS_CONFIG, formatCurrency, formatNumber, formatDateTime, cn } from '../lib/utils'
import ExportButton from '../components/ExportButton'
import Can from '../components/Can'

export default function Accounting() {
  const [vouchers, setVouchers] = useState<Voucher[]>([])
  const [loading, setLoading] = useState(true)
  const [filterStatus, setFilterStatus] = useState<VoucherStatus | 'all'>('all')
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [settling, setSettling] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const voucherList = await apiService.getVouchers()
    setVouchers(voucherList)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const filtered = vouchers.filter((v) => filterStatus === 'all' || v.status === filterStatus)
  const pending = vouchers.filter((v) => v.status === 'pending')
  const approved = vouchers.filter((v) => v.status === 'approved')
  const posted = vouchers.filter((v) => v.status === 'posted')

  const totalAmount = vouchers.reduce((sum, v) => sum + v.amount, 0)
  const pendingAmount = pending.reduce((sum, v) => sum + v.amount, 0)

  // 构造会计凭证汇总表导出数据
  const voucherExportData = useMemo<Record<string, unknown>>(() => ({
    title: '会计凭证汇总表',
    sheet_name: '会计凭证',
    headers: ['凭证号', '日期', '摘要', '借方科目', '贷方科目', '金额', '制单人', '状态'],
    rows: vouchers.map((v) => [
      v.voucher_no,
      v.date,
      v.summary,
      v.debit_account,
      v.credit_account,
      v.amount,
      v.created_by,
      (VOUCHER_STATUS_CONFIG[v.status] ?? Object.values(VOUCHER_STATUS_CONFIG)[0]).label,
    ]),
    notes: [
      `凭证总数：${vouchers.length} 张`,
      `凭证总金额：${formatCurrency(totalAmount)}`,
    ],
  }), [vouchers, totalAmount])

  const handleApprove = async (id: string) => {
    setActionLoading(id)
    await apiService.approveVoucher(id)
    setActionLoading(null)
    fetchData()
  }
  const handleReject = async (id: string) => {
    setActionLoading(id)
    await apiService.rejectVoucher(id)
    setActionLoading(null)
    fetchData()
  }
  const handleSettlement = async () => {
    setSettling(true)
    await apiService.dailySettlement()
    setSettling(false)
    fetchData()
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">会计凭证</h2>
          <p className="text-sm text-slate-500 mt-0.5">凭证管理、审批操作与日清日结</p>
        </div>
        <div className="flex items-center gap-2">
          <Can resource="accounting" action="approve">
            <button
              onClick={handleSettlement}
              disabled={settling || approved.length === 0}
              className="btn-accent"
            >
              {settling ? <RefreshCw className="w-4 h-4 animate-spin" /> : <CalendarCheck className="w-4 h-4" />}
              执行日清日结
            </button>
          </Can>
          <Can resource="document" action="create">
            <ExportButton
              agentId="accounting"
              templateType="voucher"
              filename="会计凭证汇总表"
              data={voucherExportData}
              title="会计凭证汇总表"
              formats={['xlsx']}
              label="导出凭证表"
            />
          </Can>
        </div>
      </div>

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatBox
          title="凭证总数"
          value={formatNumber(vouchers.length)}
          icon={FileText}
          color="primary"
        />
        <StatBox
          title="待审批"
          value={formatNumber(pending.length)}
          subValue={formatCurrency(pendingAmount)}
          icon={Clock}
          color="paused"
        />
        <StatBox
          title="已审批"
          value={formatNumber(approved.length)}
          icon={CheckCircle2}
          color="accent"
        />
        <StatBox
          title="凭证总金额"
          value={formatCurrency(totalAmount)}
          icon={Wallet}
          color="primary"
        />
      </div>

      {/* 待审批凭证（高亮卡片） */}
      {pending.length > 0 && (
        <div className="card border-status-paused/30">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-lg bg-status-paused/10 flex items-center justify-center">
              <Clock className="w-4 h-4 text-status-paused" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">待审批凭证</h3>
              <p className="text-xs text-slate-500">由会计专员 Agent 自动生成，需人工复核</p>
            </div>
            <span className="ml-auto badge bg-status-paused/10 text-status-paused border-status-paused/30">
              {pending.length} 张待处理
            </span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {pending.map((v) => (
              <PendingVoucherCard
                key={v.id}
                voucher={v}
                loading={actionLoading === v.id}
                onApprove={() => handleApprove(v.id)}
                onReject={() => handleReject(v.id)}
              />
            ))}
          </div>
        </div>
      )}

      {/* 凭证列表 */}
      <div className="card">
        <div className="flex items-center justify-between gap-3 mb-4">
          <h3 className="text-sm font-semibold text-white">凭证列表</h3>
          <div className="flex items-center gap-1">
            {(['all', 'pending', 'approved', 'rejected', 'posted'] as const).map((st) => (
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
                {st === 'all' ? '全部' : VOUCHER_STATUS_CONFIG[st as VoucherStatus].label}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>凭证号</th>
                <th>日期</th>
                <th>摘要</th>
                <th>借方科目</th>
                <th>贷方科目</th>
                <th className="text-right">金额</th>
                <th>制单人</th>
                <th>状态</th>
                <th className="text-center">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((v) => {
                const cfg = VOUCHER_STATUS_CONFIG[v.status] ?? Object.values(VOUCHER_STATUS_CONFIG)[0]
                return (
                  <tr key={v.id}>
                    <td className="font-mono text-xs text-primary">{v.voucher_no}</td>
                    <td className="text-slate-400 text-xs">{v.date}</td>
                    <td className="font-medium text-white max-w-[200px] truncate" title={v.summary}>
                      {v.summary}
                    </td>
                    <td className="text-slate-400 text-xs font-mono">{v.debit_account}</td>
                    <td className="text-slate-400 text-xs font-mono">{v.credit_account}</td>
                    <td className="text-right tabular-nums font-semibold text-accent">
                      {formatCurrency(v.amount)}
                    </td>
                    <td className="text-slate-400 text-xs">{v.created_by}</td>
                    <td>
                      <span className={cn('badge', cfg.bg, cfg.color, cfg.border, 'py-0')}>{cfg.label}</span>
                    </td>
                    <td>
                      <div className="flex items-center justify-center gap-1">
                        {v.status === 'pending' ? (
                          <>
                            <Can resource="accounting" action="approve" fallback={<span className="text-xs text-slate-600">—</span>}>
                              <button
                                onClick={() => handleApprove(v.id)}
                                disabled={actionLoading === v.id}
                                className="p-1.5 rounded-md bg-accent/10 hover:bg-accent/20 text-accent transition-colors disabled:opacity-50"
                                title="审批通过"
                              >
                                <CheckCircle2 className="w-3.5 h-3.5" />
                              </button>
                            </Can>
                            <Can resource="accounting" action="update" fallback={<span className="text-xs text-slate-600">—</span>}>
                              <button
                                onClick={() => handleReject(v.id)}
                                disabled={actionLoading === v.id}
                                className="p-1.5 rounded-md bg-status-circuit/10 hover:bg-status-circuit/20 text-status-circuit transition-colors disabled:opacity-50"
                                title="驳回"
                              >
                                <XCircle className="w-3.5 h-3.5" />
                              </button>
                            </Can>
                          </>
                        ) : (
                          <span className="text-xs text-slate-600">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <div className="text-center py-8 text-sm text-slate-500">
              {loading ? '加载中...' : '暂无凭证记录'}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

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

function PendingVoucherCard({
  voucher,
  loading,
  onApprove,
  onReject,
}: {
  voucher: Voucher
  loading: boolean
  onApprove: () => void
  onReject: () => void
}) {
  return (
    <div className="p-4 rounded-xl bg-bg-input border border-[#2a3142] hover:border-status-paused/40 transition-colors">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div>
          <span className="font-mono text-xs text-primary">{voucher.voucher_no}</span>
          <p className="text-sm font-medium text-white mt-1">{voucher.summary}</p>
        </div>
        <span className="badge bg-status-paused/10 text-status-paused border-status-paused/30 py-0 shrink-0">
          待审批
        </span>
      </div>

      {/* 借贷分录 */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="p-2 rounded-lg bg-bg-deep">
          <p className="text-[10px] text-slate-500">借方</p>
          <p className="text-xs font-mono text-slate-300">{voucher.debit_account}</p>
        </div>
        <div className="p-2 rounded-lg bg-bg-deep">
          <p className="text-[10px] text-slate-500">贷方</p>
          <p className="text-xs font-mono text-slate-300">{voucher.credit_account}</p>
        </div>
      </div>

      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-slate-500">
          制单：{voucher.created_by} · {formatDateTime(voucher.created_at)}
        </span>
        <span className="text-base font-bold text-accent tabular-nums">
          {formatCurrency(voucher.amount)}
        </span>
      </div>

      <div className="flex gap-2">
        <Can resource="accounting" action="approve">
          <button
            onClick={onApprove}
            disabled={loading}
            className="btn-accent flex-1 !py-1.5 text-xs"
          >
            {loading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
            审批通过
          </button>
        </Can>
        <Can resource="accounting" action="update">
          <button
            onClick={onReject}
            disabled={loading}
            className="btn-danger flex-1 !py-1.5 text-xs"
          >
            <XCircle className="w-3.5 h-3.5" />
            驳回
          </button>
        </Can>
      </div>
    </div>
  )
}
