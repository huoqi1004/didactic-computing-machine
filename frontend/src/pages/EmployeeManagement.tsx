import { useState } from 'react'
import {
  Users,
  UserPlus,
  UserMinus,
  Clock,
  Mail,
  Phone,
  Building2,
  Briefcase,
  CalendarDays,
  IdCard,
  GraduationCap,
  Save,
  Eye,
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
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type EmployeeStatus = 'active' | 'probation' | 'resigned'

interface Employee {
  id: string
  name: string
  gender: string
  department: string
  position: string
  hireDate: string
  status: EmployeeStatus
  phone: string
  email: string
  education: string
  contractEnd: string
}

/* ===================== 状态配置 ===================== */

const statusConfig: Record<
  EmployeeStatus,
  { label: string; variant: 'success' | 'warning' | 'neutral' }
> = {
  active: { label: '在职', variant: 'success' },
  probation: { label: '试用期', variant: 'warning' },
  resigned: { label: '离职', variant: 'neutral' },
}

/* ===================== Mock 数据 ===================== */

const employees: Employee[] = [
  { id: 'EMP-001', name: '张伟', gender: '男', department: '技术部', position: '前端工程师', hireDate: '2021-03-15', status: 'active', phone: '138-0011-2233', email: 'zhangwei@company.com', education: '本科', contractEnd: '2027-03-14' },
  { id: 'EMP-002', name: '李娜', gender: '女', department: '产品部', position: '产品经理', hireDate: '2020-07-01', status: 'active', phone: '139-2233-4455', email: 'lina@company.com', education: '硕士', contractEnd: '2026-06-30' },
  { id: 'EMP-003', name: '王强', gender: '男', department: '技术部', position: '后端工程师', hireDate: '2022-09-05', status: 'active', phone: '137-4455-6677', email: 'wangqiang@company.com', education: '本科', contractEnd: '2028-09-04' },
  { id: 'EMP-004', name: '赵敏', gender: '女', department: '财务部', position: '会计主管', hireDate: '2019-04-20', status: 'active', phone: '136-6677-8899', email: 'zhaomin@company.com', education: '本科', contractEnd: '2026-04-19' },
  { id: 'EMP-005', name: '刘洋', gender: '男', department: '销售部', position: '销售经理', hireDate: '2024-02-13', status: 'probation', phone: '135-8899-0011', email: 'liuyang@company.com', education: '本科', contractEnd: '2025-02-12' },
  { id: 'EMP-006', name: '陈静', gender: '女', department: '人力资源部', position: 'HRBP', hireDate: '2021-11-08', status: 'active', phone: '138-1122-3344', email: 'chenjing@company.com', education: '硕士', contractEnd: '2027-11-07' },
  { id: 'EMP-007', name: '杨光', gender: '男', department: '技术部', position: '测试工程师', hireDate: '2024-06-03', status: 'probation', phone: '139-3344-5566', email: 'yangguang@company.com', education: '本科', contractEnd: '2025-06-02' },
  { id: 'EMP-008', name: '周婷', gender: '女', department: '市场部', position: '市场专员', hireDate: '2022-05-30', status: 'active', phone: '137-5566-7788', email: 'zhouting@company.com', education: '本科', contractEnd: '2027-05-29' },
  { id: 'EMP-009', name: '孙磊', gender: '男', department: '运营部', position: '运营专员', hireDate: '2024-10-21', status: 'probation', phone: '136-7788-9900', email: 'sunlei@company.com', education: '大专', contractEnd: '2025-10-20' },
  { id: 'EMP-010', name: '吴雪', gender: '女', department: '技术部', position: 'UI 设计师', hireDate: '2021-08-12', status: 'active', phone: '135-9900-1122', email: 'wuxue@company.com', education: '本科', contractEnd: '2026-08-11' },
  { id: 'EMP-011', name: '郑凯', gender: '男', department: '销售部', position: '销售代表', hireDate: '2023-10-20', status: 'resigned', phone: '138-2233-5566', email: 'zhengkai@company.com', education: '本科', contractEnd: '2024-10-19' },
  { id: 'EMP-012', name: '黄丽', gender: '女', department: '财务部', position: '出纳', hireDate: '2020-12-01', status: 'active', phone: '139-4455-7788', email: 'huangli@company.com', education: '大专', contractEnd: '2026-11-30' },
]

/* ===================== 主组件 ===================== */

export default function EmployeeManagement() {
  const [selected, setSelected] = useState<Employee | null>(null)
  const [showAdd, setShowAdd] = useState(false)

  const columns: Column<Employee>[] = [
    {
      key: 'id',
      label: '工号',
      sortable: true,
      render: (e) => <span className="font-mono text-xs text-primary">{e.id}</span>,
    },
    {
      key: 'name',
      label: '姓名',
      sortable: true,
      render: (e) => <span className="font-medium text-white">{e.name}</span>,
    },
    { key: 'department', label: '部门', sortable: true },
    { key: 'position', label: '职位' },
    { key: 'hireDate', label: '入职日期', sortable: true },
    {
      key: 'status',
      label: '状态',
      render: (e) => {
        const cfg = statusConfig[e.status]
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
      render: (e) => (
        <button
          onClick={(ev) => {
            ev.stopPropagation()
            setSelected(e)
          }}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-primary hover:bg-primary/10 transition-colors"
        >
          <Eye className="w-3.5 h-3.5" />
          查看
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="员工管理"
        description="员工档案、入职转正与人员结构管理"
        icon={<Users className="w-5 h-5" />}
        actions={
          <button onClick={() => setShowAdd(true)} className="btn-primary">
            <UserPlus className="w-4 h-4" />
            新增员工
          </button>
        }
      />

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="在职员工"
          value={86}
          icon={<Users className="w-5 h-5" />}
          variant="primary"
          trend="编制 95"
          trendUp
        />
        <StatCard
          label="本月入职"
          value={3}
          icon={<UserPlus className="w-5 h-5" />}
          variant="accent"
          trend="环比 +2"
          trendUp
        />
        <StatCard
          label="本月离职"
          value={1}
          icon={<UserMinus className="w-5 h-5" />}
          variant="warning"
          trend="环比 -1"
        />
        <StatCard
          label="待转正"
          value={5}
          icon={<Clock className="w-5 h-5" />}
          variant="warning"
        />
      </div>

      {/* 员工列表 */}
      <Card>
        <DataTable
          columns={columns}
          data={employees}
          searchKeys={['name', 'id']}
          searchPlaceholder="搜索姓名 / 工号..."
          onRowClick={(e) => setSelected(e)}
        />
      </Card>

      {/* 员工详情 */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="员工详情" size="lg">
        {selected && <EmployeeDetail employee={selected} />}
      </Modal>

      {/* 新增员工 */}
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="新增员工" size="lg">
        <AddEmployeeForm onCancel={() => setShowAdd(false)} />
      </Modal>
    </div>
  )
}

/* ===================== 员工详情 ===================== */

function EmployeeDetail({ employee }: { employee: Employee }) {
  const cfg = statusConfig[employee.status]
  const items = [
    { icon: IdCard, label: '工号', value: employee.id },
    { icon: Users, label: '姓名', value: `${employee.name}（${employee.gender}）` },
    { icon: Building2, label: '部门', value: employee.department },
    { icon: Briefcase, label: '职位', value: employee.position },
    { icon: CalendarDays, label: '入职日期', value: employee.hireDate },
    { icon: CalendarDays, label: '合同到期', value: employee.contractEnd },
    { icon: GraduationCap, label: '学历', value: employee.education },
    { icon: Phone, label: '联系电话', value: employee.phone },
    { icon: Mail, label: '邮箱', value: employee.email },
  ]

  return (
    <div className="space-y-5">
      {/* 头部 */}
      <div className="flex items-center gap-4 p-4 rounded-xl bg-bg-input border border-[#2a3142]">
        <div className="w-14 h-14 rounded-full bg-gradient-to-br from-primary/30 to-accent/30 flex items-center justify-center text-xl font-bold text-white shrink-0">
          {employee.name.charAt(0)}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-white">{employee.name}</h3>
            <StatusBadge variant={cfg.variant} dot>
              {cfg.label}
            </StatusBadge>
          </div>
          <p className="text-sm text-slate-400 mt-0.5">
            {employee.department} · {employee.position}
          </p>
        </div>
      </div>

      {/* 信息网格 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {items.map((item) => (
          <div
            key={item.label}
            className="flex items-center gap-3 p-3 rounded-lg bg-bg-input/60 border border-[#1e2536]"
          >
            <div className="w-8 h-8 rounded-md bg-primary/10 flex items-center justify-center shrink-0">
              <item.icon className="w-4 h-4 text-primary" />
            </div>
            <div className="min-w-0">
              <p className="text-xs text-slate-500">{item.label}</p>
              <p className="text-sm text-white truncate">{item.value}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ===================== 新增员工表单 ===================== */

function AddEmployeeForm({ onCancel }: { onCancel: () => void }) {
  const [form, setForm] = useState({
    id: '',
    name: '',
    gender: '男',
    department: '技术部',
    position: '',
    hireDate: '',
    phone: '',
    email: '',
  })

  const departments = ['技术部', '产品部', '市场部', '财务部', '人力资源部', '销售部', '运营部']

  const handleSubmit = () => {
    // Mock：仅关闭弹窗
    onCancel()
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <FormField label="工号" required>
          <input
            className={inputClass}
            placeholder="如：EMP-013"
            value={form.id}
            onChange={(e) => setForm({ ...form, id: e.target.value })}
          />
        </FormField>
        <FormField label="姓名" required>
          <input
            className={inputClass}
            placeholder="请输入姓名"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </FormField>
        <FormField label="性别">
          <select
            className={inputClass}
            value={form.gender}
            onChange={(e) => setForm({ ...form, gender: e.target.value })}
          >
            <option value="男">男</option>
            <option value="女">女</option>
          </select>
        </FormField>
        <FormField label="部门">
          <select
            className={inputClass}
            value={form.department}
            onChange={(e) => setForm({ ...form, department: e.target.value })}
          >
            {departments.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </FormField>
        <FormField label="职位">
          <input
            className={inputClass}
            placeholder="如：前端工程师"
            value={form.position}
            onChange={(e) => setForm({ ...form, position: e.target.value })}
          />
        </FormField>
        <FormField label="入职日期" required>
          <input
            type="date"
            className={inputClass}
            value={form.hireDate}
            onChange={(e) => setForm({ ...form, hireDate: e.target.value })}
          />
        </FormField>
        <FormField label="联系电话">
          <input
            className={inputClass}
            placeholder="138-0000-0000"
            value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
          />
        </FormField>
        <FormField label="邮箱">
          <input
            className={inputClass}
            placeholder="name@company.com"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
        </FormField>
      </div>

      <div className="flex justify-end gap-3 pt-2">
        <button onClick={onCancel} className="btn-secondary">
          取消
        </button>
        <button
          onClick={handleSubmit}
          disabled={!form.id || !form.name || !form.hireDate}
          className={cn('btn-primary')}
        >
          <Save className="w-4 h-4" />
          保存
        </button>
      </div>
    </div>
  )
}
