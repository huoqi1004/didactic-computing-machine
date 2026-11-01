import { useState, useEffect, useCallback } from 'react'
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
import { apiService } from '../services/api'

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

/* ===================== 数据映射 ===================== */

/** 解析 HH:MM 时间为分钟数 */
function parseTimeMinutes(t: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(t)
  return m ? Number(m[1]) * 60 + Number(m[2]) : null
}

/** 根据上下班打卡时间计算工时（小时），无法解析时返回 0 */
function computeWorkHours(checkIn: string, checkOut: string): number {
  const ci = parseTimeMinutes(checkIn)
  const co = parseTimeMinutes(checkOut)
  if (ci == null || co == null) return 0
  return Math.max(0, (co - ci) / 60)
}

/** 将后端返回的考勤记录映射为前端 AttendanceRecord 结构（兼容 snake_case / camelCase） */
function mapAttendanceRecord(raw: Record<string, unknown>): AttendanceRecord {
  const rawStatus = String(raw.status ?? 'normal')
  let status: AttendanceStatus = 'normal'
  if (rawStatus === 'late') status = 'late'
  else if (rawStatus === 'early') status = 'early'
  else if (rawStatus === 'absent' || rawStatus === 'leave') status = 'absent'
  const checkIn = String(raw.check_in ?? raw.checkIn ?? '—')
  const checkOut = String(raw.check_out ?? raw.checkOut ?? '—')
  return {
    id: String(raw.id ?? raw.employee_id ?? raw.record_id ?? ''),
    name: String(raw.name ?? raw.employee_name ?? raw.employee_id ?? '—'),
    department: String(raw.department ?? raw.dept ?? ''),
    checkIn,
    checkOut,
    status,
    workHours: Number(raw.work_hours ?? raw.workHours) || computeWorkHours(checkIn, checkOut),
  }
}

/* ===================== 主组件 ===================== */

export default function Attendance() {
  const today = new Date().toISOString().slice(0, 10)
  const [date, setDate] = useState(today)
  const [records, setRecords] = useState<AttendanceRecord[]>([])
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async (d: string) => {
    setLoading(true)
    const list = await apiService.getAttendance({ start_date: d, end_date: d })
    setRecords((Array.isArray(list) ? list : []).map(mapAttendanceRecord))
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData(date)
  }, [date, fetchData])

  const presentCount = records.filter((r) => r.status !== 'absent').length
  const attendanceRate = records.length > 0 ? (presentCount / records.length) * 100 : 0
  const lateCount = records.filter((r) => r.status === 'late').length
  const absentCount = records.filter((r) => r.status === 'absent').length
  const overtimeTotal = records.reduce((s, r) => s + Math.max(0, r.workHours - 8), 0)

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
          value={`${attendanceRate.toFixed(1)}%`}
          icon={<CalendarCheck className="w-5 h-5" />}
          variant="accent"
          trend="环比 +0.5%"
          trendUp
        />
        <StatCard
          label="今日迟到"
          value={lateCount}
          icon={<Clock className="w-5 h-5" />}
          variant="warning"
          trend="阈值 9:00"
        />
        <StatCard
          label="今日请假"
          value={absentCount}
          icon={<UserX className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="加班总时长"
          value={`${Math.round(overtimeTotal)} h`}
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
          data={records}
          loading={loading}
          searchKeys={['name']}
          searchPlaceholder="搜索姓名..."
          pageSize={10}
        />
      </Card>
    </div>
  )
}
