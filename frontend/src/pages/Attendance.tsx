import { useState } from 'react'
import {
  Clock,
  TimerReset,
  CalendarCheck,
  UserX,
  LogIn,
  LogOut,
  CalendarDays,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  StatCard,
  inputClass,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type AttendanceStatus = 'normal' | 'late' | 'early' | 'absent'

interface AttendanceRecord {
  id: string
  name: string
  department: string
  checkIn: string
  checkOut: string
  status: AttendanceStatus
  workHours: number
}

/* ===================== 状态配置 ===================== */

const statusConfig: Record<
  AttendanceStatus,
  { label: string; variant: 'success' | 'warning' | 'danger' }
> = {
  normal: { label: '正常', variant: 'success' },
  late: { label: '迟到', variant: 'warning' },
  early: { label: '早退', variant: 'warning' },
  absent: { label: '缺勤', variant: 'danger' },
}

/* ===================== Mock 数据 ===================== */

const attendanceRecords: AttendanceRecord[] = [
  { id: 'EMP-001', name: '张伟', department: '技术部', checkIn: '08:55', checkOut: '18:12', status: 'normal', workHours: 9.3 },
  { id: 'EMP-002', name: '李娜', department: '产品部', checkIn: '09:02', checkOut: '18:30', status: 'late', workHours: 9.5 },
  { id: 'EMP-003', name: '王强', department: '技术部', checkIn: '08:48', checkOut: '18:05', status: 'normal', workHours: 9.3 },
  { id: 'EMP-004', name: '赵敏', department: '财务部', checkIn: '08:58', checkOut: '17:30', status: 'early', workHours: 8.5 },
  { id: 'EMP-005', name: '刘洋', department: '销售部', checkIn: '09:18', checkOut: '18:40', status: 'late', workHours: 9.4 },
  { id: 'EMP-006', name: '陈静', department: '人力资源部', checkIn: '08:45', checkOut: '18:20', status: 'normal', workHours: 9.6 },
  { id: 'EMP-007', name: '杨光', department: '技术部', checkIn: '—', checkOut: '—', status: 'absent', workHours: 0 },
  { id: 'EMP-008', name: '周婷', department: '市场部', checkIn: '08:52', checkOut: '18:08', status: 'normal', workHours: 9.3 },
  { id: 'EMP-009', name: '孙磊', department: '运营部', checkIn: '09:12', checkOut: '18:25', status: 'late', workHours: 9.2 },
  { id: 'EMP-010', name: '吴雪', department: '技术部', checkIn: '08:50', checkOut: '17:45', status: 'early', workHours: 8.9 },
  { id: 'EMP-011', name: '郑凯', department: '销售部', checkIn: '—', checkOut: '—', status: 'absent', workHours: 0 },
  { id: 'EMP-012', name: '黄丽', department: '财务部', checkIn: '08:56', checkOut: '18:15', status: 'normal', workHours: 9.3 },
  { id: 'EMP-013', name: '林峰', department: '技术部', checkIn: '09:05', checkOut: '18:30', status: 'late', workHours: 9.4 },
  { id: 'EMP-014', name: '何芳', department: '市场部', checkIn: '08:47', checkOut: '18:22', status: 'normal', workHours: 9.6 },
]

/* ===================== 主组件 ===================== */

export default function Attendance() {
  const today = new Date().toISOString().slice(0, 10)
  const [date, setDate] = useState(today)

  const columns: Column<AttendanceRecord>[] = [
    {
      key: 'id',
      label: '工号',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.id}</span>,
    },
    {
      key: 'name',
      label: '姓名',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    { key: 'department', label: '部门', sortable: true },
    {
      key: 'checkIn',
      label: '上班打卡',
      align: 'center',
      render: (r) => (
        <span
          className={cn(
            'inline-flex items-center gap-1 tabular-nums',
            r.checkIn === '—' ? 'text-slate-600' : r.status === 'late' ? 'text-status-paused' : 'text-slate-300',
          )}
        >
          <LogIn className="w-3.5 h-3.5" />
          {r.checkIn}
        </span>
      ),
    },
    {
      key: 'checkOut',
      label: '下班打卡',
      align: 'center',
      render: (r) => (
        <span
          className={cn(
            'inline-flex items-center gap-1 tabular-nums',
            r.checkOut === '—' ? 'text-slate-600' : r.status === 'early' ? 'text-status-paused' : 'text-slate-300',
          )}
        >
          <LogOut className="w-3.5 h-3.5" />
          {r.checkOut}
        </span>
      ),
    },
    {
      key: 'status',
      label: '状态',
      render: (r) => {
        const cfg = statusConfig[r.status]
        return (
          <StatusBadge variant={cfg.variant} dot>
            {cfg.label}
          </StatusBadge>
        )
      },
    },
    {
      key: 'workHours',
      label: '工时',
      align: 'right',
      sortable: true,
      render: (r) => (
        <span className={cn('tabular-nums', r.workHours === 0 ? 'text-slate-600' : 'text-accent font-medium')}>
          {r.workHours > 0 ? `${r.workHours.toFixed(1)} h` : '—'}
        </span>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="考勤管理"
        description="员工考勤打卡、出勤统计与异常监控"
        icon={<Clock className="w-5 h-5" />}
        actions={
          <div className="flex items-center gap-2">
            <CalendarDays className="w-4 h-4 text-slate-500" />
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className={cn(inputClass, 'w-auto !py-1.5 text-xs')}
            />
          </div>
        }
      />

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="出勤率"
          value="96.8%"
          icon={<CalendarCheck className="w-5 h-5" />}
          variant="accent"
          trend="环比 +0.5%"
          trendUp
        />
        <StatCard
          label="今日迟到"
          value={4}
          icon={<Clock className="w-5 h-5" />}
          variant="warning"
          trend="阈值 9:00"
        />
        <StatCard
          label="今日请假"
          value={7}
          icon={<UserX className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="加班总时长"
          value="156 h"
          icon={<TimerReset className="w-5 h-5" />}
          variant="primary"
          trend="本周累计"
          trendUp
        />
      </div>

      {/* 今日考勤记录 */}
      <Card
        title="今日考勤记录"
        action={
          <span className="text-xs text-slate-500">
            日期：<span className="text-slate-300 tabular-nums">{date}</span>
          </span>
        }
      >
        <DataTable
          columns={columns}
          data={attendanceRecords}
          searchKeys={['name']}
          searchPlaceholder="搜索姓名..."
          pageSize={10}
        />
      </Card>
    </div>
  )
}
