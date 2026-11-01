/**
 * 全局布局壳（WorkBuddy 风格 × 未来科技浅蓝）：
 * 深空蓝渐变侧边栏 + 右侧内容区；侧边栏底部为用户区弹出菜单。
 */
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { BarChart3, BookOpen, Brain, ChevronUp, FolderOpen,
         Home, LogOut, MessageSquare, Settings, Shield,
         Store, UserCog } from 'lucide-react'
import { clearToken } from '../lib/api'
import { useAppStore, ROLE_LABELS } from '../store'

const NAV_ITEMS = [
  { to: '/', label: '首页', icon: Home, roles: null },
  { to: '/knowledge', label: '知识库', icon: BookOpen, roles: null },
  { to: '/records', label: '资料库', icon: FolderOpen, roles: null },
  { to: '/im', label: '消息', icon: MessageSquare, roles: null },
  { to: '/market', label: '能力市场', icon: Store, roles: null },
  { to: '/dashboard', label: '看板', icon: BarChart3,
    roles: ['leader', 'chairman', 'sys_admin'] },
  { to: '/admin', label: '管理台', icon: Shield,
    roles: ['sys_admin', 'dept_admin'] },
]

export default function Shell() {
  const { user, clearUser } = useAppStore()
  const nav = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node))
        setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const logout = () => {
    clearToken(); clearUser(); nav('/login', { replace: true })
  }

  const items = NAV_ITEMS.filter(n => !n.roles || n.roles.includes(user?.role || ''))

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ===== 左侧菜单栏：深空蓝渐变 ===== */}
      <aside className="w-56 shrink-0 bg-gradient-brand-v text-white flex flex-col relative">
        {/* 顶部青色光晕 */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-40 tech-grid opacity-60"
             style={{ maskImage: 'linear-gradient(180deg, black, transparent)' }} />

        {/* 顶部 Logo */}
        <div className="relative flex items-center gap-2.5 px-5 h-14 border-b border-white/10">
          <span className="w-8 h-8 rounded-lg bg-gradient-brand shadow-glow flex items-center justify-center shrink-0">
            <Brain size={18} className="text-white" />
          </span>
          <span className="font-display font-semibold tracking-wide text-[15px]">企业数字化大脑</span>
        </div>

        {/* 中部导航 */}
        <nav className="relative flex-1 overflow-y-auto px-3 py-4 space-y-1">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'}
                     className={({ isActive }) =>
                       `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-all ${
                         isActive ? 'bg-gradient-pill shadow-glow font-medium text-white'
                                  : 'text-white/70 hover:bg-white/10 hover:text-white'}`}>
              <Icon size={17} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* ===== 左下角：登录注册 + 设置 功能菜单按钮 ===== */}
        <div ref={menuRef} className="relative border-t border-white/10 p-3">
          {menuOpen && (
            <div className="absolute bottom-full left-3 right-3 mb-2 rounded-lg bg-card text-ink shadow-lift border border-border overflow-hidden">
              <div className="h-0.5 bg-gradient-brand" />
              <Link to="/settings" onClick={() => setMenuOpen(false)}
                    className="flex items-center gap-2.5 px-3 py-2.5 text-sm hover:bg-canvas">
                <Settings size={15} className="text-navy" /> 个人设置
              </Link>
              <button onClick={() => { setMenuOpen(false); nav('/login') }}
                      className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm hover:bg-canvas text-left">
                <UserCog size={15} className="text-navy" /> 切换账号 / 注册
              </button>
              <button onClick={logout}
                      className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-alert hover:bg-red-50 text-left border-t border-border">
                <LogOut size={15} /> 退出登录
              </button>
            </div>
          )}
          <button onClick={() => setMenuOpen(v => !v)}
                  className="w-full flex items-center gap-2.5 rounded-lg px-2 py-2 hover:bg-white/10 transition-colors">
            <span className="w-8 h-8 rounded-full bg-gradient-brand shadow-glow flex items-center justify-center text-sm font-semibold shrink-0">
              {(user?.display_name || '用')[0]}
            </span>
            <span className="flex-1 min-w-0 text-left">
              <span className="block text-sm truncate">{user?.display_name || '加载中…'}</span>
              <span className="block text-xs text-white/55">
                {ROLE_LABELS[user?.role || ''] || user?.role || ''}
              </span>
            </span>
            <ChevronUp size={15} className={`text-white/60 transition-transform ${menuOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </aside>

      {/* ===== 右侧功能页面展示区 ===== */}
      <main className="flex-1 overflow-y-auto bg-canvas">
        <Outlet />
      </main>
    </div>
  )
}
