import { useState, Fragment } from 'react'
import {
  GitBranch,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Check,
  X,
  ChevronRight,
  Timer,
  User,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  StatCard,
  type Column,
} from '../components/ui'
import { cn, formatDateTime } from '../lib/utils'
import { AgentApprovalPanel } from '../components/AgentApprovalPanel'

/* ===================== 类型定义 ===================== */

type RiskLevel = 'normal' | 'sensitive' | 'high_risk'
type ApprovalStatus = 'pending' | 'approved' | 'rejected'

interface ApprovalRequest {
  id: string
  applicant: string
  department: string
  request_type: string
  risk_level: RiskLevel
  amount: number
  submitted_at: string
  status: ApprovalStatus
  timeout_warning: boolean
}

/* ===================== 审批级别配置 ===================== */

interface ApprovalLevel {
  key: RiskLevel
  name: string
  timeout: string
  description: string
  chain: string[]
  accent: 'accent' | 'primary' | 'danger'
}

const APPROVAL_LEVELS: ApprovalLevel[] = [
  {
    key: 'normal',
    name: '普通审批',
    timeout: '1 个工作日',
    description: '常规业务申请，金额 < 5 万元',
    chain: ['直属主管', '部门经理'],
    accent: 'accent',
  },
  {
    key: 'sensitive',
    name: '敏感审批',
    timeout: '2 个工作日',
    description: '涉及资金 / 合同，金额 5 万 - 100 万元',
    chain: ['部门经理', '财务总监', 'CFO'],
    accent: 'primary',
  },
  {
    key: 'high_risk',
    name: '高风险审批',
    timeout: '3 个工作日',
    description: '重大决策事项，金额 > 100 万元',
    chain: ['部门经理', '分管副总裁', 'CEO', '董事会'],
    accent: 'danger',
  },
]

const RISK_CONFIG: Record<RiskLevel, { label: string; variant: 'success' | 'info' | 'danger' }> = {
  normal: { label: '普通', variant: 'success' },
  sensitive: { label: '敏感', variant: 'info' },
  high_risk: { label: '高风险', variant: 'danger' },
}

const STATUS_CONFIG: Record<ApprovalStatus, { label: string; variant: 'warning' | 'success' | 'danger' }> = {
  pending: { label: '待审批', variant: 'warning' },
  approved: { label: '已批准', variant: 'success' },
  rejected: { label: '已拒绝', variant: 'danger' },
}

const accentMap: Record<ApprovalLevel['accent'], { text: string; bg: string; border: string; dot: string }> = {
  accent: { text: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/20', dot: 'bg-accent' },
  primary: { text: 'text-primary', bg: 'bg-primary/10', border: 'border-primary/20', dot: 'bg-primary' },
  danger: { text: 'text-status-failed', bg: 'bg-status-failed/10', border: 'border-status-failed/20', dot: 'bg-status-failed' },
}

/* ===================== Mock 审批请求 ===================== */

const INITIAL_REQUESTS: ApprovalRequest[] = [
  {
    id: 'REQ-2026-0718',
    applicant: '张明',
    department: '采购部',
    request_type: '采购审批',
    risk_level: 'normal',
    amount: 32000,
    submitted_at: '2026-07-28T09:15:00',
    status: 'pending',
    timeout_warning: false,
  },
  {
    id: 'REQ-2026-0712',
    applicant: '李芳',
    department: '市场部',
    request_type: '报销审批',
    risk_level: 'normal',
    amount: 18600,
    submitted_at: '2026-07-26T14:30:00',
    status: 'pending',
    timeout_warning: true,
  },
  {
    id: 'REQ-2026-0705',
    applicant: '王强',
    department: '法务部',
    request_type: '合同审批',
    risk_level: 'sensitive',
    amount: 480000,
    submitted_at: '2026-07-27T16:20:00',
    status: 'pending',
    timeout_warning: false,
  },
  {
    id: 'REQ-2026-0698',
    applicant: '赵敏',
    department: '财务部',
    request_type: '预算调整',
    risk_level: 'sensitive',
    amount: 760000,
    submitted_at: '2026-07-25T10:00:00',
    status: 'pending',
    timeout_warning: true,
  },
  {
    id: 'REQ-2026-0691',
    applicant: '陈伟',
    department: '运营部',
    request_type: '大额支付',
    risk_level: 'high_risk',
    amount: 2350000,
    submitted_at: '2026-07-27T11:45:00',
    status: 'pending',
    timeout_warning: false,
  },
  {
    id: 'REQ-2026-0685',
    applicant: '刘洋',
    department: '采购部',
    request_type: '采购审批',
    risk_level: 'normal',
    amount: 9800,
    submitted_at: '2026-07-28T08:30:00',
    status: 'approved',
    timeout_warning: false,
  },
  {
    id: 'REQ-2026-0680',
    applicant: '孙磊',
    department: '法务部',
    request_type: '合同审批',
    risk_level: 'sensitive',
    amount: 156000,
    submitted_at: '2026-07-27T09:00:00',
    status: 'approved',
    timeout_warning: false,
  },
]

/* ===================== 主组件 ===================== */

export default function ApprovalWorkflow() {
  const [requests, setRequests] = useState<ApprovalRequest[]>(INITIAL_REQUESTS)
  const [todayApproved, setTodayApproved] = useState(12)

  const pendingCount = requests.filter((r) => r.status === 'pending').length
  const timeoutCount = requests.filter((r) => r.status === 'pending' && r.timeout_warning).length

  const handleApprove = (id: string) => {
    setRequests((prev) =>
      prev.map((r) => (r.id === id ? { ...r, status: 'approved' as ApprovalStatus } : r)),
    )
    setTodayApproved((n) => n + 1)
  }

  const handleReject = (id: string) => {
    setRequests((prev) =>
      prev.map((r) => (r.id === id ? { ...r, status: 'rejected' as ApprovalStatus } : r)),
    )
  }

  // 各级别待处理数
  const levelPendingCount = (key: RiskLevel) =>
    requests.filter((r) => r.risk_level === key && r.status === 'pending').length

  const columns: Column<ApprovalRequest>[] = [
    {
      key: 'applicant',
      label: '申请人',
      sortable: true,
      render: (r) => (
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
            <User className="w-3.5 h-3.5 text-primary" />
          </div>
          <div className="min-w-0">
            <p className="text-sm text-white truncate">{r.applicant}</p>
            <p className="text-[11px] text-slate-500 truncate">{r.department}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'request_type',
      label: '请求类型',
      sortable: true,
      render: (r) => (
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-200">{r.request_type}</span>
          <span className="text-[11px] text-slate-500 tabular-nums">
            ¥{r.amount.toLocaleString('zh-CN')}
          </span>
        </div>
      ),
    },
    {
      key: 'risk_level',
      label: '风险级别',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={(RISK_CONFIG[r.risk_level] ?? Object.values(RISK_CONFIG)[0]).variant} dot>
          {(RISK_CONFIG[r.risk_level] ?? Object.values(RISK_CONFIG)[0]).label}
        </StatusBadge>
      ),
    },
    {
      key: 'submitted_at',
      label: '提交时间',
      sortable: true,
      render: (r) => (
        <span className="text-xs text-slate-400 tabular-nums">{formatDateTime(r.submitted_at)}</span>
      ),
    },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={(STATUS_CONFIG[r.status] ?? Object.values(STATUS_CONFIG)[0]).variant} dot>
          {(STATUS_CONFIG[r.status] ?? Object.values(STATUS_CONFIG)[0]).label}
        </StatusBadge>
      ),
    },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <div className="flex items-center justify-center gap-2">
          {r.status === 'pending' ? (
            <>
              <button
                onClick={() => handleApprove(r.id)}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-accent/10 text-accent hover:bg-accent/20 transition-colors"
              >
                <Check className="w-3.5 h-3.5" />
                批准
              </button>
              <button
                onClick={() => handleReject(r.id)}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-status-failed/10 text-status-failed hover:bg-status-failed/20 transition-colors"
              >
                <X className="w-3.5 h-3.5" />
                拒绝
              </button>
            </>
          ) : (
            <span className="text-xs text-slate-600">已处理</span>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="审批工作流"
        description="多级审批流程配置与待办请求处理"
        icon={<GitBranch className="w-5 h-5" />}
      />

      {/* Agent 工具人审（write_high 中断任务，批准后自动恢复执行） */}
      <AgentApprovalPanel />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          label="待审批"
          value={pendingCount}
          icon={<Clock className="w-5 h-5" />}
          variant="warning"
        />
        <StatCard
          label="今日已审批"
          value={todayApproved}
          icon={<CheckCircle2 className="w-5 h-5" />}
          variant="accent"
          trend="今日累计"
          trendUp
        />
        <StatCard
          label="超时预警"
          value={timeoutCount}
          icon={<AlertTriangle className="w-5 h-5" />}
          variant="danger"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 左侧：审批流程列表 */}
        <div className="lg:col-span-1 space-y-4">
          <div className="flex items-center gap-2">
            <GitBranch className="w-4 h-4 text-primary" />
            <h3 className="text-sm font-semibold text-white">审批流程配置</h3>
          </div>
          {APPROVAL_LEVELS.map((level) => {
            const cfg = accentMap[level.accent] ?? Object.values(accentMap)[0]
            const pending = levelPendingCount(level.key)
            return (
              <div
                key={level.key}
                className="bg-bg-card/80 backdrop-blur-sm rounded-xl border border-[#1e2536] p-4"
              >
                {/* 头部 */}
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className={cn('w-2 h-2 rounded-full', cfg.dot)} />
                    <span className="text-sm font-semibold text-white">{level.name}</span>
                  </div>
                  {pending > 0 && (
                    <span
                      className={cn(
                        'inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border',
                        cfg.bg,
                        cfg.text,
                        cfg.border,
                      )}
                    >
                      {pending} 待处理
                    </span>
                  )}
                </div>

                {/* 超时 */}
                <div className="flex items-center gap-1.5 mb-2 text-xs text-slate-400">
                  <Timer className="w-3.5 h-3.5 text-status-paused" />
                  超时时限：<span className="text-slate-300 font-medium">{level.timeout}</span>
                </div>

                {/* 描述 */}
                <p className="text-[11px] text-slate-500 mb-3 leading-relaxed">{level.description}</p>

                {/* 审批链 */}
                <div className="pt-3 border-t border-[#1e2536]">
                  <p className="text-[11px] text-slate-500 mb-2">审批链路</p>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {level.chain.map((node, i) => (
                      <Fragment key={node}>
                        <span
                          className={cn(
                            'px-2 py-1 rounded-md text-[11px] border',
                            cfg.bg,
                            cfg.text,
                            cfg.border,
                          )}
                        >
                          {node}
                        </span>
                        {i < level.chain.length - 1 && (
                          <ChevronRight className="w-3.5 h-3.5 text-slate-600" />
                        )}
                      </Fragment>
                    ))}
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {/* 右侧：审批请求列表 */}
        <div className="lg:col-span-2">
          <Card>
            <DataTable
              columns={columns}
              data={requests}
              searchKeys={['applicant', 'request_type', 'department']}
              searchPlaceholder="搜索申请人 / 请求类型 / 部门..."
              pageSize={10}
            />
          </Card>
        </div>
      </div>
    </div>
  )
}
