import { useState } from 'react'
import { Briefcase, Users, UserCheck, UserPlus, UserMinus, Plus, Eye } from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  type Column,
} from '../components/ui'

/* ===================== 类型定义 ===================== */

type CustomerLevel = 'S' | 'A' | 'B' | 'C'
type CustomerStatus = 'active' | 'dormant' | 'churned'

interface Customer {
  id: string
  code: string
  name: string
  contact: string
  phone: string
  industry: string
  level: CustomerLevel
  status: CustomerStatus
  address: string
  email: string
  revenue: number
  createdAt: string
  lastContact: string
  remark: string
}

/* ===================== 状态配置 ===================== */

const LEVEL_VARIANT: Record<CustomerLevel, 'primary' | 'info' | 'success' | 'neutral'> = {
  S: 'primary',
  A: 'info',
  B: 'success',
  C: 'neutral',
}

const STATUS_VARIANT: Record<CustomerStatus, 'success' | 'warning' | 'danger'> = {
  active: 'success',
  dormant: 'warning',
  churned: 'danger',
}

const STATUS_LABEL: Record<CustomerStatus, string> = {
  active: '活跃',
  dormant: '沉睡',
  churned: '流失',
}

const fmtMoney = (n: number) => `¥${n.toLocaleString('zh-CN')}`

/* ===================== Mock 数据 ===================== */

const mockCustomers: Customer[] = [
  { id: '1', code: 'CUST-2024-001', name: '上海智云科技有限公司', contact: '张明', phone: '138-0000-1001', industry: '信息技术', level: 'S', status: 'active', address: '上海市浦东新区张江高科技园区', email: 'zhangming@zhiyun.com', revenue: 2860000, createdAt: '2023-03-15', lastContact: '2026-07-25', remark: '核心战略客户，年采购额稳定增长，合作意愿强。' },
  { id: '2', code: 'CUST-2024-002', name: '北京华信数据股份', contact: '李芳', phone: '139-0000-1002', industry: '金融服务', level: 'A', status: 'active', address: '北京市朝阳区国贸中心', email: 'lifang@huaxin.com', revenue: 1680000, createdAt: '2023-06-22', lastContact: '2026-07-20', remark: '重点客户，金融数据解决方案合作中。' },
  { id: '3', code: 'CUST-2024-003', name: '深圳前海智能制造', contact: '王强', phone: '137-0000-1003', industry: '智能制造', level: 'A', status: 'active', address: '深圳市南山区科技园', email: 'wangqiang@qianhai.com', revenue: 1320000, createdAt: '2023-08-10', lastContact: '2026-07-22', remark: '产线数字化改造项目进行中。' },
  { id: '4', code: 'CUST-2024-004', name: '广州绿源生物科技', contact: '赵丽', phone: '136-0000-1004', industry: '生物医药', level: 'B', status: 'dormant', address: '广州市黄埔区科学城', email: 'zhaoli@lvyuan.com', revenue: 580000, createdAt: '2023-11-05', lastContact: '2026-05-18', remark: '近两月无新订单，需主动跟进激活。' },
  { id: '5', code: 'CUST-2024-005', name: '杭州数联零售集团', contact: '陈浩', phone: '135-0000-1005', industry: '零售连锁', level: 'S', status: 'active', address: '杭州市余杭区未来科技城', email: 'chenhao@shulian.com', revenue: 3200000, createdAt: '2022-12-01', lastContact: '2026-07-26', remark: '全国门店数字化升级主力供应商。' },
  { id: '6', code: 'CUST-2024-006', name: '成都西部物流股份', contact: '孙伟', phone: '134-0000-1006', industry: '物流运输', level: 'B', status: 'active', address: '成都市龙泉驿区物流园', email: 'sunwei@xibu.com', revenue: 760000, createdAt: '2024-01-18', lastContact: '2026-07-15', remark: '仓储管理系统续约中。' },
  { id: '7', code: 'CUST-2024-007', name: '武汉光谷电子', contact: '周婷', phone: '133-0000-1007', industry: '电子信息', level: 'C', status: 'churned', address: '武汉市东湖高新区', email: 'zhouting@guanggu.com', revenue: 120000, createdAt: '2023-04-20', lastContact: '2026-02-10', remark: '合同到期未续约，已流失，建议制定挽回方案。' },
  { id: '8', code: 'CUST-2024-008', name: '南京建工集团', contact: '吴磊', phone: '132-0000-1008', industry: '建筑工程', level: 'B', status: 'dormant', address: '南京市建邺区河西新城', email: 'wulei@jiangong.com', revenue: 430000, createdAt: '2023-09-12', lastContact: '2026-06-05', remark: '项目验收阶段，后续待启动新需求。' },
  { id: '9', code: 'CUST-2024-009', name: '苏州工业园区能源', contact: '郑凯', phone: '131-0000-1009', industry: '能源电力', level: 'A', status: 'active', address: '苏州市工业园区独墅湖', email: 'zhengkai@nengyuan.com', revenue: 1950000, createdAt: '2023-02-28', lastContact: '2026-07-24', remark: '智慧能源管理平台长期合作。' },
  { id: '10', code: 'CUST-2024-010', name: '重庆山城食品', contact: '黄丽', phone: '130-0000-1010', industry: '食品饮料', level: 'C', status: 'active', address: '重庆市渝北区食品城', email: 'huangli@shancheng.com', revenue: 280000, createdAt: '2024-03-08', lastContact: '2026-07-19', remark: '供应链系统新签客户，处于培育期。' },
  { id: '11', code: 'CUST-2024-011', name: '天津港通贸易', contact: '林峰', phone: '188-0000-1011', industry: '进出口贸易', level: 'B', status: 'dormant', address: '天津市滨海新区', email: 'linfeng@tongmao.com', revenue: 510000, createdAt: '2023-07-30', lastContact: '2026-05-22', remark: '受外贸环境影响，项目暂缓推进。' },
  { id: '12', code: 'CUST-2024-012', name: '青岛海蓝新材料', contact: '高翔', phone: '199-0000-1012', industry: '新材料', level: 'A', status: 'active', address: '青岛市崂山区新材料产业园', email: 'gaoxiang@hailan.com', revenue: 1430000, createdAt: '2023-05-14', lastContact: '2026-07-23', remark: '研发数据中台项目交付中。' },
]

/* ===================== 主组件 ===================== */

export default function CustomerManagement() {
  const [selected, setSelected] = useState<Customer | null>(null)

  const columns: Column<Customer>[] = [
    {
      key: 'code',
      label: '客户编号',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.code}</span>,
    },
    {
      key: 'name',
      label: '客户名称',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    { key: 'contact', label: '联系人', render: (r) => <span className="text-slate-300">{r.contact}</span> },
    {
      key: 'phone',
      label: '电话',
      render: (r) => <span className="text-slate-400 font-mono text-xs">{r.phone}</span>,
    },
    { key: 'industry', label: '行业', render: (r) => <span className="text-slate-400">{r.industry}</span> },
    {
      key: 'level',
      label: '客户等级',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant={LEVEL_VARIANT[r.level]}>{r.level} 级</StatusBadge>,
    },
    {
      key: 'status',
      label: '状态',
      align: 'center',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={STATUS_VARIANT[r.status]} dot>
          {STATUS_LABEL[r.status]}
        </StatusBadge>
      ),
    },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <button
          onClick={(e) => {
            e.stopPropagation()
            setSelected(r)
          }}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
        >
          <Eye className="w-3.5 h-3.5" /> 查看
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="客户管理"
        description="客户档案、分级与全生命周期管理"
        icon={<Briefcase className="w-5 h-5" />}
        actions={
          <button className="btn-primary">
            <Plus className="w-4 h-4" /> 新增客户
          </button>
        }
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="总客户数" value={248} icon={<Users className="w-5 h-5" />} trend="较上月 +2.1%" trendUp variant="primary" />
        <StatCard label="活跃客户" value={186} icon={<UserCheck className="w-5 h-5" />} trend="活跃率 75%" trendUp variant="accent" />
        <StatCard label="本月新增" value={12} icon={<UserPlus className="w-5 h-5" />} trend="环比 +3 家" trendUp variant="primary" />
        <StatCard label="流失客户" value={3} icon={<UserMinus className="w-5 h-5" />} trend="需重点挽回" variant="danger" />
      </div>

      {/* 客户列表 */}
      <Card title="客户列表">
        <DataTable
          columns={columns}
          data={mockCustomers}
          searchKeys={['name', 'contact']}
          searchPlaceholder="搜索客户名称、联系人..."
          onRowClick={(r) => setSelected(r)}
          pageSize={10}
        />
      </Card>

      {/* 客户详情弹窗 */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="客户详情" size="lg">
        {selected && <CustomerDetail customer={selected} />}
      </Modal>
    </div>
  )
}

/* ===================== 客户详情 ===================== */

function CustomerDetail({ customer }: { customer: Customer }) {
  const rows: { label: string; value: string }[] = [
    { label: '客户编号', value: customer.code },
    { label: '客户名称', value: customer.name },
    { label: '联系人', value: customer.contact },
    { label: '联系电话', value: customer.phone },
    { label: '所属行业', value: customer.industry },
    { label: '客户等级', value: `${customer.level} 级` },
    { label: '客户状态', value: STATUS_LABEL[customer.status] },
    { label: '电子邮箱', value: customer.email },
    { label: '联系地址', value: customer.address },
    { label: '累计营收', value: fmtMoney(customer.revenue) },
    { label: '建档日期', value: customer.createdAt },
    { label: '最近联系', value: customer.lastContact },
  ]

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 flex-wrap">
        <StatusBadge variant={LEVEL_VARIANT[customer.level]} dot>
          {customer.level} 级客户
        </StatusBadge>
        <StatusBadge variant={STATUS_VARIANT[customer.status]} dot>
          {STATUS_LABEL[customer.status]}
        </StatusBadge>
        <StatusBadge variant="neutral">{customer.industry}</StatusBadge>
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
        <p className="text-xs text-slate-500 mb-1.5">客户备注</p>
        <p className="text-sm text-slate-300 leading-relaxed">{customer.remark}</p>
      </div>
    </div>
  )
}
