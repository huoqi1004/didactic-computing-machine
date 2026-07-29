import { useState } from 'react'
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

/* ===================== Mock 数据 ===================== */

const PROJECTS: Project[] = [
  {
    id: '1',
    code: 'PRJ-2026-001',
    name: 'ERP 系统升级',
    manager: '张明',
    start_date: '2026-01-15',
    end_date: '2026-06-30',
    progress: 68,
    status: 'in_progress',
    budget: 1280000,
    description: '对企业现有 ERP 系统进行全面升级，包括财务、库存、采购模块重构。',
    department: '信息技术部',
    members: 12,
  },
  {
    id: '2',
    code: 'PRJ-2026-002',
    name: '智能仓储建设',
    manager: '李华',
    start_date: '2026-02-01',
    end_date: '2026-08-15',
    progress: 45,
    status: 'in_progress',
    budget: 2560000,
    description: '建设自动化智能仓储中心，引入 AGV 机器人和 WMS 系统。',
    department: '供应链部',
    members: 18,
  },
  {
    id: '3',
    code: 'PRJ-2025-088',
    name: '官网改版',
    manager: '王芳',
    start_date: '2025-10-01',
    end_date: '2026-01-20',
    progress: 100,
    status: 'completed',
    budget: 380000,
    description: '企业官网全新改版，提升品牌形象与用户体验。',
    department: '市场部',
    members: 6,
  },
  {
    id: '4',
    code: 'PRJ-2026-003',
    name: '数据中台搭建',
    manager: '陈强',
    start_date: '2026-03-01',
    end_date: '2026-09-30',
    progress: 22,
    status: 'in_progress',
    budget: 3200000,
    description: '搭建企业级数据中台，统一数据治理与分析能力。',
    department: '信息技术部',
    members: 15,
  },
  {
    id: '5',
    code: 'PRJ-2025-076',
    name: 'ISO27001 认证',
    manager: '刘洋',
    start_date: '2025-08-01',
    end_date: '2026-02-28',
    progress: 90,
    status: 'in_progress',
    budget: 560000,
    description: '完成信息安全管理体系 ISO27001 认证全流程。',
    department: '安全合规部',
    members: 8,
  },
  {
    id: '6',
    code: 'PRJ-2025-092',
    name: '客户 CRM 迁移',
    manager: '赵敏',
    start_date: '2025-11-15',
    end_date: '2026-02-10',
    progress: 100,
    status: 'completed',
    budget: 720000,
    description: '将客户关系管理系统迁移至云端架构。',
    department: '销售部',
    members: 10,
  },
  {
    id: '7',
    code: 'PRJ-2026-004',
    name: '生产线自动化改造',
    manager: '孙伟',
    start_date: '2026-01-10',
    end_date: '2026-07-20',
    progress: 15,
    status: 'paused',
    budget: 4800000,
    description: '对现有生产线进行自动化改造，提升产能 40%。',
    department: '生产部',
    members: 22,
  },
  {
    id: '8',
    code: 'PRJ-2026-005',
    name: '移动办公 App 开发',
    manager: '周婷',
    start_date: '2026-02-20',
    end_date: '2026-05-30',
    progress: 55,
    status: 'in_progress',
    budget: 680000,
    description: '开发企业移动办公应用，支持审批、通讯、报表等功能。',
    department: '信息技术部',
    members: 9,
  },
  {
    id: '9',
    code: 'PRJ-2025-070',
    name: '财务共享中心建设',
    manager: '吴刚',
    start_date: '2025-06-01',
    end_date: '2025-12-31',
    progress: 100,
    status: 'completed',
    budget: 1850000,
    description: '建立财务共享服务中心，集中处理报销、凭证、结算业务。',
    department: '财务部',
    members: 14,
  },
  {
    id: '10',
    code: 'PRJ-2026-006',
    name: '供应商门户平台',
    manager: '郑磊',
    start_date: '2026-03-10',
    end_date: '2026-08-01',
    progress: 8,
    status: 'planning',
    budget: 920000,
    description: '搭建供应商协同门户，实现采购全流程线上化。',
    department: '采购部',
    members: 7,
  },
  {
    id: '11',
    code: 'PRJ-2025-095',
    name: '能耗监测系统',
    manager: '黄丽',
    start_date: '2025-09-15',
    end_date: '2026-01-10',
    progress: 100,
    status: 'completed',
    budget: 460000,
    description: '部署工厂能耗实时监测系统，助力节能减排。',
    department: '工程部',
    members: 5,
  },
  {
    id: '12',
    code: 'PRJ-2026-007',
    name: '员工培训平台',
    manager: '林雪',
    start_date: '2026-04-01',
    end_date: '2026-08-30',
    progress: 0,
    status: 'planning',
    budget: 320000,
    description: '搭建在线学习与培训管理平台，覆盖全员能力发展。',
    department: '人力资源部',
    members: 6,
  },
]

/* ===================== 主组件 ===================== */

export default function ProjectManagement() {
  const [selectedProject, setSelectedProject] = useState<Project | null>(null)

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
        const cfg = STATUS_CONFIG[row.status]
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
          value="18"
          icon={<PlayCircle className="w-5 h-5" />}
          trend="较上月 +2"
          trendUp
          variant="primary"
        />
        <StatCard
          label="已完成"
          value="86"
          icon={<CheckCircle2 className="w-5 h-5" />}
          trend="本季新增 12"
          trendUp
          variant="accent"
        />
        <StatCard
          label="延期项目"
          value="3"
          icon={<AlertTriangle className="w-5 h-5" />}
          trend="需关注"
          trendUp={false}
          variant="warning"
        />
        <StatCard
          label="总预算"
          value="¥12,580,000"
          icon={<Wallet className="w-5 h-5" />}
          variant="primary"
        />
      </div>

      {/* 项目列表 */}
      <Card title="项目列表">
        <DataTable
          columns={columns}
          data={PROJECTS}
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
  const cfg = STATUS_CONFIG[project.status]
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
