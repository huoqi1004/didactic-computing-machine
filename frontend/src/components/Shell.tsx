/**
 * 全局布局壳（WorkBuddy 风格）：左侧菜单栏 + 右侧功能内容区。
 * 侧边栏底部为用户区：头像/姓名/角色 + 弹出菜单（个人设置/切换账号/退出登录）。
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
      {/* ===== 左侧菜单栏 ===== */}
      <aside className="w-56 shrink-0 bg-navy text-white flex flex-col">
        {/* 顶部 Logo */}
        <div className="flex items-center gap-2.5 px-5 h-14 border-b border-white/10">
          <Brain size={22} className="text-steel-light" />
          <span className="font-semibold tracking-wide">企业数字化大脑</span>
        </div>
        {/* 中部导航 */}
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'}
                     className={({ isActive }) =>
                       `flex items-center gap-3 rounded-md px-3 py-2.5 text-sm transition-colors ${
                         isActive ? 'bg-steel/60 font-medium'
                                  : 'text-white/75 hover:bg-white/10 hover:text-white'}`}>
              <Icon size={17} />
              {label}
            </NavLink>
          ))}
        </nav>
        {/* ===== 左下角：登录注册 + 设置 功能菜单按钮 ===== */}
        <div ref={menuRef} className="relative border-t border-white/10 p-3">
          {menuOpen && (
            <div className="absolute bottom-full left-3 right-3 mb-2 rounded-md bg-card text-ink shadow-lg border border-border overflow-hidden">
              <Link to="/settings" onClick={() => setMenuOpen(false)}
                    className="flex items-center gap-2.5 px-3 py-2.5 text-sm hover:bg-canvas">
                <Settings size={15} /> 个人设置
              </Link>
              <button onClick={() => { setMenuOpen(false); nav('/login') }}
                      className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm hover:bg-canvas text-left">
                <UserCog size={15} /> 切换账号 / 注册
              </button>
              <button onClick={logout}
                      className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-alert hover:bg-canvas text-left border-t border-border">
                <LogOut size={15} /> 退出登录
              </button>
            </div>
          )}
          <button onClick={() => setMenuOpen(v => !v)}
                  className="w-full flex items-center gap-2.5 rounded-md px-2 py-2 hover:bg-white/10">
            <span className="w-8 h-8 rounded-full bg-steel flex items-center justify-center text-sm font-medium shrink-0">
              {(user?.display_name || '用')[0]}
            </span>
            <span className="flex-1 min-w-0 text-left">
              <span className="block text-sm truncate">{user?.display_name || '加载中…'}</span>
              <span className="block text-xs text-white/60">
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
