/**
 * 全局布局壳（WorkBuddy 风格 × 未来科技浅蓝）：
 * 上方：新对话按钮 + 会话历史（点击进入，右键删除）
 * 下方：功能导航（知识库/资料库/消息/能力市场/看板/管理台）
 * 最底：用户区弹出菜单（个人设置/切换账号注册/退出登录）+ 今日 token 额度
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { BarChart3, Bell, BookOpen, Brain, Check, ChevronUp, FolderOpen, History,
         LogOut, MessageSquare, MessageSquarePlus, Settings,
         Shield, Store, Trash2, UserCog, X } from 'lucide-react'
import api, { clearToken } from '../lib/api'
import { useAppStore, ROLE_LABELS } from '../store'

const NAV_ITEMS = [
  { to: '/knowledge', label: '知识库', icon: BookOpen, roles: null },
  { to: '/records', label: '资料库', icon: FolderOpen, roles: null },
  { to: '/im', label: '消息', icon: MessageSquare, roles: null },
  { to: '/market', label: '能力市场', icon: Store, roles: null },
  { to: '/dashboard', label: '看板', icon: BarChart3,
    roles: ['leader', 'chairman', 'sys_admin'] },
  { to: '/admin', label: '管理台', icon: Shield,
    roles: ['sys_admin', 'dept_admin'] },
]

interface Conv { id: number; title: string; workspace: string }

export default function Shell() {
  const { user, clearUser, convVersion, bumpConv, quota, setQuota } = useAppStore()
  const nav = useNavigate()
  const [search] = useSearchParams()
  const activeConv = Number(search.get('c') || 0)
  const [convs, setConvs] = useState<Conv[]>([])
  const [historyOpen, setHistoryOpen] = useState(true)
  const [menuOpen, setMenuOpen] = useState(false)
  const [ctxMenu, setCtxMenu] = useState<{ id: number; x: number; y: number } | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const loadConvs = useCallback(() => {
    api.get('/conversation').then(r => setConvs(r.items || [])).catch(() => {})
  }, [])
  useEffect(() => { loadConvs() }, [loadConvs, convVersion])

  // 待办事项（含安全告警推送，M10）
  const [todos, setTodos] = useState<any[]>([])
  const [todosOpen, setTodosOpen] = useState(false)
  const loadTodos = useCallback(() => {
    api.get('/todos').then(r => setTodos(r.items || [])).catch(() => {})
  }, [])
  useEffect(() => { loadTodos() }, [loadTodos, convVersion])

  const finishTodo = async (id: number) => {
    try { await api.post(`/todos/${id}/done`) } catch {}
    loadTodos()
  }

  // 今日 token 额度
  useEffect(() => {
    api.get('/me/quota').then(r => setQuota(r)).catch(() => {})
  }, [setQuota, convVersion])

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node))
        setMenuOpen(false)
      setCtxMenu(null)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const logout = () => { clearToken(); clearUser(); nav('/login', { replace: true }) }

  const deleteConv = async (id: number) => {
    try { await api.delete(`/conversation/${id}`) } catch {}
    bumpConv()
    if (activeConv === id) nav('/')
  }

  const newConversation = () => nav('/')

  const items = NAV_ITEMS.filter(n => !n.roles || n.roles.includes(user?.role || ''))
  const quotaPct = quota && quota.limit > 0
    ? Math.min(100, Math.round((quota.used / quota.limit) * 100)) : 0

  return (
    <div className="flex h-screen overflow-hidden" onClick={() => setMenuOpen(false)}>
      {/* ===== 左侧菜单栏 ===== */}
      <aside className="w-60 shrink-0 bg-gradient-brand-v text-white flex flex-col relative">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-40 tech-grid opacity-60"
             style={{ maskImage: 'linear-gradient(180deg, black, transparent)' }} />

        {/* 顶部 Logo */}
        <div className="relative flex items-center gap-2.5 px-5 h-14 border-b border-white/10">
          <span className="w-8 h-8 rounded-lg bg-gradient-brand shadow-glow flex items-center justify-center shrink-0">
            <Brain size={18} className="text-white" />
          </span>
          <span className="font-display font-semibold tracking-wide text-[15px]">企业数字化大脑</span>
        </div>

        {/* ===== 上方：新对话 + 会话历史 ===== */}
        <div className="relative px-3 pt-3">
          <button onClick={newConversation}
                  className="w-full flex items-center justify-center gap-2 bg-gradient-brand shadow-glow rounded-lg py-2.5 text-sm font-medium hover:opacity-90 transition-opacity">
            <MessageSquarePlus size={16} /> 新对话
          </button>
          <button onClick={() => setHistoryOpen(v => !v)}
                  className="w-full flex items-center gap-2 mt-3 px-2 py-1.5 text-xs text-white/60 hover:text-white">
            <History size={13} /> 会话历史
            <span className="ml-auto">{convs.length}</span>
            <ChevronUp size={13} className={`transition-transform ${historyOpen ? '' : 'rotate-180'}`} />
          </button>
        </div>
        {historyOpen && (
          <div className="relative flex-1 min-h-0 overflow-y-auto px-3 pb-2 space-y-0.5">
            {convs.length === 0 && (
              <p className="text-xs text-white/40 px-2 py-2">暂无历史会话</p>
            )}
            {convs.map(c => (
              <button key={c.id}
                      onClick={() => nav(`/?c=${c.id}`)}
                      onContextMenu={e => {
                        e.preventDefault()
                        setCtxMenu({ id: c.id, x: e.clientX, y: e.clientY })
                      }}
                      className={`w-full text-left px-2.5 py-2 rounded-lg text-xs group transition-colors ${
                        activeConv === c.id ? 'bg-gradient-pill shadow-glow text-white'
                                            : 'text-white/70 hover:bg-white/10'}`}>
                <span className="block truncate">{c.title}</span>
                <span className="block text-[10px] text-white/45 truncate">📁 {c.workspace}</span>
              </button>
            ))}
          </div>
        )}
        {!historyOpen && <div className="flex-1" />}

        {/* ===== 待办事项（含安全告警推送） ===== */}
        <div className="relative px-3 pb-1">
          <button onClick={() => setTodosOpen(v => !v)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-white/60 hover:text-white">
            <Bell size={13} /> 待办事项
            {todos.length > 0 && (
              <span className="ml-1 min-w-4 h-4 px-1 rounded-full bg-alert text-white text-[10px] flex items-center justify-center">
                {todos.length}
              </span>
            )}
            <ChevronUp size={13} className={`ml-auto transition-transform ${todosOpen ? '' : 'rotate-180'}`} />
          </button>
          {todosOpen && (
            <div className="mt-1 space-y-0.5 max-h-44 overflow-y-auto">
              {todos.length === 0 && <p className="text-[11px] text-white/40 px-2 py-1">暂无待办</p>}
              {todos.slice(0, 8).map(t => (
                <div key={t.id} className="flex items-start gap-1.5 px-2 py-1.5 rounded-lg hover:bg-white/10 group">
                  <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${t.source === '安全告警' ? 'bg-alert' : 'bg-steel-light'}`} />
                  <button onClick={() => t.link && nav(t.link)}
                          className="flex-1 min-w-0 text-left" title={t.title}>
                    <span className="block text-[11px] leading-snug text-white/85 break-all line-clamp-2">{t.title}</span>
                    <span className="block text-[10px] text-white/45">{t.source}</span>
                  </button>
                  <button onClick={() => finishTodo(t.id)} title="标记完成"
                          className="opacity-0 group-hover:opacity-100 text-white/50 hover:text-ok shrink-0">
                    <Check size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ===== 下方：功能导航 ===== */}
        <nav className="relative border-t border-white/10 px-3 py-2 space-y-0.5">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to}
                     className={({ isActive }) =>
                       `flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition-all ${
                         isActive ? 'bg-gradient-pill shadow-glow font-medium text-white'
                                  : 'text-white/70 hover:bg-white/10 hover:text-white'}`}>
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* ===== 最底：用户区（登录注册+设置菜单） ===== */}
        <div ref={menuRef} className="relative border-t border-white/10 p-3" onClick={e => e.stopPropagation()}>
          {/* 今日额度 */}
          {quota && (
            <div className="px-2 pb-2">
              <div className="flex justify-between text-[10px] text-white/50 mb-1">
                <span>今日额度</span>
                <span>{quota.used.toLocaleString()} / {quota.limit.toLocaleString()}</span>
              </div>
              <div className="h-1 rounded-full bg-white/15 overflow-hidden">
                <div className={`h-full rounded-full ${quotaPct > 90 ? 'bg-alert' : 'bg-gradient-brand'}`}
                     style={{ width: `${Math.max(quotaPct, 2)}%` }} />
              </div>
            </div>
          )}
          {menuOpen && (
            <div className="absolute bottom-full left-3 right-3 mb-2 rounded-lg bg-card text-ink shadow-lift border border-border overflow-hidden z-20">
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
          <button onClick={e => { e.stopPropagation(); setMenuOpen(v => !v) }}
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
      <main className="flex-1 overflow-hidden bg-canvas">
        <Outlet />
      </main>

      {/* 会话右键菜单 */}
      {ctxMenu && (
        <div className="fixed z-50 bg-card text-ink rounded-lg shadow-lift border border-border py-1 w-36"
             style={{ left: ctxMenu.x, top: ctxMenu.y }}>
          <button onClick={() => { deleteConv(ctxMenu.id); setCtxMenu(null) }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-alert hover:bg-red-50 text-left">
            <Trash2 size={14} /> 删除会话
          </button>
        </div>
      )}
    </div>
  )
}
