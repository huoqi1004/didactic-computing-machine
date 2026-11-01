/**
 * 登录/注册页（未来科技浅蓝 × 商务办公）
 * 左侧：深空蓝渐变品牌栏（科技网格+光晕）；右侧：登录/注册双 Tab 白卡。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Brain, ShieldCheck, Sparkles, Users } from 'lucide-react'
import api, { setToken } from '../lib/api'
import { useAppStore } from '../store'

type Tab = 'login' | 'register'

const FEATURES = [
  { icon: Sparkles, title: 'AI 数字分身', desc: '每个岗位拥有专属智能助理' },
  { icon: ShieldCheck, title: '全链路可信', desc: '审计哈希链留痕 · 防篡改' },
  { icon: Users, title: '五级权限协同', desc: '知识 · 资料 · IM · 能力市场' },
]

export default function Login() {
  const [tab, setTab] = useState<Tab>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [department, setDepartment] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const nav = useNavigate()
  const setUser = useAppStore(s => s.setUser)

  const doLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      const r = await api.post('/auth/login', { username, password })
      setToken(r.access_token)
      setUser(r.user)
      nav('/', { replace: true })
    } catch (err: any) {
      setError(err?.response?.data?.detail || '登录失败')
    } finally { setLoading(false) }
  }

  const doRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true); setError(''); setNotice('')
    try {
      const r = await api.post('/auth/register',
        { username, password, display_name: displayName, department })
      setNotice(r.message || '注册成功，等待管理员审核激活')
      setTab('login')
    } catch (err: any) {
      setError(err?.response?.data?.detail || '注册失败')
    } finally { setLoading(false) }
  }

  return (
    <div className="min-h-screen flex">
      {/* ===== 左侧品牌栏：深空蓝渐变 + 科技网格 ===== */}
      <div className="hidden md:flex w-1/2 bg-gradient-brand-v text-white flex-col justify-between p-12 relative overflow-hidden">
        <div className="absolute inset-0 tech-grid" />
        {/* 光晕 */}
        <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full pointer-events-none"
             style={{ background: 'radial-gradient(circle, rgba(56,189,248,.28), transparent 65%)' }} />
        <div className="absolute -bottom-32 -left-20 w-[28rem] h-[28rem] rounded-full pointer-events-none"
             style={{ background: 'radial-gradient(circle, rgba(37,99,235,.3), transparent 65%)' }} />

        <div className="relative flex items-center gap-2.5">
          <span className="w-10 h-10 rounded-xl bg-gradient-brand shadow-glow flex items-center justify-center">
            <Brain size={22} />
          </span>
          <span className="text-lg font-display font-semibold tracking-wide">企业数字化大脑</span>
        </div>

        <div className="relative space-y-8">
          <h1 className="font-display text-4xl font-bold leading-tight">
            Agent-Native ERP
            <span className="block mt-2 bg-gradient-brand bg-clip-text text-transparent">
              让每个岗位拥有数字分身
            </span>
          </h1>
          <div className="space-y-4">
            {FEATURES.map(({ icon: Icon, title, desc }) => (
              <div key={title} className="flex items-center gap-3.5">
                <span className="w-10 h-10 rounded-lg bg-white/10 border border-white/15 flex items-center justify-center shrink-0">
                  <Icon size={18} className="text-steel-light" />
                </span>
                <div>
                  <p className="text-sm font-medium">{title}</p>
                  <p className="text-xs text-white/55 mt-0.5">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="relative text-white/40 text-xs">© 2026 企业数字化大脑 · 商务办公平台</p>
      </div>

      {/* ===== 右侧表单 ===== */}
      <div className="flex-1 flex items-center justify-center bg-canvas p-6 relative">
        <div className="absolute inset-0 tech-grid opacity-40 md:hidden" />
        <div className="w-full max-w-sm bg-card rounded-2xl shadow-soft border border-border p-8 space-y-5 relative">
          <div className="h-1 w-12 rounded-full bg-gradient-brand mx-auto" />
          {/* 登录/注册 Tab */}
          <div className="flex rounded-lg bg-canvas border border-border overflow-hidden text-sm p-1 gap-1">
            {(['login', 'register'] as Tab[]).map(t => (
              <button key={t} type="button" onClick={() => { setTab(t); setError(''); setNotice('') }}
                      className={`flex-1 py-2 rounded-md transition-all ${
                        tab === t ? 'bg-gradient-brand shadow-glow text-white font-medium'
                                  : 'text-slate2 hover:text-navy'}`}>
                {t === 'login' ? '登 录' : '注 册'}
              </button>
            ))}
          </div>

          <form onSubmit={tab === 'login' ? doLogin : doRegister} className="space-y-4">
            <input value={username} onChange={e => setUsername(e.target.value)}
                   placeholder="用户名" minLength={3} required
                   className="w-full border border-border rounded-lg px-3.5 py-2.5 text-sm bg-white transition-shadow focus:shadow-glow focus:border-steel-light"/>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                   placeholder="密码（至少6位）" minLength={6} required
                   className="w-full border border-border rounded-lg px-3.5 py-2.5 text-sm bg-white transition-shadow focus:shadow-glow focus:border-steel-light"/>
            {tab === 'register' && (
              <>
                <input value={displayName} onChange={e => setDisplayName(e.target.value)}
                       placeholder="姓名（选填）"
                       className="w-full border border-border rounded-lg px-3.5 py-2.5 text-sm bg-white focus:border-steel-light"/>
                <input value={department} onChange={e => setDepartment(e.target.value)}
                       placeholder="部门（选填）"
                       className="w-full border border-border rounded-lg px-3.5 py-2.5 text-sm bg-white focus:border-steel-light"/>
                <p className="text-xs text-slate2">注册后需管理员审核激活方可登录</p>
              </>
            )}
            {error && <p className="text-alert text-xs">{error}</p>}
            {notice && <p className="text-ok text-xs">{notice}</p>}
            <button disabled={loading}
                    className="w-full bg-gradient-brand rounded-lg py-2.5 text-sm font-medium text-white shadow-glow hover:opacity-90 transition-opacity disabled:opacity-50">
              {loading ? (tab === 'login' ? '登录中…' : '注册中…')
                       : (tab === 'login' ? '登 录' : '提交注册')}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
