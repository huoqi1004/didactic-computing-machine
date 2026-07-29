import { useState } from 'react'
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

/* ===================== Mock 数据 ===================== */

const jobPositions: JobPosition[] = [
  { id: 'JOB-001', title: '高级前端工程师', department: '技术部', headcount: 2, resumes: 38, status: 'urgent' },
  { id: 'JOB-002', title: '产品经理', department: '产品部', headcount: 1, resumes: 25, status: 'open' },
  { id: 'JOB-003', title: '数据分析师', department: '技术部', headcount: 3, resumes: 42, status: 'open' },
  { id: 'JOB-004', title: '销售经理', department: '销售部', headcount: 2, resumes: 19, status: 'open' },
  { id: 'JOB-005', title: 'UI 设计师', department: '技术部', headcount: 1, resumes: 31, status: 'urgent' },
  { id: 'JOB-006', title: '财务主管', department: '财务部', headcount: 1, resumes: 12, status: 'open' },
  { id: 'JOB-007', title: '市场专员', department: '市场部', headcount: 2, resumes: 15, status: 'paused' },
  { id: 'JOB-008', title: '运营专员', department: '运营部', headcount: 2, resumes: 4, status: 'closed' },
]

const resumes: Resume[] = [
  { id: 'R-1001', name: '周明', position: '高级前端工程师', education: '本科', years: 5, status: 'interviewing', phone: '138-0011-2233', email: 'zhouming@mail.com', expectSalary: '25-30K', appliedAt: '2026-07-20' },
  { id: 'R-1002', name: '吴婷', position: '产品经理', education: '硕士', years: 3, status: 'screening', phone: '139-2233-4455', email: 'wuting@mail.com', expectSalary: '20-25K', appliedAt: '2026-07-22' },
  { id: 'R-1003', name: '郑昊', position: '数据分析师', education: '硕士', years: 4, status: 'offered', phone: '137-4455-6677', email: 'zhenghao@mail.com', expectSalary: '22-28K', appliedAt: '2026-07-15' },
  { id: 'R-1004', name: '王丽', position: 'UI 设计师', education: '本科', years: 2, status: 'interviewing', phone: '136-6677-8899', email: 'wangli@mail.com', expectSalary: '15-18K', appliedAt: '2026-07-21' },
  { id: 'R-1005', name: '刘强', position: '销售经理', education: '本科', years: 6, status: 'screening', phone: '135-8899-0011', email: 'liuqiang@mail.com', expectSalary: '18-25K', appliedAt: '2026-07-23' },
  { id: 'R-1006', name: '陈颖', position: '财务主管', education: '本科', years: 7, status: 'rejected', phone: '138-1122-3344', email: 'chenying@mail.com', expectSalary: '20-24K', appliedAt: '2026-07-12' },
  { id: 'R-1007', name: '黄磊', position: '高级前端工程师', education: '硕士', years: 8, status: 'offered', phone: '139-3344-5566', email: 'huanglei@mail.com', expectSalary: '30-35K', appliedAt: '2026-07-10' },
  { id: 'R-1008', name: '林娜', position: '运营专员', education: '大专', years: 1, status: 'screening', phone: '137-5566-7788', email: 'linna@mail.com', expectSalary: '8-10K', appliedAt: '2026-07-24' },
  { id: 'R-1009', name: '孙伟', position: '数据分析师', education: '本科', years: 3, status: 'interviewing', phone: '136-7788-9900', email: 'sunwei@mail.com', expectSalary: '18-22K', appliedAt: '2026-07-19' },
  { id: 'R-1010', name: '赵静', position: '市场专员', education: '本科', years: 2, status: 'rejected', phone: '135-9900-1122', email: 'zhaojing@mail.com', expectSalary: '10-12K', appliedAt: '2026-07-14' },
  { id: 'R-1011', name: '冯刚', position: '产品经理', education: '本科', years: 5, status: 'screening', phone: '138-2233-5566', email: 'fenggang@mail.com', expectSalary: '22-26K', appliedAt: '2026-07-25' },
  { id: 'R-1012', name: '许敏', position: 'UI 设计师', education: '硕士', years: 4, status: 'interviewing', phone: '139-4455-7788', email: 'xumin@mail.com', expectSalary: '18-22K', appliedAt: '2026-07-18' },
]

/* ===================== 主组件 ===================== */

export default function Recruitment() {
  const [selected, setSelected] = useState<Resume | null>(null)

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
          value={12}
          icon={<Briefcase className="w-5 h-5" />}
          variant="primary"
        />
        <StatCard
          label="收到简历"
          value={186}
          icon={<FileText className="w-5 h-5" />}
          variant="accent"
          trend="本周 +42"
          trendUp
        />
        <StatCard
          label="面试中"
          value={24}
          icon={<Users className="w-5 h-5" />}
          variant="warning"
        />
        <StatCard
          label="已发 offer"
          value={8}
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
          <span className="text-xs text-slate-500">共 {jobPositions.length} 个岗位</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {jobPositions.map((job) => {
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
          })}
        </div>
      </div>

      {/* 最新简历 */}
      <Card title="最新简历" action={<span className="text-xs text-slate-500">最近投递</span>}>
        <DataTable
          columns={resumeColumns}
          data={resumes}
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
        <button className="btn-secondary">下载简历</button>
        <button className={cn('btn-primary')}>安排面试</button>
      </div>
    </div>
  )
}
