import { useState, useEffect, useCallback } from 'react'
import {
  UserPlus,
  Briefcase,
  Building2,
  FileText,
  Users,
  Mail,
  Phone,
  GraduationCap,
  Wallet,
  Eye,
  Flame,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'
import { apiService } from '../services/api'
import Can from '../components/Can'

/* ===================== 类型定义 ===================== */

type JobStatus = 'urgent' | 'open' | 'paused' | 'closed'
type ResumeStatus = 'screening' | 'interviewing' | 'offered' | 'rejected'

interface JobPosition {
  id: string
  title: string
  department: string
  headcount: number
  resumes: number
  status: JobStatus
}

interface Resume {
  id: string
  name: string
  position: string
  education: string
  years: number
  status: ResumeStatus
  phone: string
  email: string
  expectSalary: string
  appliedAt: string
}

/* ===================== 状态配置 ===================== */

const jobStatusConfig: Record<
  JobStatus,
  { label: string; variant: 'primary' | 'success' | 'warning' | 'neutral' }
> = {
  urgent: { label: '紧急', variant: 'primary' },
  open: { label: '在招', variant: 'success' },
  paused: { label: '暂停', variant: 'warning' },
  closed: { label: '已结束', variant: 'neutral' },
}

const resumeStatusConfig: Record<
  ResumeStatus,
  { label: string; variant: 'neutral' | 'info' | 'success' | 'danger' }
> = {
  screening: { label: '待筛选', variant: 'neutral' },
  interviewing: { label: '面试中', variant: 'info' },
  offered: { label: '已 offer', variant: 'success' },
  rejected: { label: '已淘汰', variant: 'danger' },
}

/* ===================== 后端统计类型 ===================== */

/** 后端招聘统计响应结构（字段均为可选，兼容降级） */
interface RecruitmentStats {
  total_positions?: number
  open_positions?: number
  total_candidates?: number
  candidates_by_status?: Record<string, number>
  total_interviews?: number
  total_offers?: number
  accepted_offers?: number
  offer_acceptance_rate?: number
}

/* ===================== 数据映射 ===================== */

/** 将后端职位数据（snake_case）映射为前端 JobPosition 结构 */
function mapPosition(raw: any): JobPosition {
  const rawStatus = String(raw.status ?? 'open')
  let status: JobStatus = 'open'
  if (rawStatus === 'closed') status = 'closed'
  else if (rawStatus === 'paused') status = 'paused'
  return {
    id: String(raw.position_id ?? raw.id ?? ''),
    title: String(raw.title ?? '—'),
    department: String(raw.department ?? '—'),
    headcount: Number(raw.headcount) || 0,
    resumes: Number(raw.resumes) || 0,
    status,
  }
}

/** 将后端候选人数据（snake_case）映射为前端 Resume 结构 */
function mapCandidate(raw: any, positionMap: Record<string, string>): Resume {
  const rawStatus = String(raw.status ?? 'new')
  let status: ResumeStatus = 'screening'
  if (rawStatus === 'interviewing') status = 'interviewing'
  else if (rawStatus === 'offered' || rawStatus === 'accepted') status = 'offered'
  else if (rawStatus === 'rejected') status = 'rejected'

  const positionId = String(raw.position_id ?? '')
  const positionTitle = positionMap[positionId] || String(raw.position ?? '—')
  const appliedAt = String(raw.created_at ?? raw.applied_at ?? '')
  const dateStr = appliedAt ? appliedAt.slice(0, 10) : ''

  return {
    id: String(raw.candidate_id ?? raw.id ?? ''),
    name: String(raw.name ?? '—'),
    position: positionTitle,
    education: String(raw.education ?? '—'),
    years: Number(raw.years) || 0,
    status,
    phone: String(raw.phone ?? '—'),
    email: String(raw.email ?? '—'),
    expectSalary: String(raw.expect_salary ?? raw.expectSalary ?? '—'),
    appliedAt: dateStr,
  }
}

/* ===================== 主组件 ===================== */

export default function Recruitment() {
  const [positions, setPositions] = useState<JobPosition[]>([])
  const [resumes, setResumes] = useState<Resume[]>([])
  const [stats, setStats] = useState<RecruitmentStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Resume | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [posList, recruitmentStats, candidateList] = await Promise.all([
      apiService.getRecruitmentPositions(),
      apiService.getRecruitmentStats(),
      apiService.getCandidates(),
    ])
    const mappedPositions = (Array.isArray(posList) ? posList : []).map(mapPosition)
    setPositions(mappedPositions)
    setStats(recruitmentStats ?? null)

    // 构建 positionId -> title 映射，用于候选人展示
    const positionMap: Record<string, string> = {}
    mappedPositions.forEach((p) => {
      positionMap[p.id] = p.title
    })
    setResumes((Array.isArray(candidateList) ? candidateList : []).map((raw) => mapCandidate(raw, positionMap)))
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const resumeColumns: Column<Resume>[] = [
    {
      key: 'name',
      label: '姓名',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    { key: 'position', label: '应聘岗位', sortable: true },
    {
      key: 'education',
      label: '学历',
      align: 'center',
      render: (r) => <span className="text-slate-300">{r.education}</span>,
    },
    {
      key: 'years',
      label: '工作年限',
      align: 'center',
      sortable: true,
      render: (r) => <span className="tabular-nums text-slate-300">{r.years} 年</span>,
    },
    {
      key: 'status',
      label: '状态',
      render: (r) => {
        const cfg = resumeStatusConfig[r.status]
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
      render: (r) => (
        <button
          onClick={() => setSelected(r)}
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
        title="招聘管理"
        description="招聘岗位管理、简历筛选与面试流程跟踪"
        icon={<UserPlus className="w-5 h-5" />}
      />

      {/* 概览统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="在招岗位"
          value={stats?.open_positions ?? positions.filter((p) => p.status === 'open' || p.status === 'urgent').length}
          icon={<Briefcase className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="收到简历"
          value={stats?.total_candidates ?? resumes.length}
          icon={<FileText className="w-5 h-5" />}
          variant="accent"
          trend="持续收集中"
          trendUp
        />
        <StatCard
          label="面试中"
          value={stats?.candidates_by_status?.interviewing ?? resumes.filter((r) => r.status === 'interviewing').length}
          icon={<Users className="w-5 h-5" />}
          variant="warning"
        />
        <StatCard
          label="已发 offer"
          value={stats?.total_offers ?? resumes.filter((r) => r.status === 'offered').length}
          icon={<UserPlus className="w-5 h-5" />}
          variant="accent"
          trend="本月累计"
          trendUp
        />
      </div>

      {/* 在招岗位列表 */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-white">在招岗位</h3>
          <span className="text-xs text-slate-500">共 {positions.length} 个岗位</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {loading ? (
            <div className="col-span-full text-center py-12">
              <p className="text-sm text-slate-500">加载中...</p>
            </div>
          ) : positions.length === 0 ? (
            <div className="col-span-full text-center py-12">
              <p className="text-sm text-slate-500">暂无在招岗位</p>
            </div>
          ) : (
            positions.map((job) => {
            const cfg = jobStatusConfig[job.status]
            return (
              <div
                key={job.id}
                className="bg-bg-card/80 backdrop-blur-sm rounded-xl border border-[#1e2536] p-4 hover:border-primary/40 hover:shadow-card transition-all"
              >
                <div className="flex items-start justify-between mb-3 gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      {job.status === 'urgent' && <Flame className="w-3.5 h-3.5 text-primary shrink-0" />}
                      <h4 className="text-sm font-semibold text-white truncate">{job.title}</h4>
                    </div>
                    <p className="text-xs text-slate-500 mt-1 flex items-center gap-1">
                      <Building2 className="w-3 h-3" />
                      {job.department}
                    </p>
                  </div>
                  <StatusBadge variant={cfg.variant} dot>
                    {cfg.label}
                  </StatusBadge>
                </div>
                <div className="flex items-center gap-4 text-xs pt-3 border-t border-[#1e2536]">
                  <div className="flex items-center gap-1.5">
                    <Users className="w-3.5 h-3.5 text-primary" />
                    <span className="text-slate-500">招聘</span>
                    <span className="text-white font-medium tabular-nums">{job.headcount}</span>
                    <span className="text-slate-500">人</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <FileText className="w-3.5 h-3.5 text-accent" />
                    <span className="text-slate-500">简历</span>
                    <span className="text-accent font-medium tabular-nums">{job.resumes}</span>
                  </div>
                </div>
              </div>
            )
          })
          )}
        </div>
      </div>

      {/* 最新简历 */}
      <Card title="最新简历" action={<span className="text-xs text-slate-500">最近投递</span>}>
        <DataTable
          columns={resumeColumns}
          data={resumes}
          loading={loading}
          searchKeys={['name', 'position']}
          searchPlaceholder="搜索姓名 / 应聘岗位..."
          pageSize={10}
        />
      </Card>

      {/* 简历详情 */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="简历详情" size="md">
        {selected && <ResumeDetail resume={selected} />}
      </Modal>
    </div>
  )
}

/* ===================== 简历详情 ===================== */

function ResumeDetail({ resume }: { resume: Resume }) {
  const cfg = resumeStatusConfig[resume.status]
  const items = [
    { icon: Users, label: '姓名', value: resume.name },
    { icon: Briefcase, label: '应聘岗位', value: resume.position },
    { icon: GraduationCap, label: '学历', value: resume.education },
    { icon: Users, label: '工作年限', value: `${resume.years} 年` },
    { icon: Wallet, label: '期望薪资', value: resume.expectSalary },
    { icon: Phone, label: '联系电话', value: resume.phone },
    { icon: Mail, label: '邮箱', value: resume.email },
    { icon: FileText, label: '投递时间', value: resume.appliedAt },
  ]

  return (
    <div className="space-y-5">
      {/* 头部 */}
      <div className="flex items-center gap-4 p-4 rounded-xl bg-bg-input border border-[#2a3142]">
        <div className="w-14 h-14 rounded-full bg-gradient-to-br from-primary/30 to-accent/30 flex items-center justify-center text-xl font-bold text-white shrink-0">
          {resume.name.charAt(0)}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-white">{resume.name}</h3>
            <StatusBadge variant={cfg.variant} dot>
              {cfg.label}
            </StatusBadge>
          </div>
          <p className="text-sm text-slate-400 mt-0.5">
            {resume.position} · {resume.education} · {resume.years} 年经验
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

      <div className="flex justify-end gap-3 pt-1">
        <Can resource="document" action="create">
          <button className="btn-secondary">下载简历</button>
        </Can>
        <Can resource="recruitment" action="update">
          <button className={cn('btn-primary')}>安排面试</button>
        </Can>
      </div>
    </div>
  )
}
