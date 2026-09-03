import { useState, useEffect, useCallback } from 'react'
import {
  Boxes,
  PackageCheck,
  PackageMinus,
  Wallet,
  Plus,
  Eye,
  Pencil,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  FormField,
  inputClass,
  type Column,
} from '../components/ui'
import { apiService } from '../services/api'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type AssetCategory = 'it' | 'furniture' | 'production' | 'vehicle' | 'property'
type AssetStatus = 'in_use' | 'idle' | 'maintenance' | 'scrapped'

interface Asset {
  id: string
  code: string
  name: string
  category: AssetCategory
  user: string
  location: string
  purchase_date: string
  original_value: number
  net_value: number
  status: AssetStatus
}

/* ===================== 状态配置 ===================== */

const CATEGORY_CONFIG: Record<AssetCategory, { label: string }> = {
  it: { label: 'IT 设备' },
  furniture: { label: '办公家具' },
  production: { label: '生产设备' },
  vehicle: { label: '车辆' },
  property: { label: '房产' },
}

const ASSET_STATUS_CONFIG: Record<
  AssetStatus,
  { label: string; variant: 'success' | 'neutral' | 'warning' | 'danger' }
> = {
  in_use: { label: '在用', variant: 'success' },
  idle: { label: '闲置', variant: 'neutral' },
  maintenance: { label: '维修中', variant: 'warning' },
  scrapped: { label: '已报废', variant: 'danger' },
}

/** 后端资产类别 → 前端 AssetCategory 映射 */
const ASSET_CATEGORY_MAP: Record<string, AssetCategory> = {
  it_equipment: 'it',
  office_equipment: 'furniture',
  machinery: 'production',
  vehicle: 'vehicle',
  property: 'property',
  real_estate: 'property',
}

/** 后端资产统计响应结构（字段均为可选，兼容降级） */
interface AssetStats {
  total_assets?: number
  in_use?: number
  disposed?: number
  total_original_value?: number
  total_net_value?: number
  total_accumulated_depreciation?: number
}

/* ===================== 数据映射 ===================== */

/** 将后端资产数据（snake_case）映射为前端 Asset 结构 */
function mapAsset(raw: any): Asset {
  const rawCategory = String(raw.category ?? '')
  const category: AssetCategory = ASSET_CATEGORY_MAP[rawCategory] ?? 'furniture'
  const rawStatus = String(raw.status ?? 'in_use')
  let status: AssetStatus = 'in_use'
  if (rawStatus === 'idle') {
    status = 'idle'
  } else if (rawStatus === 'disposed' || rawStatus === 'scrapped') {
    status = 'scrapped'
  } else if (rawStatus === 'maintenance') {
    status = 'maintenance'
  }
  return {
    id: String(raw.asset_id ?? raw.id ?? ''),
    code: String(raw.code ?? raw.asset_id ?? raw.id ?? ''),
    name: String(raw.name ?? '—'),
    category,
    user: String(raw.user ?? raw.department ?? '—'),
    location: String(raw.location ?? '—'),
    purchase_date: String(raw.purchase_date ?? ''),
    original_value: Number(raw.original_value ?? 0),
    net_value: Number(raw.net_value ?? 0),
    status,
  }
}

/* ===================== 主组件 ===================== */

export default function AssetManagement() {
  const [assets, setAssets] = useState<Asset[]>([])
  const [stats, setStats] = useState<AssetStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, assetStats] = await Promise.all([
      apiService.getAssets(),
      apiService.getAssetStats(),
    ])
    setAssets((Array.isArray(list) ? list : []).map(mapAsset))
    setStats(assetStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const totalAssets = stats?.total_assets ?? 0
  const inUseCount = stats?.in_use ?? 0
  const idleCount = Math.max(0, totalAssets - inUseCount - (stats?.disposed ?? 0))
  const totalValue = stats?.total_original_value ?? 0
  const inUseRatio = totalAssets > 0 ? (inUseCount / totalAssets) * 100 : 0

  const columns: Column<Asset>[] = [
    {
      key: 'code',
      label: '资产编号',
      sortable: true,
      render: (row) => <span className="font-mono text-xs text-primary">{row.code}</span>,
    },
    {
      key: 'name',
      label: '资产名称',
      sortable: true,
      render: (row) => <span className="font-medium text-white">{row.name}</span>,
    },
    {
      key: 'category',
      label: '类别',
      sortable: true,
      render: (row) => (
        <span className="text-slate-400">{CATEGORY_CONFIG[row.category].label}</span>
      ),
    },
    {
      key: 'user',
      label: '使用人',
      render: (row) => <span className="text-slate-300">{row.user}</span>,
    },
    {
      key: 'location',
      label: '所在位置',
      render: (row) => <span className="text-slate-400 text-xs">{row.location}</span>,
    },
    {
      key: 'purchase_date',
      label: '购置日期',
      sortable: true,
      render: (row) => <span className="text-slate-400 text-xs">{row.purchase_date}</span>,
    },
    {
      key: 'original_value',
      label: '原值',
      sortable: true,
      align: 'right',
      render: (row) => (
        <span className="tabular-nums text-slate-300">
          ¥{row.original_value.toLocaleString('zh-CN')}
        </span>
      ),
    },
    {
      key: 'net_value',
      label: '净值',
      sortable: true,
      align: 'right',
      render: (row) => (
        <span className="tabular-nums text-accent">
          ¥{row.net_value.toLocaleString('zh-CN')}
        </span>
      ),
    },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (row) => {
        const cfg = ASSET_STATUS_CONFIG[row.status]
        return (
          <StatusBadge variant={cfg.variant} dot>
            {cfg.label}
          </StatusBadge>
        )
      },
    },
    {
      key: 'action',
      label: '操作',
      align: 'center',
      render: () => (
        <div className="flex items-center justify-center gap-1">
          <button
            className="p-1.5 rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
            title="查看"
          >
            <Eye className="w-3.5 h-3.5" />
          </button>
          <button
            className="p-1.5 rounded-md bg-bg-input text-slate-400 hover:text-white border border-[#2a3142] transition-colors"
            title="编辑"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="资产管理"
        description="企业固定资产全生命周期管理与折旧跟踪"
        icon={<Boxes className="w-5 h-5" />}
        actions={
          <button
            onClick={() => setShowAddModal(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 transition-colors shadow-glow"
          >
            <Plus className="w-4 h-4" />
            新增资产
          </button>
        }
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="资产总数"
          value={totalAssets}
          icon={<Boxes className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="在用资产"
          value={inUseCount}
          icon={<PackageCheck className="w-5 h-5" />}
          trend={`${inUseRatio.toFixed(1)}%`}
          trendUp
          variant="accent"
        />
        <StatCard
          label="闲置资产"
          value={idleCount}
          icon={<PackageMinus className="w-5 h-5" />}
          trend="待调配"
          trendUp={false}
          variant="warning"
        />
        <StatCard
          label="资产总值"
          value={`¥${totalValue.toLocaleString('zh-CN')}`}
          icon={<Wallet className="w-5 h-5" />}
          variant="primary"
        />
      </div>

      {/* 资产列表 */}
      <Card title="资产列表">
        <DataTable
          columns={columns}
          data={assets}
          loading={loading}
          pageSize={10}
          searchKeys={['name', 'code']}
          searchPlaceholder="搜索资产名称/编号..."
        />
      </Card>

      {/* 新增资产弹窗 */}
      <Modal
        open={showAddModal}
        onClose={() => setShowAddModal(false)}
        title="新增资产"
        size="lg"
      >
        <AddAssetForm onClose={() => setShowAddModal(false)} />
      </Modal>
    </div>
  )
}

/* ===================== 新增资产表单 ===================== */

function AddAssetForm({ onClose }: { onClose: () => void }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <FormField label="资产编号" required>
          <input className={inputClass} placeholder="如 AST-IT-0001" />
        </FormField>
        <FormField label="资产名称" required>
          <input className={inputClass} placeholder="请输入资产名称" />
        </FormField>
        <FormField label="类别" required>
          <select className={inputClass}>
            <option value="">请选择类别</option>
            <option value="it">IT 设备</option>
            <option value="furniture">办公家具</option>
            <option value="production">生产设备</option>
            <option value="vehicle">车辆</option>
            <option value="property">房产</option>
          </select>
        </FormField>
        <FormField label="使用人">
          <input className={inputClass} placeholder="请输入使用人" />
        </FormField>
        <FormField label="所在位置">
          <input className={inputClass} placeholder="如 机房 A-01" />
        </FormField>
        <FormField label="购置日期" required>
          <input type="date" className={inputClass} />
        </FormField>
        <FormField label="原值（元）" required>
          <input type="number" className={inputClass} placeholder="0.00" />
        </FormField>
        <FormField label="净值（元）">
          <input type="number" className={inputClass} placeholder="0.00" />
        </FormField>
      </div>

      <FormField label="备注" hint="选填，可记录资产补充信息">
        <textarea
          className={`${inputClass} resize-none`}
          rows={2}
          placeholder="请输入备注..."
        />
      </FormField>

      <div className="flex gap-3 pt-2">
        <button
          onClick={onClose}
          className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-bg-input text-slate-200 hover:bg-[#242b3d] border border-[#2a3142] text-sm font-medium transition-colors"
        >
          取消
        </button>
        <button
          onClick={onClose}
          className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 transition-colors shadow-glow"
        >
          确认新增
        </button>
      </div>
    </div>
  )
}
