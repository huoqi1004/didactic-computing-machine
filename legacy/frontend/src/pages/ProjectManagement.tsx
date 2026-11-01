import { useState, useEffect, useCallback } from 'react'
import {
  FolderKanban,
  PlayCircle,
  CheckCircle2,
  AlertTriangle,
  Wallet,
  Eye,
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

/* ===================== 类型定义 ===================== */

type ProjectStatus = 'planning' | 'in_progress' | 'completed' | 'paused'

interface Project {
  id: string
  code: string
  name: string
  manager: string
  start_date: string
  end_date: string
  progress: number
  status: ProjectStatus
  budget: number
  description: string
  department: string
  members: number
}

/* ===================== 状态配置 ===================== */

const STATUS_CONFIG: Record<
  ProjectStatus,
  { label: string; variant: 'info' | 'primary' | 'success' | 'warning' }
> = {
  planning: { label: '规划中', variant: 'info' },
  in_progress: { label: '进行中', variant: 'primary' },
  completed: { label: '已完成', variant: 'success' },
  paused: { label: '已暂停', variant: 'warning' },
}

/** 后端项目状态 → 前端 ProjectStatus 映射 */
const PROJECT_STATUS_MAP: Record<string, ProjectStatus> = {
  planning: 'planning',
  in_progress: 'in_progress',
  pending_acceptance: 'in_progress',
  completed: 'completed',
  cancelled: 'paused',
  paused: 'paused',
}

/** 后端项目统计响应结构（字段均为可选，兼容降级） */
interface ProjectStats {
  total_projects?: number
  by_status?: Record<string, number>
  by_department?: Record<string, number>
  total_budget?: number
  total_cost?: number
  over_budget_projects?: number
  budget_warning_projects?: number
}

/* ===================== 数据映射 ===================== */

/** 将后端项目数据（snake_case）映射为前端 Project 结构 */
function mapProject(raw: any): Project {
  const rawStatus = String(raw.status ?? 'in_progress')
  const status: ProjectStatus = PROJECT_STATUS_MAP[rawStatus] ?? 'in_progress'
  const progress = Number(
    raw.progress ?? (status === 'completed' ? 100 : 0),
  )
  return {
    id: String(raw.project_id ?? raw.id ?? ''),
    code: String(raw.code ?? raw.project_id ?? raw.id ?? ''),
    name: String(raw.name ?? '—'),
    manager: String(raw.manager ?? '—'),
    start_date: String(raw.start_date ?? ''),
    end_date: String(raw.end_date ?? ''),
    progress,
    status,
    budget: Number(raw.budget ?? 0),
    description: String(raw.description ?? ''),
    department: String(raw.department ?? ''),
    members: Number(raw.members ?? 0),
  }
}

/* ===================== 主组件 ===================== */

export default function ProjectManagement() {
  const [projects, setProjects] = useState<Project[]>([])
  const [stats, setStats] = useState<ProjectStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedProject, setSelectedProject] = useState<Project | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    const [list, projectStats] = await Promise.all([
      apiService.getProjects(),
      apiService.getProjectStats(),
    ])
    setProjects((Array.isArray(list) ? list : []).map(mapProject))
    setStats(projectStats ?? null)
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const statusDist = stats?.by_status ?? {}
  const inProgressCount =
    (statusDist.in_progress ?? 0) + (statusDist.pending_acceptance ?? 0)
  const completedCount = statusDist.completed ?? 0
  const delayedCount = stats?.over_budget_projects ?? 0
  const totalBudget = stats?.total_budget ?? 0

  const columns: Column<Project>[] = [
    {
      key: 'code',
      label: '项目编号',
      sortable: true,
      render: (row) => <span className="font-mono text-xs text-primary">{row.code}</span>,
    },
    {
      key: 'name',
      label: '项目名称',
      sortable: true,
      render: (row) => <span className="font-medium text-white">{row.name}</span>,
    },
    {
      key: 'manager',
      label: '负责人',
      render: (row) => <span className="text-slate-300">{row.manager}</span>,
    },
    {
      key: 'start_date',
      label: '开始日期',
      sortable: true,
      render: (row) => <span className="text-slate-400 text-xs">{row.start_date}</span>,
    },
    {
      key: 'end_date',
      label: '结束日期',
      sortable: true,
      render: (row) => <span className="text-slate-400 text-xs">{row.end_date}</span>,
    },
    {
      key: 'progress',
      label: '进度',
      sortable: true,
      render: (row) => (
        <div className="flex items-center gap-2 min-w-[120px]">
          <div className="flex-1 h-1.5 rounded-full bg-bg-input overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all',
                row.progress === 100
                  ? 'bg-accent'
                  : row.status === 'paused'
                    ? 'bg-status-paused'
                    : 'bg-primary',
              )}
              style={{ width: `${row.progress}%` }}
            />
          </div>
          <span className="text-xs text-slate-400 tabular-nums w-9 text-right">{row.progress}%</span>
        </div>
      ),
    },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (row) => {
        const cfg = STATUS_CONFIG[row.status] ?? Object.values(STATUS_CONFIG)[0]
        return (
          <StatusBadge variant={cfg.variant} dot>
            {cfg.label}
          </StatusBadge>
        )
      },
    },
    {
      key: 'budget',
      label: '预算',
      sortable: true,
      align: 'right',
      render: (row) => (
        <span className="tabular-nums text-slate-300">
          ¥{row.budget.toLocaleString('zh-CN')}
        </span>
      ),
    },
    {
      key: 'action',
      label: '操作',
      align: 'center',
      render: (row) => (
        <button
          onClick={(e) => {
            e.stopPropagation()
            setSelectedProject(row)
          }}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors text-xs"
        >
          <Eye className="w-3.5 h-3.5" />
          详情
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="项目管理"
        description="企业项目全生命周期跟踪与预算监控"
        icon={<FolderKanban className="w-5 h-5" />}
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="进行中项目"
          value={inProgressCount}
          icon={<PlayCircle className="w-5 h-5" />}
          trend="较上月 +2"
          trendUp
          variant="primary"
        />
        <StatCard
          label="已完成"
          value={completedCount}
          icon={<CheckCircle2 className="w-5 h-5" />}
          trend="本季新增 12"
          trendUp
          variant="accent"
        />
        <StatCard
          label="延期项目"
          value={delayedCount}
          icon={<AlertTriangle className="w-5 h-5" />}
          trend="需关注"
          trendUp={false}
          variant="warning"
        />
        <StatCard
          label="总预算"
          value={`¥${totalBudget.toLocaleString('zh-CN')}`}
          icon={<Wallet className="w-5 h-5" />}
          variant="primary"
        />
      </div>

      {/* 项目列表 */}
      <Card title="项目列表">
        <DataTable
          columns={columns}
          data={projects}
          loading={loading}
          pageSize={10}
          searchKeys={['name']}
          searchPlaceholder="搜索项目名称..."
          onRowClick={(row) => setSelectedProject(row)}
        />
      </Card>

      {/* 项目详情弹窗 */}
      <Modal
        open={!!selectedProject}
        onClose={() => setSelectedProject(null)}
        title="项目详情"
        size="lg"
      >
        {selectedProject && <ProjectDetail project={selectedProject} />}
      </Modal>
    </div>
  )
}

/* ===================== 项目详情组件 ===================== */

function ProjectDetail({ project }: { project: Project }) {
  const cfg = STATUS_CONFIG[project.status] ?? Object.values(STATUS_CONFIG)[0]
  const progressColor =
    project.progress === 100
      ? 'bg-accent'
      : project.status === 'paused'
        ? 'bg-status-paused'
        : 'bg-primary'

  return (
    <div className="space-y-5">
      {/* 项目头部 */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="font-mono text-xs text-primary">{project.code}</span>
            <StatusBadge variant={cfg.variant} dot>
              {cfg.label}
            </StatusBadge>
          </div>
          <h3 className="text-lg font-bold text-white">{project.name}</h3>
          <p className="text-sm text-slate-400 mt-1">{project.description}</p>
        </div>
      </div>

      {/* 进度条 */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-slate-500 font-medium">项目进度</span>
          <span className="text-sm font-bold text-white tabular-nums">{project.progress}%</span>
        </div>
        <div className="h-2 rounded-full bg-bg-input overflow-hidden">
          <div
            className={cn('h-full rounded-full transition-all', progressColor)}
            style={{ width: `${project.progress}%` }}
          />
        </div>
      </div>

      {/* 信息网格 */}
      <div className="grid grid-cols-2 gap-4">
        <InfoItem label="负责人" value={project.manager} />
        <InfoItem label="所属部门" value={project.department} />
        <InfoItem label="开始日期" value={project.start_date} />
        <InfoItem label="结束日期" value={project.end_date} />
        <InfoItem label="项目成员" value={`${project.members} 人`} />
        <InfoItem
          label="项目预算"
          value={`¥${project.budget.toLocaleString('zh-CN')}`}
          highlight
        />
      </div>
    </div>
  )
}

function InfoItem({
  label,
  value,
  highlight,
}: {
  label: string
  value: string
  highlight?: boolean
}) {
  return (
    <div className="p-3 rounded-lg bg-bg-input border border-[#1e2536]">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p
        className={cn(
          'text-sm font-medium tabular-nums',
          highlight ? 'text-accent' : 'text-white',
        )}
      >
        {value}
      </p>
    </div>
  )
}
