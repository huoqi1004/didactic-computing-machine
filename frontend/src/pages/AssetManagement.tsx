import { useState } from 'react'
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

/* ===================== Mock 数据 ===================== */

const ASSETS: Asset[] = [
  {
    id: '1',
    code: 'AST-IT-0001',
    name: '戴尔 PowerEdge R750 服务器',
    category: 'it',
    user: '陈强',
    location: '机房 A-01',
    purchase_date: '2024-03-15',
    original_value: 86000,
    net_value: 51600,
    status: 'in_use',
  },
  {
    id: '2',
    code: 'AST-IT-0002',
    name: 'MacBook Pro 16 寸',
    category: 'it',
    user: '张明',
    location: '研发部 3F',
    purchase_date: '2025-06-20',
    original_value: 22999,
    net_value: 18399,
    status: 'in_use',
  },
  {
    id: '3',
    code: 'AST-FN-0105',
    name: '人体工学办公椅（赫曼米勒）',
    category: 'furniture',
    user: '李华',
    location: '行政部 5F',
    purchase_date: '2023-09-10',
    original_value: 12800,
    net_value: 7680,
    status: 'in_use',
  },
  {
    id: '4',
    code: 'AST-PD-0201',
    name: 'CNC 数控加工中心',
    category: 'production',
    user: '孙伟',
    location: '车间 B-03',
    purchase_date: '2022-01-08',
    original_value: 680000,
    net_value: 340000,
    status: 'maintenance',
  },
  {
    id: '5',
    code: 'AST-VH-0030',
    name: '丰田叉车 8FGU25',
    category: 'vehicle',
    user: '仓库组',
    location: '仓库 C 区',
    purchase_date: '2023-04-22',
    original_value: 156000,
    net_value: 109200,
    status: 'in_use',
  },
  {
    id: '6',
    code: 'AST-PR-0001',
    name: '研发中心办公楼',
    category: 'property',
    user: '行政部',
    location: '科技园 3 号楼',
    purchase_date: '2020-12-01',
    original_value: 5200000,
    net_value: 4680000,
    status: 'in_use',
  },
  {
    id: '7',
    code: 'AST-IT-0003',
    name: '联想 ThinkPad X1 Carbon',
    category: 'it',
    user: '—',
    location: 'IT 仓库',
    purchase_date: '2024-11-30',
    original_value: 13999,
    net_value: 10499,
    status: 'idle',
  },
  {
    id: '8',
    code: 'AST-PD-0202',
    name: '注塑机海天 MA2500',
    category: 'production',
    user: '孙伟',
    location: '车间 B-05',
    purchase_date: '2021-07-15',
    original_value: 420000,
    net_value: 168000,
    status: 'in_use',
  },
  {
    id: '9',
    code: 'AST-IT-0004',
    name: '华为 24 口千兆交换机',
    category: 'it',
    user: '陈强',
    location: '机房 A-02',
    purchase_date: '2023-02-28',
    original_value: 8500,
    net_value: 4250,
    status: 'scrapped',
  },
  {
    id: '10',
    code: 'AST-FN-0106',
    name: '电动升降办公桌',
    category: 'furniture',
    user: '王芳',
    location: '市场部 4F',
    purchase_date: '2024-08-05',
    original_value: 4600,
    net_value: 3680,
    status: 'in_use',
  },
  {
    id: '11',
    code: 'AST-VH-0031',
    name: '别克 GL8 商务车',
    category: 'vehicle',
    user: '行政部',
    location: '地下车库',
    purchase_date: '2024-05-18',
    original_value: 289000,
    net_value: 231200,
    status: 'in_use',
  },
  {
    id: '12',
    code: 'AST-PD-0203',
    name: '激光切割机',
    category: 'production',
    user: '—',
    location: '车间 B-06',
    purchase_date: '2019-10-12',
    original_value: 380000,
    net_value: 76000,
    status: 'idle',
  },
]

/* ===================== 主组件 ===================== */

export default function AssetManagement() {
  const [showAddModal, setShowAddModal] = useState(false)

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
          value="1,286"
          icon={<Boxes className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="在用资产"
          value="1,082"
          icon={<PackageCheck className="w-5 h-5" />}
          trend="84.1%"
          trendUp
          variant="accent"
        />
        <StatCard
          label="闲置资产"
          value="86"
          icon={<PackageMinus className="w-5 h-5" />}
          trend="待调配"
          trendUp={false}
          variant="warning"
        />
        <StatCard
          label="资产总值"
          value="¥8,560,000"
          icon={<Wallet className="w-5 h-5" />}
          variant="primary"
        />
      </div>

      {/* 资产列表 */}
      <Card title="资产列表">
        <DataTable
          columns={columns}
          data={ASSETS}
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
