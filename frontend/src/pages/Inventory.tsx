import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Package,
  Plus,
  Minus,
  TrendingDown,
  AlertTriangle,
  Search,
  RefreshCw,
  ShoppingCart,
  Warehouse,
  Boxes,
} from 'lucide-react'
import {
  type InventoryItem,
  type InventoryStatus,
  type RestockSuggestion,
} from '../stores/systemStore'
import { apiService } from '../services/api'
import {
  INVENTORY_STATUS_CONFIG,
  formatCurrency,
  formatNumber,
  cn,
} from '../lib/utils'
import ExportButton from '../components/ExportButton'
import Can from '../components/Can'

export default function Inventory() {
  const [inventory, setInventory] = useState<InventoryItem[]>([])
  const [restockSuggestions, setRestockSuggestions] = useState<RestockSuggestion[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filterStatus, setFilterStatus] = useState<InventoryStatus | 'all'>('all')
  const [adjustModal, setAdjustModal] = useState<InventoryItem | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [inventoryList, restockList] = await Promise.all([
      apiService.getInventory(),
      apiService.getRestockSuggestions(),
    ])
    setInventory(inventoryList)
    setRestockSuggestions(restockList)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const filtered = inventory.filter((item) => {
    const matchSearch =
      item.name.includes(search) ||
      item.sku.toLowerCase().includes(search.toLowerCase()) ||
      item.category.includes(search)
    const matchStatus = filterStatus === 'all' || item.status === filterStatus
    return matchSearch && matchStatus
  })

  const totalValue = inventory.reduce((sum, i) => sum + i.quantity * i.unit_price, 0)
  const lowCount = inventory.filter((i) => i.status === 'low' || i.status === 'out').length

  // 导出数据构造：库存盘点表
  const inventoryExportData = useMemo(() => {
    const today = new Date().toISOString().split('T')[0]
    return {
      title: '库存盘点表',
      warehouse: '全部仓库',
      count_date: today,
      items: {
        headers: [
          '序号',
          'SKU',
          '品名',
          '分类',
          '库存量',
          '安全库存',
          '单价',
          '库存价值',
          '仓库',
          '状态',
        ],
        rows: inventory.map((item, idx) => [
          idx + 1,
          item.sku,
          item.name,
          item.category,
          item.quantity,
          item.safety_stock,
          item.unit_price,
          item.quantity * item.unit_price,
          item.warehouse,
          INVENTORY_STATUS_CONFIG[item.status].label,
        ]),
      },
    }
  }, [inventory])

  // 导出数据构造：智能补货建议清单
  const restockExportData = useMemo(
    () => ({
      title: '智能补货建议清单',
      headers: ['SKU', '品名', '当前库存', '建议补货', '预估成本', '建议原因'],
      rows: restockSuggestions.map((s) => [
        s.sku,
        s.name,
        s.current_qty,
        s.suggested_qty,
        s.estimated_cost,
        s.reason,
      ]),
    }),
    [restockSuggestions],
  )

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">库存管理</h2>
          <p className="text-sm text-slate-500 mt-0.5">库存监控、调整与智能补货建议</p>
        </div>
        <div className="flex items-center gap-2">
          <ExportButton
            agentId="inventory"
            templateType="inventory"
            filename="库存盘点表"
            data={inventoryExportData}
            title="库存盘点表"
            formats={['xlsx']}
            label="导出盘点表"
            size="sm"
          />
          <ExportButton
            agentId="inventory"
            templateType="basic"
            filename="智能补货建议清单"
            data={restockExportData}
            title="智能补货建议清单"
            formats={['xlsx']}
            label="导出补货清单"
            size="sm"
          />
        </div>
      </div>

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <OverviewCard
          title="库存品类"
          value={formatNumber(inventory.length)}
          icon={Boxes}
          color="primary"
        />
        <OverviewCard
          title="库存总价值"
          value={formatCurrency(totalValue)}
          icon={Package}
          color="accent"
        />
        <OverviewCard
          title="预警品类"
          value={String(lowCount)}
          icon={AlertTriangle}
          color="paused"
        />
        <OverviewCard
          title="补货建议"
          value={String(restockSuggestions.length)}
          icon={ShoppingCart}
          color="circuit"
        />
      </div>

      {/* 补货建议 */}
      {restockSuggestions.length > 0 && (
        <div className="card">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-lg bg-status-circuit/10 flex items-center justify-center">
              <TrendingDown className="w-4 h-4 text-status-circuit" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">智能补货建议</h3>
              <p className="text-xs text-slate-500">由库存管家 Agent 自动分析生成</p>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>品名</th>
                  <th className="text-right">当前库存</th>
                  <th className="text-right">建议补货</th>
                  <th className="text-right">预估成本</th>
                  <th>建议原因</th>
                </tr>
              </thead>
              <tbody>
                {restockSuggestions.map((s) => (
                  <tr key={s.sku}>
                    <td className="font-mono text-xs text-primary">{s.sku}</td>
                    <td className="font-medium text-white">{s.name}</td>
                    <td className="text-right tabular-nums text-status-circuit">{s.current_qty}</td>
                    <td className="text-right tabular-nums text-accent font-semibold">+{s.suggested_qty}</td>
                    <td className="text-right tabular-nums text-slate-300">{formatCurrency(s.estimated_cost)}</td>
                    <td className="text-xs text-slate-400 max-w-xs">{s.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 库存列表 */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h3 className="text-sm font-semibold text-white">库存明细</h3>
          <div className="flex items-center gap-2 flex-wrap">
            {/* 状态筛选 */}
            <div className="flex items-center gap-1">
              {(['all', 'normal', 'low', 'out', 'overstock'] as const).map((st) => (
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
                  {st === 'all' ? '全部' : INVENTORY_STATUS_CONFIG[st as InventoryStatus].label}
                </button>
              ))}
            </div>
            {/* 搜索 */}
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索品名/SKU..."
                className="input !py-1.5 !pl-8 w-44 text-xs"
              />
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>SKU</th>
                <th>品名</th>
                <th>分类</th>
                <th className="text-right">库存量</th>
                <th className="text-right">安全库存</th>
                <th className="text-right">单价</th>
                <th className="text-right">库存价值</th>
                <th>仓库</th>
                <th>状态</th>
                <th className="text-center">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => {
                const cfg = INVENTORY_STATUS_CONFIG[item.status]
                const stockRatio = Math.min((item.quantity / item.max_stock) * 100, 100)
                return (
                  <tr key={item.id}>
                    <td className="font-mono text-xs text-primary">{item.sku}</td>
                    <td className="font-medium text-white">{item.name}</td>
                    <td className="text-slate-400">{item.category}</td>
                    <td className="text-right">
                      <span className="tabular-nums font-semibold text-white">
                        {formatNumber(item.quantity)}
                      </span>
                      <span className="text-xs text-slate-500 ml-1">{item.unit}</span>
                    </td>
                    <td className="text-right tabular-nums text-slate-400">
                      {formatNumber(item.safety_stock)}
                    </td>
                    <td className="text-right tabular-nums text-slate-300">
                      {formatCurrency(item.unit_price)}
                    </td>
                    <td className="text-right tabular-nums text-accent">
                      {formatCurrency(item.quantity * item.unit_price)}
                    </td>
                    <td>
                      <span className="flex items-center gap-1 text-xs text-slate-400">
                        <Warehouse className="w-3 h-3" />
                        {item.warehouse}
                      </span>
                    </td>
                    <td>
                      <span className={cn('badge', cfg.bg, cfg.color, cfg.border, 'py-0')}>{cfg.label}</span>
                      {/* 库存进度条 */}
                      <div className="mt-1 w-20 h-1 rounded-full bg-bg-input overflow-hidden">
                        <div
                          className={cn('h-full rounded-full', cfg.color.replace('text-', 'bg-'))}
                          style={{ width: `${stockRatio}%` }}
                        />
                      </div>
                    </td>
                    <td>
                      <div className="flex items-center justify-center gap-1">
                        <Can
                          resource="inventory"
                          action="update"
                          fallback={<span className="text-xs text-slate-600">—</span>}
                        >
                          <button
                            onClick={() => setAdjustModal(item)}
                            className="p-1.5 rounded-md bg-bg-hover hover:bg-primary/20 text-slate-400 hover:text-primary transition-colors"
                            title="调整库存"
                          >
                            <Plus className="w-3.5 h-3.5" />
                          </button>
                        </Can>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <div className="text-center py-8 text-sm text-slate-500">
              {loading ? '加载中...' : '未找到匹配的库存项'}
            </div>
          )}
        </div>
      </div>

      {/* 调整库存弹窗 */}
      {adjustModal && (
        <AdjustModal
          item={adjustModal}
          onClose={() => setAdjustModal(null)}
          onSubmit={async (delta, reason) => {
            await apiService.adjustInventory(adjustModal.sku, delta, reason)
            setAdjustModal(null)
            fetchData()
          }}
        />
      )}
    </div>
  )
}

function OverviewCard({
  title,
  value,
  icon: Icon,
  color,
}: {
  title: string
  value: string
  icon: typeof Package
  color: 'primary' | 'accent' | 'paused' | 'circuit'
}) {
  const colorMap = {
    primary: 'text-primary bg-primary/10',
    accent: 'text-accent bg-accent/10',
    paused: 'text-status-paused bg-status-paused/10',
    circuit: 'text-status-circuit bg-status-circuit/10',
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
        </div>
      </div>
    </div>
  )
}

function AdjustModal({
  item,
  onClose,
  onSubmit,
}: {
  item: InventoryItem
  onClose: () => void
  onSubmit: (delta: number, reason: string) => Promise<void>
}) {
  const [type, setType] = useState<'in' | 'out'>('in')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async () => {
    const amt = parseInt(amount, 10)
    if (!amt || amt <= 0 || !reason.trim()) return
    setLoading(true)
    await onSubmit(type === 'in' ? amt : -amt, reason.trim())
    setLoading(false)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md card !p-6 animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-5">
          <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
            <Package className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-white">调整库存</h3>
            <p className="text-xs text-slate-500">
              {item.name} · {item.sku}
            </p>
          </div>
        </div>

        {/* 当前库存 */}
        <div className="flex items-center justify-between p-3 rounded-lg bg-bg-input mb-4">
          <span className="text-xs text-slate-500">当前库存</span>
          <span className="text-sm font-semibold text-white tabular-nums">
            {formatNumber(item.quantity)} {item.unit}
          </span>
        </div>

        {/* 出入库切换 */}
        <div className="grid grid-cols-2 gap-2 mb-4">
          <button
            onClick={() => setType('in')}
            className={cn(
              'flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all',
              type === 'in'
                ? 'bg-accent/15 text-accent border border-accent/30'
                : 'bg-bg-input text-slate-400 border border-[#2a3142]',
            )}
          >
            <Plus className="w-4 h-4" /> 入库
          </button>
          <button
            onClick={() => setType('out')}
            className={cn(
              'flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all',
              type === 'out'
                ? 'bg-status-circuit/15 text-status-circuit border border-status-circuit/30'
                : 'bg-bg-input text-slate-400 border border-[#2a3142]',
            )}
          >
            <Minus className="w-4 h-4" /> 出库
          </button>
        </div>

        {/* 数量 */}
        <div className="mb-4">
          <label className="label">调整数量</label>
          <input
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="请输入数量"
            min="1"
            className="input"
          />
        </div>

        {/* 原因 */}
        <div className="mb-5">
          <label className="label">调整原因</label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="请输入出入库原因..."
            rows={2}
            className="input resize-none"
          />
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-3">
          <button onClick={onClose} className="btn-secondary flex-1">
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !amount || !reason.trim()}
            className="btn-primary flex-1"
          >
            {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : null}
            确认调整
          </button>
        </div>
      </div>
    </div>
  )
}
