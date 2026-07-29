import { useState, useEffect, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  Bot,
  Package,
  Calculator,
  Activity,
  ShieldCheck,
  Menu,
  X,
  Cpu,
  Wifi,
  WifiOff,
  MessageSquare,
  AlertCircle,
  Receipt,
  Wallet,
  Landmark,
  LogOut,
  ChevronDown,
  User,
  Users,
  Clock,
  Banknote,
  UserPlus,
  Briefcase,
  TrendingUp,
  Megaphone,
  FileText,
  AlertTriangle,
  FolderKanban,
  BadgeCheck,
  Boxes,
  BarChart3,
  PiggyBank,
  Leaf,
  Shield,
  Key,
  GitBranch,
  BookOpen,
  Settings,
  type LucideIcon,
} from 'lucide-react'
import { useSystemStore } from '../stores/systemStore'
import { useAuthStore } from '../stores/authStore'
import { authService } from '../services/authService'
import { cn } from '../lib/utils'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  /** 访问所需资源权限，不填则仅需登录 */
  resource?: string
  /** 访问所需动作权限，不填则仅需登录 */
  action?: string
}

interface NavGroup {
  title: string
  items: NavItem[]
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: '总览',
    items: [
      { to: '/', label: '仪表盘', icon: LayoutDashboard },
      { to: '/agents', label: 'Agent 管理', icon: Bot, resource: 'agent', action: 'read' },
      { to: '/conversation', label: '对话操作', icon: MessageSquare, resource: 'agent', action: 'read' },
      { to: '/exceptions', label: '例外管理', icon: AlertCircle, resource: 'agent', action: 'read' },
      { to: '/events', label: '事件流', icon: Activity, resource: 'system', action: 'read' },
      { to: '/knowledge', label: '知识库', icon: BookOpen, resource: 'knowledge', action: 'read' },
    ],
  },
  {
    title: '财务',
    items: [
      { to: '/invoice', label: '发票管理', icon: Receipt, resource: 'invoice', action: 'read' },
      { to: '/reimbursement', label: '报销管理', icon: Wallet, resource: 'reimbursement', action: 'read' },
      { to: '/tax', label: '报税管理', icon: Landmark, resource: 'tax', action: 'read' },
      { to: '/accounting', label: '会计凭证', icon: Calculator, resource: 'accounting', action: 'read' },
    ],
  },
  {
    title: '人力资源',
    items: [
      { to: '/employees', label: '员工管理', icon: Users, resource: 'hr', action: 'read' },
      { to: '/attendance', label: '考勤管理', icon: Clock, resource: 'hr', action: 'read' },
      { to: '/payroll', label: '薪酬管理', icon: Banknote, resource: 'hr', action: 'read' },
      { to: '/recruitment', label: '招聘管理', icon: UserPlus, resource: 'recruitment', action: 'read' },
    ],
  },
  {
    title: '销售与市场',
    items: [
      { to: '/customers', label: '客户管理', icon: Briefcase, resource: 'crm', action: 'read' },
      { to: '/sales-pipeline', label: '销售漏斗', icon: TrendingUp, resource: 'sales', action: 'read' },
      { to: '/campaigns', label: '营销活动', icon: Megaphone, resource: 'marketing', action: 'read' },
    ],
  },
  {
    title: '法务与风控',
    items: [
      { to: '/contracts', label: '合同管理', icon: FileText, resource: 'contract', action: 'read' },
      { to: '/risk', label: '风险管理', icon: AlertTriangle, resource: 'risk', action: 'read' },
    ],
  },
  {
    title: '运营管理',
    items: [
      { to: '/inventory', label: '库存管理', icon: Package, resource: 'inventory', action: 'read' },
      { to: '/projects', label: '项目管理', icon: FolderKanban, resource: 'project', action: 'read' },
      { to: '/quality', label: '质量管理', icon: BadgeCheck, resource: 'quality', action: 'read' },
      { to: '/assets', label: '资产管理', icon: Boxes, resource: 'asset', action: 'read' },
    ],
  },
  {
    title: '战略分析',
    items: [
      { to: '/bi-reports', label: 'BI 报表', icon: BarChart3, resource: 'bi', action: 'read' },
      { to: '/budget', label: '预算管理', icon: PiggyBank, resource: 'budget', action: 'read' },
      { to: '/esg', label: 'ESG 管理', icon: Leaf, resource: 'esg', action: 'read' },
    ],
  },
  {
    title: '系统管理',
    items: [
      { to: '/roles', label: '角色管理', icon: Shield, resource: 'role', action: 'read' },
      { to: '/permissions', label: '权限管理', icon: Key, resource: 'permission', action: 'read' },
      { to: '/approvals', label: '审批工作流', icon: GitBranch, resource: 'system', action: 'read' },
      { to: '/security', label: '安全审计', icon: ShieldCheck, resource: 'security', action: 'read' },
      { to: '/cio-admin', label: 'CIO 管理后台', icon: Settings, resource: 'system', action: 'manage' },
    ],
  },
]

export default function Layout({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const wsConnected = useSystemStore((s) => s.wsConnected)
  const stats = useSystemStore((s) => s.stats)
  const { user, logout, hasPermission } = useAuthStore()
  const navigate = useNavigate()

  /** 检查菜单项是否对当前用户可见 */
  const canSee = (item: NavItem): boolean => {
    if (!item.resource || !item.action) return true
    return hasPermission(item.resource, item.action)
  }

  /** 过滤出当前用户可访问的菜单分组 */
  const visibleGroups = NAV_GROUPS
    .map((g) => ({ ...g, items: g.items.filter(canSee) }))
    .filter((g) => g.items.length > 0)

  const handleLogout = async () => {
    await authService.logout()
    logout()
    navigate('/login', { replace: true })
  }

  // 点击外部关闭用户菜单
  useEffect(() => {
    if (!userMenuOpen) return
    const handler = () => setUserMenuOpen(false)
    window.addEventListener('click', handler)
    return () => window.removeEventListener('click', handler)
  }, [userMenuOpen])

  return (
    <div className="min-h-screen flex bg-bg grid-bg">
      {/* ========== 侧边栏 ========== */}
      <aside
        className={cn(
          'fixed lg:sticky top-0 left-0 z-50 h-screen w-64 shrink-0',
          'bg-bg-card/95 backdrop-blur-md border-r border-[#1e2536]',
          'flex flex-col transition-transform duration-300',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
        )}
      >
        {/* Logo 区 */}
        <div className="h-16 flex items-center gap-3 px-5 border-b border-[#1e2536]">
          <div className="w-9 h-9 rounded-lg bg-gradient-primary flex items-center justify-center shadow-glow shrink-0">
            <Cpu className="w-5 h-5 text-white" />
          </div>
          <div className="min-w-0">
            <h1 className="text-sm font-bold text-white leading-tight">企业数字化大脑</h1>
            <p className="text-[11px] text-slate-500 leading-tight">Agent-Native ERP</p>
          </div>
          <button
            onClick={() => setSidebarOpen(false)}
            className="lg:hidden ml-auto text-slate-400 hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 导航菜单 — 分组（按权限过滤） */}
        <nav className="flex-1 overflow-y-auto py-3 px-3 space-y-3">
          {visibleGroups.map((group) => (
            <div key={group.title}>
              <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-600 uppercase tracking-wider">
                {group.title}
              </div>
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const Icon = item.icon
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      end={item.to === '/'}
                      onClick={() => setSidebarOpen(false)}
                      className={({ isActive }) =>
                        cn(
                          'group flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200',
                          isActive
                            ? 'bg-primary/15 text-primary border border-primary/30 shadow-glow'
                            : 'text-slate-400 hover:text-white hover:bg-bg-hover border border-transparent',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          <Icon
                            className={cn(
                              'w-[18px] h-[18px] shrink-0 transition-colors',
                              isActive ? 'text-primary' : 'text-slate-500 group-hover:text-slate-300',
                            )}
                          />
                          <div className="min-w-0">
                            <div className="truncate">{item.label}</div>
                          </div>
                        </>
                      )}
                    </NavLink>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* 连接状态 */}
        <div className="p-3 border-t border-[#1e2536]">
          <div className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-bg-input">
            <div className="flex items-center gap-2">
              {wsConnected ? (
                <Wifi className="w-4 h-4 text-accent" />
              ) : (
                <WifiOff className="w-4 h-4 text-status-paused" />
              )}
              <span className="text-xs font-medium text-slate-300">
                {wsConnected ? '已连接后端' : '演示模式'}
              </span>
            </div>
            <span className="flex items-center gap-1.5">
              <span
                className={cn(
                  'pulse-dot',
                  wsConnected ? 'bg-accent' : 'bg-status-paused',
                )}
              />
              <span className={cn('text-[10px]', wsConnected ? 'text-accent' : 'text-status-paused')}>
                {wsConnected ? 'Live' : 'Mock'}
              </span>
            </span>
          </div>
        </div>
      </aside>

      {/* 遮罩层（移动端） */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ========== 主内容区 ========== */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 顶栏 */}
        <header className="sticky top-0 z-30 h-16 flex items-center gap-4 px-4 lg:px-6 bg-bg-card/80 backdrop-blur-md border-b border-[#1e2536]">
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden text-slate-400 hover:text-white"
          >
            <Menu className="w-6 h-6" />
          </button>

          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-white">实时态势</span>
            <span className="hidden sm:inline text-xs text-slate-500">
              {stats ? `${stats.events_per_minute} 事件/分钟` : ''}
            </span>
          </div>

          <div className="ml-auto flex items-center gap-4">
            {/* 系统指标 */}
            <div className="hidden md:flex items-center gap-5 text-xs">
              <div className="flex items-center gap-1.5">
                <span className="text-slate-500">在线 Agent</span>
                <span className="text-accent font-semibold tabular-nums">
                  {stats?.active_agents ?? '--'}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-slate-500">成功率</span>
                <span className="text-primary font-semibold tabular-nums">
                  {stats ? `${stats.success_rate}%` : '--'}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-slate-500">待审批</span>
                <span className="text-status-paused font-semibold tabular-nums">
                  {stats?.pending_vouchers ?? '--'}
                </span>
              </div>
            </div>

            {/* 系统时钟 */}
            <SystemClock />

            {/* 用户菜单 */}
            {user && (
              <div className="relative" onClick={(e) => e.stopPropagation()}>
                <button
                  onClick={() => setUserMenuOpen(!userMenuOpen)}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-bg-input border border-[#2a3142] hover:border-primary/30 transition-colors"
                >
                  <div className="w-7 h-7 rounded-full bg-gradient-primary flex items-center justify-center shrink-0">
                    <User className="w-4 h-4 text-white" />
                  </div>
                  <div className="hidden sm:block text-left">
                    <div className="text-xs font-semibold text-white leading-tight">
                      {user.username}
                    </div>
                    <div className="text-[10px] text-slate-500 leading-tight">
                      {user.role_codes?.join(', ') || 'user'}
                    </div>
                  </div>
                  <ChevronDown
                    className={cn(
                      'w-4 h-4 text-slate-400 transition-transform',
                      userMenuOpen && 'rotate-180',
                    )}
                  />
                </button>

                {/* 下拉菜单 */}
                {userMenuOpen && (
                  <div className="absolute right-0 top-full mt-2 w-56 bg-bg-card border border-[#2a3142] rounded-xl shadow-2xl overflow-hidden z-50">
                    <div className="px-4 py-3 border-b border-[#1e2536]">
                      <div className="text-sm font-semibold text-white">{user.username}</div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {user.email || `${user.username}@company.com`}
                      </div>
                      <div className="text-xs text-primary mt-1">
                        {user.role_codes?.join(' · ') || 'user'}
                      </div>
                    </div>
                    <button
                      onClick={handleLogout}
                      className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-slate-400 hover:text-status-failed hover:bg-status-failed/5 transition-colors"
                    >
                      <LogOut className="w-4 h-4" />
                      退出登录
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </header>

        {/* 页面内容 */}
        <main className="flex-1 p-4 lg:p-6 overflow-x-hidden">{children}</main>
      </div>
    </div>
  )
}

/* 系统时钟组件 */
function SystemClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])
  return (
    <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-bg-input border border-[#2a3142]">
      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
      <span className="text-xs font-mono text-slate-300 tabular-nums">
        {now.toLocaleTimeString('zh-CN', { hour12: false })}
      </span>
    </div>
  )
}
