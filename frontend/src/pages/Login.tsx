/**
 * 登录/注册页（商务办公风格：深蓝左栏 + 白卡表单）
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Brain } from 'lucide-react'
import api, { setToken } from '../lib/api'
import { useAppStore } from '../store'

type Tab = 'login' | 'register'

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
      {/* 左侧品牌栏 */}
      <div className="hidden md:flex w-1/2 bg-navy text-white flex-col justify-between p-10">
        <div className="flex items-center gap-2.5">
          <Brain size={26} className="text-steel-light" />
          <span className="text-lg font-semibold tracking-wide">企业数字化大脑</span>
        </div>
        <div className="space-y-3">
          <h1 className="text-3xl font-bold leading-snug">Agent-Native ERP<br/>让每个岗位拥有数字分身</h1>
          <p className="text-white/70 text-sm leading-relaxed">
            知识库 · 实时资料 · 智能协作 · 能力市场<br/>
            审计哈希链全程留痕，权限分级安全可控
          </p>
        </div>
        <p className="text-white/40 text-xs">© 2026 企业数字化大脑 · 商务办公平台</p>
      </div>

      {/* 右侧表单 */}
      <div className="flex-1 flex items-center justify-center bg-canvas p-6">
        <div className="w-full max-w-sm bg-card rounded-lg shadow-md border border-border p-8 space-y-5">
          {/* 登录/注册 Tab */}
          <div className="flex rounded-md border border-border overflow-hidden text-sm">
            {(['login', 'register'] as Tab[]).map(t => (
              <button key={t} type="button" onClick={() => { setTab(t); setError(''); setNotice('') }}
                      className={`flex-1 py-2 ${tab === t ? 'bg-navy text-white font-medium' : 'bg-card text-slate2 hover:bg-canvas'}`}>
                {t === 'login' ? '登 录' : '注 册'}
              </button>
            ))}
          </div>

          <form onSubmit={tab === 'login' ? doLogin : doRegister} className="space-y-4">
            <input value={username} onChange={e => setUsername(e.target.value)}
                   placeholder="用户名" minLength={3} required
                   className="w-full border border-border rounded px-3 py-2 text-sm"/>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                   placeholder="密码（至少6位）" minLength={6} required
                   className="w-full border border-border rounded px-3 py-2 text-sm"/>
            {tab === 'register' && (
              <>
                <input value={displayName} onChange={e => setDisplayName(e.target.value)}
                       placeholder="姓名（选填）"
                       className="w-full border border-border rounded px-3 py-2 text-sm"/>
                <input value={department} onChange={e => setDepartment(e.target.value)}
                       placeholder="部门（选填）"
                       className="w-full border border-border rounded px-3 py-2 text-sm"/>
                <p className="text-xs text-slate2">注册后需管理员审核激活方可登录</p>
              </>
            )}
            {error && <p className="text-alert text-xs">{error}</p>}
            {notice && <p className="text-ok text-xs">{notice}</p>}
            <button disabled={loading}
                    className="w-full bg-navy text-white rounded py-2 text-sm font-medium hover:bg-navy-dark disabled:opacity-50">
              {loading ? (tab === 'login' ? '登录中…' : '注册中…')
                       : (tab === 'login' ? '登 录' : '提交注册')}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
