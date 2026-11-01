import { useState, useEffect, useCallback } from 'react'
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
import { apiService } from '../services/api'
import Can from '../components/Can'

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

/* ===================== HR 统计类型 ===================== */

interface HRStats {
  total_employees?: number
  active_employees?: number
  inactive_employees?: number
  by_department?: Record<string, number>
  total_attendance_records?: number
  total_leave_requests?: number
  pending_leave_requests?: number
  departments?: number
}

/* ===================== 数据映射 ===================== */

/** 将后端返回的员工数据映射为前端 Employee 结构（兼容 snake_case / camelCase） */
function mapEmployee(raw: Record<string, unknown>): Employee {
  const rawStatus = String(raw.status ?? 'active')
  let status: EmployeeStatus = 'active'
  if (rawStatus === 'probation') status = 'probation'
  else if (rawStatus === 'inactive' || rawStatus === 'resigned') status = 'resigned'
  return {
    id: String(raw.id ?? raw.employee_id ?? ''),
    name: String(raw.name ?? '—'),
    gender: String(raw.gender ?? '—'),
    department: String(raw.department ?? ''),
    position: String(raw.position ?? ''),
    hireDate: String(raw.hire_date ?? raw.hireDate ?? ''),
    status,
    phone: String(raw.phone ?? ''),
    email: String(raw.email ?? ''),
    education: String(raw.education ?? '—'),
    contractEnd: String(raw.contract_end ?? raw.contractEnd ?? '—'),
  }
}

/* ===================== 主组件 ===================== */

export default function EmployeeManagement() {
  const [selected, setSelected] = useState<Employee | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [employees, setEmployees] = useState<Employee[]>([])
  const [stats, setStats] = useState<HRStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, hrStats] = await Promise.all([
      apiService.getEmployees(),
      apiService.getHRStats(),
    ])
    setEmployees((Array.isArray(list) ? list : []).map(mapEmployee))
    setStats(hrStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const currentMonth = new Date().toISOString().slice(0, 7)
  const activeCount = stats?.active_employees ?? employees.filter((e) => e.status === 'active').length
  const hiredThisMonth = employees.filter((e) => e.hireDate.startsWith(currentMonth)).length
  const resignedCount = stats?.inactive_employees ?? employees.filter((e) => e.status === 'resigned').length
  const probationCount = employees.filter((e) => e.status === 'probation').length

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
        <Can
          resource="employee"
          action="update"
          fallback={<span className="text-xs text-slate-600">—</span>}
        >
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
        </Can>
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
          <Can resource="employee" action="create">
            <button onClick={() => setShowAdd(true)} className="btn-primary">
              <UserPlus className="w-4 h-4" />
              新增员工
            </button>
          </Can>
        }
      />

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="在职员工"
          value={activeCount}
          icon={<Users className="w-5 h-5" />}
          variant="primary"
          trend="编制 95"
          trendUp
        />
        <StatCard
          label="本月入职"
          value={hiredThisMonth}
          icon={<UserPlus className="w-5 h-5" />}
          variant="accent"
          trend="环比 +2"
          trendUp
        />
        <StatCard
          label="本月离职"
          value={resignedCount}
          icon={<UserMinus className="w-5 h-5" />}
          variant="warning"
          trend="环比 -1"
        />
        <StatCard
          label="待转正"
          value={probationCount}
          icon={<Clock className="w-5 h-5" />}
          variant="warning"
        />
      </div>

      {/* 员工列表 */}
      <Card>
        <DataTable
          columns={columns}
          data={employees}
          loading={loading}
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
